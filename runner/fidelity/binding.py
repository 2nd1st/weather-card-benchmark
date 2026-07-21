"""max/min global one-to-one binding (scheme §3 max/min 绑定, all v12 branches).

Distances are the §3 二元组 ``(DOM-edge count via LCA, |text-coordinate diff|)``,
compared lexicographically (first component then second); a "total distance" is
the component-wise sum. Branches:

  * **E=∅ pre-rule** — no temperature candidate → both ``not-found``
    (``no-temperature-candidate``); no canonical, no branch.
  * **double-label** — both effective label classes present. ``|E|=1`` → both
    ``ambiguous`` (insufficient-distinct-temperature-nodes); ``|E|≥2`` → enumerate
    every legal ``(L_max→T_max, L_min→T_min)`` with ``T_max≠T_min`` (occurrence
    identity) and take the lexicographic-min of
    ``(total, max-dist, min-dist, T_max, T_min, L_max, L_min)``. No downgrade.
  * **single-label** — exactly one class. Pre: ``|E|≥3`` → both ``ambiguous``.
    Else the labelled side binds by ``(dist, T, L)``; the other side binds the
    numeric extremum over the REMAINING candidates (larger=max / smaller=min;
    same-value → smallest-occurrence-order canonical); empty remainder →
    ``ambiguous``.
  * **no-label** — none. Pre: ``|E|≥3`` → both ``ambiguous``. Else numeric rule.

Step 4: equal bound max/min value → both ``ambiguous``. Canonical is published
(gating hourly) only when BOTH canonical occurrences are produced with distinct
IDs AND both terminal states are non-ambiguous (scheme §3 canonical 发布条件).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .fields import Salience, value_state
from .normalize import DomModel
from .occurrences import Occurrence

OID = tuple[int, int, int]
Dist = tuple[int, int]


def distance(model: DomModel, label: Occurrence, temp: Occurrence) -> Dist:
    """§3 二元组: (DOM edges label.parent↔temp.parent via LCA, |Δ text-coord|)."""
    dom = model.dom_edges(label.parent, temp.parent)
    txt = abs(label.text_coord - temp.text_coord)
    return (dom, txt)


@dataclass
class Side:
    state: str = "pending"
    reason: str | None = None
    label_id: OID | None = None
    temp_id: OID | None = None
    canonical_id: OID | None = None
    value: Decimal | None = None
    display_decimals: int | None = None
    display_str: str | None = None


@dataclass
class DistEntry:
    label_id: OID | None
    temp_id: OID
    dom_edges: int
    text_coord_diff: int


@dataclass
class BindingResult:
    branch: str
    max: Side
    min: Side
    distances: list[DistEntry] = field(default_factory=list)
    canonical_published: bool = False
    decision_path: list[tuple[str, str | None]] = field(default_factory=list)


def _bind_from_temp(side: Side, label: Occurrence | None, temp: Occurrence, canonical: Occurrence) -> None:
    side.label_id = label.oid if label else None
    side.temp_id = temp.oid
    side.canonical_id = canonical.oid
    side.value = canonical.value
    side.display_decimals = canonical.display_decimals
    side.display_str = canonical.display_str


def _numeric_side(remaining: list[Occurrence], want_max: bool) -> Occurrence | None:
    """Extremum occurrence over ``remaining`` by value; ties → smallest occ order."""
    if not remaining:
        return None
    ext = (max if want_max else min)(o.value for o in remaining)
    equal = [o for o in remaining if o.value == ext]
    return min(equal, key=lambda o: o.oid)


def bind_max_min(
    model: DomModel,
    salience: Salience,
    max_labels: list[Occurrence],
    min_labels: list[Occurrence],
    expected_max: Decimal,
    expected_min: Decimal,
) -> BindingResult:
    E = salience.e
    smax, smin = Side(), Side()
    path: list[tuple[str, str | None]] = []
    distances: list[DistEntry] = []

    # ---- E=∅ pre-rule ------------------------------------------------------
    if not E:
        for s in (smax, smin):
            s.state, s.reason = "not-found", "no-temperature-candidate"
        path.append(("e-empty", "no temperature candidate"))
        return BindingResult("E-empty", smax, smin, distances, False, path)

    has_max, has_min = bool(max_labels), bool(min_labels)

    # ---- branch selection --------------------------------------------------
    if has_max and has_min:
        branch = "double-label"
        path.append(("branch-double-label", None))
        # record evaluated distances (labels × E)
        for L in max_labels + min_labels:
            for T in E:
                d = distance(model, L, T)
                distances.append(DistEntry(L.oid, T.oid, d[0], d[1]))
        if len(E) == 1:
            for s in (smax, smin):
                s.state, s.reason = "ambiguous", "insufficient-distinct-temperature-nodes"
            path.append(("double-label-single-candidate-ambiguous", None))
        else:
            best = None
            for Lmax in max_labels:
                for Tmax in E:
                    dmax = distance(model, Lmax, Tmax)
                    for Lmin in min_labels:
                        for Tmin in E:
                            if Tmax.oid == Tmin.oid:
                                continue
                            dmin = distance(model, Lmin, Tmin)
                            total = (dmax[0] + dmin[0], dmax[1] + dmin[1])
                            key = (total, dmax, dmin, Tmax.oid, Tmin.oid, Lmax.oid, Lmin.oid)
                            if best is None or key < best[0]:
                                best = (key, Lmax, Tmax, Lmin, Tmin)
            _, Lmax, Tmax, Lmin, Tmin = best
            _bind_from_temp(smax, Lmax, Tmax, Tmax)
            _bind_from_temp(smin, Lmin, Tmin, Tmin)
            path.append(("double-label-bound", f"Tmax={Tmax.oid} Tmin={Tmin.oid}"))

    elif has_max or has_min:
        branch = "single-label"
        labeled_is_max = has_max
        labels = max_labels if has_max else min_labels
        path.append(("branch-single-label", "max" if has_max else "min"))
        for L in labels:
            for T in E:
                d = distance(model, L, T)
                distances.append(DistEntry(L.oid, T.oid, d[0], d[1]))
        if len(E) >= 3:
            for s in (smax, smin):
                s.state, s.reason = "ambiguous", "insufficient-distinct-temperature-nodes"
            path.append(("single-label-ge3-ambiguous", None))
        else:
            best = None
            for L in labels:
                for T in E:
                    d = distance(model, L, T)
                    key = (d, T.oid, L.oid)
                    if best is None or key < best[0]:
                        best = (key, L, T)
            _, L_b, T_b = best
            labeled, numeric = (smax, smin) if labeled_is_max else (smin, smax)
            _bind_from_temp(labeled, L_b, T_b, T_b)
            remaining = [o for o in E if o.oid != T_b.oid]
            pick = _numeric_side(remaining, want_max=not labeled_is_max)
            if pick is None:
                numeric.state, numeric.reason = "ambiguous", "no-remaining-candidate"
                path.append(("single-label-no-remaining-ambiguous", None))
            else:
                _bind_from_temp(numeric, None, pick, pick)
                path.append(("single-label-bound", f"labeled={T_b.oid} numeric={pick.oid}"))

    else:
        branch = "no-label"
        path.append(("branch-no-label", None))
        if len(E) >= 3:
            for s in (smax, smin):
                s.state, s.reason = "ambiguous", "insufficient-distinct-temperature-nodes"
            path.append(("no-label-ge3-ambiguous", None))
        else:
            top = _numeric_side(E, want_max=True)
            bot = _numeric_side(E, want_max=False)
            _bind_from_temp(smax, None, top, top)
            _bind_from_temp(smin, None, bot, bot)
            path.append(("no-label-numeric-bound", f"max={top.oid} min={bot.oid}"))

    # ---- step 4: equal bound value → ambiguous ----------------------------
    both_bound = smax.value is not None and smin.value is not None and smax.state == "pending" and smin.state == "pending"
    if both_bound and (smax.value == smin.value or smax.canonical_id == smin.canonical_id):
        for s in (smax, smin):
            s.state, s.reason = "ambiguous", "max-equals-min"
            s.canonical_id = None
        path.append(("equal-value-ambiguous", None))

    # ---- value match / publish --------------------------------------------
    if smax.state == "pending":
        smax.state = value_state(smax.value, smax.display_decimals, expected_max)
    if smin.state == "pending":
        smin.state = value_state(smin.value, smin.display_decimals, expected_min)

    published = (
        smax.canonical_id is not None
        and smin.canonical_id is not None
        and smax.canonical_id != smin.canonical_id
        and smax.state in ("match", "mismatch")
        and smin.state in ("match", "mismatch")
    )
    path.append(("canonical-published", "true" if published else "false"))
    return BindingResult(branch, smax, smin, distances, published, path)
