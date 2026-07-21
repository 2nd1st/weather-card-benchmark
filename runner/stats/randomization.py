"""The single §5 inferential procedure: the P-min/P-q quality-instruction paired
randomization test (scheme §5 / appendix A). NOTHING else in stats.json is a test.

Unified statistic ``T_{config,h}`` (scheme §5):
  * channel h  → self_consistency_q − self_consistency_min
                 (self pair set + n_eff≥4 eligibility; either side ineligible → null)
  * L1-scalar h → mean_q − mean_min
                 (mean over valid runs; either side 0 valid values → null)

Re-randomization draws an assignment in Ω_c (§1.2 encoding), re-labels each block's
outcome by (block, time-position), and recomputes T FROM SCRATCH.

Design for testability + the exhaustive pre-check: because |Ω_c| ≤ 20, we precompute
``T_table[config][rank]`` for every rank ∈ 0..|Ω_c|−1 ONCE. The per-config exact
enumeration and the pooled MC then read that table (the pooled MC never recomputes
similarity — it only averages precomputed T's, which is also exactly the exhaustive
mask×Ω pre-check the scheme mandates before sampling).

Data access is injected through ``s_between(config_id, run_a, run_b, channel)`` and
``scalar_of(config_id, run, scalar_name)`` (run = ``(variant_dir, slot_index)``), so
tests can drive the whole procedure with synthetic outcomes and hand-checked p.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Callable, Optional, Sequence

from numpy.random import Generator, PCG64

from ..seed import bits_from_value, enumerate_omega
from .serialize import seed_h

Run = tuple[str, int]  # (variant_dir "min"|"q", slot_index)
SBetween = Callable[[str, Run, Run, str], Optional[float]]
ScalarOf = Callable[[str, Run, str], Optional[float]]

N_EFF_MIN = 4  # self-consistency eligibility (scheme §5)


@dataclass(frozen=True)
class ConfigRunModel:
    """One config's block structure for the test.

    ``blocks[k]`` = ``{"min": Run|None, "q": Run|None}`` — the P-min / P-q physical
    outcome of block k (None if that slot is missing / not valid). ``obs_bits[k]`` =
    observed assignment bit for block k (0 = min→q i.e. P-min ran first).
    """

    config_id: str
    blocks: tuple[dict, ...]
    obs_bits: tuple[int, ...]
    obs_rank: int

    def labeled_sets(self, bits: Sequence[int]) -> tuple[list[Run], list[Run]]:
        """Under assignment ``bits``, return (min_set, q_set) of VALID runs.

        The outcome sticks to (block, time-position); ``bits[k]`` re-interprets which
        time-position is labeled min. Physical time-positions are recovered from the
        OBSERVED bit: ``phys0`` = the run executed first, ``phys1`` = second."""
        min_set: list[Run] = []
        q_set: list[Run] = []
        for k, block in enumerate(self.blocks):
            min_ref = block["min"]  # P-min physical outcome of block k
            q_ref = block["q"]      # P-q physical outcome of block k
            b_obs = self.obs_bits[k]
            phys0 = min_ref if b_obs == 0 else q_ref
            phys1 = q_ref if b_obs == 0 else min_ref
            min_run = phys0 if bits[k] == 0 else phys1
            q_run = phys1 if bits[k] == 0 else phys0
            if min_run is not None:
                min_set.append(min_run)
            if q_run is not None:
                q_set.append(q_run)
        return min_set, q_set


def _self_consistency(runset: list[Run], s_lookup: Callable[[Run, Run], Optional[float]]) -> Optional[float]:
    """Mean of non-null S over the self pair set C(runset,2); null if n_eff<4
    (scheme §5 self-consistency + eligibility)."""
    vals = [s for a, b in combinations(runset, 2) if (s := s_lookup(a, b)) is not None]
    if len(vals) < N_EFF_MIN:
        return None
    return sum(vals) / len(vals)


def _mean_scalar(runset: list[Run], scalar_lookup: Callable[[Run], Optional[float]]) -> Optional[float]:
    """Mean of the scalar over the runs that have a value; null if none (scheme §5:
    either side 0 valid values → null)."""
    vals = [v for r in runset if (v := scalar_lookup(r)) is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def T_channel(model: ConfigRunModel, bits: Sequence[int], channel: str, s_between: SBetween) -> Optional[float]:
    min_set, q_set = model.labeled_sets(bits)
    lk = lambda a, b: s_between(model.config_id, a, b, channel)  # noqa: E731
    sc_min = _self_consistency(min_set, lk)
    sc_q = _self_consistency(q_set, lk)
    if sc_min is None or sc_q is None:
        return None
    return sc_q - sc_min


def T_l1(model: ConfigRunModel, bits: Sequence[int], scalar: str, scalar_of: ScalarOf) -> Optional[float]:
    min_set, q_set = model.labeled_sets(bits)
    lk = lambda r: scalar_of(model.config_id, r, scalar)  # noqa: E731
    m_min = _mean_scalar(min_set, lk)
    m_q = _mean_scalar(q_set, lk)
    if m_min is None or m_q is None:
        return None
    return m_q - m_min


def build_T_table(
    model: ConfigRunModel,
    N: int,
    target: dict,
    s_between: SBetween,
    scalar_of: ScalarOf,
) -> list[Optional[float]]:
    """T_{config,h}(ω) for every ω∈Ω_c, indexed by rank 0..|Ω_c|−1 (ascending int
    value == rank order, scheme §1.2)."""
    omega_values = enumerate_omega(N)
    out: list[Optional[float]] = []
    for value in omega_values:
        bits = bits_from_value(value, N)
        if target["kind"] == "channel":
            out.append(T_channel(model, bits, target["channel"], s_between))
        else:
            out.append(T_l1(model, bits, target["l1_scalar"], scalar_of))
    return out


# --------------------------------------------------------------------------- #
# per-config EXPLORATORY test — exact full enumeration of Ω_c (scheme §5)
# --------------------------------------------------------------------------- #
def exploratory(T_table: list[Optional[float]], obs_rank: int) -> tuple[Optional[float], Optional[float]]:
    """Return (T_obs, p). Two-sided p = #{ω: |T(ω)|≥|T_obs|}/|Ω_c| (observed ∈ Ω).
    p=null if T_obs is null OR any ω gives T(ω)=null (strict null propagation)."""
    omega_size = len(T_table)
    T_obs = T_table[obs_rank]
    if T_obs is None or any(t is None for t in T_table):
        return T_obs, None
    abs_obs = abs(T_obs)
    b = sum(1 for t in T_table if abs(t) >= abs_obs)  # type: ignore[arg-type]
    return T_obs, b / omega_size


# --------------------------------------------------------------------------- #
# pooled MAIN test — B_perm Monte-Carlo with exhaustive pre-check (scheme §5)
# --------------------------------------------------------------------------- #
@dataclass
class PooledResult:
    hypothesis_id: str
    target: dict
    seed_h: int
    precheck_null: bool
    T_pool_obs: Optional[float]
    b: Optional[int]
    p: Optional[float]


def pooled(
    hypothesis_id: str,
    target: dict,
    mask_config_ids: Sequence[str],
    T_tables: dict[str, list[Optional[float]]],
    obs_ranks: dict[str, int],
    omega_size: int,
    B_perm: int = 10000,
) -> PooledResult:
    """Pooled hypothesis result (scheme §5).

    Exhaustive pre-check: T_tables already hold T_{config,h}(ω) for the full mask ×
    all ω∈Ω_c. ANY null → p=null, no sampling. Otherwise B_perm draws from ONE
    Generator(PCG64(seed_h)); each round draws one index per config in persisted
    (mask) order; T_pool = equal-weight mean over the mask; observed NOT injected;
    b = Σ 1[|T_pool(ω_j)|≥|T_pool(ω_obs)|]; p=(b+1)/(B_perm+1)."""
    seed = seed_h(hypothesis_id)
    precheck_null = any(t is None for cid in mask_config_ids for t in T_tables[cid])
    if precheck_null:
        return PooledResult(hypothesis_id, target, seed, True, None, None, None)

    m = len(mask_config_ids)
    T_pool_obs = sum(T_tables[cid][obs_ranks[cid]] for cid in mask_config_ids) / m  # type: ignore[arg-type]
    abs_obs = abs(T_pool_obs)

    gen = Generator(PCG64(seed))
    b = 0
    for _ in range(B_perm):
        acc = 0.0
        for cid in mask_config_ids:  # persisted order, one draw each (scheme §1.2)
            r = int(gen.integers(0, omega_size))
            acc += T_tables[cid][r]  # type: ignore[operator]
        if abs(acc / m) >= abs_obs:
            b += 1
    p = (b + 1) / (B_perm + 1)
    return PooledResult(hypothesis_id, target, seed, False, T_pool_obs, b, p)


# --------------------------------------------------------------------------- #
# Holm step-down (scheme §5: null excluded from ranking, FULL k as denominator)
# --------------------------------------------------------------------------- #
def holm(results: Sequence[PooledResult], family_size_k: int) -> dict[str, dict]:
    """Holm-adjusted p per hypothesis_id. p=null hypotheses are EXCLUDED from the
    ordering but the step-down denominator stays the full pre-registered family
    size k (conservative). No significance/reject verdict is produced."""
    out: dict[str, dict] = {}
    non_null = sorted(
        [r for r in results if r.p is not None], key=lambda r: r.p  # type: ignore[arg-type,return-value]
    )
    running = 0.0
    for i, r in enumerate(non_null):
        rank = i + 1
        adj = min((family_size_k - rank + 1) * r.p, 1.0)  # type: ignore[operator]
        running = max(running, adj)
        out[r.hypothesis_id] = {"rank": rank, "adjusted_p": running, "excluded_null": False}
    for r in results:
        if r.p is None:
            out[r.hypothesis_id] = {"rank": None, "adjusted_p": None, "excluded_null": True}
    return out
