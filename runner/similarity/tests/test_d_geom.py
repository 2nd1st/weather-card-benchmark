"""Golden micro-fixtures for channel **d-geom** (scheme §4, appendix A line 217).

Expected values are hand-computed here from the appendix-A formula
(occupancy o(cell)=min(1, Σ area(rect∩cell)/1600); weighted IoU =
Σ min(o₁,o₂) / Σ max(o₁,o₂)), NOT produced by the code under test.
"""

from __future__ import annotations

import pytest

from runner.similarity import d_geom


# --------------------------------------------------------------------------
# tiny synthetic dom-dump builder
# --------------------------------------------------------------------------
def _dom(*boxes, **overrides):
    """Build a minimal dom dump.

    Each *box* is either (x, y, w, h) → a visible element, or a dict with
    explicit ``box`` / ``style`` / ``type`` to exercise the visibility gate.
    """
    nodes = []
    for i, b in enumerate(boxes):
        if isinstance(b, dict):
            node = {"preorder": i, "type": b.get("type", "element")}
            if "box" in b:
                node["box"] = b["box"]
            if "style" in b:
                node["style"] = b["style"]
            nodes.append(node)
            continue
        x, y, w, h = b
        nodes.append(
            {
                "preorder": i,
                "type": "element",
                "box": {"x": x, "y": y, "width": w, "height": h},
                "style": {"display": "block", "visibility": "visible", "opacity": 1.0},
            }
        )
    d = {"nodes": nodes, "viewport": {"width": [1280, 800]}}
    d.update(overrides)
    return d


# --------------------------------------------------------------------------
# occupancy_grid: hand-checked geometry
# --------------------------------------------------------------------------
def test_single_full_cell_occupancy():
    # a 40×40 box exactly on cell (col=0,row=0) → o=1.0 there, 0 elsewhere.
    grid = d_geom.occupancy_grid(_dom((0, 0, 40, 40)))
    assert len(grid) == 32 * 20
    assert grid[0] == 1.0
    assert sum(grid) == 1.0  # only one occupied cell


def test_half_cell_occupancy():
    # 40×20 box covers the top half of cell(0,0): area 800 / 1600 = 0.5
    grid = d_geom.occupancy_grid(_dom((0, 0, 40, 20)))
    assert grid[0] == pytest.approx(0.5)
    assert sum(grid) == pytest.approx(0.5)


def test_box_spanning_two_cells():
    # 60×40 box from x=0: cell0 fully (40 wide), cell1 half (20 wide, area 800→0.5)
    grid = d_geom.occupancy_grid(_dom((0, 0, 60, 40)))
    assert grid[0] == 1.0            # col0,row0
    assert grid[1] == pytest.approx(0.5)  # col1,row0
    assert sum(grid) == pytest.approx(1.5)


def test_overlapping_elements_sum_then_cap():
    # two identical half-cell boxes on the same cell → raw 0.5+0.5=1.0, capped 1.0
    grid = d_geom.occupancy_grid(_dom((0, 0, 40, 20), (0, 0, 40, 20)))
    assert grid[0] == 1.0
    # three quarter-cell boxes would exceed 1 → still capped at 1
    grid2 = d_geom.occupancy_grid(_dom((0, 0, 40, 30), (0, 0, 40, 30)))
    assert grid2[0] == 1.0  # raw 0.75+0.75=1.5 → cap 1.0


def test_box_clipped_to_viewport():
    # box extends past right/bottom edges → only the in-viewport part counts.
    # x=1260 w=100 → clipped to [1260,1280] width 20; y=780 h=100 → [780,800] h20
    # falls in last cell col31,row19; area 20*20=400 → 0.25
    grid = d_geom.occupancy_grid(_dom((1260, 780, 100, 100)))
    idx = 19 * 32 + 31
    assert grid[idx] == pytest.approx(0.25)
    assert sum(grid) == pytest.approx(0.25)


def test_offscreen_box_is_zero():
    # entirely left of viewport → no occupancy
    grid = d_geom.occupancy_grid(_dom((-200, 0, 100, 100)))
    assert sum(grid) == 0.0


