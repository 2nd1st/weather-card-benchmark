"""Assemble ``fidelity-trace.json`` + the ``meta.json`` terminal verdict (scheme §3/§8).

``extract`` runs the whole §3 pipeline on one card's ``dom.json`` against a frozen
``ExpectedSnapshot`` and returns both artifacts:

  * ``trace`` — validates against ``fidelity-trace.schema.json`` (the load-bearing
    "全部落盘" audit record);
  * ``meta``  — the ``fidelity`` object for ``meta.json`` (slot-meta schema),
    the lightweight terminal verdict for the card page.

FLAG (R5 report): ``request_side`` contract validation needs the per-request
network log, which is NOT present in ``dom.json`` (it is the harness/§6 network
white-list's output). R5 emits a placeholder ``{"status":"match","violations":[]}``
so the object is slot-meta-valid; the real request-side check is wired where the
network log lives (out of the dom-only extractor's scope).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .binding import BindingResult, bind_max_min
from .expected import EXTRACTOR_VERSION, ExpectedSnapshot
from .fields import (
    canonical_decimal,
    compute_salience,
    judge_condition,
    judge_date,
    judge_name,
)
from .hourly import bind_hourly
from .normalize import DomModel
from .occurrences import NS_HOURLY, NS_MAX, NS_MIN, NS_TEMP, match_occurrences

OID = tuple[int, int, int]


def _oid(oid: OID | None) -> dict | None:
    if oid is None:
        return None
    return {"node_preorder": oid[0], "start": oid[1], "end": oid[2]}


def _path(steps) -> list[dict]:
    return [{"code": code, "detail": detail} for code, detail in steps]


def _side(side) -> dict:
    return {
        "label_id": _oid(side.label_id),
        "temp_id": _oid(side.temp_id),
        "canonical_id": _oid(side.canonical_id),
        "value": canonical_decimal(side.value) if side.value is not None else None,
        "display_decimals": side.display_decimals,
    }


def _temp_verdict(side) -> dict:
    return {
        "state": side.state,
        "reason": side.reason,
        "value": canonical_decimal(side.value) if side.value is not None else None,
        "display_decimals": side.display_decimals,
    }


def extract(
    dom: dict,
    expected: ExpectedSnapshot,
    slot_index: int = 0,
    extractor_version: str = EXTRACTOR_VERSION,
    include_text_model: bool = True,
) -> dict[str, Any]:
    """Run the §3 fidelity pipeline; return ``{"trace":..., "meta":...}``."""
    model = DomModel(dom)
    occ = match_occurrences(model)
    salience = compute_salience(occ.pool(NS_TEMP))

    binding: BindingResult = bind_max_min(
        model, salience,
        occ.pool(NS_MAX), occ.pool(NS_MIN),
        expected.temp_max, expected.temp_min,
    )
    hourly = bind_hourly(
        model,
        all_temps=occ.pool(NS_TEMP),
        hourly_labels=occ.pool(NS_HOURLY),
        max_canonical=binding.max.canonical_id,
        min_canonical=binding.min.canonical_id,
        canonical_published=binding.canonical_published,
        expected_hourly=expected.hourly,
    )

    name_v = judge_name(model, expected.name)
    date_v = judge_date(model, expected.day)
    cond_v = judge_condition(model, expected.weather_code)

    # ------------------------------------------------------------------ trace
    occ_items = [
        {
            "id": _oid(o.oid),
            "pattern_id": o.pattern_id,
            "namespace": o.namespace,
            "merged_categories": list(o.merged_categories),
            "voided": o.voided,
            "voided_reason": o.voided_reason,
        }
        for o in occ.occurrences
    ]
    considered = [
        {
            "node_preorder": c.node_preorder,
            "start": c.start,
            "length": c.length,
            "pattern_id": c.pattern_id,
            "selected": c.selected,
        }
        for c in occ.considered
    ]
    sal_cands = [
        {
            "occurrence_id": _oid(o.oid),
            "font_size": o.font_size,
            "salience_ratio": float(salience.ratios[o.oid]),
            "in_E": any(e.oid == o.oid for e in salience.e),
        }
        for o in salience.candidates
        if o.font_size is not None and o.font_size > 0 and o.oid in salience.ratios
    ]

    trace: dict[str, Any] = {
        "extractor_version": extractor_version,
        "slot_index": slot_index,
        "occurrences": occ_items,
        "arbitration": {"considered": considered},
        "salience": {
            "f_max": salience.f_max,
            "candidates": sal_cands,
            "E": [_oid(e.oid) for e in salience.e],
        },
        "max_min_binding": {
            "branch": binding.branch,
            "distances": [
                {
                    "label_id": _oid(d.label_id),
                    "temp_id": _oid(d.temp_id),
                    "dom_edges": d.dom_edges,
                    "text_coord_diff": d.text_coord_diff,
                }
                for d in binding.distances
            ],
            "max": _side(binding.max),
            "min": _side(binding.min),
            "canonical_published": binding.canonical_published,
            "decision_path": _path(binding.decision_path),
        },
        "hourly_binding": {
            "pretriage": hourly.pretriage,
            "has_label": hourly.has_label,
            "candidates": [_oid(c) for c in hourly.candidates],
            "label_parse": [
                {"occurrence_id": _oid(oid), "parsed_hour": ph}
                for oid, ph in hourly.label_parse
            ],
            "bindings": [
                {
                    "label_id": _oid(b.label_id),
                    "temp_id": _oid(b.temp_id),
                    "hour": b.hour,
                    "dom_edges": b.dom_edges,
                    "text_coord_diff": b.text_coord_diff,
                }
                for b in hourly.bindings
            ],
            "signature": [_oid(s) for s in hourly.signature],
            "decision_path": _path(hourly.decision_path),
        },
        "field_paths": {
            "name": {"state": name_v.state, "decision_path": _path(name_v.path)},
            "date": {"state": date_v.state, "decision_path": _path(date_v.path)},
            "condition": {"state": cond_v.state, "decision_path": _path(cond_v.path)},
            "max": {"state": binding.max.state, "decision_path": _path(binding.decision_path)},
            "min": {"state": binding.min.state, "decision_path": _path(binding.decision_path)},
            "hourly": {"state": hourly.state, "decision_path": _path(hourly.decision_path)},
        },
    }
    if include_text_model:
        trace["text_model"] = [
            {
                "node_preorder": vt.preorder,
                "text_coord_base": vt.base,
                "codepoint_length": vt.length,
            }
            for vt in model.visible
        ]

    # ------------------------------------------------------------------- meta
    meta = {
        "extractor_version": extractor_version,
        "name": {"state": name_v.state, "reason": name_v.reason},
        "date": {"state": date_v.state, "reason": date_v.reason},
        "condition": {"state": cond_v.state, "reason": cond_v.reason},
        "max": _temp_verdict(binding.max),
        "min": _temp_verdict(binding.min),
        "hourly": {
            "state": hourly.state,
            "reason": hourly.reason,
            "points": [{"hour": h, "state": s} for h, s in hourly.points],
            "points_match": hourly.points_match,
            "points_mismatch": hourly.points_mismatch,
            "points_not_shown": hourly.points_not_shown,
            "coverage": hourly.coverage,
        },
        # FLAG: placeholder — request-side needs the network log (not in dom.json).
        "request_side": {"status": "match", "violations": []},
    }

    return {"trace": trace, "meta": meta}
