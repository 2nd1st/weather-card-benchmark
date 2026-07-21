"""Golden micro-fixtures for channel c-ast-js (scheme §4, 附录 A row).

附录 A def: acorn-loose@8.4.0 (acorn@8.14.0), ecmaVersion=2023, script-then-
module; preorder-traversal ADJACENT node-type-pair bigram histogram, cosine;
parse-failure → token-bigram, pairwise-consistent (成对一致). No inline JS or a
degenerate (empty-bigram) side → s = None.

Expected cosine values below are hand-derived from the ESTree preorder node-type
sequences (independent of the implementation), per §4's "golden fixtures 期望值
独立预先写定" rule.
"""

import math

import pytest

from runner.similarity import c_ast_js as ca


def _html(js: str) -> str:
    return f"<!doctype html><html><body><div>x</div><script>{js}</script></body></html>"


def _card(js: str) -> dict:
    """compute() artifact carrying raw card.html (a bare string would be read as
    a filesystem path, matching the other c-* channels' contract)."""
    return {"card_html": _html(js)}


# --------------------------------------------------------------------------
# JS extraction from HTML
# --------------------------------------------------------------------------
def test_extract_inline_js():
    assert ca.extract_js(_html("var a=1;")) == "var a=1;"


def test_extract_skips_external_and_nonjs():
    html = (
        '<html><head>'
        '<script src="app.js"></script>'
        '<script type="application/json">{"k":1}</script>'
        '<script type="importmap">{}</script>'
        '</head><body>'
        '<script type="text/javascript">var a=1;</script>'
        '<script type="module">let b=2;</script>'
        "</body></html>"
    )
    js = ca.extract_js(html)
    assert "var a=1;" in js and "let b=2;" in js
    assert "app.js" not in js and '"k":1' not in js


def test_extract_no_script_is_empty():
    assert ca.extract_js("<html><body><p>hi</p></body></html>") == ""


# --------------------------------------------------------------------------
# cosine core (hand-checked)
# --------------------------------------------------------------------------
def test_cosine_identical():
    h = {"A\x01B": 1, "B\x01C": 2}
    assert ca._cosine(h, dict(h)) == pytest.approx(1.0)


def test_cosine_orthogonal():
    assert ca._cosine({"A\x01B": 1}, {"X\x01Y": 1}) == 0.0


def test_cosine_zero_vector_is_none():
    assert ca._cosine({}, {"A\x01B": 1}) is None
    assert ca._cosine({"A\x01B": 1}, {}) is None
    assert ca._cosine({}, {}) is None  # double-empty → None


# --------------------------------------------------------------------------
# channel-level golden cases (real acorn-loose via node)
# --------------------------------------------------------------------------
def test_identity_pair_is_one():
    b = _card("var x=1; function f(){ return x+1; }")
    r = ca.compute(b, b)
    assert r["s"] == 1.0
    assert r["method"] == "ast"
    assert r["channel"] == "c-ast-js"


def test_same_structure_different_names_is_one():
    # `var x=1;` and `var y=2;` share the exact preorder node-type sequence
    # (Program,VariableDeclaration,VariableDeclarator,Identifier,Literal) →
    # identical bigram histograms → cosine 1.0.
    r = ca.compute(_card("var x=1;"), _card("var y=2;"))
    assert r["s"] == 1.0


def test_orthogonal_pair_is_zero():
    # `var x=1;` bigrams {P|VD, VD|VDr, VDr|I, I|L} vs `x;` bigrams
    # {P|ExprStmt, ExprStmt|Identifier} share no keys → cosine 0.
    r = ca.compute(_card("var x=1;"), _card("x;"))
    assert r["s"] == 0.0


