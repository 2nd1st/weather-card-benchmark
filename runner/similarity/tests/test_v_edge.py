"""Golden micro-fixtures for channel v-edge (scheme §4, appendix A row).

Appendix-A def: 320×200 grayscale (Rec.601, LANCZOS) → sobel gx(axis=1)/gy(axis=0)
mode="reflect" → θ folded to [0,180) → 8-bin magnitude-weighted orientation
histogram per 4×4 cell (80×50 px) → per-cell L1-norm (all-zero stays zero) →
concat cell-row-major × bin 0..7 → 128-dim cosine. Zero-vector (flat image, no
gradient) on either side → s = None.

Hand-computed anchors (no dependency on the code under test to produce them):
  * a pure VERTICAL grayscale ramp (value ∝ column) has gradient only in x,
    so θ = atan2(0, +) = 0° → every pixel lands in bin 0; each cell's L1-normed
    histogram is exactly [1,0,0,0,0,0,0,0].
  * a pure HORIZONTAL ramp (value ∝ row) has gradient only in y, θ = atan2(+,0)
    = 90° → bin 4 → each cell exactly [0,0,0,0,1,0,0,0].
  * those two descriptors share no non-zero bin in any cell ⇒ dot = 0 ⇒
    cosine = 0 exactly.
  * a perfectly flat image has zero gradient everywhere ⇒ zero descriptor ⇒
    s = None.
"""

import io

import numpy as np
import pytest
from PIL import Image

