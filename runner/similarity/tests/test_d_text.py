"""Golden micro-fixtures for channel d-text (scheme §4, appendix A row).

Appendix-A def: 可见文本剔除动态字段;k=5 token shingle,Jaccard.
Zero / empty shingle set on either side (double-empty included) → s = None (§4).

Fixtures are tiny synthetic dom-dump-v1 objects with hand-computed expected
values (expectations written independently of the implementation, per §4:
"golden fixtures 期望值独立预先写定").
"""

import copy

import pytest

from runner.similarity import d_text as dt


# --------------------------------------------------------------------------
# helpers — build a minimal dom-dump-v1 object from a list of text strings
# --------------------------------------------------------------------------
def _dom(*texts, visible=True):
    """dom.json with one root element + one visible text node per string."""
    nodes = [
        {"preorder": 0, "type": "element", "tag": "div", "parent": None},
    ]
    p = 1
    for t in texts:
        nodes.append(
            {
                "preorder": p,
                "type": "text",
                "parent": 0,
                "text_raw": t,
                "text_visible": visible,
            }
        )
        p += 1
    return {"dump_format_version": "dom-dump-v1", "nodes": nodes}


# a stream of >=5 non-dynamic tokens so a shingle set exists
_WORDS = "alpha beta gamma delta epsilon zeta eta theta"


# --------------------------------------------------------------------------
# tokenization / dynamic-field excision
# --------------------------------------------------------------------------
def test_tokenize_lowercases_nfc():
    toks = dt.visible_text_tokens(_dom("Today MAX Min"))
    assert toks == ["today", "max", "min"]


def test_dynamic_digit_tokens_placeholdered():
    # v14: "27", "14", "00" (from 14:00) and "88" are dynamic → replaced by the
    # DYNAMIC placeholder IN PLACE, not deleted. The shape of the text survives.
    toks = dt.visible_text_tokens(_dom("Max 27° Min 14° at 14:00 humidity 88%"))
    assert toks == ["max", "#", "min", "#", "at", "#", "#", "humidity", "#"]


def test_placeholder_keeps_non_adjacent_words_apart():
    # v14 regression: under deletion this collapsed to ["high","low"], fabricating
    # an adjacency ("high low") that appears nowhere on the card.
    assert dt.visible_text_tokens(_dom("high 25° low 16°")) == [
        "high", "#", "low", "#",
    ]


def test_leftover_letter_from_unit_kept():
    # "27°C" → \w+ tokens "27","c"; "27" → placeholder, "c" retained.
    assert dt.visible_text_tokens(_dom("27°C")) == ["#", "c"]


def test_placeholder_cannot_collide_with_a_real_token():
    # DYNAMIC is outside \w+, so no card text can ever tokenize to it.
    assert not dt._TOKEN_RE.fullmatch(dt.DYNAMIC)
    assert dt.visible_text_tokens(_dom("# ## hash")) == ["hash"]


def test_invisible_text_dropped():
    dom = _dom(_WORDS, visible=False)
    assert dt.visible_text_tokens(dom) == []


def test_preorder_ordering_and_cross_node_concat():
    # two nodes, tokens concatenated in preorder into one stream
    toks = dt.visible_text_tokens(_dom("alpha beta", "gamma delta"))
    assert toks == ["alpha", "beta", "gamma", "delta"]


# --------------------------------------------------------------------------
# shingling
# --------------------------------------------------------------------------
def test_shingles_count_and_content():
    toks = ["a", "b", "c", "d", "e", "f"]  # 6 tokens → 2 five-shingles
    sh = dt.shingles(toks, 5)
    assert sh == {("a", "b", "c", "d", "e"), ("b", "c", "d", "e", "f")}


def test_shingles_fewer_than_k_is_empty():
    assert dt.shingles(["a", "b", "c", "d"], 5) == set()


def test_shingles_span_node_boundaries():
    # 3 nodes × 2 tokens = 6 tokens → 2 shingles crossing node boundaries
    toks = dt.visible_text_tokens(_dom("alpha beta", "gamma delta", "epsilon zeta"))
    assert len(dt.shingles(toks, 5)) == 2


# --------------------------------------------------------------------------
# channel-level golden cases
# --------------------------------------------------------------------------
def test_identity_is_one():
    dom = _dom(_WORDS)
    r = dt.compute({"dom": dom}, {"dom": dom})
    assert r["s"] == 1.0
    assert r["intersection"] == r["union"]


