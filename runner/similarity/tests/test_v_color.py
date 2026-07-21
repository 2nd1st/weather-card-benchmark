"""Golden micro-fixture tests for the v-color similarity channel (scheme §4).

Expected values are hand-computed / independently constructed here (never from
the code under test), per §4 golden-fixture rule.

v-color = 128×80 RGB, per-channel ``>>6`` (4 levels), 64-bin C-order histogram
(index = r*16 + g*4 + b), proportion-normalized, cosine.

Solid-color images are LANCZOS-resize invariant (a constant field stays
constant), so their histogram is an exact one-hot at the quantized bin — the
foundation of the hand-computed cases below.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image

from runner.similarity.v_color import (
    NBINS,
    compute,
    cosine,
    histogram_from_image,
)

# quantized bin index for a few solid colors (r*16 + g*4 + b, each channel >>6)
BIN_RED = (255 >> 6) * 16 + (0 >> 6) * 4 + (0 >> 6)   # (3,0,0) -> 48
BIN_BLUE = (0 >> 6) * 16 + (0 >> 6) * 4 + (255 >> 6)  # (0,0,3) -> 3
BIN_WHITE = (255 >> 6) * 16 + (255 >> 6) * 4 + (255 >> 6)  # (3,3,3) -> 63


def _solid(color, size=(200, 100)):
    return Image.new("RGB", size, color)


def _write_solid(path, color, size=(200, 100)):
    _solid(color, size).save(path, format="PNG")
    return path


# --------------------------------------------------------------------------
# histogram building blocks
# --------------------------------------------------------------------------
def test_bin_indices_hand_computed():
    assert BIN_RED == 48
    assert BIN_BLUE == 3
    assert BIN_WHITE == 63


def test_histogram_solid_is_one_hot():
    h = histogram_from_image(_solid((255, 0, 0)))
    assert h is not None
    assert h.shape == (NBINS,)
    assert h.sum() == pytest.approx(1.0)
    expected = np.zeros(NBINS)
    expected[BIN_RED] = 1.0
    assert np.array_equal(h, expected)


def test_histogram_zero_area_none():
    # PIL permits a zero-area image; v-color must return None (no pixels).
    try:
        empty = Image.new("RGB", (0, 10))
    except ValueError:
        pytest.skip("PIL refuses zero-area image on this build")
    assert histogram_from_image(empty) is None


# --------------------------------------------------------------------------
# cosine core (hand-computed)
# --------------------------------------------------------------------------
def test_cosine_orthogonal_zero():
    a = np.zeros(NBINS)
    a[BIN_RED] = 1.0
    b = np.zeros(NBINS)
    b[BIN_BLUE] = 1.0
    assert cosine(a, b) == 0.0


def test_cosine_identity_one():
    a = np.zeros(NBINS)
    a[BIN_RED] = 1.0
    assert cosine(a, a) == pytest.approx(1.0)


def test_cosine_hand_value_half_mix():
    # h1 = one-hot@red ; h2 = 0.5@red + 0.5@blue
    # cos = 0.5 / (1 * sqrt(0.5)) = 1/sqrt(2)
    a = np.zeros(NBINS)
    a[BIN_RED] = 1.0
    b = np.zeros(NBINS)
    b[BIN_RED] = 0.5
    b[BIN_BLUE] = 0.5
    assert cosine(a, b) == pytest.approx(1.0 / math.sqrt(2.0))


def test_cosine_zero_vector_none():
    a = np.zeros(NBINS)
    b = np.zeros(NBINS)
    b[BIN_RED] = 1.0
    assert cosine(a, b) is None
    assert cosine(b, a) is None
    assert cosine(a, a) is None  # double-empty → None


# --------------------------------------------------------------------------
# compute() end-to-end
# --------------------------------------------------------------------------
def test_compute_identity_solid():
    img = _solid((255, 0, 0))
    out = compute(img, img)
    assert out["s"] == pytest.approx(1.0)
    assert out["channel"] == "v-color"
    assert out["a_present"] and out["b_present"]


def test_compute_orthogonal_solids():
    out = compute(_solid((255, 0, 0)), _solid((0, 0, 255)))
    assert out["s"] == 0.0


def test_compute_symmetry():
    a = _solid((240, 20, 20))
    b = _solid((10, 30, 250))
    assert compute(a, b)["s"] == compute(b, a)["s"]


def test_compute_determinism_repeat():
    a = _solid((123, 45, 67))
    b = _solid((200, 200, 10))
    first = compute(a, b)["s"]
    second = compute(a, b)["s"]
    assert first == second


def test_compute_from_card_dir(tmp_path):
    da = tmp_path / "cardA"
    db = tmp_path / "cardB"
    da.mkdir()
    db.mkdir()
    _write_solid(da / "shot.png", (255, 0, 0))
    _write_solid(db / "shot.png", (255, 0, 0))
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
    assert out["a_present"] is True
    assert out["b_present"] is False


def test_compute_both_absent_none():
    assert compute(None, None)["s"] is None
