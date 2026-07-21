"""v-phash similarity channel (COMPARISON-SCHEME-v12.md §4 + appendix A).

Appendix A definition (byte-exact, implement verbatim):

    v-phash | 灰度 LANCZOS→32×32,float64;scipy.fft.dctn(type=2, norm="ortho")
    (scipy 钉版);取 [0:8,0:8] 含 DC,C-order 展平为 64 系数;阈值=numpy.median
    (64 值,偶数取中二均值);bit=1 若系数>中位数,=中位数→0;sim=1−hamming/64

DEVIATION FROM APPENDIX A (2026-07-20, deliberate — bug fix): appendix A writes the
grayscale step as "float64". Taking that literally (np.float64 Rec.601 → "F"-mode
Pillow image → LANCZOS) is numerically broken: Pillow's LANCZOS downscale of a float
image yields NaN / ~1e35 overflow on real screenshots. Because every bit here is an
absolute "coefficient > median" test, NaN made every bit 0 → all-zero hashes → ~79%
of config pairs collided at s=1.0, destroying the channel. The grayscale is therefore
taken at uint8 precision via Pillow's ``convert("L")`` (the same Rec.601 weights, and
the identical path already used by v-edge / v-layout / v-ssim). ``v_ssim``'s docstring
had documented this Pillow trap; v-phash and v-dhash simply had not been updated.

Pipeline (shared imaging conventions, appendix A "图像预处理", matching the sibling
hash channel ``v_dhash``):
  * alpha 白底合成 (composite any alpha over an opaque white background),
  * 灰度 = Rec.601 via Pillow ``convert("L")`` (uint8),
  * LANCZOS-resize the grayscale to a 32×32 grid,
  * DCT-II orthonormal over both axes via ``scipy.fft.dctn(type=2, norm="ortho")``,
  * take the top-left 8×8 block **including DC**, C-order (row-major) flatten → 64
    coefficients,
  * threshold = ``numpy.median`` of the 64 coefficients (for the even count 64,
    numpy averages the two middle order statistics — "偶数取中二均值"),
  * bit = 1 iff coefficient **strictly greater** than the median (== median → 0),
  * sim = 1 − hamming(bits_a, bits_b) / 64 ∈ [0, 1].

Degenerate / null semantics (§4): the appendix-A null rule ("零向量/空集/空 bag →
S=null") is written for cosine / kernel channels whose similarity is undefined on a
zero vector. Hamming similarity is defined for every 64-bit string (including the
all-zero string a uniform-dark card legitimately produces — a solid black card
yields an all-zero DCT and thus an all-zero hash), so the only degenerate case here
is a genuinely absent / undecodable / zero-area screenshot on either side → s = None
(double-empty included). A uniform image (all-zero hash) is NOT treated as null; see
the FLAG in the implementation report. This matches the sibling ``v_dhash`` policy.
"""

from __future__ import annotations

import io
import os
from collections.abc import Mapping
from typing import Any

import numpy as np
import scipy.fft
from PIL import Image

CHANNEL = "v-phash"

# Grayscale resize target and DCT block geometry (appendix A).
PHASH_SIDE = 32          # 灰度 LANCZOS → 32×32
BLOCK = 8                # 取 [0:8, 0:8]
N_BITS = BLOCK * BLOCK   # 64 coefficients / bits (DC included)

GRAYSCALE_MODE = "L"     # Pillow "L" == Rec.601 (0.299, 0.587, 0.114), uint8

# Rec.601 luma weights (appendix A imaging). Retained for reference/goldens; the
# live path uses Pillow's equivalent integer "L" conversion — see _phash_bits.
_R601 = (0.299, 0.587, 0.114)


def _resolve_shot_png(artifacts: Any) -> bytes | None:
    """Resolve the shot.png byte payload from a channel artifact argument.

    Accepts, in order of precedence:
      * ``bytes`` / ``bytearray`` → treated directly as PNG bytes;
      * ``Mapping`` with ``"shot_png"`` (bytes | path str) or ``"shot_png_bytes"``
        (bytes), else ``"dir"`` / ``"card_dir"`` / ``"path"`` (directory holding
        ``shot.png``);
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


def _phash_bits(png_bytes: bytes) -> np.ndarray | None:
    """Decode PNG → Rec.601 gray float64 → LANCZOS 32×32 → DCT → 64-bit phash.

    Returns a uint8 array of shape (64,) with values in {0, 1}, or ``None`` if
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

    # 灰度 = Pillow "L" (uint8 Rec.601 0.299/0.587/0.114) → LANCZOS 32×32.
    # NB (2026-07-20 fix): the previous float path — np.float64 Rec.601 into an
    # "F"-mode Pillow image, then LANCZOS — is NUMERICALLY BROKEN. Pillow's LANCZOS
    # downscale of a float image returns NaN (or ~1e35 overflow) for real card
    # screenshots; NaN > median is False for every coefficient, so the hash collapsed
    # to all-zero and ~79% of config pairs collided at s=1.0. The uint8 "L" path is
    # the robust, byte-reproducible one and matches every sibling imaging channel
    # (v-edge / v-layout / v-ssim, whose docstring already documented this trap).
    gray_im = rgb.convert(GRAYSCALE_MODE)
    small = gray_im.resize((PHASH_SIDE, PHASH_SIDE), Image.LANCZOS)
    g = np.asarray(small, dtype=np.float64)  # (32, 32)

    return _phash_bits_from_gray(g)


def _phash_bits_from_gray(gray: np.ndarray) -> np.ndarray:
    """DCT-hash a 32×32 float grayscale grid → 64-bit phash.

    Split out from :func:`_phash_bits` so the DCT / median / threshold math can be
    golden-tested on a directly supplied grid, free of LANCZOS-resize roundoff.

      * ``scipy.fft.dctn(type=2, norm="ortho")`` over both axes (scipy 钉版),
      * top-left 8×8 block **including DC**, C-order flatten → 64 coefficients,
      * threshold = ``numpy.median`` (even count → mean of the two middle values),
      * bit = 1 iff coefficient strictly > median (== median → 0).
    """
    dct = scipy.fft.dctn(np.asarray(gray, dtype=np.float64), type=2, norm="ortho")

    # 取 [0:8, 0:8] 含 DC, C-order 展平 → 64 系数.
    coeffs = dct[:BLOCK, :BLOCK].reshape(-1)  # C-order (row-major) by default

    # 阈值 = numpy.median (even count → mean of the two middle order statistics).
    median = np.median(coeffs)

    # bit = 1 iff coefficient strictly > median (== median → 0).
    return (coeffs > median).astype(np.uint8)  # (64,)


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """Compute the v-phash similarity for a pair of cards.

    Returns ``{"s": float | None, "channel": "v-phash", "hamming": int | None,
    "hash_a": hex|None, "hash_b": hex|None, "note": str|None}``. ``s`` is
    ``None`` when either screenshot is missing / undecodable / zero-area (§4).
    """
    png_a = _resolve_shot_png(card_a_artifacts)
    png_b = _resolve_shot_png(card_b_artifacts)

    bits_a = _phash_bits(png_a) if png_a is not None else None
    bits_b = _phash_bits(png_b) if png_b is not None else None

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
    """64-bit array → 16-char lowercase hex (MSB = bit index 0), or None."""
    if bits is None:
        return None
    packed = np.packbits(bits.astype(np.uint8))  # big-endian within each byte
    return packed.tobytes().hex()
