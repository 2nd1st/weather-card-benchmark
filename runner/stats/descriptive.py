"""L3 DESCRIPTIVE statistics over the pair sets (scheme §5). Descriptive-only —
statistics, no inferential claim.

Per prompt variant, over the 15 FORMAL channels (diagnostics excluded, scheme §4):
  * self_consistency  — per (config, channel): mean/median/IQR/n_eff/m + LORO over
                        the self pair set C(m,2); self-consistency := mean of non-null
                        self-pair S. Eligibility n_eff≥4; m=1 → null; m=0 → omitted.
  * cross             — per (a<b, channel): mean/median/IQR/n_eff/m_a/m_b + LORO over
                        A×B. Eligibility n_eff≥4 ∧ m_a≥2 ∧ m_b≥2.
  * separation(A)     — mean(intra A) − mean over B of sim(A,B), config-balanced
                        (equal weight over B), 80% coverage gate.
  * distinctiveness(A)— 1 − max over B of sim(A,B), same B set / coverage rules;
                        nearest-neighbor config (tie → NFC-UTF8 smallest).

LORO is the ONLY sensitivity量 (bootstrap deleted). Inputs are the frozen raw
per-pair S from similarity/pairs — this module never recomputes S.
"""
from __future__ import annotations

from itertools import combinations
from typing import Optional, Sequence

from ..manifest import nfc
from ..similarity.runner import _percentile_linear
from .hypotheses import FORMAL_CHANNELS
from .serialize import coverage_meets_threshold, coverage_string

N_EFF_MIN = 4


# --------------------------------------------------------------------------- #
# low-level cell computation
# --------------------------------------------------------------------------- #
def _cell_stats(non_null: list[float]) -> tuple[float, float, dict]:
    """mean, median, iqr for an eligible non-empty value list."""
    mean = sum(non_null) / len(non_null)
    median = _percentile_linear(non_null, 50.0)
    iqr = {"p25": _percentile_linear(non_null, 25.0), "p75": _percentile_linear(non_null, 75.0)}
    return mean, median, iqr


def _self_loro(valid_slots: Sequence[int], pairmap: dict[tuple[int, int], Optional[float]]) -> Optional[dict]:
    """LORO for a self cell: drop each run; valid drop needs remaining runs≥2 AND
    remaining non-null pair≥1; report min/max of recomputed mean + drop count."""
    records: list[float] = []
    for r in valid_slots:
        active = [x for x in valid_slots if x != r]
        if len(active) < 2:
            continue
        vals = [v for i, j in combinations(active, 2) if (v := pairmap.get((i, j))) is not None]
        if not vals:
            continue
        records.append(sum(vals) / len(vals))
    if not records:
        return None
    return {"min": min(records), "max": max(records), "n_drops": len(records)}


def _cross_loro(
    slots_a: Sequence[int],
    slots_b: Sequence[int],
    pairmap: dict[tuple[int, int], Optional[float]],
) -> Optional[dict]:
    """LORO for a cross cell: drop each run on either side; valid drop needs both
    sides non-empty AND remaining non-null pair≥1."""
    def cross_mean(aa: Sequence[int], bb: Sequence[int]) -> Optional[float]:
        vals = [v for i in aa for j in bb if (v := pairmap.get((i, j))) is not None]
        return sum(vals) / len(vals) if vals else None

    records: list[float] = []
    for r in slots_a:
        aa = [x for x in slots_a if x != r]
        if aa and slots_b and (m := cross_mean(aa, slots_b)) is not None:
            records.append(m)
    for r in slots_b:
        bb = [x for x in slots_b if x != r]
        if slots_a and bb and (m := cross_mean(slots_a, bb)) is not None:
            records.append(m)
    if not records:
        return None
    return {"min": min(records), "max": max(records), "n_drops": len(records)}