def test_partial_overlap_handcomputed():
    # A = `f(); g();`  preorder types:
    #   [Program, ExpressionStatement, CallExpression, Identifier,
    #             ExpressionStatement, CallExpression, Identifier]
    #   bigrams: P|ES:1, ES|CE:2, CE|Id:2, Id|ES:1   (norm=√10)
    # B = `f();`  bigrams: P|ES:1, ES|CE:1, CE|Id:1   (norm=√3)
    #   dot = 1 + 2 + 2 = 5  →  cos = 5/√30
    r = ca.compute(_card("f(); g();"), _card("f();"))
    assert r["s"] == pytest.approx(5.0 / math.sqrt(30.0))
    assert r["method"] == "ast"


def test_s_in_unit_interval():
    r = ca.compute(_card("for(let i=0;i<10;i++){ console.log(i); }"),
                   _card("const a=[1,2,3].map(n=>n*2);"))
    assert r["s"] is not None and 0.0 <= r["s"] <= 1.0


# --------------------------------------------------------------------------
# null semantics
# --------------------------------------------------------------------------
def test_no_js_side_is_none():
    no_js = "<html><body><p>no script here</p></body></html>"
    assert ca.compute({"card_html": no_js}, _card("var x=1;"))["s"] is None
    assert ca.compute(_card("var x=1;"), {"card_html": no_js})["s"] is None
    assert ca.compute({"card_html": no_js}, {"card_html": no_js})["s"] is None  # double-empty → None


def test_no_js_null_reason():
    r = ca.compute({"card_html": "<html></html>"}, _card("var x=1;"))
    assert r["s"] is None and r["null_reason"] == "no-js"


def test_degenerate_comment_only_is_none():
    # A non-empty script that is only a comment → 1 AST node (Program), no
    # bigrams → empty histogram → zero-vector → None.
    r = ca.compute(_card("// just a comment"), _card("var x=1;"))
    assert r["s"] is None
    assert r["null_reason"] == "empty-bigram-histogram"


# --------------------------------------------------------------------------
# invariants: symmetry & determinism
# --------------------------------------------------------------------------
def test_symmetry():
    a = _card("const t = document.querySelector('.temp'); t.textContent = `${20}°`;")
    b = _card("let el = document.getElementById('x'); el.innerHTML = 'hi';")
    assert ca.compute(a, b)["s"] == ca.compute(b, a)["s"]


def test_determinism_repeat_call():
    a = _card("function g(a,b){ return a*b - 1; }")
    b = _card("const h = (p,q) => p + q;")
    assert ca.compute(a, b)["s"] == ca.compute(a, b)["s"]


# --------------------------------------------------------------------------
# pairwise-consistent token fallback (成对一致)
# acorn-loose essentially never fails, so drive the branch by injecting a
# repr with ast_ok=False into the cache and asserting BOTH sides go token.
# --------------------------------------------------------------------------
def test_pairwise_consistent_token_fallback():
    js_a = "alpha beta;"
    js_b = "gamma delta;"   # distinct body → distinct cache key
    ka, kb = ca._js_key(js_a), ca._js_key(js_b)
    # side A "failed" to parse → token method for the whole pair.
    ca._REPR_CACHE[ka] = {
        "ast_ok": False, "source_type": None, "n_nodes": 0, "n_tokens": 2,
        "ast_bigrams": {}, "token_bigrams": {"NAME\x01NAME": 1, "NAME\x01op:;": 1},
    }
    ca._REPR_CACHE[kb] = {
        "ast_ok": True, "source_type": "script", "n_nodes": 3, "n_tokens": 2,
        "ast_bigrams": {"Program\x01ExpressionStatement": 1,
                        "ExpressionStatement\x01Identifier": 1},
        "token_bigrams": {"NAME\x01NAME": 1, "NAME\x01op:;": 1},
    }
    r = ca.compute({"card_html": f"<script>{js_a}</script>"},
                   {"card_html": f"<script>{js_b}</script>"})
    assert r["method"] == "token"          # one side failed → both token
    assert r["ast_ok_a"] is False and r["ast_ok_b"] is True
    assert r["s"] == pytest.approx(1.0)    # identical token histograms
    del ca._REPR_CACHE[ka], ca._REPR_CACHE[kb]
