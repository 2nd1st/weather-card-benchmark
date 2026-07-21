"""v-dhash similarity channel (COMPARISON-SCHEME-v12.md §4 + appendix A).

Appendix A definition (byte-exact, implement verbatim):

    v-dhash | 灰度 17×16,水平相邻差分(右>左→1)→256 bit;sim=1−hamming/256

Pipeline (shared imaging conventions, appendix A "图像预处理"):
  * alpha 白底合成 (composite any alpha over an opaque white background),
  * 灰度 = Rec.601 (0.299, 0.587, 0.114),
  * resize = LANCZOS.

Order follows the sibling hash channel v-phash ("灰度 LANCZOS→32×32"): compute the
Rec.601 grayscale at full resolution as float64, then LANCZOS-resize to the target
grid. Target grid is 17 wide × 16 tall; the horizontal adjacent difference over
each of the 16 rows yields 16 comparisons per row (col[c+1] > col[c]) → 16×16 =
256 bits, C-order flattened (row-major, left→right within a row). bit = 1 iff the
right pixel is strictly greater than the left pixel.

sim = 1 − hamming(bits_a, bits_b) / 256 ∈ [0, 1].

Degenerate / null semantics (§4): the appendix-A null rule ("零向量/空集/空 bag →
S=null") is written for cosine / kernel channels whose similarity is undefined on a
zero vector. Hamming similarity is defined for every 256-bit string (including the
all-zero string a uniform card legitimately produces — two blank cards *are*
maximally similar), so the only degenerate case here is a genuinely absent /
undecodable / zero-area screenshot on either side → s = None (double-empty
included). A uniform image (all-zero hash) is NOT treated as null; see the FLAG in
the implementation report.
"""

from __future__ import annotations

import io
import os
from collections.abc import Mapping
from typing import Any

import numpy as np
from PIL import Image

CHANNEL = "v-dhash"

# Target grayscale grid: 17 columns × 16 rows (width × height).
DHASH_W = 17
DHASH_H = 16
N_BITS = DHASH_H * (DHASH_W - 1)  # 16 * 16 = 256

# Rec.601 luma weights (appendix A imaging).
GRAYSCALE_MODE = "L"     # Pillow "L" == Rec.601 (0.299, 0.587, 0.114), uint8

# Rec.601 luma weights. Retained for reference/goldens; the live path uses Pillow's
# equivalent integer "L" conversion — see _dhash_bits.
_R601 = (0.299, 0.587, 0.114)


def _resolve_shot_png(artifacts: Any) -> bytes | None:
    """Resolve the shot.png byte payload from a channel artifact argument.

    Accepts, in order of precedence:
      * ``bytes`` / ``bytearray`` → treated directly as PNG bytes;
      * ``Mapping`` with ``"shot_png"`` (bytes | path str) or ``"shot_png_bytes"``
        (bytes), else ``"dir"`` / ``"card_dir"`` (directory holding ``shot.png``);
      * ``os.PathLike`` / ``str`` → a ``shot.png`` file directly, or a card
        directory containing ``shot.png``.

    Returns the raw bytes, or ``None`` when nothing usable / the file is missing.
    """
    if artifacts is None:
        return None
    if isinstance(artifacts, (bytes, bytearray)):
        return bytes(artifacts) or None

    if isinstance(artifacts, Mapping):
        if artifacts.get("shot_png_bytes") is not None:
            b = artifacts["shot_png_bytes"]
            return bytes(b) or None
        val = artifacts.get("shot_png")
        if isinstance(val, (bytes, bytearray)):
            return bytes(val) or None
        if val is not None:
            return _read_file(os.fspath(val))
        for key in ("dir", "card_dir", "path"):
            d = artifacts.get(key)
            if d is not None:
                return _read_file(os.path.join(os.fspath(d), "shot.png"))
        return None

    # os.PathLike or str
    p = os.fspath(artifacts)
    if os.path.isdir(p):
        return _read_file(os.path.join(p, "shot.png"))
    return _read_file(p)


def _read_file(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except (OSError, TypeError):
        return None
    return data or None


def _dhash_bits(png_bytes: bytes) -> np.ndarray | None:
    """Decode PNG → Rec.601 gray float64 → LANCZOS 17×16 → 256-bit dhash.

    Returns a uint8 array of shape (256,) with values in {0, 1}, or ``None`` if
    the image cannot be decoded or has zero area.
    """
    try:
        with Image.open(io.BytesIO(png_bytes)) as im:
            im.load()
            # alpha 白底合成: composite any transparency over opaque white.
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                rgb = Image.alpha_composite(bg, rgba).convert("RGB")
            else:
                rgb = im.convert("RGB")
    except Exception:
        return None

    w, h = rgb.size
    if w <= 0 or h <= 0:
        return None

    # 灰度 = Pillow "L" (uint8 Rec.601) → LANCZOS 17×16 (width × height).
    # NB (2026-07-20 fix): the previous float path ("F"-mode Pillow image, float32
    # LANCZOS) is numerically unsound — Pillow's LANCZOS downscale of a float image
    # returns NaN / ~1e35 overflow on real screenshots. v-dhash survived visibly
    # (its bits are RELATIVE, g[:,1:] > g[:,:-1], so overflow largely preserved the
    # ordering) where the sibling v-phash collapsed outright, but the input was
    # garbage either way. Now on the same robust uint8 "L" path as v-phash /
    # v-edge / v-layout / v-ssim.
    gray_im = rgb.convert(GRAYSCALE_MODE)
    small = gray_im.resize((DHASH_W, DHASH_H), Image.LANCZOS)
    g = np.asarray(small, dtype=np.float64)  # (16, 17)

    # 水平相邻差分: 右 > 左 → 1, C-order flatten (row-major).
    bits = (g[:, 1:] > g[:, :-1]).astype(np.uint8)  # (16, 16)
    return bits.reshape(-1)  # (256,)


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """Compute the v-dhash similarity for a pair of cards.

    Returns ``{"s": float | None, "channel": "v-dhash", "hamming": int | None,
    "hash_a": hex|None, "hash_b": hex|None, "note": str|None}``. ``s`` is
    ``None`` when either screenshot is missing / undecodable / zero-area (§4).
    """
    png_a = _resolve_shot_png(card_a_artifacts)
    png_b = _resolve_shot_png(card_b_artifacts)

    bits_a = _dhash_bits(png_a) if png_a is not None else None
    bits_b = _dhash_bits(png_b) if png_b is not None else None

    hash_a = _bits_to_hex(bits_a)
    hash_b = _bits_to_hex(bits_b)

    if bits_a is None or bits_b is None:
        return {
            "s": None,
            "channel": CHANNEL,
            "hamming": None,
            "hash_a": hash_a,
            "hash_b": hash_b,
            "note": "empty" if (png_a is None or png_b is None) else "undecodable",
        }

    hamming = int(np.count_nonzero(bits_a != bits_b))
    s = 1.0 - hamming / float(N_BITS)
    return {
        "s": s,
        "channel": CHANNEL,
        "hamming": hamming,
        "hash_a": hash_a,
        "hash_b": hash_b,
        "note": None,
    }


def _bits_to_hex(bits: np.ndarray | None) -> str | None:
    """256-bit array → 64-char lowercase hex (MSB = bit index 0), or None."""
    if bits is None:
        return None
    packed = np.packbits(bits.astype(np.uint8))  # big-endian within each byte
    return packed.tobytes().hex()
