"""Text normalization + text-coordinate system + DOM model (scheme §3).

Two things live here, both PURE-PYTHON codepoint arithmetic (no UTF-16):

1. ``normalize_text`` — the canonical fidelity normalization pipeline
   **NFC → trim → whitespace-fold → casefold** (附录 A 保真: 文本归一).

2. ``DomModel`` — the read-only view of one ``dom.json`` (dom-dump-v1) the whole
   extractor computes on:

   * the **visible-text-node sequence** in DOM preorder (``type=="text"`` ∧
     ``text_visible``), each carrying its NORMALIZED text and its **text
     coordinate base** = cumulative normalized-codepoint length of all preceding
     visible text nodes, **with no inter-node separator** (scheme §3 文本坐标);
   * DOM **parent / depth / LCA** over element nodes (for the binding distance
     二元组's first component = DOM-edge count via LCA);
   * per-element **computed font-size** (for temperature-candidate salience
     ``f_max``).

All occurrence offsets (start/end) are codepoint indices into a node's
NORMALIZED text; ``text_coord(occ) = node.base + occ.start`` (scheme §3 取
occurrence 的 start).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# One regex collapses every Unicode whitespace run to a single U+0020; a trailing
# ``.strip()`` performs the trim. Applied post-NFC, pre-casefold.
_WS_RE = re.compile(r"\s+", re.UNICODE)


def normalize_text(raw: str) -> str:
    """Canonical §3 normalization: NFC → trim → whitespace-fold → casefold.

    Whitespace-fold + trim are done together (collapse runs to one space, then
    strip the ends); casefold is applied last, exactly in the order 附录 A lists
    ("NFC+trim+空白折叠+casefold"). Length is measured on the FINAL string —
    the extractor's text coordinates live entirely in this normalized space.
    """
    s = unicodedata.normalize("NFC", raw)
    s = _WS_RE.sub(" ", s).strip()
    return s.casefold()


@dataclass(frozen=True)
class VisibleTextNode:
    """One visible text node in the normalized text-coordinate system."""

    preorder: int  # DOM preorder index of the text node (occurrence-ID component)
    parent: int  # preorder index of the enclosing element
    norm: str  # normalized node text
    length: int  # codepoint length of ``norm``
    base: int  # text-coordinate base = cumulative normalized length before it


class DomModel:
    """Read-only structural + text view of one ``dom.json`` (dom-dump-v1)."""

    def __init__(self, dom: dict):
        self.dom = dom
        nodes = dom.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError("dom.json has no nodes list")
        self.nodes = nodes

        # parent pointers + per-element font size (elements only).
        self.parent_of: dict[int, int | None] = {}
        self.font_of: dict[int, float | None] = {}
        self.tag_of: dict[int, str] = {}
        for n in nodes:
            pre = n["preorder"]
            self.parent_of[pre] = n.get("parent")
            if n.get("type") == "element":
                self.tag_of[pre] = n.get("tag", "")
                style = n.get("style") or {}
                fs = style.get("font_size")
                self.font_of[pre] = float(fs) if isinstance(fs, (int, float)) else None

        # element depth = number of element ancestors (each parent-step is one
        # DOM edge; text-node parents are always elements, so a walk over
        # parent_of from any element visits only elements).
        self._depth_cache: dict[int, int] = {}

        # visible-text-node sequence in preorder, with cumulative text-coord base.
        self.visible: list[VisibleTextNode] = []
        self.visible_by_pre: dict[int, VisibleTextNode] = {}
        base = 0
        vt = [
            n
            for n in nodes
            if n.get("type") == "text" and n.get("text_visible")
        ]
        vt.sort(key=lambda n: n["preorder"])
        for n in vt:
            norm = normalize_text(n.get("text_raw") or "")
            node = VisibleTextNode(
                preorder=n["preorder"],
                parent=n.get("parent"),
                norm=norm,
                length=len(norm),
                base=base,
            )
            self.visible.append(node)
            self.visible_by_pre[node.preorder] = node
            base += node.length  # NO inter-node separator (scheme §3)

    # -- structure -----------------------------------------------------------
    def depth(self, elem_pre: int) -> int:
        """Element depth = count of parent-steps to the root."""
        cached = self._depth_cache.get(elem_pre)
        if cached is not None:
            return cached
        d = 0
        cur = self.parent_of.get(elem_pre)
        seen = {elem_pre}
        while cur is not None and cur not in seen:
            seen.add(cur)
            d += 1
            cur = self.parent_of.get(cur)
        self._depth_cache[elem_pre] = d
        return d

    def _ancestors(self, elem_pre: int) -> list[int]:
        """Chain [elem, parent, ..., root]."""
        chain = []
        cur: int | None = elem_pre
        seen: set[int] = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = self.parent_of.get(cur)
        return chain

    def lca(self, a: int, b: int) -> int | None:
        """Lowest common ancestor element of two elements (preorder indices)."""
        anc_a = set(self._ancestors(a))
        for x in self._ancestors(b):
            if x in anc_a:
                return x
        return None

    def dom_edges(self, a: int, b: int) -> int:
        """DOM-edge count between two elements via their LCA (scheme §3 二元组)."""
        if a == b:
            return 0
        anc = self.lca(a, b)
        if anc is None:
            # disjoint trees — should not happen for a single document root;
            # fall back to the sum of depths (a large, deterministic distance).
            return self.depth(a) + self.depth(b)
        return self.depth(a) + self.depth(b) - 2 * self.depth(anc)

    # -- text coordinates ----------------------------------------------------
    def text_coord(self, node_pre: int, offset: int) -> int:
        """Text coordinate of ``offset`` inside visible text node ``node_pre``."""
        vt = self.visible_by_pre.get(node_pre)
        if vt is None:
            return offset
        return vt.base + offset

    def font_size(self, elem_pre: int) -> float | None:
        return self.font_of.get(elem_pre)
