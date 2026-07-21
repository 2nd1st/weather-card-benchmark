"""Golden micro-fixtures for channel v-dhash (scheme §4, appendix A row).

Appendix-A def: 灰度 17×16, 水平相邻差分(右>左→1) → 256 bit; sim = 1 − hamming/256.

Hand-computed anchors: a full-width strictly-increasing horizontal grayscale ramp
downscales (LANCZOS) to strictly-increasing column means → every horizontal
adjacent diff (right>left) is positive → all-ones dhash (0xff·32); the reversed
ramp → all-zeros; a uniform card → all-zeros. These are verified byte-exact.
"""

import io

import numpy as np
import pytest
from PIL import Image

from runner.similarity import v_dhash as vd


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _png_img(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _ramp(size=(340, 160), flip: bool = False) -> bytes:
    """Full-width strictly-increasing horizontal grayscale ramp (RGB).

    Downscales to strictly-increasing column means → all-ones dhash (or all-zeros
    reversed).
    """
    w, h = size
    row = np.linspace(0, 255, w, dtype=np.float64)
    if flip:
        row = row[::-1]
    arr = np.tile(row, (h, 1)).astype(np.uint8)
    rgb = np.stack([arr, arr, arr], axis=-1)
    return _png_img(Image.fromarray(np.ascontiguousarray(rgb), mode="RGB"))


def _gradient(size=(320, 200), flip=False) -> bytes:
    w, h = size
    row = np.linspace(0, 255, w, dtype=np.float64)
    if flip:
        row = row[::-1]
    arr = np.tile(row, (h, 1)).astype(np.uint8)
    rgb = np.stack([arr, arr, arr], axis=-1)
    return _png_img(Image.fromarray(rgb, mode="RGB"))


# --------------------------------------------------------------------------
# hand-computed byte-exact anchors
# --------------------------------------------------------------------------
def test_increasing_ramp_hash_all_ones():
    bits = vd._dhash_bits(_ramp(flip=False))
    assert bits.shape == (256,)
    assert bits.sum() == 256
    assert vd._bits_to_hex(bits) == "ff" * 32


def test_decreasing_ramp_hash_all_zeros():
    bits = vd._dhash_bits(_ramp(flip=True))
    assert bits.sum() == 0
    assert vd._bits_to_hex(bits) == "00" * 32


def test_opposite_ramps_hamming_256_sim_zero():
    r = vd.compute(_ramp(flip=False), _ramp(flip=True))
    assert r["hamming"] == 256
    assert r["s"] == 0.0
    assert r["hash_a"] == "ff" * 32
    assert r["hash_b"] == "00" * 32


# --------------------------------------------------------------------------
# identity / range / structure
# --------------------------------------------------------------------------
def test_identity_is_one():
    b = _ramp()
    r = vd.compute(b, b)
    assert r["s"] == 1.0
    assert r["hamming"] == 0
    assert r["channel"] == "v-dhash"


def test_identity_real_gradient_is_one():
    b = _gradient()
    assert vd.compute(b, b)["s"] == 1.0


def test_s_in_unit_interval():
    s = vd.compute(_gradient(), _gradient(flip=True))["s"]
    assert 0.0 <= s <= 1.0


def test_hash_is_64_hex_chars():
    r = vd.compute(_gradient(), _gradient())
    assert len(r["hash_a"]) == 64 and len(r["hash_b"]) == 64
    int(r["hash_a"], 16)  # valid hex


# --------------------------------------------------------------------------
# symmetry / determinism
# --------------------------------------------------------------------------
def test_symmetry():
    a, b = _gradient(), _gradient(flip=True)
    assert vd.compute(a, b)["s"] == vd.compute(b, a)["s"]
    assert vd.compute(a, b)["hamming"] == vd.compute(b, a)["hamming"]


def test_determinism_repeat_call():
    a, b = _gradient(), _gradient(flip=True)
    assert vd.compute(a, b) == vd.compute(a, b)


# --------------------------------------------------------------------------
# degenerate / null (§4)
# --------------------------------------------------------------------------
def test_missing_side_is_none():
    good = _ramp()
    assert vd.compute(None, good)["s"] is None
    assert vd.compute(good, None)["s"] is None
    assert vd.compute(None, None)["s"] is None  # double-empty


def test_empty_bytes_is_none():
    good = _ramp()
    assert vd.compute(b"", good)["s"] is None
    assert vd.compute(good, b"")["s"] is None


def test_undecodable_bytes_is_none():
    good = _ramp()
    r = vd.compute(b"not a png at all", good)
    assert r["s"] is None
    assert r["note"] == "undecodable"


def test_uniform_image_is_not_null():
    """A flat card → all-zero hash, but hamming is defined → sim computed, not null."""
    flat = _png_img(Image.new("RGB", (320, 200), (120, 120, 120)))
    r = vd.compute(flat, flat)
    assert r["s"] == 1.0  # two flat cards ARE maximally similar
    assert r["hash_a"] == "00" * 32


# --------------------------------------------------------------------------
# alpha compositing + artifact resolution
# --------------------------------------------------------------------------
def test_alpha_composited_over_white():
    transparent = _png_img(Image.new("RGBA", (320, 200), (0, 0, 0, 0)))
    white = _png_img(Image.new("RGB", (320, 200), (255, 255, 255)))
    # transparent → composites to white → identical hash to a white card
    assert vd.compute(transparent, white)["s"] == 1.0


def test_artifact_dict_and_path(tmp_path):
    b = _ramp()
    d = tmp_path / "card"
    d.mkdir()
    (d / "shot.png").write_bytes(b)
    base = vd.compute(b, b)["hash_a"]
    assert vd.compute({"dir": str(d)}, b)["hash_a"] == base
    assert vd.compute(str(d), b)["hash_a"] == base  # directory path
    assert vd.compute(str(d / "shot.png"), b)["hash_a"] == base  # file path
    assert vd.compute({"shot_png": b}, b)["hash_a"] == base  # inline bytes
