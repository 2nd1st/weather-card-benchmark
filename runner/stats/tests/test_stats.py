"""L3 stats tests (scheme §5).

Coverage:
  * seed_h / mask_hash byte-exact formulas.
  * coverage decimal-string grammar (terminating / non-terminating / null) + the
    EXACT (Fraction) 80% gate.
  * THE test — a hand-computed golden: N=3, |Ω_c|=6, an L1-scalar hypothesis whose
    per-config exact-enumeration p is verified BY HAND (= 2/6).
  * determinism (same hypothesis_id → same seed → same b/p).
  * strict null propagation (channel self-consistency ineligible at N=3; all-null
    scalar) and the pooled exhaustive pre-check.
  * Holm: null excluded from ranking, FULL family size k stays the denominator.
  * descriptive: self_consistency / cross / separation / distinctiveness / LORO.
  * observed-assignment invariance (ω_obs re-labels back to the true P-min set).
  * integration determinism on the M1 dev batch (byte-identical re-run).
"""
from __future__ import annotations

import hashlib
import json

import pytest
from pathlib import Path

import rfc8785

from runner.manifest import nfc
from runner.seed import bits_from_value, enumerate_omega
from runner.stats.descriptive import descriptive_for_variant
from runner.stats.hypotheses import l1_target, channel_target, pooled_family
from runner.stats.randomization import (
    ConfigRunModel,
    PooledResult,
    build_T_table,
    exploratory,
    holm,
    pooled,
)
from runner.stats.serialize import (
    coverage_meets_threshold,
    coverage_string,
    mask_hash,
    seed_h,
)

BATCH = Path(__file__).resolve().parents[3] / "data" / "batches-dev" / "2026-07-16--m1-dev-e2e-smoke"

# BATCH is a dev smoke batch that no longer exists — it was removed in the data
# cleanup, and it was never published. Absent corpus is a skip, not a failure
# (see runner/tests/conftest.py for the same rule elsewhere).
_requires_batch = pytest.mark.skipif(
    not BATCH.is_dir(), reason=f"stats smoke batch not present: {BATCH.name}"
)


# --------------------------------------------------------------------------- #
# seed / hash formulas
# --------------------------------------------------------------------------- #
def test_seed_h_matches_scheme_formula():
    hid = "H-pooled-channel-v-phash"
    expected = int.from_bytes(
        hashlib.sha256(b"perm\x00" + nfc(hid).encode("utf-8")).digest()[0:8],
        "little",
    ) ^ 20260716
    assert seed_h(hid) == expected
    assert 0 <= seed_h(hid) < 2**64


def test_mask_hash_is_jcs_sha256():
    ids = ["gpt-5.6-sol--api--raw--dev", "grok-4.5--api--raw--dev"]
    expected = hashlib.sha256(rfc8785.dumps([nfc(i) for i in ids])).hexdigest()
    assert mask_hash(ids) == expected
    assert len(mask_hash(ids)) == 64 and mask_hash(ids).islower()


# --------------------------------------------------------------------------- #
# coverage decimal string + exact gate
# --------------------------------------------------------------------------- #
def test_coverage_string_terminating():
    assert coverage_string(3, 5) == "0.75"   # 3/4
    assert coverage_string(1, 2) == "1"       # 1/1
    assert coverage_string(0, 3) == "0"       # 0/2
    assert coverage_string(2, 5) == "0.5"     # 2/4


def test_coverage_string_nonterminating_rounds():
    assert coverage_string(2, 4) == "0.666667"   # 2/3, ROUND_HALF_UP @ 6dp
    assert coverage_string(1, 4) == "0.333333"   # 1/3


def test_coverage_string_zero_denominator_is_null():
    assert coverage_string(5, 1) is None


def test_coverage_threshold_uses_exact_fraction():
    assert coverage_meets_threshold(4, 5) is True      # 4/4 = 1.0
    assert coverage_meets_threshold(3, 5) is False     # 3/4 = 0.75 < 0.8
    assert coverage_meets_threshold(2, 4) is False     # 2/3 ≈ 0.667
    assert coverage_meets_threshold(5, 1) is False     # denom 0


def test_coverage_threshold_boundary_exact_80pct():
    # |B|/(reg-1) == 0.8 exactly must PASS (>= 0.8): reg=6 → denom 5 → 4/5 = 0.8.
    assert coverage_meets_threshold(4, 6) is True


