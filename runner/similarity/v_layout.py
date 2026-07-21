"""Channel **v-layout** — coarse spatial-layout similarity (scheme §4, appendix A).

Appendix A definition (byte-exact, implemented verbatim):

    v-layout | 32×20 灰度展平, cosine

Shared imaging conventions (appendix A "图像预处理"), applied here:
  * Pillow pinned; **resize = LANCZOS**.
  * grayscale = Rec.601 (0.299, 0.587, 0.114) — this is exactly Pillow's
    ``Image.convert("L")`` (ITU-R 601-2 luma), so we use it as the canonical
    grayscale operator.
  * alpha → white-background composite before grayscale.

Pipeline (per card):
  1. Decode shot.png.
  2. If it has alpha, composite over an opaque WHITE background.
  3. Rec.601 grayscale (``convert("L")``).
  4. LANCZOS resize to 32×20 (W×H).
  5. Flatten C-order → 640-dim vector (float64).

Similarity: cosine of the two 640-vectors. All grayscale values are ≥ 0, so
cosine ∈ [0, 1]; identical layout → 1. A vector is a **zero vector** only if the
grayscale image is entirely black (all pixels 0). Per §4 / appendix A "统一规则",
if EITHER side is a zero vector (double-zero included) → ``s = None``.

ORDERING NOTE (scheme-silent → FLAGGED, choice most consistent with stated rules):
the phrase "32×20 灰度" does not pin whether grayscale precedes or follows the
resize. We do **grayscale → resize**, matching every sibling visual row that
writes the grayscale operator before the target size (v-phash "灰度 LANCZOS→32×32",
v-dhash "灰度 17×16", v-edge "…灰度 float64"). See report for the flag.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# --- appendix-A constants (v-layout row + shared imaging) -----------------
GRID_W = 32                 # target width  (columns)
GRID_H = 20                 # target height (rows)
VEC_LEN = GRID_W * GRID_H   # 640-dim flattened vector
RESAMPLE = Image.LANCZOS    # shared imaging: resize = LANCZOS
GRAYSCALE_MODE = "L"        # Pillow "L" == Rec.601 (0.299, 0.587, 0.114)
SHOT_FILENAME = "shot.png"


# --------------------------------------------------------------------------
# artifact resolution
# --------------------------------------------------------------------------
def _shot_bytes(artifacts: Any) -> bytes:
    """Resolve raw PNG bytes for a card's main (frozen) screenshot.

    Accepts, in order of precedence:
      * raw ``bytes`` (the PNG itself),
      * a ``dict`` with one of: ``shot_png`` (bytes), ``shot_path`` (file),
        ``dir`` / ``path`` (card directory containing shot.png),
      * a ``str`` / ``os.PathLike`` pointing at either shot.png directly or the
        card directory.
    """
    if isinstance(artifacts, (bytes, bytearray)):
        return bytes(artifacts)

    if isinstance(artifacts, dict):
        if artifacts.get("shot_png") is not None:
            return bytes(artifacts["shot_png"])
        for key in ("shot_path", "dir", "path"):
            if artifacts.get(key) is not None:
                return _shot_bytes_from_path(Path(artifacts[key]))
        raise ValueError(
            "v-layout: artifacts dict lacks 'shot_png'/'shot_path'/'dir'/'path'"
        )

    if isinstance(artifacts, (str, os.PathLike)):
        return _shot_bytes_from_path(Path(artifacts))

    raise TypeError(f"v-layout: unsupported artifacts type {type(artifacts)!r}")


def _shot_bytes_from_path(p: Path) -> bytes:
    if p.is_dir():
        p = p / SHOT_FILENAME
    return p.read_bytes()


# --------------------------------------------------------------------------
# feature vector
# --------------------------------------------------------------------------
def layout_vector(png_bytes: bytes) -> np.ndarray:
    """Decode → white-composite → Rec.601 gray → LANCZOS 32×20 → flat float64."""
    with Image.open(io.BytesIO(png_bytes)) as im:
        im.load()
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, rgba)
        gray = im.convert(GRAYSCALE_MODE)                 # Rec.601 luma
        small = gray.resize((GRID_W, GRID_H), RESAMPLE)   # LANCZOS
    vec = np.asarray(small, dtype=np.float64).reshape(-1)  # C-order flatten
    assert vec.shape == (VEC_LEN,), vec.shape
    return vec


def _cosine(a: np.ndarray, b: np.ndarray) -> float | None:
    """Cosine similarity; None if either vector has zero norm (zero vector)."""
    na = float(np.sqrt(np.dot(a, a)))
    nb = float(np.sqrt(np.dot(b, b)))
    if na == 0.0 or nb == 0.0:
        return None
    if np.array_equal(a, b):
        return 1.0  # cosine of a vector with itself is exactly 1 (avoid sqrt noise)
    s = float(np.dot(a, b) / (na * nb))
    # grayscale is non-negative ⇒ s ∈ [0, 1] mathematically; clip float noise.
    return float(np.clip(s, 0.0, 1.0))


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """v-layout similarity between two cards.

    Returns ``{"s": float | None, ...diagnostics}``. ``s`` is None when either
    side's 32×20 grayscale vector is a zero vector (all-black), per §4.
    """
    va = layout_vector(_shot_bytes(card_a_artifacts))
    vb = layout_vector(_shot_bytes(card_b_artifacts))
    na = float(np.sqrt(np.dot(va, va)))
    nb = float(np.sqrt(np.dot(vb, vb)))
    s = _cosine(va, vb)
    return {
        "s": s,
        "channel": "v-layout",
        "grid": [GRID_W, GRID_H],
        "vec_len": VEC_LEN,
        "norm_a": na,
        "norm_b": nb,
        "zero_a": na == 0.0,
        "zero_b": nb == 0.0,
    }
