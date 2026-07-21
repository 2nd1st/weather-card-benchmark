"""Golden micro-fixture tests for the v-phash similarity channel (scheme §4).

Expected values are hand-computed / independently derived here (never from the
code under test), per the §4 golden-fixture rule.

Two layers of goldens, chosen for robustness:

1. **DCT / median / threshold math** — tested via ``_phash_bits_from_gray`` on a
   directly supplied 32×32 grid, so there is NO LANCZOS-resize roundoff. For an
   orthonormal 2D DCT-II of an N×N constant field ``c`` (N = 32): DC = c·N, all AC
   coefficients exactly 0. The top-left 8×8 block flattened C-order is
   ``[c·32, 0, …, 0]``; ``numpy.median`` (even count 64) = 0; so:
     * c > 0 → only DC > 0 → bit 0 set → hash 0x8000_0000_0000_0000 (1 bit),
     * c == 0 → all-zero hash 0x0000_0000_0000_0000.

2. **End-to-end pipeline** (``compute`` / ``_phash_bits`` from PNG bytes) — tested
   only with cases that are invariant to LANCZOS-resize float32 roundoff:
     * a solid-black card → an exactly-zero grid → all-zero hash (0·weights = 0),
     * identity, symmetry, determinism, and the §4 null (missing / undecodable).

   NOTE: a general non-black *solid* is NOT a reliable golden — the F-mode LANCZOS
   resize applies boundary-clipped kernels whose float32 renormalization makes the
   32×32 field very slightly non-constant, so a handful of AC coefficients flip
   around the (near-zero) median for some luma values. That roundoff is
   deterministic on the pinned Pillow/scipy stack but not analytically clean, so
   we do not assert exact hashes for arbitrary solids end-to-end.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from runner.similarity.v_phash import (
    N_BITS,
    PHASH_SIDE,
    CHANNEL,
    compute,
    _phash_bits,
    _phash_bits_from_gray,
    _bits_to_hex,
)

HASH_DC_ONLY = "8000000000000000"   # bit index 0 (MSB) set, hand-derived
HASH_ALL_ZERO = "0000000000000000"  # all-zero grid, hand-derived


def _solid_png_bytes(color, size=(200, 100)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _write_solid(path, color, size=(200, 100)):
    Image.new("RGB", color=color, size=size).save(path, format="PNG")
    return path


# --------------------------------------------------------------------------
# 1. DCT / median / threshold math (resize-free, hand-derived)
# --------------------------------------------------------------------------
def test_dctn_constant_field_ac_is_exactly_zero():
    # Independent check of the premise behind the hand-derived hashes.
    import scipy.fft

    grid = np.full((PHASH_SIDE, PHASH_SIDE), 100.0)
    dct = scipy.fft.dctn(grid, type=2, norm="ortho")
    assert dct[0, 0] == pytest.approx(100.0 * PHASH_SIDE)  # DC = c·N
    assert np.abs(dct.reshape(-1)[1:]).max() == 0.0        # all AC exactly 0


def test_gray_constant_positive_is_dc_only():
    for c in (1.0, 76.245, 100.0, 149.685, 255.0):
        bits = _phash_bits_from_gray(np.full((PHASH_SIDE, PHASH_SIDE), c))
        assert bits.shape == (N_BITS,)
        assert int(bits.sum()) == 1
        assert bits[0] == 1
        assert _bits_to_hex(bits) == HASH_DC_ONLY


def test_gray_all_zero_is_all_zero_hash():
    bits = _phash_bits_from_gray(np.zeros((PHASH_SIDE, PHASH_SIDE)))
    assert int(bits.sum()) == 0
    assert _bits_to_hex(bits) == HASH_ALL_ZERO


def test_gray_positive_vs_zero_hamming_one():
    # DC-only (0x8000…) vs all-zero (0x0000…) differ in exactly bit 0.
    a = _phash_bits_from_gray(np.full((PHASH_SIDE, PHASH_SIDE), 200.0))
    b = _phash_bits_from_gray(np.zeros((PHASH_SIDE, PHASH_SIDE)))
    assert int(np.count_nonzero(a != b)) == 1


def test_threshold_is_strict_greater():
    # A grid whose 8×8 block coefficients straddle the median: values equal to the
    # median must NOT be set. Build gray so the DCT block has a clear structure:
    # a single-column ramp gives a nonzero DC plus a few AC terms; the median lands
    # among the zeros, and coefficients exactly equal to it stay 0.
    # (Structural assertion — no equal-to-median coefficient may be set.)
    rng = np.arange(PHASH_SIDE, dtype=np.float64)
    grid = np.tile(rng, (PHASH_SIDE, 1))  # horizontal ramp, columns 0..31
    import scipy.fft

    dct = scipy.fft.dctn(grid, type=2, norm="ortho")
    coeffs = dct[:8, :8].reshape(-1)
    med = np.median(coeffs)
    bits = _phash_bits_from_gray(grid)
    # every set bit is strictly greater than the median; none equal to it.
    assert np.all(coeffs[bits == 1] > med)
    assert not np.any(coeffs[bits == 1] == med)


def test_bits_hex_msb_first():
    b = np.zeros(N_BITS, dtype=np.uint8)
    b[0] = 1
    assert _bits_to_hex(b) == "8000000000000000"
    b2 = np.zeros(N_BITS, dtype=np.uint8)
    b2[63] = 1
    assert _bits_to_hex(b2) == "0000000000000001"


# --------------------------------------------------------------------------
# 2. End-to-end pipeline (resize-roundoff-robust cases only)
# --------------------------------------------------------------------------
def test_solid_black_endtoend_all_zero():
    # 0·(LANCZOS weights) = 0 exactly → all-zero grid → all-zero hash (robust).
    bits = _phash_bits(_solid_png_bytes((0, 0, 0)))
    assert bits is not None
    assert _bits_to_hex(bits) == HASH_ALL_ZERO


def test_undecodable_bytes_none():
    assert _phash_bits(b"not a png") is None


def test_compute_identity_solid():
    img = _solid_png_bytes((123, 45, 67))
    out = compute(img, img)
    assert out["s"] == pytest.approx(1.0)
    assert out["channel"] == CHANNEL
    assert out["hamming"] == 0
    assert out["hash_a"] == out["hash_b"]
    assert out["note"] is None


def test_compute_identity_black_exact_hash():
    img = _solid_png_bytes((0, 0, 0))
    out = compute(img, img)
    assert out["s"] == pytest.approx(1.0)
    assert out["hamming"] == 0
    assert out["hash_a"] == HASH_ALL_ZERO


def test_compute_black_vs_generic_bounded():
    # Cross-content similarity is a well-defined fraction in [0, 1].
    out = compute(_solid_png_bytes((0, 0, 0)), _solid_png_bytes((17, 200, 130)))
    assert out["s"] is not None
    assert 0.0 <= out["s"] <= 1.0
    assert out["hamming"] == int(round((1.0 - out["s"]) * N_BITS))


def test_compute_symmetry():
    a = _solid_png_bytes((0, 0, 0))
    b = _solid_png_bytes((240, 20, 20))
    assert compute(a, b)["s"] == compute(b, a)["s"]


def test_compute_symmetry_nontrivial():
    left = np.zeros((100, 200, 3), dtype=np.uint8)
    left[:, 100:, :] = 255  # left-half black, right-half white
    right = np.zeros((100, 200, 3), dtype=np.uint8)
    right[:, :100, :] = 255  # mirror
    ba = _to_png(left)
    bb = _to_png(right)
    assert compute(ba, bb)["s"] == compute(bb, ba)["s"]


def test_compute_determinism_repeat():
    a = _solid_png_bytes((123, 45, 67))
    b = _solid_png_bytes((200, 200, 10))
    assert compute(a, b) == compute(a, b)


def test_compute_from_card_dir(tmp_path):
    da = tmp_path / "cardA"
    db = tmp_path / "cardB"
    da.mkdir()
    db.mkdir()
    _write_solid(da / "shot.png", (10, 20, 30))
    _write_solid(db / "shot.png", (10, 20, 30))  # identical content → s=1
    out = compute(da, db)  # dir path → reads shot.png
    assert out["s"] == pytest.approx(1.0)


def test_compute_missing_shot_is_none(tmp_path):
    da = tmp_path / "cardA"
    da.mkdir()
    _write_solid(da / "shot.png", (255, 0, 0))
    empty = tmp_path / "empty"
    empty.mkdir()
    out = compute(da, empty)  # empty side has no shot.png
    assert out["s"] is None
    assert out["note"] == "empty"
    assert out["hash_a"] is not None
    assert out["hash_b"] is None


def test_compute_undecodable_is_none():
    out = compute(b"garbage-not-png", _solid_png_bytes((1, 2, 3)))
    assert out["s"] is None
    assert out["note"] == "undecodable"


def test_compute_both_absent_none():
    out = compute(None, None)
    assert out["s"] is None
    assert out["note"] == "empty"


def _to_png(rgb_array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb_array, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()
