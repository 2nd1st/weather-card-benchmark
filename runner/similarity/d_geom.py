"""Channel **d-geom** — weighted-IoU over element occupancy (scheme §4, appendix A).

Appendix-A definition (byte-exact, implemented verbatim, line 217, **v12.2**):

    d-geom | 可见元素(rect∩viewport ∧ display/visibility 非隐藏 ∧ opacity>0.05);
            **满幅排除(v12.2 修正)**:元素与视口裁剪后盒面积 ≥ 0.99 × 视口面积
            (1280×800 下即 ≥1013760 px²)者不参与占格——含 html/body 与满幅背景
            包装元素;
            网格 32×20(cell=40×40 px on 1280×800);
            占用 o(cell)=min(1, Σ_elem area(rect∩cell)/area(cell));
            **weighted IoU = Σ_cell min(o₁,o₂) / Σ_cell max(o₁,o₂)**;
            双侧全零 → S=null

v12.2 amendment (implementation-time fix, first formal-batch lock): the §11 audit
found the original predicate degenerate — ``<html>``/``<body>`` and full-bleed
background wrappers each deposit ≥1600 px into *every* cell, saturating all 640
cells on every card → weighted IoU ≡ 1.0 for all pairs (zero variance). The LAW
(appendix A d-geom row) was amended to EXCLUDE any element whose viewport-clipped
box covers ≥ 99 % of the viewport area from the occupancy accumulation; on the
dev set this recovers a discriminating 0.74–0.85 band.

Inputs: ``dom.json`` (the frozen main-capture DOM dump, ``captured_at_virtual_ms``
== t_v == 5000). Each element node carries ``box`` = {x, y, width, height} in
1280×800 viewport CSS-px coordinates and ``style`` = {display, visibility,
opacity, font_size}.

Visibility predicate (appendix A d-geom row, implemented from box+style directly,
NOT from the dump's precomputed ``element_visible`` — the LAW pins the opacity
threshold at > 0.05 and the exact predicate, so we evaluate it verbatim):
  * type == "element" (text nodes have no box),
  * ``display`` != "none",
  * ``visibility`` not in {"hidden", "collapse"},
  * ``opacity`` > 0.05  (strict, per LAW; element's own computed opacity),
  * rect ∩ viewport has positive area (rect∩viewport).

Occupancy grid (32 cols × 20 rows, cell = 40×40 px):
  cell (col, row) covers x∈[col·40,(col+1)·40), y∈[row·40,(row+1)·40).
  For each visible element, its box is first clipped to the viewport [0,1280]×
  [0,800], then its per-cell overlap AREA is accumulated (overlapping elements
  SUM — a cell covered twice reaches raw 2·1600 before capping). Finally
  o(cell) = min(1, Σ_elem area(rect∩cell) / 1600).

Similarity: weighted IoU = Σ_cell min(o₁,o₂) / Σ_cell max(o₁,o₂). Identical
occupancy → 1. min/max are symmetric ⇒ s(a,b) == s(b,a) exactly.

ZERO-VECTOR / NULL RULE (scheme §4 line 86 + unified table line 225 + pairs-schema
``sValue``): if EITHER side's grid is all-zero (no visible element contributes any
positive area) → ``s = None`` (double-zero included). This subsumes the d-geom
row's narrower "双侧全零 → S=null" (which only spells out the 0/0 case that would
otherwise be undefined). See report — the row wording is FLAGGED, resolved toward
the global "任一侧 → null" rule that §4, the unified table, the pairs schema, the
task instruction ("Zero-vector/empty → s=None per §4"), and the sibling channel
(v-layout, package __init__) all state.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# --- appendix-A constants (d-geom row + viewport) -------------------------
VIEWPORT_W = 1280            # logical viewport width  (scheme §0.4, appendix A)
VIEWPORT_H = 800            # logical viewport height
GRID_COLS = 32              # 32 columns  (1280 / 40)
GRID_ROWS = 20             # 20 rows     (800 / 40)
CELL_PX = 40                # cell = 40×40 px
CELL_AREA = float(CELL_PX * CELL_PX)   # 1600.0
OPACITY_MIN = 0.05          # visible ⇔ opacity > 0.05  (strict)
HIDDEN_VISIBILITY = frozenset({"hidden", "collapse"})
DOM_FILENAME = "dom.json"

# v12.2 full-viewport exclusion — an element whose viewport-clipped box area is
# ≥ 0.99 × viewport area does NOT participate in occupancy (html/body/full-bleed
# background wrappers). 0.99 × 1280 × 800 = 1013760.0 px² exactly (appendix A).
VIEWPORT_AREA = float(VIEWPORT_W * VIEWPORT_H)   # 1024000.0
FULL_VIEWPORT_FRAC = 0.99
FULL_VIEWPORT_MIN_AREA = FULL_VIEWPORT_FRAC * VIEWPORT_AREA   # 1013760.0


# --------------------------------------------------------------------------
# artifact resolution
# --------------------------------------------------------------------------
def _dom(artifacts: Any) -> dict:
    """Resolve the parsed dom.json for a card.

    Accepts, in order of precedence:
      * a ``dict`` that already looks like a parsed dom dump (has "nodes"),
      * a ``dict`` with one of: ``dom`` (parsed dump), ``dom_path`` (file),
        ``dir`` / ``path`` (card directory containing dom.json),
      * a ``str`` / ``os.PathLike`` pointing at either dom.json directly or the
        card directory.
    """
    if isinstance(artifacts, dict):
        if "nodes" in artifacts:
            return artifacts
        if artifacts.get("dom") is not None:
            return artifacts["dom"]
        for key in ("dom_path", "dir", "path"):
            if artifacts.get(key) is not None:
                return _dom_from_path(Path(artifacts[key]))
        raise ValueError(
            "d-geom: artifacts dict lacks 'nodes'/'dom'/'dom_path'/'dir'/'path'"
        )

    if isinstance(artifacts, (str, os.PathLike)):
        return _dom_from_path(Path(artifacts))

    raise TypeError(f"d-geom: unsupported artifacts type {type(artifacts)!r}")


def _dom_from_path(p: Path) -> dict:
    if p.is_dir():
        p = p / DOM_FILENAME
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# visibility + occupancy
# --------------------------------------------------------------------------
def _is_visible(node: dict) -> bool:
    """Appendix-A d-geom visibility predicate, evaluated from box + style."""
    if node.get("type") != "element":
        return False
    box = node.get("box")
    if not box:
        return False
    style = node.get("style") or {}
    if style.get("display") == "none":
        return False
    if str(style.get("visibility", "visible")) in HIDDEN_VISIBILITY:
        return False
    try:
        opacity = float(style.get("opacity", 1.0))
    except (TypeError, ValueError):
        opacity = 1.0
    if not (opacity > OPACITY_MIN):
        return False
    # rect ∩ viewport with positive area
    x0, y0, x1, y1 = _clip_to_viewport(box)
    return (x1 - x0) > 0.0 and (y1 - y0) > 0.0


def _is_full_viewport(x0: float, y0: float, x1: float, y1: float) -> bool:
    """v12.2 exclusion: True iff the viewport-clipped box covers ≥ 0.99 of the
    viewport area (html/body/full-bleed wrappers) → excluded from occupancy."""
    return (x1 - x0) * (y1 - y0) >= FULL_VIEWPORT_MIN_AREA


def _clip_to_viewport(box: dict) -> tuple[float, float, float, float]:
    """Return the box clipped to [0,VIEWPORT_W]×[0,VIEWPORT_H] as (x0,y0,x1,y1)."""
    x = float(box.get("x", 0.0))
    y = float(box.get("y", 0.0))
    w = float(box.get("width", 0.0))
    h = float(box.get("height", 0.0))
    x0 = max(0.0, x)
    y0 = max(0.0, y)
    x1 = min(float(VIEWPORT_W), x + w)
    y1 = min(float(VIEWPORT_H), y + h)
    return x0, y0, x1, y1


def occupancy_grid(dom: dict) -> list[float]:
    """Build the 32×20 occupancy grid (row-major, len 640) from a dom dump.

    o(cell) = min(1, Σ_elem area(rect∩cell) / 1600). Element boxes are clipped to
    the viewport, and per-cell overlap areas from all visible elements SUM before
    the min(1, ·) cap. v12.2: elements whose clipped box covers ≥ 0.99 of the
    viewport (html/body/full-bleed wrappers) are excluded from the accumulation.
    """
    # raw accumulated intersection area per cell (before the min(1,·) cap)
    acc = [0.0] * (GRID_COLS * GRID_ROWS)
    for node in dom.get("nodes", []):
        if not _is_visible(node):
            continue
        x0, y0, x1, y1 = _clip_to_viewport(node["box"])
        if x1 <= x0 or y1 <= y0:
            continue
        # v12.2 full-viewport exclusion (appendix A d-geom row)
        if _is_full_viewport(x0, y0, x1, y1):
            continue
        col_lo = int(x0 // CELL_PX)
        col_hi = int((x1 - 1e-9) // CELL_PX)
        row_lo = int(y0 // CELL_PX)
        row_hi = int((y1 - 1e-9) // CELL_PX)
        col_lo = max(0, col_lo)
        col_hi = min(GRID_COLS - 1, col_hi)
        row_lo = max(0, row_lo)
        row_hi = min(GRID_ROWS - 1, row_hi)
        for row in range(row_lo, row_hi + 1):
            cell_y0 = row * CELL_PX
            cell_y1 = cell_y0 + CELL_PX
            oy = min(y1, cell_y1) - max(y0, cell_y0)
            if oy <= 0.0:
                continue
            base = row * GRID_COLS
            for col in range(col_lo, col_hi + 1):
                cell_x0 = col * CELL_PX
                cell_x1 = cell_x0 + CELL_PX
                ox = min(x1, cell_x1) - max(x0, cell_x0)
                if ox <= 0.0:
                    continue
                acc[base + col] += ox * oy
    # cap each cell at 1.0
    return [a / CELL_AREA if a < CELL_AREA else 1.0 for a in acc]


def _weighted_iou(grid_a: list[float], grid_b: list[float]):
    """Return ``(sum_min, sum_max)`` = (Σ_cell min(o₁,o₂), Σ_cell max(o₁,o₂)).

    The caller (``compute``) forms the weighted IoU ``sum_min / sum_max`` and
    applies the all-zero → ``None`` rule itself; this helper only accumulates the
    two sums (both symmetric in a,b, so s(a,b) == s(b,a) exactly)."""
    sum_min = 0.0
    sum_max = 0.0
    for oa, ob in zip(grid_a, grid_b):
        if oa < ob:
            sum_min += oa
            sum_max += ob
        else:
            sum_min += ob
            sum_max += oa
    return sum_min, sum_max


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """d-geom similarity (weighted IoU over element occupancy) between two cards.

    Returns ``{"s": float | None, ...diagnostics}``. ``s`` is None when either
    side's occupancy grid is all-zero (no visible element), per §4.
    """
    grid_a = occupancy_grid(_dom(card_a_artifacts))
    grid_b = occupancy_grid(_dom(card_b_artifacts))

    occ_a = sum(1 for o in grid_a if o > 0.0)
    occ_b = sum(1 for o in grid_b if o > 0.0)
    zero_a = occ_a == 0
    zero_b = occ_b == 0

    sum_min, sum_max = _weighted_iou(grid_a, grid_b)

    if zero_a or zero_b:
        s = None
    else:
        # sum_max > 0 guaranteed (both grids have a positive cell)
        s = sum_min / sum_max
        if s < 0.0:
            s = 0.0
        elif s > 1.0:
            s = 1.0

    return {
        "s": s,
        "channel": "d-geom",
        "grid": [GRID_COLS, GRID_ROWS],
        "occupied_cells_a": occ_a,
        "occupied_cells_b": occ_b,
        "sum_min": sum_min,
        "sum_max": sum_max,
        "zero_a": zero_a,
        "zero_b": zero_b,
    }
