"""Golden micro-fixture tests for the c-winnow similarity channel (§4 / 附录 A).

Expected values come from an INDEPENDENT reference (a from-scratch re-derivation
of the lexer → k-gram → winnow → Jaccard pipeline written here) plus hand-reasoned
invariants, never from the code under test — per §4 "golden fixtures 期望值独立预先
写定(手算或冻结时独立参考实现,禁被测代码自生成)".
"""

from __future__ import annotations

import hashlib
import re

import pytest

from runner.similarity import c_winnow

# ---- independent reference implementation of the appendix-A pipeline ---------
# Deliberately re-written (not imported from the channel) so a bug in the channel
# cannot mask itself. Same LAW: foldings ID/CLS/NUM/STR, k=5, w=4, min-of-window
# fingerprint set, Jaccard.

_REF_RE = re.compile(
    r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""  # STR
    r"""|(\d+\.\d+|\.\d+|\d+)"""                    # NUM
    r"""|(\.[A-Za-z_-][A-Za-z0-9_-]*)"""            # CLS
    r"""|([A-Za-z_][A-Za-z0-9_-]*)"""               # ID
    r"""|(\S)""",                                    # PUNCT
    re.DOTALL,
)


def _ref_tokens(src: str) -> list[str]:
    toks: list[str] = []
    for m in _REF_RE.finditer(src):
        if m.group(1) is not None:
            toks.append("STR")
        elif m.group(2) is not None:
            toks.append("NUM")
        elif m.group(3) is not None:
            toks.append("CLS")
        elif m.group(4) is not None:
            toks.append("ID")
        else:
            toks.append(m.group(5))
    return toks


def _ref_fingerprint(src: str) -> frozenset[int]:
    toks = _ref_tokens(src)
    if len(toks) < 5:
        return frozenset()
    grams = [tuple(toks[i : i + 5]) for i in range(len(toks) - 4)]
    hashes = [
        int.from_bytes(hashlib.sha256("\n".join(g).encode("utf-8")).digest()[:8], "big")
        for g in grams
    ]
    we = min(4, len(hashes))
    return frozenset(min(hashes[i : i + we]) for i in range(len(hashes) - we + 1))


def _ref_s(a: str, b: str):
    fa, fb = _ref_fingerprint(a), _ref_fingerprint(b)
    if not fa or not fb:
        return None
    return len(fa & fb) / len(fa | fb)


# Deterministic fixtures. These two share the ``<div><p>ID</p>`` prefix but diverge
# in the middle (``<p><span>`` vs ``<ul><li></ul><section>``) → PARTIAL fingerprint
# overlap (independently computed S = 0.8), so the channel must discriminate.
_HTML_A = "<div><p>a</p><p>b</p><p>c</p><span>d</span></div>"
_HTML_B = "<div><p>a</p><ul><li>b</li></ul><section>c</section></div>"

# A pair that folds to IDENTICAL token structure (only identifier / number / string
# VALUES differ) — used to show winnowing sees through cosmetic variation.
_HTML_FOLD_1 = (
    "<div class='card'><style>.card{color:red;font-size:16px}</style>"
    "<h1>Sunny</h1><p>Temp 27.4</p><script>fetch('/a')</script></div>"
)
_HTML_FOLD_2 = (
    "<div class='tile'><style>.tile{color:blue;font-size:18px}</style>"
    "<h1>Rainy</h1><p>Temp 13.9</p><script>fetch('/b')</script></div>"
)


# ---- golden: channel wiring matches the independent reference ----------------

def test_golden_matches_independent_reference():
    got = c_winnow.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    assert got["s"] == pytest.approx(_ref_s(_HTML_A, _HTML_B), abs=0.0)
    assert 0.0 <= got["s"] <= 1.0
    # Partial structural overlap → strictly between disjoint and identical.
    assert 0.0 < got["s"] < 1.0
    # Independently pinned expected value for this exact fixture pair.
    assert got["s"] == pytest.approx(0.8, abs=0.0)


def test_golden_diagnostic_fields():
    got = c_winnow.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    assert got["extractor_version"] == "c-winnow-extractor-v1"
    assert got["k"] == 5 and got["w"] == 4
    for key in ("n_tokens_a", "n_tokens_b", "n_fingerprints_a", "n_fingerprints_b"):
        assert isinstance(got[key], int) and got[key] > 0
    assert got["reason"] is None
    assert 0 <= got["intersection"] <= got["union"]


# ---- fully hand-reasoned golden: singleton fingerprint sets (S ∈ {0,1}) ------
# A 5-token document produces exactly ONE 5-gram → ONE fingerprint. With
# single-element fingerprint sets, Jaccard is 1 iff the two 5-grams are identical
# (same folded token sequence) else 0 — verifiable WITHOUT knowing any hash value.