# --------------------------------------------------------------------------- #
# per-variant descriptive block
# --------------------------------------------------------------------------- #
def descriptive_for_variant(
    variant: str,
    config_ids: Sequence[str],
    m_map: dict[str, int],
    valid_slots: dict[str, list[int]],
    self_pairs: dict[str, list[dict]],
    cross_pairs: dict[tuple[str, str], list[dict]],
) -> dict:
    """Build one schema ``descriptive[]`` entry for a prompt variant.

    ``self_pairs[cid]`` / ``cross_pairs[(a,b)]`` = list of ``{"slot_a","slot_b",
    "s":{channel:val|None}}`` (raw similarity/pairs rows). ``(a,b)`` keyed in
    persisted order (a precedes b)."""
    idx = {c: i for i, c in enumerate(config_ids)}

    # ---- self_consistency + a per-(config,channel) mean/eligible lookup ------
    self_cells: list[dict] = []
    self_mean_lookup: dict[tuple[str, str], Optional[float]] = {}
    for cid in config_ids:
        if m_map[cid] == 0:  # m=0 → config omitted (scheme §5)
            continue
        pairs = self_pairs.get(cid, [])
        for ch in FORMAL_CHANNELS:
            pairmap = {(p["slot_a"], p["slot_b"]): p["s"].get(ch) for p in pairs}
            non_null = [v for v in pairmap.values() if v is not None]
            n_eff = len(non_null)
            eligible = n_eff >= N_EFF_MIN
            if eligible:
                mean, median, iqr = _cell_stats(non_null)
                loro = _self_loro(valid_slots[cid], pairmap)
            else:
                mean = median = iqr = loro = None
            self_mean_lookup[(cid, ch)] = mean
            self_cells.append({
                "config_id": cid, "channel": ch,
                "self_consistency": mean, "mean": mean, "median": median,
                "iqr": iqr, "n_eff": n_eff, "m": m_map[cid], "loro": loro,
                "eligible": eligible,
            })

    # ---- cross + a per-(unordered pair, channel) mean/eligible lookup -------
    cross_cells: list[dict] = []
    cross_mean_lookup: dict[tuple[str, str, str], Optional[float]] = {}
    cross_eligible_lookup: dict[tuple[str, str, str], bool] = {}
    for a, b in combinations(config_ids, 2):  # persisted order → a precedes b
        pairs = cross_pairs.get((a, b), [])
        m_a, m_b = m_map[a], m_map[b]
        for ch in FORMAL_CHANNELS:
            pairmap = {(p["slot_a"], p["slot_b"]): p["s"].get(ch) for p in pairs}
            non_null = [v for v in pairmap.values() if v is not None]
            n_eff = len(non_null)
            eligible = n_eff >= N_EFF_MIN and m_a >= 2 and m_b >= 2
            if eligible:
                mean, median, iqr = _cell_stats(non_null)
                loro = _cross_loro(valid_slots[a], valid_slots[b], pairmap)
            else:
                mean = median = iqr = loro = None
            cross_mean_lookup[(a, b, ch)] = mean
            cross_eligible_lookup[(a, b, ch)] = eligible
            cross_cells.append({
                "config_a": a, "config_b": b, "channel": ch,
                "mean": mean, "median": median, "iqr": iqr, "n_eff": n_eff,
                "m_a": m_a, "m_b": m_b, "loro": loro, "eligible": eligible,
            })

    def _ordered(a: str, b: str) -> tuple[str, str]:
        return (a, b) if idx[a] < idx[b] else (b, a)

    # ---- separation + distinctiveness (shared B set) ------------------------
    registered_total = len(config_ids)
    separation_cells: list[dict] = []
    distinctiveness_cells: list[dict] = []
    for cid in config_ids:
        for ch in FORMAL_CHANNELS:
            self_mean = self_mean_lookup.get((cid, ch))  # None if self ineligible/omitted
            # B set = every OTHER config with an ELIGIBLE cross cell vs cid (scheme §5).
            b_pairs: list[tuple[str, float]] = []
            for other in config_ids:
                if other == cid:
                    continue
                key = (*_ordered(cid, other), ch)
                if cross_eligible_lookup.get(key) and cross_mean_lookup.get(key) is not None:
                    b_pairs.append((other, cross_mean_lookup[key]))  # type: ignore[arg-type]
            b_set_size = len(b_pairs)
            coverage = coverage_string(b_set_size, registered_total)
            meets = coverage_meets_threshold(b_set_size, registered_total)
            base_eligible = (self_mean is not None) and (b_set_size > 0)

            if base_eligible and meets:
                sep_value: Optional[float] = self_mean - sum(m for _, m in b_pairs) / b_set_size
                max_sim = max(m for _, m in b_pairs)
                dist_value: Optional[float] = 1.0 - max_sim
                nearest = min(
                    (c for c, m in b_pairs if m == max_sim),
                    key=lambda c: nfc(c).encode("utf-8"),
                )
            else:
                sep_value = None
                dist_value = None
                nearest = None

            separation_cells.append({
                "config_id": cid, "channel": ch, "value": sep_value,
                "coverage": coverage, "b_set_size": b_set_size,
                "registered_total": registered_total,
            })
            distinctiveness_cells.append({
                "config_id": cid, "channel": ch, "value": dist_value,
                "coverage": coverage, "nearest_config_id": nearest,
            })

    return {
        "variant": variant,
        "self_consistency": self_cells,
        "cross": cross_cells,
        "separation": separation_cells,
        "distinctiveness": distinctiveness_cells,
    }
