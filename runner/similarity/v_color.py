"""v-color similarity channel (scheme §4 / appendix A).

Appendix A definition (verbatim, byte-exact):

    v-color | 128×80 RGB,每通道 >>6(4 级),64 bin 直方图,占比归一,cosine

Pipeline:
  1. Load the card's frozen main screenshot (``shot.png``).
  2. Alpha → white-background composite; convert to RGB
     (appendix A imaging: "alpha 白底合成").
  3. Resize to 128×80 with LANCZOS (appendix A imaging: "resize=LANCZOS").
  4. Quantize each 8-bit channel with ``>> 6`` → 4 levels {0,1,2,3}.
  5. 3-D histogram over (r,g,b) quantized levels, C-order flattened index
     ``r*16 + g*4 + b`` → 64 bins; counts normalized to proportions
     ("占比归一").
  6. Cosine similarity between the two 64-vectors.

§4 unified rule: zero-vector / empty / absent input on either side (double-empty
included) → ``s = None``. A histogram is a zero vector only when the source image
has no pixels (missing/empty screenshot); a real screenshot always yields a
strictly positive histogram, so ``s`` is ``None`` iff a side's artifact is
absent/undecodable/empty.

Cosine of two non-negative vectors lies in [0, 1]; identical vectors → 1.0.
No clamping is applied (scheme is silent on clamping for v-color, unlike v-ssim
/ d-geom / c-ncd which pin explicit clamps); floating-point drift is bounded by
machine epsilon.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

CHANNEL = "v-color"

# --- appendix A pinned constants (implement verbatim, never simplify) ---
RESIZE_W = 128          # 128×80 RGB
RESIZE_H = 80
CHANNEL_SHIFT = 6       # 每通道 >>6
LEVELS = 4              # >>6 on a uint8 → {0,1,2,3} (4 级)
NBINS = LEVELS ** 3     # 64 bin 直方图
RESAMPLE = Image.LANCZOS  # appendix A imaging: resize=LANCZOS
WHITE = (255, 255, 255)   # appendix A imaging: alpha 白底合成
SHOT_FILENAME = "shot.png"


# --------------------------------------------------------------------------
# image → histogram
# --------------------------------------------------------------------------
def _composite_white(img: Image.Image) -> Image.Image:
    """Composite any alpha over an opaque white background, return RGB."""
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info
    )
    if has_alpha:
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, WHITE + (255,))
        img = Image.alpha_composite(bg, rgba)
    return img.convert("RGB")


def histogram_from_image(img: Image.Image) -> np.ndarray | None:
    """64-bin proportion histogram of a PIL image, or ``None`` if degenerate.

    Returns ``None`` when the image has zero area (nothing to histogram) — the
    only way v-color produces a zero vector.
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return None
    rgb = _composite_white(img)
    resized = rgb.resize((RESIZE_W, RESIZE_H), RESAMPLE)
    arr = np.asarray(resized, dtype=np.uint8)  # (RESIZE_H, RESIZE_W, 3)
    if arr.size == 0:
        return None
    q = (arr >> CHANNEL_SHIFT).astype(np.int64)  # each in {0,1,2,3}
    idx = q[..., 0] * (LEVELS * LEVELS) + q[..., 1] * LEVELS + q[..., 2]  # C-order
    counts = np.bincount(idx.ravel(), minlength=NBINS).astype(np.float64)
    total = counts.sum()
    if total <= 0.0:
        return None
    return counts / total  # 占比归一


def _load_image(artifacts: Any) -> Image.Image | None:
    """Resolve a card's shot.png from flexible artifact references.

    Accepts:
      * ``PIL.Image.Image`` — used directly (test fixtures);
      * ``bytes`` — decoded as an image;
      * path-like to a directory — reads ``<dir>/shot.png``;
      * path-like to a file — read directly;
      * mapping with ``shot_png`` / ``shot.png`` / ``dir`` / ``path`` keys.
    Returns ``None`` when nothing decodable is found.
    """
    if artifacts is None:
        return None
    if isinstance(artifacts, Image.Image):
        return artifacts
    if isinstance(artifacts, (bytes, bytearray)):
        try:
            return Image.open(io.BytesIO(bytes(artifacts)))
        except Exception:
            return None
    if isinstance(artifacts, dict):
        for key in ("shot_png", "shot.png", "shot", "path", "file"):
            if key in artifacts and artifacts[key] is not None:
                return _load_image(artifacts[key])
        if "dir" in artifacts and artifacts["dir"] is not None:
            return _load_image(artifacts["dir"])
        return None
    # path-like (str / os.PathLike / Path)
    try:
        p = Path(artifacts)
    except TypeError:
        return None
    if p.is_dir():
        p = p / SHOT_FILENAME
    if not p.is_file():
        return None
    try:
        return Image.open(p)
    except Exception:
        return None


def _histogram_from_artifacts(artifacts: Any) -> np.ndarray | None:
    img = _load_image(artifacts)
    if img is None:
        return None
    try:
        return histogram_from_image(img)
    finally:
        # close only images we opened from disk/bytes, not caller-owned ones
        if not isinstance(artifacts, Image.Image):
            try:
                img.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# similarity
# --------------------------------------------------------------------------
def cosine(h_a: np.ndarray, h_b: np.ndarray) -> float | None:
    """Cosine similarity of two histograms; ``None`` if either is a zero vector."""
    na = float(np.dot(h_a, h_a))
    nb = float(np.dot(h_b, h_b))
    if na <= 0.0 or nb <= 0.0:
        return None
    num = float(np.dot(h_a, h_b))
    return num / (math.sqrt(na) * math.sqrt(nb))


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """v-color similarity between two cards.

    Returns ``{"s": float | None, ...diagnostics}``. ``s`` is ``None`` when
    either side's screenshot is absent / undecodable / empty (§4).
    """
    h_a = _histogram_from_artifacts(card_a_artifacts)
    h_b = _histogram_from_artifacts(card_b_artifacts)
    diag: dict[str, Any] = {
        "channel": CHANNEL,
        "nbins": NBINS,
        "a_present": h_a is not None,
        "b_present": h_b is not None,
    }
    if h_a is None or h_b is None:
        return {"s": None, **diag}
    s = cosine(h_a, h_b)
    return {"s": s, **diag}
