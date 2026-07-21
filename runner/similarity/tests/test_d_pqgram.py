"""Golden micro-fixtures for channel d-pqgram (scheme §4, appendix A row).

Appendix-A def: p=2, q=3, label=tag; pq-gram profile (Augsten et al.) compared
by multiplicity Sørensen–Dice: sim = 2·Σ_g min(cnt₁,cnt₂) / (|bag₁|+|bag₂|);
empty bag on either side (double-empty included) → s = None.

All expected values below are hand-computed from the pq-gram definition.
"""

import json

import pytest

from runner.similarity import d_pqgram as dp


# --------------------------------------------------------------------------
# tiny dom.json builders
# --------------------------------------------------------------------------
def _dom(nodes):
    return {"dump_format_version": "test", "nodes": nodes}


def _el(preorder, tag, parent):
    return {"preorder": preorder, "type": "element", "tag": tag, "parent": parent}


def _txt(preorder, parent):
    return {"preorder": preorder, "type": "text", "parent": parent, "text_raw": "x"}


# div > span (leaf)
DOM_DIV_SPAN = _dom([_el(0, "div", None), _el(1, "span", 0)])
# div > p (leaf)
DOM_DIV_P = _dom([_el(0, "div", None), _el(1, "p", 0)])
# div > [span, span]
DOM_DIV_SPAN_SPAN = _dom([_el(0, "div", None), _el(1, "span", 0), _el(2, "span", 0)])
# single element, no children
DOM_LONE = _dom([_el(0, "div", None)])
# text-only (no element nodes) → empty bag
DOM_TEXTONLY = _dom([_txt(0, None)])
DOM_EMPTY = _dom([])


# --------------------------------------------------------------------------
# pq-gram bag construction — verified against the hand traced profile
# --------------------------------------------------------------------------
def test_bag_div_span_exact():
    """div>span(leaf), p=2 q=3, null=None. Root div (1 child) emits 1+ (q-1)=3
    grams; leaf span emits 1. Total mass 4, all distinct."""
    bag = dp.pqgram_bag(DOM_DIV_SPAN)
    expected = {
        (None, "div", None, None, "span"),   # base window: (*,*,span)
        (None, "div", None, "span", None),   # (*,span,*)
        (None, "div", "span", None, None),   # (span,*,*)
        ("div", "span", None, None, None),   # leaf span, base all-null
    }
    assert set(bag) == expected
    assert all(c == 1 for c in bag.values())
    assert sum(bag.values()) == 4


def test_bag_lone_element():
    """A single element with no children still yields exactly one pq-gram."""
    bag = dp.pqgram_bag(DOM_LONE)
    assert dict(bag) == {("div", None, None, None): None} or sum(bag.values()) == 1
    # stem (*,div) + base (*,*,*)
    assert list(bag) == [(None, "div", None, None, None)]
    assert sum(bag.values()) == 1


def test_bag_multiplicity():
    """div>[span,span]: the leaf gram (div,span,*,*,*) has multiplicity 2."""
    bag = dp.pqgram_bag(DOM_DIV_SPAN_SPAN)
    assert bag[("div", "span", None, None, None)] == 2
    assert sum(bag.values()) == 6  # 4 root grams (2 children +2 trailing) + 2 leaves


# --------------------------------------------------------------------------
# multiplicity-Dice golden values (hand computed)
# --------------------------------------------------------------------------
def test_identity_is_one():
    r = dp.compute(DOM_DIV_SPAN, DOM_DIV_SPAN)
    assert r["s"] == 1.0
    assert r["dist"] == 0.0


def test_disjoint_leaf_label_is_zero():
    """div>span vs div>p share no pq-gram (span vs p differ in every gram) → 0."""
    r = dp.compute(DOM_DIV_SPAN, DOM_DIV_P)
    assert r["s"] == 0.0
    assert r["dist"] == 1.0
    assert r["intersection_mass"] == 0


def test_partial_overlap_golden_0_6():
    """div>[span,span] (mass 6) vs div>span (mass 4).
    Σ min over shared grams = 3 → sim = 2*3/(6+4) = 0.6 (hand computed)."""
    r = dp.compute(DOM_DIV_SPAN_SPAN, DOM_DIV_SPAN)
    assert r["bag_a_mass"] == 6
    assert r["bag_b_mass"] == 4
    assert r["intersection_mass"] == 3
    assert r["s"] == pytest.approx(0.6)
    assert r["dist"] == pytest.approx(0.4)


# --------------------------------------------------------------------------
# null semantics (§4): empty bag on either side → None
# --------------------------------------------------------------------------
def test_empty_bag_text_only_is_none():
    r = dp.compute(DOM_TEXTONLY, DOM_DIV_SPAN)
    assert r["s"] is None
    assert r["null_reason"] == "empty-bag-a"


def test_empty_bag_other_side_is_none():
    r = dp.compute(DOM_DIV_SPAN, DOM_EMPTY)
    assert r["s"] is None
    assert r["null_reason"] == "empty-bag-b"


def test_double_empty_is_none():
    r = dp.compute(DOM_EMPTY, DOM_TEXTONLY)
    assert r["s"] is None
    assert r["null_reason"] == "empty-bag-both"


def test_missing_artifact_is_none():
    r = dp.compute(None, DOM_DIV_SPAN)
    assert r["s"] is None
    assert r["null_reason"] == "empty-bag-a"


# --------------------------------------------------------------------------
# invariants: symmetry, determinism, self-identity on real data
# --------------------------------------------------------------------------
def test_symmetry():
    ab = dp.compute(DOM_DIV_SPAN_SPAN, DOM_DIV_P)
    ba = dp.compute(DOM_DIV_P, DOM_DIV_SPAN_SPAN)
    assert ab["s"] == ba["s"]


def test_determinism_repeat_call():
    a = dp.compute(DOM_DIV_SPAN_SPAN, DOM_DIV_SPAN)
    b = dp.compute(DOM_DIV_SPAN_SPAN, DOM_DIV_SPAN)
    assert a == b


def test_child_order_matters():
    """pq-gram is order-sensitive: div>[a,b] differs from div>[b,a]."""
    dom_ab = _dom([_el(0, "div", None), _el(1, "a", 0), _el(2, "b", 0)])
    dom_ba = _dom([_el(0, "div", None), _el(1, "b", 0), _el(2, "a", 0)])
    r = dp.compute(dom_ab, dom_ba)
    assert r["s"] < 1.0  # structure order distinguishes them


# --------------------------------------------------------------------------
# real devset artifact smoke: path-based artifact resolution + self=1
# --------------------------------------------------------------------------
def _devset_card(slug):
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    return root / "data" / "batches-dev" / "devset-42" / "cards" / slug


def test_real_card_self_identity():
    card = _devset_card("r1__claude-fable-5")
    if not (card / "dom.json").is_file():
        pytest.skip("devset card not present")
    r = dp.compute(card, card)  # dir path → reads dom.json
    assert r["s"] == 1.0
    assert r["bag_a_mass"] == r["bag_b_mass"] > 0


def test_real_card_symmetry_and_range():
    a = _devset_card("r1__claude-fable-5")
    b = _devset_card("r1__gpt-5.4")
    if not (a / "dom.json").is_file() or not (b / "dom.json").is_file():
        pytest.skip("devset cards not present")
    ab = dp.compute(str(a), str(b))
    ba = dp.compute(str(b), str(a))
    assert ab["s"] == ba["s"]
    assert 0.0 <= ab["s"] <= 1.0
