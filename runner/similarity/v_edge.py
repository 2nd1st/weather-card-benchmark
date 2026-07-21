"""Channel **v-edge** — HOG-like oriented-edge similarity (scheme §4, appendix A).

Appendix A definition (byte-exact, implemented verbatim):

    v-edge | 输入 320×200 灰度 float64；
            gx=scipy.ndimage.sobel(img, axis=1, mode="reflect"),
            gy=scipy.ndimage.sobel(img, axis=0, mode="reflect")；
            θ=atan2(gy,gx)·180/π，映射 ((θ mod 180)+180) mod 180 ∈[0,180)；
            幅值=√(gx²+gy²)；
            硬分箱 [0,22.5),…,[157.5,180) 8 bins（角度升序 bin 0..7），幅值加权；
            4×4 cells（每 cell 80×50 px）；cell 内 L1 归一（全零 cell 保持全零）；
            拼接序 = cell 行主序（上→下，行内左→右）× cell 内 bin 0..7，128 维 cosine

Shared imaging conventions (appendix A "图像预处理"), applied here:
  * Pillow pinned; **resize = LANCZOS**.
  * grayscale = Rec.601 (0.299, 0.587, 0.114) — exactly Pillow's ``convert("L")``
    (ITU-R 601-2 luma), so we use it as the canonical grayscale operator.
  * alpha → opaque-WHITE-background composite before grayscale.

Pipeline (per card):
  1. Decode shot.png.
  2. If it has alpha, composite over an opaque WHITE background.
  3. Rec.601 grayscale (``convert("L")``).
  4. LANCZOS resize to 320×200 (W×H).
  5. Cast to float64  →  the "输入 320×200 灰度 float64".
  6. Sobel gx (axis=1) / gy (axis=0), mode="reflect".
  7. Orientation θ folded to [0,180); magnitude = hypot(gx,gy).
  8. 8-bin magnitude-weighted orientation histogram per 4×4 cell (each 80×50 px).
  9. L1-normalize each cell's 8-bin histogram (all-zero cell stays all-zero).
 10. Concatenate cell row-major (top→bottom, left→right within a row) × bin 0..7
     → 128-dim descriptor (float64).

Similarity: cosine of the two 128-vectors. All histogram entries are ≥ 0, so
cosine ∈ [0, 1]; identical descriptors → 1. A descriptor is a **zero vector**
only when the image has NO gradient energy anywhere (every cell all-zero), i.e.
a perfectly flat image. Per §4 / appendix A "统一规则", if EITHER side is a zero
vector (double-zero included) → ``s = None``.

ORDERING NOTE (scheme-silent → FLAGGED, choice most consistent with stated
rules): the phrase "输入 320×200 灰度" does not pin whether grayscale precedes or
follows the resize. We do **grayscale → resize**, matching every sibling visual
row that writes the grayscale operator before the target size (v-phash
"灰度 LANCZOS→32×32", v-dhash "灰度 17×16", v-layout "32×20 灰度") and the house
helper in ``v_layout.layout_vector``. See report for the flag.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

# --- appendix-A constants (v-edge row + shared imaging) -------------------
TARGET_W = 320              # input width  (columns)   — "320×200"
TARGET_H = 200              # input height (rows)
CELLS_X = 4                 # cells across width  (4×4 cells)
CELLS_Y = 4                 # cells down height
CELL_W = 80                 # per-cell width  px  (320 / 4)  — "每 cell 80×50 px"
CELL_H = 50                 # per-cell height px  (200 / 4)
N_BINS = 8                  # orientation bins  [0,22.5)…[157.5,180)
BIN_WIDTH = 22.5            # 180 / 8 degrees
VEC_LEN = CELLS_X * CELLS_Y * N_BINS   # 4*4*8 = 128-dim descriptor
SOBEL_MODE = "reflect"      # scipy.ndimage.sobel mode (appendix A verbatim)
RESAMPLE = Image.LANCZOS    # shared imaging: resize = LANCZOS
GRAYSCALE_MODE = "L"        # Pillow "L" == Rec.601 (0.299, 0.587, 0.114)
SHOT_FILENAME = "shot.png"

# sanity: the cell grid must tile the target exactly
assert CELLS_X * CELL_W == TARGET_W
assert CELLS_Y * CELL_H == TARGET_H


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
            "v-edge: artifacts dict lacks 'shot_png'/'shot_path'/'dir'/'path'"
        )

    if isinstance(artifacts, (str, os.PathLike)):
        return _shot_bytes_from_path(Path(artifacts))

    raise TypeError(f"v-edge: unsupported artifacts type {type(artifacts)!r}")


def _shot_bytes_from_path(p: Path) -> bytes:
    if p.is_dir():
        p = p / SHOT_FILENAME
    return p.read_bytes()


# --------------------------------------------------------------------------
# imaging → 320×200 grayscale float64
# --------------------------------------------------------------------------
def gray_320x200(png_bytes: bytes) -> np.ndarray:
    """Decode → white-composite → Rec.601 gray → LANCZOS 320×200 → float64.

    Returns an array of shape (TARGET_H, TARGET_W) = (200, 320).
    """
    with Image.open(io.BytesIO(png_bytes)) as im:
        im.load()
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, rgba)
        gray = im.convert(GRAYSCALE_MODE)                    # Rec.601 luma
        small = gray.resize((TARGET_W, TARGET_H), RESAMPLE)  # LANCZOS, (W,H)
    arr = np.asarray(small, dtype=np.float64)
    assert arr.shape == (TARGET_H, TARGET_W), arr.shape
    return arr


# --------------------------------------------------------------------------
# HOG-like edge descriptor
# --------------------------------------------------------------------------
def edge_descriptor(gray: np.ndarray) -> np.ndarray:
    """320×200 float64 grayscale → 128-dim oriented-edge histogram descriptor.

    Byte-exact per appendix A: sobel(axis=1/axis=0, reflect) → fold θ to
    [0,180) → hard 8-bin magnitude-weighted histogram per 4×4 cell → L1-norm
    each cell (all-zero stays all-zero) → concat cell-row-major × bin 0..7.
    """
    gray = np.asarray(gray, dtype=np.float64)
    if gray.shape != (TARGET_H, TARGET_W):
        raise ValueError(
            f"v-edge: gray must be {(TARGET_H, TARGET_W)}, got {gray.shape}"
        )

    gx = ndimage.sobel(gray, axis=1, mode=SOBEL_MODE)   # horizontal (columns)
    gy = ndimage.sobel(gray, axis=0, mode=SOBEL_MODE)   # vertical   (rows)

    theta = np.arctan2(gy, gx) * 180.0 / np.pi          # (-180, 180]
    angle = ((theta % 180.0) + 180.0) % 180.0           # fold to [0, 180)
    mag = np.hypot(gx, gy)                               # √(gx²+gy²)

    # hard bin index: floor(angle / 22.5), clamped to [0, 7]
    binidx = np.floor(angle / BIN_WIDTH).astype(np.int64)
    np.clip(binidx, 0, N_BINS - 1, out=binidx)

    desc = np.zeros(VEC_LEN, dtype=np.float64)
    for cy in range(CELLS_Y):                # top → bottom
        r0, r1 = cy * CELL_H, (cy + 1) * CELL_H
        for cx in range(CELLS_X):            # left → right within a row
            c0, c1 = cx * CELL_W, (cx + 1) * CELL_W
            cell_bins = binidx[r0:r1, c0:c1].reshape(-1)
            cell_mag = mag[r0:r1, c0:c1].reshape(-1)
            # magnitude-weighted 8-bin histogram
            hist = np.bincount(cell_bins, weights=cell_mag, minlength=N_BINS)
            hist = hist[:N_BINS].astype(np.float64)
            total = hist.sum()               # L1 norm (entries ≥ 0)
            if total > 0.0:
                hist = hist / total          # else all-zero cell stays all-zero
            base = (cy * CELLS_X + cx) * N_BINS
            desc[base:base + N_BINS] = hist
    return desc


def edge_descriptor_from_artifacts(artifacts: Any) -> np.ndarray:
    """Resolve artifacts → shot.png → 320×200 gray → 128-dim descriptor."""
    return edge_descriptor(gray_320x200(_shot_bytes(artifacts)))


def _cosine(a: np.ndarray, b: np.ndarray) -> float | None:
    """Cosine similarity; None if either vector has zero norm (zero vector)."""
    na = float(np.sqrt(np.dot(a, a)))
    nb = float(np.sqrt(np.dot(b, b)))
    if na == 0.0 or nb == 0.0:
        return None
    if np.array_equal(a, b):
        return 1.0  # cosine of a vector with itself is exactly 1 (avoid sqrt noise)
    s = float(np.dot(a, b) / (na * nb))
    # histogram entries are non-negative ⇒ s ∈ [0, 1] mathematically; clip noise.
    return float(np.clip(s, 0.0, 1.0))


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """v-edge similarity between two cards.

    Returns ``{"s": float | None, ...diagnostics}``. ``s`` is None when either
    side's 128-dim edge descriptor is a zero vector (perfectly flat image, no
    gradient energy in any cell), per §4.
    """
    va = edge_descriptor_from_artifacts(card_a_artifacts)
    vb = edge_descriptor_from_artifacts(card_b_artifacts)
    na = float(np.sqrt(np.dot(va, va)))
    nb = float(np.sqrt(np.dot(vb, vb)))
    s = _cosine(va, vb)
    return {
        "s": s,
        "channel": "v-edge",
        "input_size": [TARGET_W, TARGET_H],
        "cells": [CELLS_X, CELLS_Y],
        "n_bins": N_BINS,
        "vec_len": VEC_LEN,
        "norm_a": na,
        "norm_b": nb,
        "zero_a": na == 0.0,
        "zero_b": nb == 0.0,
    }