# --------------------------------------------------------------------------
# visibility gate
# --------------------------------------------------------------------------
def test_visibility_gate_excludes_hidden():
    hidden_display = {"box": {"x": 0, "y": 0, "width": 40, "height": 40},
                      "style": {"display": "none", "visibility": "visible", "opacity": 1.0}}
    hidden_vis = {"box": {"x": 40, "y": 0, "width": 40, "height": 40},
                  "style": {"display": "block", "visibility": "hidden", "opacity": 1.0}}
    low_opacity = {"box": {"x": 80, "y": 0, "width": 40, "height": 40},
                   "style": {"display": "block", "visibility": "visible", "opacity": 0.05}}
    text_node = {"type": "text", "box": {"x": 120, "y": 0, "width": 40, "height": 40}}
    grid = d_geom.occupancy_grid(_dom(hidden_display, hidden_vis, low_opacity, text_node))
    assert sum(grid) == 0.0  # every candidate rejected


def test_opacity_boundary_strict():
    # opacity == 0.05 rejected (strict >), opacity 0.051 accepted.
    at = {"box": {"x": 0, "y": 0, "width": 40, "height": 40},
          "style": {"display": "block", "visibility": "visible", "opacity": 0.05}}
    just_above = {"box": {"x": 0, "y": 0, "width": 40, "height": 40},
                  "style": {"display": "block", "visibility": "visible", "opacity": 0.051}}
    assert sum(d_geom.occupancy_grid(_dom(at))) == 0.0
    assert d_geom.occupancy_grid(_dom(just_above))[0] == 1.0


# --------------------------------------------------------------------------
# compute(): identity / null / symmetry / determinism / weighted-IoU value
# --------------------------------------------------------------------------
def test_identity_pair_is_one():
    dom = _dom((0, 0, 100, 100), (200, 200, 40, 40))
    r = d_geom.compute(dom, dom)
    assert r["s"] == 1.0
    assert r["zero_a"] is False and r["zero_b"] is False


def test_empty_side_is_null():
    empty = _dom()  # no nodes
    nonempty = _dom((0, 0, 40, 40))
    assert d_geom.compute(empty, nonempty)["s"] is None
    assert d_geom.compute(nonempty, empty)["s"] is None
    assert d_geom.compute(empty, empty)["s"] is None  # double-empty also null


def test_all_hidden_side_is_null():
    hidden = _dom({"box": {"x": 0, "y": 0, "width": 40, "height": 40},
                   "style": {"display": "none", "visibility": "visible", "opacity": 1.0}})
    nonempty = _dom((0, 0, 40, 40))
    assert d_geom.compute(hidden, nonempty)["s"] is None


def test_symmetry():
    a = _dom((0, 0, 80, 40))
    b = _dom((0, 0, 40, 40))
    assert d_geom.compute(a, b)["s"] == d_geom.compute(b, a)["s"]


def test_determinism_repeat_call():
    a = _dom((0, 0, 120, 80), (400, 300, 60, 60))
    b = _dom((0, 0, 40, 40), (400, 300, 90, 40))
    r1 = d_geom.compute(a, b)["s"]
    r2 = d_geom.compute(a, b)["s"]
    assert r1 == r2


def test_weighted_iou_hand_computed():
    # A: one full cell(0,0) o=1 ; B: two half-cell boxes → cell0 o=1 (0.5+0.5),
    # wait — keep it simple & disjoint-checkable:
    # A occupies cell(0,0) fully (o1=1) and nothing else.
    # B occupies cell(0,0) at 0.5 and cell(1,0) at 1.0.
    #   cell0: min(1,0.5)=0.5  max(1,0.5)=1.0
    #   cell1: min(0,1)=0      max(0,1)=1.0
    #   all others 0.
    # weighted IoU = (0.5+0) / (1.0+1.0) = 0.25
    a = _dom((0, 0, 40, 40))                 # cell0 full
    b = _dom((0, 0, 40, 20), (40, 0, 40, 40))  # cell0 half + cell1 full
    r = d_geom.compute(a, b)
    assert r["s"] == pytest.approx(0.25)
    assert r["sum_min"] == pytest.approx(0.5)
    assert r["sum_max"] == pytest.approx(2.0)


