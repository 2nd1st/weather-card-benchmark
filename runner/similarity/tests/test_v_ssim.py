"""Golden micro-fixture tests for the v-ssim similarity channel (scheme §4).

Expected values are hand-computed / independently derived here (never taken from
the code under test), per the §4 golden-fixture rule.

v-ssim = skimage.metrics.structural_similarity pinned (win_size=7,
gaussian_weights=False, data_range=255, channel_axis=None, K1=0.01, K2=0.03,
use_sample_covariance=True); raw = returned scalar; formal s = clip((raw+1)/2,0,1).

Closed-form anchor used below — for two CONSTANT images (variance = 0,
covariance = 0) the SSIM contrast/structure factors cancel and

    raw = (2·c1·c2 + C1) / (c1² + c2² + C1),   C1 = (K1·data_range)² = (0.01·255)²

which is hand-computable independently of skimage.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from runner.similarity.v_ssim import (
    CHANNEL,
    TARGET_H,
    TARGET_W,
    compute,
    ssim_raw,
    to_s,
)

C1 = (0.01 * 255) ** 2  # 6.502500000000001  (K1·data_range)²


# --------------------------------------------------------------------------
# to_s mapping (pure, fully hand-computed)
# --------------------------------------------------------------------------
def test_to_s_endpoints_and_midpoint():
    assert to_s(1.0) == 1.0          # perfect similarity → channel max
    assert to_s(-1.0) == 0.0         # perfect anti-similarity → 0
    assert to_s(0.0) == 0.5          # midpoint
    assert to_s(0.5) == 0.75


def test_to_s_clips_out_of_range():
    # raw is mathematically in [-1,1]; clip guards fp overshoot regardless.
    assert to_s(1.0 + 1e-9) == 1.0
    assert to_s(2.0) == 1.0
    assert to_s(-3.0) == 0.0


# --------------------------------------------------------------------------
# ssim_raw core (hand-computed expectations)
# --------------------------------------------------------------------------
def test_ssim_raw_identity_is_one():
    g = np.arange(100, dtype=np.uint8).reshape(10, 10)
    assert ssim_raw(g, g) == 1.0


def test_ssim_raw_two_constants_closed_form():
    # const(100) vs const(150): raw = (2·100·150 + C1)/(100²+150²+C1)
    c1, c2 = 100.0, 150.0
    expected = (2 * c1 * c2 + C1) / (c1 * c1 + c2 * c2 + C1)
    a = np.full((10, 10), 100, np.uint8)
    b = np.full((10, 10), 150, np.uint8)
    got = ssim_raw(a, b)
    assert got == pytest.approx(expected, abs=1e-12)
    # sanity: the literal hand value
    assert got == pytest.approx(0.923092310530793, abs=1e-12)


def test_ssim_raw_black_vs_white_closed_form():
    # const(0) vs const(255): raw = (0 + C1)/(0 + 255² + C1)
    expected = C1 / (255.0 * 255.0 + C1)
    a = np.zeros((10, 10), np.uint8)
    b = np.full((10, 10), 255, np.uint8)
    assert ssim_raw(a, b) == pytest.approx(expected, abs=1e-12)


def test_ssim_raw_symmetry():
    rng = np.random.default_rng(0)
    a = rng.integers(0, 256, size=(20, 20), dtype=np.uint8)
    b = rng.integers(0, 256, size=(20, 20), dtype=np.uint8)
    assert ssim_raw(a, b) == ssim_raw(b, a)


# --------------------------------------------------------------------------
# compute() end-to-end
# --------------------------------------------------------------------------
def _solid(color, size=(400, 250)):
    return Image.new("RGB", size, color)


def _write_solid(path, color, size=(400, 250)):
    _solid(color, size).save(path, format="PNG")
    return path


def test_compute_identity_solid():
    img = _solid((123, 45, 67))
    out = compute(img, img)
    assert out["s"] == 1.0
    assert out["raw"] == 1.0
    assert out["channel"] == CHANNEL
    assert out["a_present"] and out["b_present"]
    assert out["input_size"] == [TARGET_W, TARGET_H]
    # pinned params surfaced as diagnostics
    assert out["win_size"] == 7
    assert out["data_range"] == 255
    assert out["k1"] == 0.01 and out["k2"] == 0.03
    assert out["gaussian_weights"] is False
    assert out["use_sample_covariance"] is True
    assert out["channel_axis"] is None


def test_compute_identity_photo_like():
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, size=(250, 400, 3), dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    out = compute(img, img)
    assert out["s"] == 1.0
    assert out["raw"] == 1.0


def test_compute_s_is_mapped_from_raw():
    # two solid grays → constant after grayscale/resize → closed form on the
    # Rec.601 luma of the two solids.
    a = _solid((100, 100, 100))
    b = _solid((150, 150, 150))
    out = compute(a, b)
    assert out["s"] == pytest.approx(to_s(out["raw"]), abs=1e-15)
    assert 0.0 <= out["s"] <= 1.0
    assert -1.0 <= out["raw"] <= 1.0


def test_compute_symmetry():
    a = _solid((240, 20, 20))
    b = _solid((10, 30, 250))
    oa = compute(a, b)
    ob = compute(b, a)
    assert oa["s"] == ob["s"]
    assert oa["raw"] == ob["raw"]


def test_compute_determinism_repeat():
    a = _solid((123, 45, 67))
    b = _solid((200, 200, 10))
    first = compute(a, b)
    second = compute(a, b)
    assert first["s"] == second["s"]
    assert first["raw"] == second["raw"]


def test_compute_from_card_dir(tmp_path):
    da = tmp_path / "cardA"
    db = tmp_path / "cardB"
    da.mkdir()
    db.mkdir()
    _write_solid(da / "shot.png", (255, 0, 0))
    _write_solid(db / "shot.png", (255, 0, 0))
    out = compute(da, db)  # dir path → reads shot.png
    assert out["s"] == 1.0
    assert out["raw"] == 1.0


def test_compute_missing_shot_is_none(tmp_path):
    da = tmp_path / "cardA"
    da.mkdir()
    _write_solid(da / "shot.png", (255, 0, 0))
    empty = tmp_path / "empty"
    empty.mkdir()
    out = compute(da, empty)  # empty side has no shot.png
    assert out["s"] is None
    assert out["raw"] is None
    assert out["a_present"] is True
    assert out["b_present"] is False


def test_compute_both_absent_none():
    out = compute(None, None)  # double-absent → None (§4)
    assert out["s"] is None
    assert out["raw"] is None
    assert out["a_present"] is False
    assert out["b_present"] is False


def test_compute_zero_area_none():
    try:
        empty = Image.new("RGB", (0, 10))
    except ValueError:
        pytest.skip("PIL refuses zero-area image on this build")
    out = compute(empty, _solid((10, 10, 10)))
    assert out["s"] is None
    assert out["raw"] is None