def test_disjoint_is_zero():
    a = _dom("alpha beta gamma delta epsilon")           # 1 shingle
    b = _dom("one two three four five six seven")         # all non-dynamic words
    r = dt.compute({"dom": a}, {"dom": b})
    assert r["s"] == 0.0  # no shared 5-shingle


def test_known_jaccard():
    # a tokens: a b c d e f  → shingles {abcde, bcdef}
    # b tokens: a b c d e g  → shingles {abcde, bcdeg}
    # intersection = {abcde} = 1 ; union = 3 ; Jaccard = 1/3
    a = _dom("a b c d e f")
    b = _dom("a b c d e g")
    r = dt.compute({"dom": a}, {"dom": b})
    assert r["intersection"] == 1
    assert r["union"] == 3
    assert r["s"] == pytest.approx(1.0 / 3.0)


def test_empty_side_is_none():
    full = _dom(_WORDS)
    short = _dom("only three tokens")  # < 5 → empty shingle set
    assert dt.compute({"dom": full}, {"dom": short})["s"] is None
    assert dt.compute({"dom": short}, {"dom": full})["s"] is None
    assert dt.compute({"dom": short}, {"dom": short})["s"] is None  # double-empty


def test_no_dom_is_none():
    assert dt.compute(None, {"dom": _dom(_WORDS)})["s"] is None
    assert dt.compute({"dom": _dom(_WORDS)}, None)["s"] is None
    assert dt.compute(None, None)["s"] is None


def test_all_dynamic_tokens_is_defined_not_none():
    # v14: an all-numeric card is no longer "empty". It HAS visible text, and its
    # text-shape is "########" — a real, comparable signal. So S is DEFINED (0.0
    # against a labelled card: no shared 5-gram), where v13 returned None because
    # deletion had emptied the stream. Null is reserved for "no text at all".
    dom = _dom("27 14 88 12 06 18 24 00")
    assert dt.compute({"dom": dom}, {"dom": _dom(_WORDS)})["s"] == 0.0
    # two pure-numeric cards agree on that shape — degenerate but honest.
    assert dt.compute({"dom": dom}, {"dom": _dom("1 2 3 4 5 6 7 8")})["s"] == 1.0


def test_no_visible_text_at_all_is_none():
    # the surviving null path: nothing visible → empty stream → S = None.
    blank = _dom(_WORDS, visible=False)
    assert dt.compute({"dom": blank}, {"dom": _dom(_WORDS)})["s"] is None


# --------------------------------------------------------------------------
# invariants: symmetry, determinism, unit interval, diagnostics
# --------------------------------------------------------------------------
def test_symmetry():
    a = _dom("a b c d e f")
    b = _dom("a b c d e g")
    assert dt.compute({"dom": a}, {"dom": b})["s"] == dt.compute({"dom": b}, {"dom": a})["s"]


def test_determinism_repeat_call():
    a = _dom("a b c d e f g")
    b = _dom("a b c x e f h")
    r1 = dt.compute({"dom": a}, {"dom": b})["s"]
    r2 = dt.compute({"dom": copy.deepcopy(a)}, {"dom": copy.deepcopy(b)})["s"]
    assert r1 == r2


def test_s_in_unit_interval():
    a = _dom("a b c d e f g")
    b = _dom("a b c x e f h")
    s = dt.compute({"dom": a}, {"dom": b})["s"]
    assert 0.0 <= s <= 1.0


def test_diagnostics_present():
    r = dt.compute({"dom": _dom(_WORDS)}, {"dom": _dom(_WORDS)})
    assert r["channel"] == "d-text"
    assert r["k"] == 5
    assert r["shingles_a"] > 0 and r["shingles_b"] > 0


# --------------------------------------------------------------------------
# artifact-handle flexibility (mirrors sibling channels)
# --------------------------------------------------------------------------
def test_artifact_dom_object_directly():
    dom = _dom(_WORDS)
    # a Mapping carrying "nodes" IS the dom
    assert dt.compute(dom, dom)["s"] == 1.0


def test_artifact_dir(tmp_path):
    import json as _json

    d = tmp_path / "card"
    d.mkdir()
    (d / "dom.json").write_text(_json.dumps(_dom(_WORDS)), encoding="utf-8")
    assert dt.compute(str(d), {"dom": _dom(_WORDS)})["s"] == 1.0