def test_partial_overlap_value():
    # A: cell0 full (o=1). B: cell0 full (o=1) + cell1 full (o=1).
    #   cell0 min=1 max=1 ; cell1 min=0 max=1 → IoU = 1/2 = 0.5
    a = _dom((0, 0, 40, 40))
    b = _dom((0, 0, 80, 40))
    r = d_geom.compute(a, b)
    assert r["s"] == pytest.approx(0.5)


def test_disjoint_regions_zero_similarity():
    # A occupies cell(0,0); B occupies cell(5,5): no shared cell → IoU 0 (defined, not null)
    a = _dom((0, 0, 40, 40))
    b = _dom((200, 200, 40, 40))
    r = d_geom.compute(a, b)
    assert r["s"] == 0.0
    assert r["s"] is not None


# --------------------------------------------------------------------------
# v12.2 full-viewport exclusion (appendix A d-geom row): elements whose
# viewport-clipped box covers ≥ 0.99 × viewport area do NOT participate in
# occupancy (html/body / full-bleed background wrappers).
# --------------------------------------------------------------------------
def test_full_viewport_element_excluded():
    # a full 1280×800 element (html/body) contributes nothing → all-zero grid.
    grid = d_geom.occupancy_grid(_dom((0, 0, 1280, 800)))
    assert sum(grid) == 0.0


def test_full_viewport_exclusion_boundary_inclusive():
    # area == 0.99×viewport (1280×792 = 1013760) is EXCLUDED (predicate is ≥).
    at = d_geom.occupancy_grid(_dom((0, 0, 1280, 792)))
    assert sum(at) == 0.0
    # one row shorter (1280×791 = 1012480 < threshold) → INCLUDED, occupies cells.
    below = d_geom.occupancy_grid(_dom((0, 0, 1280, 791)))
    assert sum(below) > 0.0


def test_full_viewport_clipped_area_counts():
    # exclusion uses the VIEWPORT-CLIPPED area, not the raw box: a huge box that
    # overflows the viewport still clips to 1280×800 ≥ threshold → excluded.
    grid = d_geom.occupancy_grid(_dom((-100, -100, 3000, 3000)))
    assert sum(grid) == 0.0


def test_full_bleed_wrapper_excluded_content_retained():
    # html + body + full-bleed wrapper (all viewport-filling) are excluded; only
    # the inner content box drives occupancy → the grid is NOT saturated.
    dom = _dom((0, 0, 1280, 800), (0, 0, 1280, 800), (0, 0, 1280, 800),
               (120, 120, 40, 40))  # cell-aligned inner content (col3,row3)
    grid = d_geom.occupancy_grid(dom)
    occupied = sum(1 for o in grid if o > 0.0)
    assert occupied == 1  # only the inner 40×40 content cell


def test_v122_recovers_discriminating_signal():
    # Two cards, each = full-bleed wrapper + distinct content. Pre-v12.2 both
    # grids were saturated (all 640 cells = 1.0) → S ≡ 1.0. With the exclusion
    # the wrappers drop out and the differing content yields S < 1.0.
    a = _dom((0, 0, 1280, 800), (0, 0, 40, 40))       # content top-left
    b = _dom((0, 0, 1280, 800), (400, 400, 40, 40))   # content elsewhere
    r = d_geom.compute(a, b)
    assert r["s"] is not None
    assert r["s"] < 1.0
    assert r["occupied_cells_a"] == 1 and r["occupied_cells_b"] == 1


def test_only_full_viewport_element_is_null():
    # a card whose only visible element is viewport-filling → all-zero after
    # exclusion → S=null (double-zero rule).
    only_wrapper = _dom((0, 0, 1280, 800))
    content = _dom((100, 100, 40, 40))
    assert d_geom.compute(only_wrapper, content)["s"] is None
    assert d_geom.compute(only_wrapper, only_wrapper)["s"] is None
