"""Golden micro-fixtures for channel d-tagpath (scheme §4, appendix A row).

Appendix-A def: 诊断;root→leaf tag 路径集合 Jaccard.
S = |P_a ∩ P_b| / |P_a ∪ P_b|, where P_x is the SET of distinct root→leaf tag
paths (tuples of tag names). Empty set on either side (double-empty included) →
s = None.

All expected values below are hand-computed from the definition.
"""

import itertools

import pytest

from runner.similarity import d_tagpath as dt


# --------------------------------------------------------------------------
# tiny dom.json builders
# --------------------------------------------------------------------------
def _dom(nodes):
    return {"dump_format_version": "test", "nodes": nodes}


def _el(preorder, tag, parent):
    return {"preorder": preorder, "type": "element", "tag": tag, "parent": parent}


def _txt(preorder, parent):
    return {"preorder": preorder, "type": "text", "parent": parent, "text_raw": "x"}


# div > span (leaf)              -> paths {(div,span)}
DOM_DIV_SPAN = _dom([_el(0, "div", None), _el(1, "span", 0)])
# div > p (leaf)                 -> paths {(div,p)}
DOM_DIV_P = _dom([_el(0, "div", None), _el(1, "p", 0)])
# div > [span, p]                -> paths {(div,span),(div,p)}
DOM_DIV_SPAN_P = _dom(
    [_el(0, "div", None), _el(1, "span", 0), _el(2, "p", 0)]
)
# div > [span, span]             -> paths {(div,span)}  (set collapses dup)
DOM_DIV_SPAN_SPAN = _dom(
    [_el(0, "div", None), _el(1, "span", 0), _el(2, "span", 0)]
)
# single lone element            -> paths {(div,)}
DOM_LONE = _dom([_el(0, "div", None)])
# text-only (no element nodes)   -> empty set
DOM_TEXTONLY = _dom([_txt(0, None)])
DOM_EMPTY = _dom([])


# --------------------------------------------------------------------------
# path-set construction — hand traced
# --------------------------------------------------------------------------
def test_pathset_div_span():
    assert dt.tagpath_set(DOM_DIV_SPAN) == {("div", "span")}


def test_pathset_lone_element_is_its_own_leaf():
    assert dt.tagpath_set(DOM_LONE) == {("div",)}


def test_pathset_duplicate_paths_collapse():
    # two span children of div both produce (div,span); set has ONE member
    assert dt.tagpath_set(DOM_DIV_SPAN_SPAN) == {("div", "span")}


def test_pathset_branching():
    assert dt.tagpath_set(DOM_DIV_SPAN_P) == {("div", "span"), ("div", "p")}


def test_pathset_textonly_empty():
    assert dt.tagpath_set(DOM_TEXTONLY) == set()
    assert dt.tagpath_set(DOM_EMPTY) == set()


def test_pathset_deep_chain():
    # a>b>c>d (leaf) -> single path (a,b,c,d)
    dom = _dom(
        [_el(0, "a", None), _el(1, "b", 0), _el(2, "c", 1), _el(3, "d", 2)]
    )
    assert dt.tagpath_set(dom) == {("a", "b", "c", "d")}


# --------------------------------------------------------------------------
# compute() — Jaccard values, hand computed
# --------------------------------------------------------------------------
def test_identity_is_one():
    r = dt.compute(DOM_DIV_SPAN_P, DOM_DIV_SPAN_P)
    assert r["s"] == 1.0
    assert r["intersection"] == 2
    assert r["union"] == 2
    assert r["null_reason"] is None


def test_identity_lone():
    assert dt.compute(DOM_LONE, DOM_LONE)["s"] == 1.0


def test_disjoint_is_zero():
    # {(div,span)} vs {(div,p)} : inter 0, union 2 -> 0.0
    r = dt.compute(DOM_DIV_SPAN, DOM_DIV_P)
    assert r["s"] == 0.0
    assert r["intersection"] == 0
    assert r["union"] == 2


def test_partial_overlap_one_third():
    # A = {(div,span),(div,p)}  B = {(div,span)}
    # inter = 1, union = 2  -> 1/2
    r = dt.compute(DOM_DIV_SPAN_P, DOM_DIV_SPAN)
    assert r["s"] == pytest.approx(0.5)
    assert r["intersection"] == 1
    assert r["union"] == 2


def test_multiset_would_differ_but_set_used():
    # DOM_DIV_SPAN_SPAN collapses to {(div,span)} == DOM_DIV_SPAN's set
    # so Jaccard is 1.0 (SET semantics, not multiset)
    r = dt.compute(DOM_DIV_SPAN_SPAN, DOM_DIV_SPAN)
    assert r["s"] == 1.0


# --------------------------------------------------------------------------
# null semantics (§4)
# --------------------------------------------------------------------------
def test_empty_side_a_none():
    r = dt.compute(DOM_TEXTONLY, DOM_DIV_SPAN)
    assert r["s"] is None
    assert r["null_reason"] == "empty-set-a"


def test_empty_side_b_none():
    r = dt.compute(DOM_DIV_SPAN, DOM_EMPTY)
    assert r["s"] is None
    assert r["null_reason"] == "empty-set-b"


def test_empty_both_none():
    r = dt.compute(DOM_EMPTY, DOM_TEXTONLY)
    assert r["s"] is None
    assert r["null_reason"] == "empty-set-both"


def test_missing_artifact_none():
    r = dt.compute(None, DOM_DIV_SPAN)
    assert r["s"] is None
    assert r["null_reason"] == "empty-set-a"


# --------------------------------------------------------------------------
# symmetry & determinism
# --------------------------------------------------------------------------
_FIXTURES = [
    DOM_DIV_SPAN,
    DOM_DIV_P,
    DOM_DIV_SPAN_P,
    DOM_DIV_SPAN_SPAN,
    DOM_LONE,
    DOM_TEXTONLY,
    DOM_EMPTY,
]


def test_symmetry():
    for a, b in itertools.combinations(_FIXTURES, 2):
        sa = dt.compute(a, b)["s"]
        sb = dt.compute(b, a)["s"]
        assert sa == sb


def test_determinism_repeat_call():
    for a, b in itertools.product(_FIXTURES, repeat=2):
        r1 = dt.compute(a, b)
        r2 = dt.compute(a, b)
        assert r1 == r2


def test_range_bounds():
    for a, b in itertools.product(_FIXTURES, repeat=2):
        s = dt.compute(a, b)["s"]
        assert s is None or (0.0 <= s <= 1.0)


# --------------------------------------------------------------------------
# real devset artifact smoke: identity on a real card = 1.0, symmetric
# --------------------------------------------------------------------------
def test_real_card_identity(tmp_path=None):
    import os
    base = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..",
        "data", "batches-dev", "devset-42", "cards",
    )
    base = os.path.abspath(base)
    if not os.path.isdir(base):
        pytest.skip("devset cards not present")
    slugs = sorted(os.listdir(base))[:2]
    if len(slugs) < 2:
        pytest.skip("need >=2 cards")
    a = os.path.join(base, slugs[0])
    b = os.path.join(base, slugs[1])
    assert dt.compute(a, a)["s"] == 1.0
    assert dt.compute(a, b)["s"] == dt.compute(b, a)["s"]
