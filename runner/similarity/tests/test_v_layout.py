"""Golden micro-fixtures for channel v-layout (scheme §4, appendix A row).

Appendix-A def: 32×20 grayscale (Rec.601), LANCZOS resize, C-order flatten,
cosine. Zero-vector (all-black) on either side → s = None.
"""

import io

import numpy as np
import pytest
from PIL import Image

from runner.similarity import v_layout as vl


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _png(color, size=(64, 40), mode="RGB"):
    """Solid-color PNG bytes."""
    return _png_img(Image.new(mode, size, color))


def _png_img(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------
# pure cosine math — hand-computed expected values (§4 cosine)
# --------------------------------------------------------------------------
def test_cosine_orthogonal():
    assert vl._cosine(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == 0.0


def test_cosine_known_angle():
    # (3,4)·(4,3)=24 ; |a|=|b|=5 ; 24/25 = 0.96
    assert vl._cosine(np.array([3.0, 4.0]), np.array([4.0, 3.0])) == pytest.approx(0.96)


def test_cosine_parallel_is_one():
    assert vl._cosine(np.array([1.0, 2.0]), np.array([2.0, 4.0])) == pytest.approx(1.0)


def test_cosine_zero_vector_is_none():
    assert vl._cosine(np.array([0.0, 0.0]), np.array([1.0, 1.0])) is None
    assert vl._cosine(np.array([1.0, 1.0]), np.array([0.0, 0.0])) is None
    assert vl._cosine(np.array([0.0, 0.0]), np.array([0.0, 0.0])) is None  # double-empty


# --------------------------------------------------------------------------
# feature vector shape / imaging
# --------------------------------------------------------------------------
def test_vector_shape_640():
    v = vl.layout_vector(_png((128, 128, 128)))
    assert v.shape == (vl.VEC_LEN,) == (640,)
    assert v.dtype == np.float64


def test_uniform_grayscale_value_rec601():
    # solid (100,150,200): Rec.601 = round(0.299*100+0.587*150+0.114*200)
    #   = round(29.9 + 88.05 + 22.8) = round(140.75) = 141
    v = vl.layout_vector(_png((100, 150, 200)))
    assert np.allclose(v, 141.0)


def test_alpha_composited_over_white():
    # fully transparent RGBA → composites to white (255) grayscale
    v = vl.layout_vector(_png((0, 0, 0, 0), mode="RGBA"))
    assert np.allclose(v, 255.0)


# --------------------------------------------------------------------------
# channel-level golden cases
# --------------------------------------------------------------------------
def test_identity_is_one():
    b = _png_img(_gradient())
    r = vl.compute(b, b)
    assert r["s"] == 1.0


def test_uniform_pair_is_parallel_one():
    # any two uniform (non-black) images → parallel vectors → cosine 1
    a = vl.compute(_png((60, 60, 60)), _png((200, 200, 200)))
    assert a["s"] == pytest.approx(1.0)


def test_black_side_is_none():
    black = _png((0, 0, 0))
    other = _png((128, 128, 128))
    assert vl.compute(black, other)["s"] is None
    assert vl.compute(other, black)["s"] is None
    assert vl.compute(black, black)["s"] is None  # double zero-vector → None


def test_symmetry():
    a = _png_img(_gradient())
    b = _png_img(_gradient(flip=True))
    assert vl.compute(a, b)["s"] == vl.compute(b, a)["s"]


def test_determinism_repeat_call():
    a = _png_img(_gradient())
    b = _png_img(_gradient(flip=True))
    r1 = vl.compute(a, b)["s"]
    r2 = vl.compute(a, b)["s"]
    assert r1 == r2


def test_s_in_unit_interval():
    a = _png_img(_gradient())
    b = _png_img(_gradient(flip=True))
    s = vl.compute(a, b)["s"]
    assert 0.0 <= s <= 1.0


def test_diagnostics_present():
    r = vl.compute(_png((128, 128, 128)), _png((64, 64, 64)))
    assert r["channel"] == "v-layout"
    assert r["grid"] == [32, 20]
    assert r["vec_len"] == 640
    assert r["zero_a"] is False and r["zero_b"] is False


# --------------------------------------------------------------------------
def _gradient(size=(64, 40), flip=False):
    """Deterministic horizontal grayscale gradient image (RGB)."""
    w, h = size
    row = np.linspace(0, 255, w, dtype=np.float64)
    if flip:
        row = row[::-1]
    arr = np.tile(row, (h, 1)).astype(np.uint8)
    rgb = np.stack([arr, arr, arr], axis=-1)
    return Image.fromarray(rgb, mode="RGB")
