"""Golden micro-fixtures for the c-feature channel (scheme §4 / appendix A).

Expected values are hand-computed here (NOT produced by the channel under test),
per §4 "golden fixtures 期望值独立预先写定".
"""

from runner.similarity import c_feature
from runner.similarity.c_feature import compute, extract_features


# --- feature extraction unit checks (hand-enumerated) -----------------------

def test_extract_families_hand_enumerated():
    html = (
        '<html><head><style>'
        '.card { color: red; padding: 4px; }'
        'a:hover { color: blue; }'          # pseudo-class, NOT a "a" property
        '@media (max-width: 700px) { .card { margin: 0; } }'  # media feature ignored
        '</style></head>'
        '<body>'
        '<div class="card big" style="display: flex; gap: 2px">'
        '<span class="big">hi</span>'
        '</div>'
        '<script>document.querySelector(".card"); const x = 1;</script>'
        '</body></html>'
    )
    feats = extract_features(html)

    # tags: html, head, style, body, div, span, script
    tags = {f for f in feats if f.startswith("tag:")}
    assert tags == {
        "tag:html", "tag:head", "tag:style", "tag:body",
        "tag:div", "tag:span", "tag:script",
    }

    # css props: color, padding (from .card), color (a:hover body IS a decl body),
    # margin (inside @media body), display + gap (inline). Pseudo/media features
    # must NOT appear as props.
    css = {f for f in feats if f.startswith("css:")}
    assert css == {"css:color", "css:padding", "css:margin", "css:display", "css:gap"}
    assert "css:a" not in feats            # a:hover selector not a property
    assert "css:max-width" not in feats    # @media feature not a property

    # class tokens (verbatim, whitespace-split, deduped): card, big
    classes = {f for f in feats if f.startswith("class:")}
    assert classes == {"class:card", "class:big"}

    # jsapi from vocabulary present in script: document, querySelector
    jsapi = {f for f in feats if f.startswith("jsapi:")}
    assert jsapi == {"jsapi:document", "jsapi:querySelector"}


def test_custom_properties_kept():
    html = "<style>:root { --sky-top: #fff; color-scheme: dark; }</style>"
    feats = extract_features(html)
    assert "css:--sky-top" in feats
    assert "css:color-scheme" in feats


def test_case_normalization():
    # tags + css props lowercased; class tokens verbatim; jsapi case-sensitive.
    html = (
        '<DIV class="Card CARD" style="COLOR: red">'
        '<script>fetch("x"); Fetch;</script></DIV>'
    )
    feats = extract_features(html)
    assert "tag:div" in feats
    assert "css:color" in feats
    assert "class:Card" in feats and "class:CARD" in feats  # distinct, verbatim
    assert "jsapi:fetch" in feats                            # lowercase matched
    # "Fetch" is not in vocab (case-sensitive); only "fetch" token present anyway


# --- Jaccard golden pairs (hand-computed s) ---------------------------------

def _html(tags_body: str) -> str:
    return f"<html><body>{tags_body}</body></html>"


def test_identity_pair_s_is_one():
    html = _html('<div class="x" style="color:red"><script>fetch(1)</script></div>')
    r = compute(html, html)
    assert r["s"] == 1.0


def test_hand_computed_jaccard():
    # A: tags {html,body,div,span}, class {a}
    a = _html('<div class="a"></div><span></span>')
    # B: tags {html,body,div,p}, class {a,b}
    b = _html('<div class="a b"></div><p></p>')
    # A feats: tag:html,tag:body,tag:div,tag:span, class:a          -> 5
    # B feats: tag:html,tag:body,tag:div,tag:p,   class:a, class:b  -> 6
    # inter: tag:html,tag:body,tag:div, class:a                     -> 4
    # union: {html,body,div,span,p}=5 tags + {a,b}=2 classes        -> 7
    # jaccard = 4/7
    r = compute(a, b)
    assert r["s"] == 4 / 7
    assert r["intersection"] == 4
    assert r["union"] == 7


def test_disjoint_pair_zero():
    # Force fully disjoint namespaced sets via different tags & classes.
    a = "<section class='alpha'></section>"   # tag:section? plus implied? use raw
    b = "<article class='beta'></article>"
    # extract_features parses fragments; tags: {section} vs {article}
    fa = extract_features(a)
    fb = extract_features(b)
    assert fa.isdisjoint(fb)
    r = compute(a, b)
    assert r["s"] == 0.0


# --- empty / degenerate → None (§4 统一规则) --------------------------------

def test_empty_both_sides_none():
    r = compute("", "")
    assert r["s"] is None
    assert r["reason"] == "empty-feature-set"


def test_empty_one_side_none():
    non_empty = _html("<div></div>")
    r = compute(non_empty, "")
    assert r["s"] is None
    # double-empty AND single-empty both null (differs from legacy 1.0 on double)
    assert compute("", non_empty)["s"] is None


# --- symmetry & determinism -------------------------------------------------

def test_symmetry():
    a = _html('<div class="a"></div><span></span>')
    b = _html('<div class="a b"></div><p></p>')
    assert compute(a, b)["s"] == compute(b, a)["s"]


def test_determinism_repeat_call():
    a = _html('<div class="a" style="color:red"></div>')
    b = _html('<div class="a b" style="color:blue;margin:0"></div>')
    first = compute(a, b)["s"]
    for _ in range(5):
        assert compute(a, b)["s"] == first


def test_range_bounds():
    a = _html('<div class="a"></div>')
    b = _html('<div class="a b"></div><p></p>')
    s = compute(a, b)["s"]
    assert 0.0 <= s <= 1.0


# --- real devset smoke: symmetric, deterministic, in-range or None ----------

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
    assert r_ab["s"] == r_ba["s"]                 # symmetry
    assert r_ab["s"] == compute(str(a_dir), str(b_dir))["s"]  # determinism
    assert r_ab["s"] is None or 0.0 <= r_ab["s"] <= 1.0
    # self pair on a real card → 1.0
    assert compute(str(a_dir), str(a_dir))["s"] == 1.0
    assert r_ab["extractor_version"] == c_feature.EXTRACTOR_VERSION
