"""v-ssim similarity channel (scheme §4 / appendix A).

Appendix A definition (verbatim, byte-exact — never simplify):

    v-ssim | 输入 320×200 灰度 uint8;
             skimage.metrics.structural_similarity 钉版:
               win_size=7, gaussian_weights=False, data_range=255,
               channel_axis=None, K1=0.01, K2=0.03, use_sample_covariance=True;
             raw = 函数返回标量(含库内边界裁剪语义);
             正式通道值 v-ssim = clip((raw+1)/2, 0, 1);
             raw 仅作诊断字段;统计/golden/报告一律用映射值

Pipeline (per card):
  1. Load the card's frozen main screenshot (``shot.png``).
  2. Alpha → opaque-white-background composite; convert to RGB
     (appendix A imaging: "alpha 白底合成").
  3. Rec.601 grayscale via Pillow ``convert("L")`` (ITU-R 601-2 luma ==
     Rec.601 0.299/0.587/0.114 — the house grayscale operator, matching every
     sibling visual channel: v_edge / v_layout / v_dhash).
  4. LANCZOS resize to 320×200 (W×H)  → the "输入 320×200 灰度 uint8".
     ``convert("L")`` yields a uint8 "L" image, so LANCZOS resize stays in
     uint8 (PIL clamps to [0,255]); ``np.asarray`` gives the uint8 raster.
  5. ``skimage.metrics.structural_similarity`` with the pinned params → raw
     scalar in [-1, 1] (library already crops the win_size//2 boundary and
     averages the valid SSIM map).
  6. Formal value ``s = clip((raw+1)/2, 0, 1)``.  ``raw`` is kept as a
     diagnostic companion ("raw 仅作诊断字段", 并存).

ORDERING NOTE (scheme-silent → FLAGGED; choice most consistent with stated
rules): the phrase "输入 320×200 灰度" does not pin whether grayscale precedes or
follows the LANCZOS resize. We do **grayscale → resize**, matching every sibling
visual row that writes the grayscale operator before the target size (v-phash
"灰度 LANCZOS→32×32", v-dhash "灰度 17×16", v-layout "32×20 灰度") and the shared
house helper ``v_edge.gray_320x200``. (Grayscale-float-then-LANCZOS is also
infeasible via Pillow: F-mode LANCZOS downscaling produces NaN; the uint8-"L"
grayscale-then-LANCZOS path is the robust, byte-reproducible one.)

NULL SEMANTICS (§4 "零向量/空集/空 bag → null"; FLAGGED interpretation): v-ssim
is a raster metric with no feature vector/bag, so the only genuine degeneracy is
an **absent / undecodable / zero-area screenshot** on either side (double-absent
included) → ``s = None``. A valid all-black screenshot is NOT nulled: SSIM is
well-defined there (two blacks → raw = 1.0 via the C1 stabilizer), exactly as the
sibling v-color treats an all-black image as a valid non-zero histogram rather
than a zero vector. See report for the flag.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity

CHANNEL = "v-ssim"

# --- appendix A pinned constants (implement verbatim, never simplify) ---
TARGET_W = 320                 # 输入 320×200 (width / columns)
TARGET_H = 200                 # 输入 320×200 (height / rows)
WIN_SIZE = 7                   # skimage win_size=7
GAUSSIAN_WEIGHTS = False       # gaussian_weights=False
DATA_RANGE = 255               # data_range=255
CHANNEL_AXIS = None            # channel_axis=None (single grayscale plane)
K1 = 0.01                      # K1=0.01
K2 = 0.03                      # K2=0.03
USE_SAMPLE_COVARIANCE = True   # use_sample_covariance=True

RESAMPLE = Image.LANCZOS       # appendix A imaging: resize=LANCZOS
GRAYSCALE_MODE = "L"           # Pillow "L" == Rec.601 (0.299, 0.587, 0.114)
WHITE = (255, 255, 255)        # appendix A imaging: alpha 白底合成
SHOT_FILENAME = "shot.png"


# --------------------------------------------------------------------------
# artifact resolution  (mirrors the house convention in v_color / v_edge)
# --------------------------------------------------------------------------
def _load_image(artifacts: Any) -> Image.Image | None:
    """Resolve a card's shot.png from flexible artifact references.

    Accepts:
      * ``PIL.Image.Image`` — used directly (test fixtures);
      * ``bytes`` — decoded as an image;
      * path-like to a directory — reads ``<dir>/shot.png``;
      * path-like to a file — read directly;
      * mapping with ``shot_png`` / ``shot.png`` / ``shot`` / ``path`` / ``file``
        / ``dir`` keys.
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


