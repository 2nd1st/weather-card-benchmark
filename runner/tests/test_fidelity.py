"""Golden tests for the R5 fidelity extractor (COMPARISON-SCHEME-v12.md §3).

Synthetic ``dom.json`` fixtures cover §3's named edge cases with fully-controlled
inputs (occurrence identity, salience, distances):

  * ``Max 27° / Min 14°`` same-node dual occurrences (double-label);
  * bind-reversal guard (text-coordinate second distance component is load-bearing);
  * E=∅ pre-rule (no temperature candidate → both not-found);
  * ambiguous multi-candidate date;
  * hourly no-label exactly-24 rule (and ≠24 → ambiguous);
  * hourly candidate set excludes the two daily canonical occurrences.

Both artifacts are schema-validated: the trace against fidelity-trace.schema.json
and the meta verdict against slot-meta.schema.json's fidelity subschema.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest

from runner.fidelity import extract
from runner.fidelity.expected import ExpectedSnapshot
from runner.fidelity.occurrences import _namespace_for, CAT_MAX, CAT_MIN

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "data" / "SCHEMA"
TRACE_SCHEMA = json.loads((SCHEMA / "fidelity-trace.schema.json").read_text())
_SLOT = json.loads((SCHEMA / "slot-meta.schema.json").read_text())
META_SCHEMA = {"$defs": _SLOT["$defs"], **_SLOT["properties"]["fidelity"]}


# --------------------------------------------------------------------------
# dom.json (dom-dump-v1) builder
# --------------------------------------------------------------------------
class DomBuilder:
    def __init__(self):
        self.nodes: list[dict] = []

    def elem(self, parent, tag="div", font=16.0):
        pre = len(self.nodes)
        self.nodes.append({
            "preorder": pre, "type": "element", "tag": tag, "parent": parent,
            "child_index": 0, "depth": 0, "attrs": {},
            "box": {"x": 0.0, "y": 0.0, "width": 100.0, "height": 20.0},
            "intersects_viewport": True,
            "style": {"display": "block", "visibility": "visible", "opacity": 1.0, "font_size": font},
            "element_visible": True,
        })
        return pre

    def text(self, parent, raw, visible=True):
        pre = len(self.nodes)
        self.nodes.append({
            "preorder": pre, "type": "text", "parent": parent, "child_index": 0,
            "depth": 0, "text_raw": raw, "codepoint_length": len(raw),
            "text_visible": visible, "visible_text_index": None,
        })
        return pre

    def dom(self):
        return {
            "dump_format_version": "dom-dump-v1", "visibility_rule": "dom-dump-v1",
            "captured_at_virtual_ms": 5000, "url": "http://x/card.html",
            "viewport": {"width": 1280, "height": 800}, "counts": {}, "nodes": self.nodes,
        }


def _validate(out):
    jsonschema.validate(out["trace"], TRACE_SCHEMA)
    jsonschema.validate(out["meta"], META_SCHEMA)


def _snap(**kw):
    base = dict(name="Berlin", day=date(2026, 7, 16), weather_code=3,
               temp_max=Decimal("28.5"), temp_min=Decimal("18.6"), hourly=())
    base.update(kw)
    return ExpectedSnapshot(**base)


# --------------------------------------------------------------------------
# 1. "Max 27° / Min 14°" same-node dual occurrences → double-label bind
# --------------------------------------------------------------------------
def test_same_node_dual_max_min():
    b = DomBuilder()
    root = b.elem(None)
    card = b.elem(root, font=40.0)
    b.text(card, "Max 27° / Min 14°")
    out = extract(b.dom(), _snap(temp_max=Decimal("27"), temp_min=Decimal("14")))
    _validate(out)
    mm = out["trace"]["max_min_binding"]
    assert mm["branch"] == "double-label"
    assert out["meta"]["max"]["state"] == "match"
    assert out["meta"]["max"]["value"] == "27"
    assert out["meta"]["min"]["state"] == "match"
    assert out["meta"]["min"]["value"] == "14"
    assert mm["canonical_published"] is True
    # both label occurrences survive independently (same node, distinct spans).
    labels = [o for o in out["trace"]["occurrences"] if o["namespace"] in ("max-label", "min-label")]
    assert {o["namespace"] for o in labels} == {"max-label", "min-label"}
    assert all(o["voided"] is False for o in labels)


# --------------------------------------------------------------------------
# 2. bind-reversal guard — text-coordinate 2nd component is load-bearing
#    Node "14° min 27° max": occurrence order puts 14° first; a distance metric
#    that ignored text coordinates would bind max→14° (smallest occ order) by
#    tie-break. The §3 二元组 text-coordinate component binds max→27° (spatially
#    nearest the 'max' label). Guards the v10 同节点绑反 fix.
# --------------------------------------------------------------------------
def test_bind_reversal_guard():
    b = DomBuilder()
    root = b.elem(None)
    card = b.elem(root, font=40.0)
    b.text(card, "14° min 27° max")
    out = extract(b.dom(), _snap(temp_max=Decimal("27"), temp_min=Decimal("14")))
    _validate(out)
    assert out["meta"]["max"]["value"] == "27"  # NOT reversed to 14
    assert out["meta"]["min"]["value"] == "14"
    assert out["meta"]["max"]["state"] == "match"
    assert out["meta"]["min"]["state"] == "match"


# --------------------------------------------------------------------------
# 3. E=∅ pre-rule — bare numbers, no degree → no temperature candidate
# --------------------------------------------------------------------------
def test_e_empty_pre_rule():
    b = DomBuilder()
    root = b.elem(None)
    card = b.elem(root)
    b.text(card, "Berlin")
    b.text(card, "High 28 Low 18")  # no degree → not temperature candidates
    out = extract(b.dom(), _snap())
    _validate(out)
    assert out["trace"]["max_min_binding"]["branch"] == "E-empty"
    assert out["meta"]["max"]["state"] == "not-found"
    assert out["meta"]["max"]["reason"] == "no-temperature-candidate"
    assert out["meta"]["min"]["state"] == "not-found"
    assert out["trace"]["max_min_binding"]["canonical_published"] is False
    assert out["trace"]["hourly_binding"]["pretriage"] == "no-candidate-not-found"
    assert out["meta"]["hourly"]["state"] == "not-found"


# --------------------------------------------------------------------------
# 4. ambiguous multi-candidate date
# --------------------------------------------------------------------------
def test_date_ambiguous_multi_candidate():
    b = DomBuilder()
    root = b.elem(None)
    card = b.elem(root)
    b.text(card, "2026-07-16")
    b.text(card, "2026-07-17")  # conflicting second candidate
    out = extract(b.dom(), _snap())
    _validate(out)
    assert out["meta"]["date"]["state"] == "ambiguous"
    assert out["meta"]["date"]["reason"] == "multi-candidate-conflict"


def test_date_match_single_locale_template():
    b = DomBuilder()
    root = b.elem(None)
    card = b.elem(root)
    b.text(card, "Thursday, July 16, 2026")
    out = extract(b.dom(), _snap())
    _validate(out)
    assert out["meta"]["date"]["state"] == "match"


# --------------------------------------------------------------------------
# 5. hourly no-label exactly-24 rule (+ ≠24 → ambiguous) and canonical deduction
# --------------------------------------------------------------------------
def _hourly_card(n_hourly: int):
    """Daily max/min (salient, labeled) + n small-font unlabeled hourly temps."""
    hourly_vals = [
        "20.8", "20.2", "20.0", "19.4", "19.0", "18.9", "18.6", "18.7",
        "19.1", "19.9", "21.1", "22.5", "23.2", "24.7", "25.8", "26.7",
        "27.7", "28.1", "28.5", "28.5", "28.3", "27.6", "26.5", "25.2",
    ]
    b = DomBuilder()
    root = b.elem(None)
    daily = b.elem(root, font=48.0)
    b.text(daily, "High 28.5° Low 18.6°")
    strip = b.elem(root, font=12.0)  # small font → below salience band
    for i in range(n_hourly):
        cell = b.elem(strip, font=12.0)
        b.text(cell, f"{hourly_vals[i % 24]}°")
    expected_hourly = tuple(Decimal(v) for v in hourly_vals)
    return b.dom(), expected_hourly


def test_hourly_no_label_exactly_24_match():
    dom, exp_hourly = _hourly_card(24)
    out = extract(dom, _snap(hourly=exp_hourly))
    _validate(out)
    hb = out["trace"]["hourly_binding"]
    assert hb["pretriage"] == "proceed"
    assert hb["has_label"] is False
    assert len(hb["candidates"]) == 24  # 26 temps − 2 canonical
    # candidate set excludes the two daily canonical occurrences
    canon = {tuple(v.values()) for v in (
        out["trace"]["max_min_binding"]["max"]["canonical_id"],
        out["trace"]["max_min_binding"]["min"]["canonical_id"],
    )}
    cand = {tuple(c.values()) for c in hb["candidates"]}
    assert canon.isdisjoint(cand)
    assert out["meta"]["hourly"]["state"] == "match"
    assert out["meta"]["hourly"]["coverage"] == "24/24"
    assert out["meta"]["hourly"]["points_match"] == 24


def test_hourly_no_label_not_24_ambiguous():
    dom, exp_hourly = _hourly_card(25)  # 27 temps − 2 canonical = 25 ≠ 24
    out = extract(dom, _snap(hourly=exp_hourly))
    _validate(out)
    hb = out["trace"]["hourly_binding"]
    assert len(hb["candidates"]) == 25
    assert out["meta"]["hourly"]["state"] == "ambiguous"
    assert out["meta"]["hourly"]["reason"] == "no-label-not-24"


# --------------------------------------------------------------------------
# 6. same-span label voiding (unit — vocab cannot co-hit a span, so exercise
#    the merge/void rule directly)
# --------------------------------------------------------------------------
def test_same_span_both_labels_voided():
    ns, voided, reason = _namespace_for({CAT_MAX, CAT_MIN})
    assert voided is True
    assert reason == "span-has-both-max-and-min"
    ns, voided, _ = _namespace_for({CAT_MAX})
    assert voided is False and ns == "max-label"


# --------------------------------------------------------------------------
# real-card end-to-end: every devset card must produce a schema-valid trace+meta
# --------------------------------------------------------------------------
@pytest.mark.parametrize("slug", sorted(
    p.name for p in (REPO / "data/batches-dev/devset-42/cards").iterdir()
    if p.is_dir() and (p / "dom.json").exists()
))
def test_devset_card_schema_valid(slug):
    from runner.fidelity import DEVSET_EXPECTED
    dom = json.loads((REPO / f"data/batches-dev/devset-42/cards/{slug}/dom.json").read_text())
    out = extract(dom, DEVSET_EXPECTED, slot_index=0)
    _validate(out)
