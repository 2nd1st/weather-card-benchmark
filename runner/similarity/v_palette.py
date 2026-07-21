"""v-palette L2 similarity channel (scheme §3 palette + §4, appendix A).

Definition (appendix A "视觉通道" row, verbatim):

    Lab bins: L∈[0,100]×4; a,b clip [−110,110] each ×6;
    bin = 半开区间 [lo,hi), 各维最末 bin 闭合 [lo,hi]; bin 索引 = C-order (L,a,b);
    代表点 = bin 中心;
    K_ij = exp(−‖c_i−c_j‖² / 2σ²), σ = 10 (ΔE76);
    S = h₁ᵀK h₂ / √(h₁ᵀK h₁ · h₂ᵀK h₂)   (PSD 有证, S ∈ [0,1]);
    L1 swatch: 仅非零 bin, ≤8, 排序键 (−weight, bin 索引).

Per-card artifact = the 144-bin Lab-grid occupancy histogram ``h`` (占比权重,
fraction of pixels per bin) extracted from ``shot.png``. The channel S between
two cards is the Gaussian-kernel quadratic form (a normalized cross-correlation
in kernel space). K is symmetric PSD, so S ∈ [0,1] and S(a,b) == S(b,a); an
identical pair gives S == 1. Either side a zero vector (no pixels / missing
artifact) → S = None (§4).

Image preprocessing (appendix A "图像预处理"): alpha → white composite;
resize = LANCZOS to 128×80 (the shared L1 visual input size, matched to the
sibling v-color channel — FLAGGED: appendix A does not restate a resize size in
the v-palette row, so 128×80 is chosen as the option most consistent with the
"输入 128×80" default header and v-color); sRGB (IEC 61966-2-1) → XYZ → CIELAB,
D65.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# ─────────────────────────────────────────────────────────────────────────────
# Appendix A constants (BYTE-EXACT — never simplify)
# ─────────────────────────────────────────────────────────────────────────────

# Lab fixed grid.
L_MIN, L_MAX, L_BINS = 0.0, 100.0, 4        # L∈[0,100]×4
A_MIN, A_MAX, A_BINS = -110.0, 110.0, 6      # a clip [−110,110]×6
B_MIN, B_MAX, B_BINS = -110.0, 110.0, 6      # b clip [−110,110]×6
N_BINS = L_BINS * A_BINS * B_BINS            # 4·6·6 = 144, C-order (L,a,b)

SIGMA = 10.0                                  # Gaussian kernel σ (ΔE76 units)

# Preprocessing target: (width, height) for PIL. 1280×800 card → 128×80.
RESIZE_WH = (128, 80)

# sRGB (IEC 61966-2-1) → XYZ linear transform, D65 reference white.
# FLAGGED: appendix A pins "sRGB (IEC 61966-2-1) → XYZ → CIELAB, D65" but not the
# numeric matrix / white point. We use the standard sRGB/D65 matrix (Lindbloom,
# consistent with IEC 61966-2-1) and D65 white = matrix row sums, so achromatic
# input maps to a≈b≈0 exactly. Most consistent with the stated rule.
_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)
_D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float64)  # ≈ row sums

# CIELAB f() piecewise constants.
_LAB_DELTA = 6.0 / 29.0
_LAB_DELTA3 = _LAB_DELTA ** 3          # threshold on t
_LAB_SLOPE = 1.0 / (3.0 * _LAB_DELTA * _LAB_DELTA)  # linear-segment slope
_LAB_OFFSET = 4.0 / 29.0


# ─────────────────────────────────────────────────────────────────────────────
# Precomputed bin centers + kernel matrix (fixed grid → module-level cache)
# ─────────────────────────────────────────────────────────────────────────────

def _bin_centers() -> np.ndarray:
    """(144, 3) Lab bin-center representative points, C-order (L, a, b)."""
    l_c = L_MIN + (np.arange(L_BINS) + 0.5) * ((L_MAX - L_MIN) / L_BINS)
    a_c = A_MIN + (np.arange(A_BINS) + 0.5) * ((A_MAX - A_MIN) / A_BINS)
    b_c = B_MIN + (np.arange(B_BINS) + 0.5) * ((B_MAX - B_MIN) / B_BINS)
    centers = np.empty((N_BINS, 3), dtype=np.float64)
    idx = 0
    for li in range(L_BINS):          # C-order: L outermost, b innermost
        for ai in range(A_BINS):
            for bi in range(B_BINS):
                centers[idx] = (l_c[li], a_c[ai], b_c[bi])
                idx += 1
    return centers


_CENTERS = _bin_centers()


def _kernel() -> np.ndarray:
    """K_ij = exp(−‖c_i − c_j‖² / (2σ²)), Lab-Euclidean (ΔE76). Symmetric PSD."""
    diff = _CENTERS[:, None, :] - _CENTERS[None, :, :]
    d2 = np.sum(diff * diff, axis=2)
    return np.exp(-d2 / (2.0 * SIGMA * SIGMA))


_K = _kernel()


# ─────────────────────────────────────────────────────────────────────────────
# Palette-histogram extraction
# ─────────────────────────────────────────────────────────────────────────────

def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(..., 3) uint8/float sRGB in [0,255] → (..., 3) CIELAB (L, a, b), D65."""
    c = rgb.astype(np.float64) / 255.0
    # Inverse sRGB companding.
    lin = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    # Linear RGB → XYZ.
    xyz = lin @ _SRGB_TO_XYZ.T
    # Normalize by white point.
    xyz = xyz / _D65
    # f(t).
    ft = np.where(
        xyz > _LAB_DELTA3,
        np.cbrt(xyz),
        _LAB_SLOPE * xyz + _LAB_OFFSET,
    )
    fx, fy, fz = ft[..., 0], ft[..., 1], ft[..., 2]
    lab = np.empty_like(ft)
    lab[..., 0] = 116.0 * fy - 16.0     # L
    lab[..., 1] = 500.0 * (fx - fy)     # a
    lab[..., 2] = 200.0 * (fy - fz)     # b
    return lab