def gray_320x200_u8(img: Image.Image) -> np.ndarray | None:
    """PIL image → white-composite → Rec.601 gray → LANCZOS 320×200 → uint8.

    Returns an ``(TARGET_H, TARGET_W) = (200, 320)`` uint8 array, or ``None``
    for a zero-area image (nothing to render).
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return None
    rgb = _composite_white(img)
    gray = rgb.convert(GRAYSCALE_MODE)                     # Rec.601 luma, uint8 "L"
    small = gray.resize((TARGET_W, TARGET_H), RESAMPLE)    # LANCZOS, (W,H)
    arr = np.asarray(small, dtype=np.uint8)
    if arr.size == 0:
        return None
    assert arr.shape == (TARGET_H, TARGET_W), arr.shape
    return arr


def _gray_from_artifacts(artifacts: Any) -> np.ndarray | None:
    img = _load_image(artifacts)
    if img is None:
        return None
    try:
        return gray_320x200_u8(img)
    finally:
        # close only images we opened from disk/bytes, not caller-owned ones
        if not isinstance(artifacts, Image.Image):
            try:
                img.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# SSIM core + mapping
# --------------------------------------------------------------------------
def ssim_raw(a: np.ndarray, b: np.ndarray) -> float:
    """Pinned ``structural_similarity`` scalar (raw, in [-1, 1]).

    Both arrays must be uint8 and identically shaped with each dimension
    ≥ ``WIN_SIZE`` (always true for the 320×200 channel input).
    """
    return float(
        structural_similarity(
            a,
            b,
            win_size=WIN_SIZE,
            gaussian_weights=GAUSSIAN_WEIGHTS,
            data_range=DATA_RANGE,
            channel_axis=CHANNEL_AXIS,
            K1=K1,
            K2=K2,
            use_sample_covariance=USE_SAMPLE_COVARIANCE,
        )
    )


def to_s(raw: float) -> float:
    """Map raw SSIM ∈ [-1, 1] to the formal channel value: clip((raw+1)/2, 0, 1)."""
    return float(np.clip((raw + 1.0) / 2.0, 0.0, 1.0))


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """v-ssim similarity between two cards.

    Returns ``{"s": float | None, "raw": float | None, ...diagnostics}``.
    ``s`` (and ``raw``) are ``None`` when either side's screenshot is absent /
    undecodable / zero-area (§4). ``s = clip((raw+1)/2, 0, 1)``; ``raw`` is the
    diagnostic companion (appendix A "raw 仅作诊断字段").
    """
    a = _gray_from_artifacts(card_a_artifacts)
    b = _gray_from_artifacts(card_b_artifacts)
    diag: dict[str, Any] = {
        "channel": CHANNEL,
        "a_present": a is not None,
        "b_present": b is not None,
        "input_size": [TARGET_W, TARGET_H],
        "win_size": WIN_SIZE,
        "data_range": DATA_RANGE,
        "k1": K1,
        "k2": K2,
        "gaussian_weights": GAUSSIAN_WEIGHTS,
        "use_sample_covariance": USE_SAMPLE_COVARIANCE,
        "channel_axis": CHANNEL_AXIS,
    }
    if a is None or b is None:
        return {"s": None, "raw": None, **diag}
    raw = ssim_raw(a, b)
    return {"s": to_s(raw), "raw": raw, **diag}
