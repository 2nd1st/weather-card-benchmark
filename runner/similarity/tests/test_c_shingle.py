"""Golden micro-fixtures for channel c-shingle (scheme §4, appendix A row).

Appendix-A def: normalize = remove-comments + collapse-whitespace + lowercase;
k=5 character shingle; Jaccard. Empty shingle set on either side → s = None.

Hand-computed goldens use tiny synthetic strings so the shingle sets and the
Jaccard ratio can be verified by inspection.
"""

import pytest

from runner.similarity import c_shingle as cs


# --------------------------------------------------------------------------
# normalization: comment stripping (the FLAGGED grammar)
# --------------------------------------------------------------------------
def test_strip_html_comment():
    assert cs.strip_comments("a<!-- hi -->b") == "ab"


def test_strip_block_comment():
    assert cs.strip_comments("a/* hi */b") == "ab"


def test_strip_line_comment_to_eol():
    assert cs.strip_comments("a// hi\nb") == "a\nb"  # newline kept


def test_line_comment_guard_protects_url_scheme():
    # `://` must NOT be treated as a line comment
    assert cs.strip_comments("https://x.com/p") == "https://x.com/p"


def test_unterminated_html_comment_runs_to_end():
    assert cs.strip_comments("a<!-- oops") == "a"


def test_unterminated_block_comment_runs_to_end():
    assert cs.strip_comments("a/* oops") == "a"


def test_slash_not_comment_is_kept():
    # single slash, or division-like, is preserved
    assert cs.strip_comments("a/b") == "a/b"


# --------------------------------------------------------------------------
# normalization: whitespace + lowercase
# --------------------------------------------------------------------------
def test_normalize_collapses_ws_and_lowercases():
    assert cs.normalize("  <A>\t\n  B  ") == "<a> b"


def test_normalize_strips_comment_then_ws():
    assert cs.normalize("X <!-- c -->   Y") == "x y"


def test_normalize_empty_inputs():
    assert cs.normalize(None) == ""
    assert cs.normalize("") == ""


# --------------------------------------------------------------------------
# shingles
# --------------------------------------------------------------------------
def test_shingles_count():
    # "abcdef" (len 6), k=5 → shingles {"abcde","bcdef"}
    assert cs.shingles("abcdef") == {"abcde", "bcdef"}


def test_shingles_shorter_than_k_is_empty():
    assert cs.shingles("abcd") == set()  # len 4 < 5
    assert cs.shingles("") == set()


def test_shingles_exact_k_single():
    assert cs.shingles("abcde") == {"abcde"}


# --------------------------------------------------------------------------
# Jaccard — hand-computed
# --------------------------------------------------------------------------
def test_jaccard_hand_computed():
    # A="abcdef" → {abcde,bcdef}; B="abcdeg" → {abcde,bcdeg}
    # ∩ = {abcde} (1) ; ∪ = {abcde,bcdef,bcdeg} (3) ; J = 1/3
    a = cs.shingles("abcdef")
    b = cs.shingles("abcdeg")
    assert cs._jaccard(a, b) == pytest.approx(1 / 3)


def test_jaccard_disjoint_is_zero():
    a = cs.shingles("aaaaa")   # {"aaaaa"}
    b = cs.shingles("bbbbb")   # {"bbbbb"}
    assert cs._jaccard(a, b) == 0.0


def test_jaccard_identical_is_one():
    a = cs.shingles("abcdef")
    assert cs._jaccard(a, a) == 1.0


def test_jaccard_empty_side_is_none():
    a = cs.shingles("abcde")
    assert cs._jaccard(a, set()) is None
    assert cs._jaccard(set(), a) is None
    assert cs._jaccard(set(), set()) is None  # double-empty


# --------------------------------------------------------------------------
# channel-level compute() — identity / empty / symmetry / determinism
# --------------------------------------------------------------------------
_HTML = {"card_html": "<div class='card'>Hello World, temperature 21°C</div>"}


def test_identity_is_one():
    r = cs.compute(_HTML, _HTML)
    assert r["s"] == 1.0
    assert r["intersection"] == r["union"] == r["n_shingles_a"]


def test_empty_html_is_none():
    empty = {"card_html": None}
    other = _HTML
    assert cs.compute(empty, other)["s"] is None
    assert cs.compute(other, empty)["s"] is None
    assert cs.compute(empty, empty)["s"] is None  # double-empty → None


def test_short_html_below_k_is_none():
    # normalized text shorter than k=5 → empty shingle set → None
    tiny = {"card_html": "<a>"}          # normalized "<a>" len 3 < 5
    assert cs.compute(tiny, _HTML)["s"] is None
    assert cs.compute(tiny, tiny)["s"] is None


def test_symmetry():
    a = {"card_html": "<div>Alpha 12°</div>"}
    b = {"card_html": "<span>Beta 34°</span>"}
    assert cs.compute(a, b)["s"] == cs.compute(b, a)["s"]


def test_determinism_repeat_call():
    a = {"card_html": "<div>Alpha 12°</div>"}
    b = {"card_html": "<span>Beta 34°</span>"}
    assert cs.compute(a, b)["s"] == cs.compute(a, b)["s"]


def test_s_in_unit_interval():
    a = {"card_html": "<div>Alpha 12°</div>"}
    b = {"card_html": "<span>Beta 34°</span>"}
    s = cs.compute(a, b)["s"]
    assert 0.0 <= s <= 1.0


def test_compute_hand_computed_jaccard():
    # normalized A = "abcdef", B = "abcdeg" → J = 1/3 (see jaccard golden)
    a = {"card_html": "abcdef"}
    b = {"card_html": "abcdeg"}
    r = cs.compute(a, b)
    assert r["s"] == pytest.approx(1 / 3)
    assert r["intersection"] == 1
    assert r["union"] == 3


def test_comment_only_difference_is_one():
    # HTML that differs ONLY inside a comment normalizes identically → s=1
    a = {"card_html": "<div>weather 21</div><!-- author: alice -->"}
    b = {"card_html": "<div>weather 21</div><!-- author: bob -->"}
    assert cs.compute(a, b)["s"] == 1.0


def test_diagnostics_present():
    r = cs.compute(_HTML, {"card_html": "<div>other 99°C</div>"})
    assert r["channel"] == "c-shingle"
    assert r["k"] == 5
    assert r["empty_a"] is False and r["empty_b"] is False
    assert r["null_reason"] is None


def test_null_reason_labels():
    empty = {"card_html": None}
    assert cs.compute(empty, _HTML)["null_reason"] == "empty-shingle-set-a"
    assert cs.compute(_HTML, empty)["null_reason"] == "empty-shingle-set-b"
    assert cs.compute(empty, empty)["null_reason"] == "empty-shingle-set-both"


def test_reads_from_directory_path(tmp_path):
    d = tmp_path / "cardX"
    d.mkdir()
    (d / "card.html").write_text("<div>hello world 12°C</div>", encoding="utf-8")
    r = cs.compute(str(d), str(d))
    assert r["s"] == 1.0


def test_missing_card_dir_is_none(tmp_path):
    d = tmp_path / "empty_card"
    d.mkdir()  # no card.html inside
    assert cs.compute(str(d), _HTML)["s"] is None
