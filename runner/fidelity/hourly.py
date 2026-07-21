"""hourly binding (scheme §3 hourly, v8 pre-triage + v12 fixed-length key vector).

Pre-triage:
  * no temperature candidate at all → ``not-found``;
  * temperature candidates exist but the two daily canonical occurrences are not
    both published → whole field ``ambiguous`` (no daily数值混入 hourly);
  * both canonical published → proceed.

``hourly_temperature_candidates = all temperature occurrences − the 2 canonical``.

Labelled branch — global **max-cardinality injective** assignment label↔candidate,
compared by the key ``(①bound-count desc; ②total distance over BOUND edges only,
§3 二元组; ③fixed-length per-label vector, lexicographic)``. The fixed-length
vector runs over ALL hourly-label occurrences in occurrence order; a bound label
contributes ``(0, dom, text, T_id)``, an unbound one ``(1, 0, 0, sentinel)`` with
``sentinel = (2³¹−1, 2³¹−1, 2³¹−1)``. The assignment **signature** is that
vector's ``T_id`` sequence. A repeated hour bound to different displayed values →
whole field ``ambiguous``.

No-label branch — hourly candidates must be **exactly 24** occurrences; ordered by
occurrence order and aligned to UTC hours 0..23; anything ``≠24`` → ``ambiguous``.

Per-point states {match, mismatch, not-shown}; field-state aggregation per §3.

FLAG (R5 report): the exact lexicographic-vector tie-break is solved exactly for
the ``|labels| ≤ |candidates|`` case (all labels bound) via a residual-cost greedy
over a min-cost-assignment oracle; the rare over-labelled case
(``|labels| > |candidates|``, only produced by spurious clock labels such as
sunrise/sunset) falls back to a single min-cost assignment (still deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
from scipy.optimize import linear_sum_assignment

from .binding import distance
from .fields import quantize_half_up
from .normalize import DomModel
from .occurrences import Occurrence

OID = tuple[int, int, int]
SENTINEL: OID = (2147483647, 2147483647, 2147483647)


@dataclass
class HourlyBinding:
    label_id: OID | None
    temp_id: OID
    hour: int
    dom_edges: int
    text_coord_diff: int


@dataclass
class HourlyResult:
    pretriage: str
    has_label: bool | None
    candidates: list[OID]
    label_parse: list[tuple[OID, int | None]]
    bindings: list[HourlyBinding]
    signature: list[OID]
    state: str
    reason: str | None
    points: list[tuple[int, str]]  # (hour, state)
    points_match: int
    points_mismatch: int
    points_not_shown: int
    coverage: str | None
    decision_path: list[tuple[str, str | None]] = field(default_factory=list)


def _min_cost(scalar: list[list[int]]) -> tuple[int, list[tuple[int, int]]]:
    """Min-cost assignment binding min(rows,cols) pairs; returns (int total, pairs)."""
    if not scalar or not scalar[0]:
        return 0, []
    arr = np.array(scalar, dtype=float)
    r, c = linear_sum_assignment(arr)
    total = sum(int(scalar[i][j]) for i, j in zip(r, c))
    return total, list(zip(r.tolist(), c.tolist()))


def _assign(model: DomModel, L: list[Occurrence], C: list[Occurrence]) -> dict[int, int]:
    """Max-cardinality min-distance min-vector label→candidate assignment.

    Exact for |L|≤|C| (residual-cost greedy in occurrence order → lexicographic-min
    vector); falls back to a single min-cost assignment otherwise (flagged)."""
    if not L or not C:
        return {}
    dist = [[distance(model, l, c) for c in C] for l in L]
    # Scalarize the §3 二元组 (dom, text) into ONE integer cost that preserves
    # lexicographic (Σdom, Σtext) order over any assignment: choose A strictly
    # greater than the largest possible Σtext. The sum of ALL text components in
    # the matrix bounds every assignment's Σtext (an assignment picks a subset),
    # so A = Σtext_all + 1 guarantees A*Σdom + Σtext == lex(Σdom, Σtext).
    # (R5 finding P2: replaces the former hardcoded 10**7, which was an unguarded
    # precondition Σtext < 10⁷; A is now derived from the data and provably safe.)
    A = sum(d[1] for row in dist for d in row) + 1
    scalar = [[d[0] * A + d[1] for d in row] for row in dist]

    if len(L) > len(C):
        _, pairs = _min_cost(scalar)
        return {i: j for i, j in pairs}

    c_star, _ = _min_cost(scalar)
    chosen: dict[int, int] = {}
    used: set[int] = set()
    cost_so_far = 0
    for li in range(len(L)):
        prefs = sorted(
            range(len(C)),
            key=lambda cj: (dist[li][cj][0], dist[li][cj][1], C[cj].oid),
        )
        for cj in prefs:
            if cj in used:
                continue
            rem_labels = list(range(li + 1, len(L)))
            rem_cands = [x for x in range(len(C)) if x not in used and x != cj]
            sub = [[scalar[i][j] for j in rem_cands] for i in rem_labels]
            subtotal, _ = _min_cost(sub)
            if cost_so_far + scalar[li][cj] + subtotal == c_star:
                chosen[li] = cj
                used.add(cj)
                cost_so_far += scalar[li][cj]
                break
    return chosen


def bind_hourly(
    model: DomModel,
    all_temps: list[Occurrence],
    hourly_labels: list[Occurrence],
    max_canonical: OID | None,
    min_canonical: OID | None,
    canonical_published: bool,
    expected_hourly: tuple[Decimal, ...],
) -> HourlyResult:
    def empty(pretriage, state, reason, has_label, path):
        return HourlyResult(
            pretriage, has_label, [], [], [], [], state, reason,
            [(h, "not-shown") for h in range(24)], 0, 0, 24, None, path,
        )

    # ---- pre-triage --------------------------------------------------------
    if not all_temps:
        return empty("no-candidate-not-found", "not-found", "no-temperature-candidate",
                     None, [("hourly-pretriage", "no temperature candidate")])
    if not canonical_published:
        return empty("canonical-incomplete-ambiguous", "ambiguous", "daily-canonical-incomplete",
                     None, [("hourly-pretriage", "daily canonical not fully published")])

    path: list[tuple[str, str | None]] = [("hourly-pretriage", "proceed")]
    excluded = {max_canonical, min_canonical}
    candidates = sorted(
        (t for t in all_temps if t.oid not in excluded), key=lambda o: o.oid
    )
    cand_ids = [c.oid for c in candidates]
    has_label = len(hourly_labels) >= 1

    bindings: list[HourlyBinding] = []
    signature: list[OID] = []
    label_parse: list[tuple[OID, int | None]] = []
    structural_conflict = False
    conflict_reason: str | None = None
    hour_to_value: dict[int, Decimal] = {}

    if has_label:
        path.append(("hourly-branch", "labeled"))
        for lab in sorted(hourly_labels, key=lambda o: o.oid):
            label_parse.append((lab.oid, lab.parsed_hour))
        L = [lab for lab in sorted(hourly_labels, key=lambda o: o.oid) if lab.parsed_hour is not None]
        chosen = _assign(model, L, candidates)
        bound_temp_by_label: dict[OID, OID] = {}
        for li, cj in chosen.items():
            lab, cand = L[li], candidates[cj]
            d = distance(model, lab, cand)
            bindings.append(HourlyBinding(lab.oid, cand.oid, lab.parsed_hour, d[0], d[1]))
            bound_temp_by_label[lab.oid] = cand.oid
            # repeated-hour conflict: same hour, different displayed value.
            prev = hour_to_value.get(lab.parsed_hour)
            if prev is not None and prev != cand.value:
                structural_conflict = True
                conflict_reason = "repeated-hour-different-value"
            else:
                hour_to_value[lab.parsed_hour] = cand.value
        # signature over ALL hourly-label occurrences (occ order); T_id or sentinel.
        for lab in sorted(hourly_labels, key=lambda o: o.oid):
            signature.append(bound_temp_by_label.get(lab.oid, SENTINEL))
        bindings.sort(key=lambda b: b.hour)
    else:
        path.append(("hourly-branch", "no-label"))
        if len(candidates) != 24:
            structural_conflict = True
            conflict_reason = "no-label-not-24"
            path.append(("no-label-not-24", str(len(candidates))))
        else:
            for i, cand in enumerate(candidates):
                bindings.append(HourlyBinding(None, cand.oid, i, 0, 0))
                hour_to_value[i] = cand.value

    # ---- point states + aggregation ---------------------------------------
    if structural_conflict:
        path.append(("hourly-structural-conflict", conflict_reason))
        return HourlyResult(
            "proceed", has_label, cand_ids, label_parse, bindings, signature,
            "ambiguous", conflict_reason,
            [(h, "not-shown") for h in range(24)], 0, 0, 24, None, path,
        )
    if not candidates:
        path.append(("hourly-no-candidate", None))
        return HourlyResult(
            "proceed", has_label, cand_ids, label_parse, bindings, signature,
            "not-found", "no-hourly-candidate",
            [(h, "not-shown") for h in range(24)], 0, 0, 24, None, path,
        )
    if len(expected_hourly) != 24:
        path.append(("hourly-no-expected", None))
        return HourlyResult(
            "proceed", has_label, cand_ids, label_parse, bindings, signature,
            "not-found", "no-expected-hourly",
            [(h, "not-shown") for h in range(24)], 0, 0, 24, None, path,
        )

    points: list[tuple[int, str]] = []
    m = x = ns = 0
    # per-hour displayed decimals: track alongside value.
    hour_to_dd: dict[int, int] = {}
    if has_label:
        for b in bindings:
            cand = next(c for c in candidates if c.oid == b.temp_id)
            hour_to_dd[b.hour] = cand.display_decimals or 0
    else:
        for i, cand in enumerate(candidates):
            hour_to_dd[i] = cand.display_decimals or 0

    for h in range(24):
        if h in hour_to_value:
            dd = hour_to_dd.get(h, 0)
            displayed = hour_to_value[h]
            if displayed == quantize_half_up(expected_hourly[h], dd):
                points.append((h, "match"))
                m += 1
            else:
                points.append((h, "mismatch"))
                x += 1
        else:
            points.append((h, "not-shown"))
            ns += 1

    if x >= 1:
        state, reason = "mismatch", None
    elif m >= 1:
        state, reason = "match", None
    else:
        state, reason = "not-found", "no-point-shown"
    path.append(("hourly-aggregate", f"M={m} X={x} NS={ns}"))
    return HourlyResult(
        "proceed", has_label, cand_ids, label_parse, bindings, signature,
        state, reason, points, m, x, ns, f"{m}/24", path,
    )