# --------------------------------------------------------------------------- #
# THE golden: N=3, |Ω_c|=6, L1 scalar, hand-computed exploratory p = 2/6
# --------------------------------------------------------------------------- #
def _golden_model_and_scalar():
    # obs assignment = ω value 3 (bits [0,1,1]), rank 2 in enumerate_omega(3).
    N = 3
    omega = enumerate_omega(N)
    assert omega == [1, 2, 3, 4, 5, 6]
    obs_rank = omega.index(3)
    obs_bits = tuple(bits_from_value(3, N))
    assert obs_bits == (0, 1, 1)

    blocks = tuple({"min": ("m", k), "q": ("qq", k)} for k in range(N))
    model = ConfigRunModel(config_id="C", blocks=blocks, obs_bits=obs_bits, obs_rank=obs_rank)

    # scalar per physical run (block, P-min/P-q outcome).
    values = {
        ("m", 0): 10.0, ("qq", 0): 20.0,
        ("m", 1): 12.0, ("qq", 1): 18.0,
        ("m", 2): 14.0, ("qq", 2): 16.0,
    }
    scalar_of = lambda cid, run, name: values.get(run)  # noqa: E731
    return model, scalar_of, N, obs_rank


def test_golden_T_table_matches_hand_computation():
    model, scalar_of, N, _ = _golden_model_and_scalar()
    table = build_T_table(model, N, l1_target("x"), s_between=None, scalar_of=scalar_of)
    # Hand-derived T per rank (see module docstring derivation): d=[10,-6,-2].
    expected = [2.0, 14 / 3, 6.0, -6.0, -14 / 3, -2.0]
    assert len(table) == 6
    for got, exp in zip(table, expected):
        assert got == exp or abs(got - exp) < 1e-12


def test_golden_exploratory_p_is_two_sixths():
    model, scalar_of, N, obs_rank = _golden_model_and_scalar()
    table = build_T_table(model, N, l1_target("x"), s_between=None, scalar_of=scalar_of)
    T_obs, p = exploratory(table, obs_rank)
    assert T_obs == 6.0
    assert p == 2 / 6  # |T|>=6 at ranks 2 and 3 only


def test_golden_pooled_single_config_determinism_and_rate():
    model, scalar_of, N, obs_rank = _golden_model_and_scalar()
    table = build_T_table(model, N, l1_target("x"), s_between=None, scalar_of=scalar_of)
    tables = {"C": table}
    r1 = pooled("H-x", l1_target("x"), ["C"], tables, {"C": obs_rank}, omega_size=6, B_perm=10000)
    r2 = pooled("H-x", l1_target("x"), ["C"], tables, {"C": obs_rank}, omega_size=6, B_perm=10000)
    assert r1.b == r2.b and r1.p == r2.p           # determinism (same seed_h stream)
    assert not r1.precheck_null
    assert r1.T_pool_obs == 6.0
    # single-config pooled draws |T[r]|>=6 with true rate 2/6; b near 3333/10000.
    assert 3000 <= r1.b <= 3700
    assert r1.p == (r1.b + 1) / 10001


# --------------------------------------------------------------------------- #
# null propagation
# --------------------------------------------------------------------------- #
def test_channel_null_at_N3_self_consistency_ineligible():
    # N=3 → min/q sets have 3 runs → C(3,2)=3 self pairs < 4 → self-consistency
    # ineligible → T null for every ω → exploratory p null + pooled precheck_null.
    N = 3
    omega = enumerate_omega(N)
    obs_rank = omega.index(3)
    blocks = tuple({"min": ("m", k), "q": ("qq", k)} for k in range(N))
    model = ConfigRunModel("C", blocks, tuple(bits_from_value(3, N)), obs_rank)
    s_between = lambda cid, a, b, ch: 0.5  # noqa: E731  (all pairs defined, but only 3)
    table = build_T_table(model, N, channel_target("v-phash"), s_between, scalar_of=None)
    assert all(t is None for t in table)
    assert exploratory(table, obs_rank) == (None, None)
    res = pooled("H", channel_target("v-phash"), ["C"], {"C": table}, {"C": obs_rank}, 6)
    assert res.precheck_null and res.p is None and res.b is None and res.T_pool_obs is None


def test_scalar_all_missing_propagates_null():
    N = 3
    obs_rank = enumerate_omega(N).index(3)
    blocks = tuple({"min": ("m", k), "q": ("qq", k)} for k in range(N))
    model = ConfigRunModel("C", blocks, tuple(bits_from_value(3, N)), obs_rank)
    scalar_of = lambda cid, run, name: None  # noqa: E731  (no scalar present anywhere)
    table = build_T_table(model, N, l1_target("x"), s_between=None, scalar_of=scalar_of)
    assert all(t is None for t in table)
    res = pooled("H", l1_target("x"), ["C"], {"C": table}, {"C": obs_rank}, 6)
    assert res.precheck_null and res.p is None


