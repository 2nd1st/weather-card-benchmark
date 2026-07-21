"""Golden micro-fixture tests for the c-ncd similarity channel (scheme §4 / 附录 A).

Expected values are computed by an INDEPENDENT reference (raw ``lzma.compress``
calls + the appendix-A formula applied by hand here), never by the code under
test — per §4 "golden fixtures 期望值独立预先写定".
"""

from __future__ import annotations

import lzma
import unicodedata

import pytest

from runner.similarity import c_ncd


# ---- independent reference implementation of the 附录 A formula ---------------

def _C(data: bytes) -> int:
    return len(lzma.compress(data, format=lzma.FORMAT_XZ, preset=6))


def _ref(html_a: str | None, html_b: str | None):
    """Independent reference → (s, s_unclamped, ncd_sym) or (None, None, None)."""
    xa = unicodedata.normalize("NFC", html_a).encode("utf-8") if html_a else b""
    xb = unicodedata.normalize("NFC", html_b).encode("utf-8") if html_b else b""
    if not xa or not xb:
        return (None, None, None)
    cx, cy = _C(xa), _C(xb)
    cxy, cyx = _C(xa + xb), _C(xb + xa)
    cmin, cmax = min(cx, cy), max(cx, cy)
    ncd_xy = (cxy - cmin) / cmax
    ncd_yx = (cyx - cmin) / cmax
    ncd_sym = (ncd_xy + ncd_yx) / 2.0
    s_unclamped = 1.0 - ncd_sym
    s = 1.0 - min(1.0, max(0.0, ncd_sym))
    return (s, s_unclamped, ncd_sym)


# Deterministic small fixtures.
_HTML_A = "<html><body><h1>Sunny</h1><p>Temp 27.4</p></body></html>"
_HTML_B = "<html><body><h1>Rainy</h1><p>Temp 13.9</p></body></html>"
_HTML_HIGHLY_COMPRESSIBLE = "<div>" + ("a" * 4000) + "</div>"


# ---- golden: formula wiring matches independent reference --------------------

def test_golden_matches_independent_reference():
    got = c_ncd.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    s_ref, unclamped_ref, ncd_ref = _ref(_HTML_A, _HTML_B)
    assert got["s"] == pytest.approx(s_ref, abs=0.0)
    assert got["s_unclamped"] == pytest.approx(unclamped_ref, abs=0.0)
    assert got["ncd_sym"] == pytest.approx(ncd_ref, abs=0.0)
    # sanity: two different tiny docs are dissimilar-ish, s well inside [0,1]
    assert 0.0 <= got["s"] <= 1.0


def test_golden_diagnostic_fields_present():
    got = c_ncd.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    for k in ("c_x", "c_y", "c_xy", "c_yx", "len_x", "len_y"):
        assert isinstance(got[k], int) and got[k] > 0
    assert got["null_reason"] is None


# ---- identity → channel-appropriate max --------------------------------------

def test_identity_is_max_similarity():
    same = c_ncd.compute({"card_html": _HTML_A}, {"card_html": _HTML_A})
    diff = c_ncd.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    # NCD of identical inputs is ~0 (not exactly, real compressor) → s near 1,
    # and is the maximum: self-similarity strictly exceeds cross-similarity here.
    assert same["s"] == pytest.approx(_ref(_HTML_A, _HTML_A)[0], abs=0.0)
    assert same["s"] > diff["s"]
    assert same["s"] <= 1.0


# ---- symmetry ----------------------------------------------------------------

def test_symmetry():
    ab = c_ncd.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    ba = c_ncd.compute({"card_html": _HTML_B}, {"card_html": _HTML_A})
    assert ab["s"] == ba["s"]
    assert ab["s_unclamped"] == ba["s_unclamped"]
    assert ab["ncd_sym"] == ba["ncd_sym"]


# ---- determinism -------------------------------------------------------------

def test_determinism_repeat_call():
    r1 = c_ncd.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    r2 = c_ncd.compute({"card_html": _HTML_A}, {"card_html": _HTML_B})
    assert r1 == r2


# ---- empty / degenerate → None -----------------------------------------------

@pytest.mark.parametrize(
    "a,b",
    [
        ({"card_html": ""}, {"card_html": _HTML_A}),   # one empty
        ({"card_html": _HTML_A}, {"card_html": ""}),   # other empty
        ({"card_html": ""}, {"card_html": ""}),        # double empty
        ({"card_html": None}, {"card_html": _HTML_A}), # absent html
        (None, {"card_html": _HTML_A}),                # absent artifact
    ],
)
def test_empty_degenerate_is_none(a, b):
    got = c_ncd.compute(a, b)
    assert got["s"] is None
    assert got["s_unclamped"] is None
    assert got["ncd_sym"] is None
    assert got["null_reason"] == "empty-input"


# ---- unclamped can go negative (diagnostic semantics) ------------------------

def test_unclamped_semantics_stay_consistent():
    # For any pair, s == 1 - clamp(ncd_sym,0,1) and s_unclamped == 1 - ncd_sym.
    got = c_ncd.compute(
        {"card_html": _HTML_HIGHLY_COMPRESSIBLE}, {"card_html": _HTML_B}
    )
    ncd = got["ncd_sym"]
    assert got["s"] == pytest.approx(1.0 - min(1.0, max(0.0, ncd)), abs=0.0)
    assert got["s_unclamped"] == pytest.approx(1.0 - ncd, abs=0.0)
    assert 0.0 <= got["s"] <= 1.0


# ---- path-based artifact resolution ------------------------------------------

def test_path_artifact_resolution(tmp_path):
    d = tmp_path / "some-card"
    d.mkdir()
    (d / "card.html").write_text(_HTML_A, encoding="utf-8")
    via_dir = c_ncd.compute(d, {"card_html": _HTML_A})
    via_file = c_ncd.compute(str(d / "card.html"), {"card_html": _HTML_A})
    inline = c_ncd.compute({"card_html": _HTML_A}, {"card_html": _HTML_A})
    assert via_dir["s"] == inline["s"] == via_file["s"]
    # missing card.html dir → None html → empty-input null
    empty_dir = tmp_path / "empty-card"
    empty_dir.mkdir()
    assert c_ncd.compute(empty_dir, {"card_html": _HTML_A})["s"] is None