def test_handcomputed_singleton_identical_is_one():
    # "a+b+c" → [ID, +, ID, +, ID]  (5 tokens → 1 fingerprint)
    a = c_winnow.compute({"card_html": "a+b+c"}, {"card_html": "x+y+z"})
    # Folded token streams are identical ([ID,+,ID,+,ID]) → same single hash → S=1.
    assert a["n_tokens_a"] == 5 and a["n_fingerprints_a"] == 1
    assert a["s"] == 1.0


def test_handcomputed_singleton_disjoint_is_zero():
    # [ID,+,ID,+,ID] vs [ID,*,ID,*,ID] differ at the punct token (+ vs *) → the two
    # singleton fingerprint sets are disjoint. (NB: '-' binds into identifiers as
    # CSS kebab-case, so 'a-b-c' would be ONE token — '*' is a clean separator.)
    d = c_winnow.compute({"card_html": "a+b+c"}, {"card_html": "a*b*c"})
    assert d["n_fingerprints_a"] == 1 and d["n_fingerprints_b"] == 1
    assert d["intersection"] == 0 and d["union"] == 2
    assert d["s"] == 0.0


def test_folding_erases_identifier_identity():
    # Winnowing's purpose: different identifier / number / string VALUES with the
    # same structure fold to the same token stream → S = 1.0. These two cards differ
    # only in class names, colors, sizes, headings, temps and string bodies.
    x = c_winnow.compute({"card_html": _HTML_FOLD_1}, {"card_html": _HTML_FOLD_2})
    assert x["s"] == 1.0


# ---- identity → channel maximum (S = 1) --------------------------------------

def test_identity_is_max():
    same = c_winnow.compute({"card_html": _HTML_A}, {"card_html": _HTML_A})
    diff = c_winnow.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    assert same["s"] == 1.0
    assert same["s"] >= diff["s"]


# ---- symmetry: S(a,b) == S(b,a) ----------------------------------------------

def test_symmetry():
    ab = c_winnow.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    ba = c_winnow.compute({"card_html": _HTML_B}, {"card_html": _HTML_A})
    assert ab["s"] == ba["s"]
    assert ab["intersection"] == ba["intersection"]
    assert ab["union"] == ba["union"]


# ---- determinism: repeat call equality ---------------------------------------

def test_determinism_repeat_call():
    r1 = c_winnow.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    r2 = c_winnow.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    assert r1 == r2


# ---- empty / degenerate → None (§4 empty-bag) --------------------------------

@pytest.mark.parametrize(
    "a,b",
    [
        ({"card_html": ""}, {"card_html": _HTML_A}),        # one empty
        ({"card_html": _HTML_A}, {"card_html": ""}),        # other empty
        ({"card_html": ""}, {"card_html": ""}),             # double empty
        ({"card_html": None}, {"card_html": _HTML_A}),      # absent html
        (None, {"card_html": _HTML_A}),                     # absent artifact
        ({"card_html": "<x"}, {"card_html": _HTML_A}),      # < 5 tokens (degenerate)
    ],
)
def test_empty_degenerate_is_none(a, b):
    got = c_winnow.compute(a, b)
    assert got["s"] is None
    assert got["reason"] == "empty-fingerprint-set"


def test_short_document_boundary():
    # Exactly 5 tokens → exactly 1 fingerprint (effective window = min(4,1) = 1).
    g = c_winnow.compute({"card_html": "a+b+c"}, {"card_html": "a+b+c"})
    assert g["n_fingerprints_a"] == 1
    # 4 tokens → 0 five-grams → empty fingerprint → None.
    short = c_winnow.compute({"card_html": "a+b+"}, {"card_html": "a+b+c"})
    assert short["s"] is None


# ---- path-based artifact resolution ------------------------------------------

def test_path_artifact_resolution(tmp_path):
    d = tmp_path / "some-card"
    d.mkdir()
    (d / "card.html").write_text(_HTML_A, encoding="utf-8")
    via_dir = c_winnow.compute(d, {"card_html": _HTML_A})
    via_file = c_winnow.compute(str(d / "card.html"), {"card_html": _HTML_A})
    inline = c_winnow.compute({"card_html": _HTML_A}, {"card_html": _HTML_A})
    assert via_dir["s"] == via_file["s"] == inline["s"] == 1.0
    # A card directory MISSING card.html → "" → None (mirrors the devset gap).
    empty_dir = tmp_path / "no-html"
    empty_dir.mkdir()
    assert c_winnow.compute(empty_dir, {"card_html": _HTML_A})["s"] is None