from runner.similarity import v_edge as ve


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _png_img(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _png(color, size=(320, 200), mode="RGB") -> bytes:
    return _png_img(Image.new(mode, size, color))


def _gray_img(arr: np.ndarray) -> Image.Image:
    """Build an RGB PNG-able image from a float grayscale array."""
    a = np.clip(np.rint(arr), 0, 255).astype(np.uint8)
    return Image.fromarray(np.stack([a, a, a], axis=-1), mode="RGB")


def _vert_ramp(size=(320, 200), flip=False) -> np.ndarray:
    """Grayscale that increases along columns (x). Gradient purely horizontal."""
    w, h = size
    row = np.linspace(0, 255, w, dtype=np.float64)
    if flip:
        row = row[::-1]
    return np.tile(row, (h, 1))


def _horiz_ramp(size=(320, 200)) -> np.ndarray:
    """Grayscale that increases along rows (y). Gradient purely vertical."""
    w, h = size
    col = np.linspace(0, 255, h, dtype=np.float64).reshape(-1, 1)
    return np.tile(col, (1, w))


# --------------------------------------------------------------------------
# pure cosine math — hand-computed expected values (§4 cosine)
# --------------------------------------------------------------------------
def test_cosine_orthogonal():
    assert ve._cosine(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == 0.0


def test_cosine_known_angle():
    # (3,4)·(4,3)=24 ; |a|=|b|=5 ; 24/25 = 0.96
    assert ve._cosine(np.array([3.0, 4.0]), np.array([4.0, 3.0])) == pytest.approx(0.96)


def test_cosine_parallel_is_one():
    assert ve._cosine(np.array([1.0, 2.0]), np.array([2.0, 4.0])) == pytest.approx(1.0)


def test_cosine_zero_vector_is_none():
    assert ve._cosine(np.array([0.0, 0.0]), np.array([1.0, 1.0])) is None
    assert ve._cosine(np.array([1.0, 1.0]), np.array([0.0, 0.0])) is None
    assert ve._cosine(np.array([0.0, 0.0]), np.array([0.0, 0.0])) is None  # double-empty


# --------------------------------------------------------------------------
# imaging → 320×200 grayscale float64
# --------------------------------------------------------------------------
def test_gray_shape_and_dtype():
    g = ve.gray_320x200(_png((128, 128, 128)))
    assert g.shape == (ve.TARGET_H, ve.TARGET_W) == (200, 320)
    assert g.dtype == np.float64


def test_uniform_grayscale_value_rec601():
    # solid (100,150,200): Rec.601 = round(0.299*100+0.587*150+0.114*200)
    #   = round(140.75) = 141
    g = ve.gray_320x200(_png((100, 150, 200)))
    assert np.allclose(g, 141.0)


def test_alpha_composited_over_white():
    # fully transparent RGBA → composites to white (255) grayscale
    g = ve.gray_320x200(_png((0, 0, 0, 0), size=(320, 200), mode="RGBA"))
    assert np.allclose(g, 255.0)


# --------------------------------------------------------------------------
# descriptor shape / binning / normalization / concat order
# --------------------------------------------------------------------------
def test_descriptor_shape_128():
    d = ve.edge_descriptor(_vert_ramp())
    assert d.shape == (ve.VEC_LEN,) == (128,)
    assert d.dtype == np.float64


def test_vertical_ramp_all_bin0():
    # gradient purely horizontal ⇒ θ=0 ⇒ bin 0 in every cell, L1-normed to 1.
    d = ve.edge_descriptor(_vert_ramp()).reshape(16, 8)
    expected = np.tile([1, 0, 0, 0, 0, 0, 0, 0], (16, 1))
    assert np.allclose(d, expected)


def test_horizontal_ramp_all_bin4():
    # gradient purely vertical ⇒ θ=90° ⇒ bin 4 in every cell.
    d = ve.edge_descriptor(_horiz_ramp()).reshape(16, 8)
    expected = np.tile([0, 0, 0, 0, 1, 0, 0, 0], (16, 1))
    assert np.allclose(d, expected)


def test_cells_l1_normalized():
    # every occupied cell's 8-bin histogram sums to exactly 1 (L1 norm).
    d = ve.edge_descriptor(_vert_ramp()).reshape(16, 8)
    assert np.allclose(d.sum(axis=1), 1.0)


def test_flat_image_zero_descriptor():
    # no gradient anywhere ⇒ zero descriptor.
    d = ve.edge_descriptor(np.full((ve.TARGET_H, ve.TARGET_W), 128.0))
    assert not np.any(d)


def test_cell_geometry_and_concat_order():
    # vertical ramp on the left two cell-columns (x<160), flat on the right.
    # left cell-columns (cx=0,1) occupied; right (cx=2,3) all-zero.
    g = np.zeros((ve.TARGET_H, ve.TARGET_W))
    g[:, :160] = np.tile(np.linspace(0, 255, 160), (ve.TARGET_H, 1))
    g[:, 160:] = 255.0
    occ = (ve.edge_descriptor(g).reshape(16, 8).sum(axis=1) > 0).reshape(4, 4)
    expected = np.array([[1, 1, 0, 0]] * 4, dtype=bool)
    assert np.array_equal(occ, expected)


def test_descriptor_wrong_shape_raises():
    with pytest.raises(ValueError):
        ve.edge_descriptor(np.zeros((10, 10)))


# --------------------------------------------------------------------------
# channel-level golden cases
# --------------------------------------------------------------------------
def test_identity_is_one():
    b = _png_img(_gray_img(_vert_ramp()))
    r = ve.compute(b, b)
    assert r["s"] == 1.0


def test_orthogonal_orientations_zero():
    # vertical-ramp (bin0) vs horizontal-ramp (bin4) → orthogonal → cosine 0.
    a = ve.edge_descriptor(_vert_ramp())
    b = ve.edge_descriptor(_horiz_ramp())
    assert ve._cosine(a, b) == 0.0


def test_flat_side_is_none():
    flat = _png((128, 128, 128))
    other = _png_img(_gray_img(_vert_ramp()))
    assert ve.compute(flat, other)["s"] is None
    assert ve.compute(other, flat)["s"] is None
    assert ve.compute(flat, flat)["s"] is None  # double zero-vector → None


def test_symmetry():
    a = _png_img(_gray_img(_vert_ramp()))
    b = _png_img(_gray_img(_vert_ramp(flip=True)))
    assert ve.compute(a, b)["s"] == ve.compute(b, a)["s"]


def test_determinism_repeat_call():
    a = _png_img(_gray_img(_vert_ramp()))
    b = _png_img(_gray_img(_horiz_ramp()))
    r1 = ve.compute(a, b)["s"]
    r2 = ve.compute(a, b)["s"]
    assert r1 == r2


def test_s_in_unit_interval():
    a = _png_img(_gray_img(_vert_ramp()))
    b = _png_img(_gray_img(_horiz_ramp()))
    s = ve.compute(a, b)["s"]
    assert 0.0 <= s <= 1.0


def test_diagnostics_present():
    r = ve.compute(_png_img(_gray_img(_vert_ramp())),
                   _png_img(_gray_img(_horiz_ramp())))
    assert r["channel"] == "v-edge"
    assert r["input_size"] == [320, 200]
    assert r["cells"] == [4, 4]
    assert r["n_bins"] == 8
    assert r["vec_len"] == 128
    assert r["zero_a"] is False and r["zero_b"] is False