# --------------------------------------------------------------------------- #
# Holm: null excluded from ranking, FULL k stays denominator
# --------------------------------------------------------------------------- #
def _pr(hid, p):
    return PooledResult(hid, {}, seed_h(hid), p is None, None if p is None else 0.0, None, p)


def test_holm_null_excluded_full_k_denominator():
    results = [_pr("h1", 0.01), _pr("h2", 0.04), _pr("h3", None), _pr("h4", 0.5)]
    out = holm(results, family_size_k=4)  # k stays 4 even though one is null
    assert out["h1"] == {"rank": 1, "adjusted_p": 0.04, "excluded_null": False}   # 4*0.01
    assert out["h2"] == {"rank": 2, "adjusted_p": 0.12, "excluded_null": False}   # 3*0.04
    assert out["h4"] == {"rank": 3, "adjusted_p": 1.0, "excluded_null": False}    # min(2*0.5,1)
    assert out["h3"] == {"rank": None, "adjusted_p": None, "excluded_null": True}


def test_holm_is_monotone_cummax():
    results = [_pr("a", 0.5), _pr("b", 0.001)]
    out = holm(results, family_size_k=2)
    # b ranks first (0.001): 2*0.001=0.002; a next: 1*0.5=0.5; cummax => 0.5.
    assert out["b"]["adjusted_p"] == 0.002
    assert out["a"]["adjusted_p"] == 0.5


# --------------------------------------------------------------------------- #
# observed-assignment invariance
# --------------------------------------------------------------------------- #
def test_observed_bits_relabel_to_true_pmin_set():
    N = 3
    obs_bits = tuple(bits_from_value(3, N))  # [0,1,1]
    blocks = tuple({"min": ("min", k), "q": ("q", k)} for k in range(N))
    model = ConfigRunModel("C", blocks, obs_bits, enumerate_omega(N).index(3))
    min_set, q_set = model.labeled_sets(obs_bits)
    assert set(min_set) == {("min", 0), ("min", 1), ("min", 2)}
    assert set(q_set) == {("q", 0), ("q", 1), ("q", 2)}


def test_swapping_all_bits_negates_channel_T():
    # For a symmetric N, flipping every bit swaps min/q sets => T -> -T.
    N = 4
    blocks = tuple({"min": ("min", k), "q": ("q", k)} for k in range(N))
    model = ConfigRunModel("C", blocks, (0, 0, 1, 1), 0)
    svals = {}
    runs = [("min", k) for k in range(N)] + [("q", k) for k in range(N)]
    for i, a in enumerate(runs):
        for bb in runs[i + 1:]:
            svals[frozenset((a, bb))] = 0.1 + 0.03 * (hash((a, bb)) % 7)
    s_between = lambda cid, a, b, ch: svals[frozenset((a, b))]  # noqa: E731
    from runner.stats.randomization import T_channel
    bits = [0, 1, 0, 1]
    t = T_channel(model, bits, "v-phash", s_between)
    t_flip = T_channel(model, [1 - x for x in bits], "v-phash", s_between)
    assert t is not None and t_flip is not None
    assert abs(t + t_flip) < 1e-12


# --------------------------------------------------------------------------- #
# descriptive
# --------------------------------------------------------------------------- #
def _self_pairs(slots, s):
    from itertools import combinations
    return [{"slot_a": i, "slot_b": j, "s": {"v-phash": s}} for i, j in combinations(slots, 2)]


def _cross_pairs(slots_a, slots_b, s):
    return [{"slot_a": i, "slot_b": j, "s": {"v-phash": s}} for i in slots_a for j in slots_b]