def _dim_bin(vals: np.ndarray, lo: float, hi: float, n: int) -> np.ndarray:
    """Per-dimension bin index with half-open [lo,hi) bins, last bin closed [lo,hi].

    Values are clipped into [lo, hi] first (appendix A: a,b clip [−110,110]; L is
    clipped to [0,100] so the last-bin-closed domain is well defined — CIELAB L
    from a valid sRGB pixel is already within [0,100] up to float rounding).
    A value on an interior edge belongs to the UPPER bin (half-open [lo,hi));
    v == hi folds into the last bin.
    """
    v = np.clip(vals, lo, hi)
    width = (hi - lo) / n
    idx = np.floor((v - lo) / width).astype(np.int64)
    np.clip(idx, 0, n - 1, out=idx)  # v == hi → n → n-1 (last bin closed)
    return idx


def extract_palette(shot_png: str | Path) -> np.ndarray | None:
    """shot.png path → (144,) float64 占比-weighted Lab-grid histogram (sums to 1).

    Returns None when the artifact is missing / unreadable / has no pixels
    (zero vector → §4 null).
    """
    p = Path(shot_png)
    if not p.is_file():
        return None
    with Image.open(p) as im:
        img = _composite_and_resize(im)
    return _histogram_from_rgb(img)


def _composite_and_resize(im: Image.Image) -> np.ndarray:
    """PIL image → (H, W, 3) uint8 RGB: alpha→white composite, LANCZOS 128×80."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, rgba).convert("RGB")
    else:
        im = im.convert("RGB")
    im = im.resize(RESIZE_WH, Image.LANCZOS)
    return np.asarray(im, dtype=np.uint8)


def _histogram_from_rgb(rgb: np.ndarray) -> np.ndarray | None:
    """(H, W, 3) uint8 → (144,) normalized占比 histogram, or None if empty."""
    pixels = rgb.reshape(-1, 3)
    if pixels.shape[0] == 0:
        return None
    lab = _srgb_to_lab(pixels)
    li = _dim_bin(lab[:, 0], L_MIN, L_MAX, L_BINS)
    ai = _dim_bin(lab[:, 1], A_MIN, A_MAX, A_BINS)
    bi = _dim_bin(lab[:, 2], B_MIN, B_MAX, B_BINS)
    flat = (li * (A_BINS * B_BINS) + ai * B_BINS + bi)  # C-order (L,a,b)
    counts = np.bincount(flat, minlength=N_BINS).astype(np.float64)
    total = counts.sum()
    if total <= 0:
        return None
    return counts / total


# ─────────────────────────────────────────────────────────────────────────────
# Artifact resolution + top-8 swatch (§3)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_hist(artifacts: Any) -> np.ndarray | None:
    """Coerce a compute() artifact argument into a (144,) histogram or None.

    Accepts, in order of precedence:
      * None                              → None
      * Mapping with 'palette_hist'       → that vector (validated length 144)
      * Mapping with 'rgb' / 'image'      → in-memory RGB ndarray / PIL image
      * Mapping with 'shot_png'           → path to a PNG
      * Mapping with 'dir'                → <dir>/shot.png
      * str / Path to a directory         → <dir>/shot.png
      * str / Path to a file              → that PNG
    """
    if artifacts is None:
        return None
    if isinstance(artifacts, Mapping):
        if artifacts.get("palette_hist") is not None:
            h = np.asarray(artifacts["palette_hist"], dtype=np.float64).reshape(-1)
            if h.shape[0] != N_BINS:
                raise ValueError(f"palette_hist must have {N_BINS} bins, got {h.shape[0]}")
            return h if h.sum() > 0 else None
        img = artifacts.get("rgb", artifacts.get("image"))
        if img is not None:
            if isinstance(img, Image.Image):
                arr = _composite_and_resize(img)
            else:
                arr = _composite_and_resize(Image.fromarray(np.asarray(img, dtype=np.uint8)))
            return _histogram_from_rgb(arr)
        if artifacts.get("shot_png") is not None:
            return extract_palette(artifacts["shot_png"])
        if artifacts.get("dir") is not None:
            return extract_palette(Path(artifacts["dir"]) / "shot.png")
        return None
    p = Path(artifacts)
    if p.is_dir():
        return extract_palette(p / "shot.png")
    return extract_palette(p)


def top8_swatches(h: np.ndarray) -> list[dict[str, Any]]:
    """L1 top-8 swatch (§3): non-zero bins only, ≤8, key (−weight, bin index)."""
    nz = np.nonzero(h)[0]
    order = sorted(nz.tolist(), key=lambda i: (-h[i], i))[:8]
    out = []
    for i in order:
        cl, ca, cb = _CENTERS[i]
        out.append(
            {
                "bin": int(i),
                "weight": float(h[i]),
                "lab_center": [float(cl), float(ca), float(cb)],
            }
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public channel entry point
# ─────────────────────────────────────────────────────────────────────────────

def _quad(hi: np.ndarray, hj: np.ndarray) -> float:
    return float(hi @ _K @ hj)


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict[str, Any]:
    """v-palette S for one card pair (scheme §4, appendix A).

    Returns {"s": float | None, ...diagnostics}. S = h₁ᵀK h₂ / √(h₁ᵀK h₁ · h₂ᵀK h₂),
    clamped into [0,1] to absorb float rounding at the S == 1 identity endpoint.
    Either side zero-vector / empty / missing → s = None.
    """
    h1 = _resolve_hist(card_a_artifacts)
    h2 = _resolve_hist(card_b_artifacts)

    diag: dict[str, Any] = {
        "channel": "v-palette",
        "sigma": SIGMA,
        "n_bins": N_BINS,
        "nonzero_bins_a": None if h1 is None else int(np.count_nonzero(h1)),
        "nonzero_bins_b": None if h2 is None else int(np.count_nonzero(h2)),
    }

    if h1 is None or h2 is None:
        # Zero-vector / empty / missing artifact on either side (double-empty too).
        diag.update({"s": None, "self_a": None, "self_b": None, "cross": None,
                     "swatch_a": None, "swatch_b": None})
        return diag

    self_a = _quad(h1, h1)
    self_b = _quad(h2, h2)
    cross = _quad(h1, h2)
    denom = self_a * self_b

    if denom <= 0.0:
        # Unreachable for non-zero h (Gaussian kernel is positive definite), but
        # guard against pathological float underflow → treat as null.
        s: float | None = None
    else:
        s = cross / math.sqrt(denom)
        s = float(min(1.0, max(0.0, s)))  # clamp float noise into [0,1]

    diag.update(
        {
            "s": s,
            "self_a": self_a,
            "self_b": self_b,
            "cross": cross,
            "swatch_a": top8_swatches(h1),
            "swatch_b": top8_swatches(h2),
        }
    )
    return diag
