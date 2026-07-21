"""Golden micro-fixtures for the c-css-prop channel (scheme §4 / appendix A).

Expected similarity values are hand-computed HERE (NOT produced by the channel
under test), per §4 "golden fixtures 期望值独立预先写定". The CSS property
*extraction* still goes through pinned postcss (that IS the appendix-A parser); for
these trivial inputs the extracted property set is unambiguous, so the hand-computed
cosine is exact.
"""

import math

import pytest

from runner.similarity import c_css_prop
from runner.similarity.c_css_prop import compute, extract_distribution


# --- extraction unit checks (hand-enumerated) -------------------------------

def test_extract_distribution_counts_frequencies():
    html = (
        "<html><head><style>"
        ".card { color: red; padding: 4px; color: blue; }"   # color x2, padding x1
        "a:hover { color: green; }"                            # color x1 (real decl)
        "@media (min-width: 600px) { .card { margin: 0; } }"  # margin x1; min-width NOT a prop
        "</style></head><body></body></html>"
    )
    dist = extract_distribution(html)
    # color: 2 (.card) + 1 (a:hover) = 3 ; padding 1 ; margin 1
    assert dist == {"color": 3, "padding": 1, "margin": 1}
    assert "min-width" not in dist   # @media feature, not a declaration
    assert "a" not in dist           # a:hover selector, not a declaration


def test_custom_properties_kept_verbatim():
    html = "<style>:root { --Sky-Top: #fff; color-scheme: dark; }</style>"
    dist = extract_distribution(html)
    assert dist == {"--Sky-Top": 1, "color-scheme": 1}  # custom prop case preserved


def test_multiple_style_blocks_summed():
    html = "<style>a{color:red}</style><style>b{color:blue;margin:0}</style>"
    dist = extract_distribution(html)
    assert dist == {"color": 2, "margin": 1}


# --- cosine golden pairs (hand-computed s) ----------------------------------

def test_identity_pair_s_is_one():
    html = "<style>.x{color:red;color:blue;margin:0;padding:1px}</style>"
    r = compute(html, html)
    assert r["s"] == pytest.approx(1.0)


def test_hand_computed_cosine():
    # A: color x2, margin x1  -> vector over {color,margin,padding} = [2,1,0]
    a = "<style>.x{color:red;color:blue;margin:0}</style>"
    # B: color x1, padding x1 -> [1,0,1]
    b = "<style>.y{color:red;padding:1px}</style>"
    # dot = 2*1 + 1*0 + 0*1 = 2 ; |a| = sqrt(4+1)=sqrt5 ; |b| = sqrt(1+1)=sqrt2
    # cos = 2 / (sqrt5 * sqrt2) = 2 / sqrt(10)
    expected = 2.0 / math.sqrt(10.0)
    r = compute(a, b)
    assert r["s"] == pytest.approx(expected)
    assert r["n_decl_a"] == 3
    assert r["n_decl_b"] == 2
    assert r["shared_props"] == 1  # only 'color' shared


def test_disjoint_props_zero():
    a = "<style>.a{color:red}</style>"
    b = "<style>.b{margin:0}</style>"
    # orthogonal frequency vectors -> cosine 0 (both non-empty, so NOT null)
    r = compute(a, b)
    assert r["s"] == pytest.approx(0.0)
    assert r["shared_props"] == 0


def test_proportional_vectors_cosine_one():
    # Same property mix, different multiplicities but proportional -> cosine 1.
    a = "<style>.a{color:red;margin:0}</style>"                       # color1 margin1
    b = "<style>.b{color:red;color:blue;margin:0;margin:1px}</style>"  # color2 margin2
    r = compute(a, b)
    assert r["s"] == pytest.approx(1.0)


# --- empty / degenerate → None (§4 统一规则) --------------------------------

def test_empty_both_sides_none():
    r = compute("<style></style>", "<style></style>")
    assert r["s"] is None
    assert r["reason"] == "empty-distribution"


def test_no_style_block_none():
    r = compute("<html><body><div>hi</div></body></html>",
                "<style>.a{color:red}</style>")
    assert r["s"] is None  # first card has no CSS at all


def test_empty_one_side_none():
    non_empty = "<style>.a{color:red}</style>"
    assert compute(non_empty, "<style></style>")["s"] is None
    assert compute("<style></style>", non_empty)["s"] is None


# --- symmetry & determinism -------------------------------------------------

def test_symmetry():
    a = "<style>.x{color:red;color:blue;margin:0}</style>"
    b = "<style>.y{color:red;padding:1px}</style>"
    assert compute(a, b)["s"] == compute(b, a)["s"]


def test_determinism_repeat_call():
    a = "<style>.x{color:red;margin:0}</style>"
    b = "<style>.y{color:blue;padding:1px;gap:2px}</style>"
    first = compute(a, b)["s"]
    for _ in range(5):
        assert compute(a, b)["s"] == first


def test_range_bounds():
    a = "<style>.a{color:red;margin:0;padding:1px}</style>"
    b = "<style>.b{color:blue;gap:2px}</style>"
    s = compute(a, b)["s"]
    assert 0.0 <= s <= 1.0


# --- real devset smoke ------------------------------------------------------

def test_real_devset_cards_smoke():
    import pathlib
    import pytest

    # Repo-relative: tests/ -> similarity/ -> runner/ -> repo root. This is a
    # dev-only fixture living under the gitignored data/batches-dev; skip when
    # absent (e.g. a fresh clone of the public repo).
    repo = pathlib.Path(__file__).resolve().parents[3]
    cards_dir = repo / "data" / "batches-dev" / "devset-42" / "cards"
    if not cards_dir.is_dir():
        pytest.skip("dev-only fixture data/batches-dev/devset-42 not present")
    slugs = sorted(p.name for p in cards_dir.iterdir() if (p / "card.html").exists())
    assert len(slugs) >= 2
    a_dir = cards_dir / slugs[0]
    b_dir = cards_dir / slugs[1]

    r_ab = compute(str(a_dir), str(b_dir))
    r_ba = compute(str(b_dir), str(a_dir))
    assert r_ab["s"] == r_ba["s"]                                # symmetry
    assert r_ab["s"] == compute(str(a_dir), str(b_dir))["s"]     # determinism
    assert r_ab["s"] is None or 0.0 <= r_ab["s"] <= 1.0
    assert compute(str(a_dir), str(a_dir))["s"] == pytest.approx(1.0)  # self → 1
    assert r_ab["extractor_version"] == c_css_prop.EXTRACTOR_VERSION
    assert r_ab["postcss_version"] == c_css_prop.POSTCSS_VERSION