def test_descriptive_separation_and_distinctiveness():
    cfg = ["A", "B", "C"]
    va = [0, 1, 2, 3]
    m_map = {"A": 4, "B": 2, "C": 2}
    valid = {"A": va, "B": [0, 1], "C": [0, 1]}
    self_pairs = {"A": _self_pairs(va, 0.9), "B": _self_pairs([0, 1], 0.4), "C": _self_pairs([0, 1], 0.4)}
    cross_pairs = {
        ("A", "B"): _cross_pairs(va, [0, 1], 0.3),
        ("A", "C"): _cross_pairs(va, [0, 1], 0.5),
        ("B", "C"): _cross_pairs([0, 1], [0, 1], 0.2),
    }
    out = descriptive_for_variant("P-min", cfg, m_map, valid, self_pairs, cross_pairs)

    sc = {(c["config_id"], c["channel"]): c for c in out["self_consistency"]}
    a = sc[("A", "v-phash")]
    assert a["eligible"] and a["n_eff"] == 6
    assert a["self_consistency"] == a["mean"] == 0.9 and a["median"] == 0.9
    assert a["loro"]["n_drops"] == 4 and a["loro"]["min"] == 0.9 and a["loro"]["max"] == 0.9

    sep = {(c["config_id"], c["channel"]): c for c in out["separation"]}
    sa = sep[("A", "v-phash")]
    assert sa["value"] == 0.9 - (0.3 + 0.5) / 2   # 0.5
    assert sa["coverage"] == "1" and sa["b_set_size"] == 2 and sa["registered_total"] == 3

    dist = {(c["config_id"], c["channel"]): c for c in out["distinctiveness"]}
    da = dist[("A", "v-phash")]
    assert da["value"] == 1.0 - 0.5 and da["nearest_config_id"] == "C"


def test_descriptive_m0_omitted_m1_null_present():
    cfg = ["A", "B"]
    m_map = {"A": 1, "B": 0}
    valid = {"A": [0], "B": []}
    self_pairs = {"A": [], "B": []}
    cross_pairs = {("A", "B"): []}
    out = descriptive_for_variant("P-min", cfg, m_map, valid, self_pairs, cross_pairs)
    present = {c["config_id"] for c in out["self_consistency"]}
    assert "A" in present and "B" not in present          # m=0 omitted, m=1 present
    a = next(c for c in out["self_consistency"] if c["config_id"] == "A" and c["channel"] == "v-phash")
    assert a["m"] == 1 and a["n_eff"] == 0 and not a["eligible"]
    assert a["self_consistency"] is None and a["median"] is None and a["loro"] is None


def test_descriptive_low_coverage_forces_null_value():
    # 4 configs; A has only 1 eligible cross neighbour → coverage 1/3 < 0.8 → null.
    cfg = ["A", "B", "C", "D"]
    va = [0, 1, 2, 3]
    m_map = {"A": 4, "B": 2, "C": 1, "D": 1}
    valid = {"A": va, "B": [0, 1], "C": [0], "D": [0]}
    self_pairs = {"A": _self_pairs(va, 0.9), "B": _self_pairs([0, 1], 0.4), "C": [], "D": []}
    cross_pairs = {
        ("A", "B"): _cross_pairs(va, [0, 1], 0.3),  # eligible
        ("A", "C"): _cross_pairs(va, [0], 0.5),      # m_b=1 → ineligible
        ("A", "D"): _cross_pairs(va, [0], 0.5),      # m_b=1 → ineligible
        ("B", "C"): [], ("B", "D"): [], ("C", "D"): [],
    }
    out = descriptive_for_variant("P-min", cfg, m_map, valid, self_pairs, cross_pairs)
    sep = {(c["config_id"], c["channel"]): c for c in out["separation"]}
    sa = sep[("A", "v-phash")]
    assert sa["b_set_size"] == 1 and sa["coverage"] == "0.333333" and sa["value"] is None


# --------------------------------------------------------------------------- #
# integration determinism on the M1 dev batch
# --------------------------------------------------------------------------- #
@_requires_batch
def test_m1_build_is_deterministic():
    from runner.stats.load import load_batch
    from runner.stats.run import build_stats_doc

    inp = load_batch(BATCH)
    d1 = build_stats_doc(inp, b_perm=2000)
    d2 = build_stats_doc(load_batch(BATCH), b_perm=2000)
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    # 15 formal channels only; family = 15 + 5 L1.
    assert d1["formal_channels"] == [
        "v-phash", "v-dhash", "v-color", "v-palette", "v-layout", "v-edge", "v-ssim",
        "c-shingle", "c-winnow", "c-feature", "c-ast-js", "c-css-prop",
        "d-geom", "d-text", "d-pqgram",
    ]
    assert d1["randomization_test"]["family_size_k"] == 20
    assert len(d1["randomization_test"]["pooled"]) == 20


@_requires_batch
def test_m1_only_schema_error_is_omega_enum():
    from runner.stats.run import run
    res = run(BATCH, dev=True)
    non_omega = [e for e in res["schema_errors"] if "omega_size" not in e]
    assert non_omega == []
    assert all("[6, 20]" in e for e in res["schema_errors"])
