"""Golden micro-fixtures + invariants for the v-palette channel (scheme §4, appendix A).

Expected values are derived independently of the module under test: single-bin
histograms reduce S to a single kernel entry K_ij = exp(−‖c_i−c_j‖²/2σ²), which
is hand-computable from the raw appendix-A grid constants.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from runner.similarity import v_palette as vp

CARDS = Path(__file__).resolve().parents[2] / "data/batches-dev/devset-42/cards"


# ── grid / kernel constants replicated independently (NOT imported from module) ──
A_WIDTH = 220.0 / 6.0          # a,b bin width
SIGMA = 10.0


def _e(bin_idx: int) -> np.ndarray:
    """Unit histogram: all mass in a single bin."""
    h = np.zeros(144, dtype=np.float64)
    h[bin_idx] = 1.0
    return h


def _idx(li: int, ai: int, bi: int) -> int:
    return li * 36 + ai * 6 + bi  # C-order (L, a, b)


# ─────────────────────────────────────────────────────────────────────────────
# Golden: single-bin histograms → S == K_ij (hand-computed)
# ─────────────────────────────────────────────────────────────────────────────

def test_single_bin_identity_is_one():
    h = _e(0)
    out = vp.compute({"palette_hist": h}, {"palette_hist": h})
    assert out["s"] == pytest.approx(1.0, abs=1e-12)


def test_single_bin_neighbor_b_kernel_value():
    # bins (0,0,0) and (0,0,1): differ only along b by one bin width.
    i, j = _idx(0, 0, 0), _idx(0, 0, 1)
    d2 = A_WIDTH ** 2                       # only the b coordinate differs
    expected = math.exp(-d2 / (2.0 * SIGMA * SIGMA))
    out = vp.compute({"palette_hist": _e(i)}, {"palette_hist": _e(j)})
    assert out["s"] == pytest.approx(expected, abs=1e-12)
    # sanity: tiny (far-apart in ΔE76 relative to σ=10)
    assert 0.0 < out["s"] < 0.01


def test_single_bin_neighbor_L_kernel_value():
    # bins (0,0,0) and (1,0,0): differ only along L by one L-bin width (25).
    i, j = _idx(0, 0, 0), _idx(1, 0, 0)
    d2 = 25.0 ** 2
    expected = math.exp(-d2 / (2.0 * SIGMA * SIGMA))
    out = vp.compute({"palette_hist": _e(i)}, {"palette_hist": _e(j)})
    assert out["s"] == pytest.approx(expected, abs=1e-12)


def test_two_bin_quadratic_form_independent_reference():
    # h1, h2 supported on bins {(0,0,0),(0,0,1)}; S via an independent closed form.
    i, j = _idx(0, 0, 0), _idx(0, 0, 1)
    k = math.exp(-(A_WIDTH ** 2) / (2.0 * SIGMA * SIGMA))
    w1a, w1b = 0.6, 0.4
    w2a, w2b = 0.3, 0.7
    self_a = w1a * w1a + w1b * w1b + 2 * w1a * w1b * k
    self_b = w2a * w2a + w2b * w2b + 2 * w2a * w2b * k
    cross = w1a * w2a + w1b * w2b + (w1a * w2b + w1b * w2a) * k
    expected = cross / math.sqrt(self_a * self_b)

    h1 = np.zeros(144); h1[i], h1[j] = w1a, w1b
    h2 = np.zeros(144); h2[i], h2[j] = w2a, w2b
    out = vp.compute({"palette_hist": h1}, {"palette_hist": h2})
    assert out["s"] == pytest.approx(expected, abs=1e-12)
    assert 0.0 <= out["s"] <= 1.0


def test_normalization_invariance():
    # S is scale-invariant: scaling either histogram must not change S.
    i, j = _idx(0, 1, 2), _idx(3, 4, 5)
    h1 = np.zeros(144); h1[i], h1[j] = 0.2, 0.8
    h2 = np.zeros(144); h2[i], h2[j] = 0.5, 0.5
    base = vp.compute({"palette_hist": h1}, {"palette_hist": h2})["s"]
    scaled = vp.compute({"palette_hist": h1 * 7.0}, {"palette_hist": h2 * 0.01})["s"]
    assert scaled == pytest.approx(base, abs=1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# Degenerate → None (§4)
# ─────────────────────────────────────────────────────────────────────────────

def test_zero_vector_left_is_none():
    z = np.zeros(144)
    nz = _e(0)
    assert vp.compute({"palette_hist": z}, {"palette_hist": nz})["s"] is None


def test_zero_vector_both_is_none():
    z = np.zeros(144)
    assert vp.compute({"palette_hist": z}, {"palette_hist": z})["s"] is None


def test_missing_artifact_is_none():
    assert vp.compute(CARDS / "does-not-exist", {"palette_hist": _e(0)})["s"] is None


def test_empty_dir_missing_shot_is_none(tmp_path):
    # A card dir that EXISTS but has no shot.png → v-palette null (absent shot).
    # (Was r1__claude-haiku-4.5's empty-dir artifact of the FLAG-1 interruption;
    # post-restore no corpus dir is empty, so we use a synthetic empty dir.)
    empty = tmp_path / "empty-card"
    empty.mkdir()
    assert vp.compute(empty, {"palette_hist": _e(0)})["s"] is None


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end invariants on a real shot.png
# ─────────────────────────────────────────────────────────────────────────────

def _first_real_card() -> Path:
    for d in sorted(CARDS.iterdir()):
        if (d / "shot.png").is_file():
            return d
    pytest.skip("no devset card with shot.png")


def test_real_identity_is_one():
    d = _first_real_card()
    out = vp.compute(d, d)
    assert out["s"] == pytest.approx(1.0, abs=1e-9)


def test_real_symmetry():
    cards = [d for d in sorted(CARDS.iterdir()) if (d / "shot.png").is_file()]
    if len(cards) < 2:
        pytest.skip("need two real cards")
    a, b = cards[0], cards[1]
    assert vp.compute(a, b)["s"] == pytest.approx(vp.compute(b, a)["s"], abs=1e-12)


def test_real_determinism_repeat_call():
    cards = [d for d in sorted(CARDS.iterdir()) if (d / "shot.png").is_file()]
    if len(cards) < 2:
        pytest.skip("need two real cards")
    a, b = cards[0], cards[1]
    s1 = vp.compute(a, b)["s"]
    s2 = vp.compute(a, b)["s"]
    assert s1 == s2  # exact bit-for-bit determinism
    assert 0.0 <= s1 <= 1.0


def test_real_value_in_unit_interval():
    cards = [d for d in sorted(CARDS.iterdir()) if (d / "shot.png").is_file()][:6]
    for x in range(len(cards)):
        for y in range(x + 1, len(cards)):
            s = vp.compute(cards[x], cards[y])["s"]
            assert s is None or (0.0 <= s <= 1.0)


def test_swatch_top8_shape_and_order():
    d = _first_real_card()
    out = vp.compute(d, d)
    sw = out["swatch_a"]
    assert 1 <= len(sw) <= 8
    keys = [(-x["weight"], x["bin"]) for x in sw]
    assert keys == sorted(keys)  # sorted by (−weight, bin index)


def test_kernel_is_symmetric_psd():
    K = vp._K
    assert np.allclose(K, K.T, atol=0)
    eig = np.linalg.eigvalsh(K)
    assert eig.min() > -1e-9  # PSD


def test_dim_bin_boundaries():
    # a == A_MAX (110) must fold into the last a-bin (index 5), not overflow
    # (last bin closed [lo,hi]).
    assert vp._dim_bin(np.array([110.0]), vp.A_MIN, vp.A_MAX, vp.A_BINS)[0] == vp.A_BINS - 1
    # a == A_MIN (−110) → bin 0.
    assert vp._dim_bin(np.array([-110.0]), vp.A_MIN, vp.A_MAX, vp.A_BINS)[0] == 0
    # Values clearly interior land in the expected bins; a value just above an
    # interior edge is in the UPPER bin, just below in the LOWER (half-open
    # [lo,hi)). (The exact-edge float is representation-fragile and measure-zero
    # for real Lab data; binning is a pure deterministic function regardless.)
    edge = vp.A_MIN + (220.0 / 6.0)  # ~boundary between bin 0 and bin 1
    assert vp._dim_bin(np.array([edge + 1e-6]), vp.A_MIN, vp.A_MAX, vp.A_BINS)[0] == 1
    assert vp._dim_bin(np.array([edge - 1e-6]), vp.A_MIN, vp.A_MAX, vp.A_BINS)[0] == 0
    # out-of-range clips into domain.
    assert vp._dim_bin(np.array([500.0]), vp.A_MIN, vp.A_MAX, vp.A_BINS)[0] == vp.A_BINS - 1
    assert vp._dim_bin(np.array([-500.0]), vp.A_MIN, vp.A_MAX, vp.A_BINS)[0] == 0
