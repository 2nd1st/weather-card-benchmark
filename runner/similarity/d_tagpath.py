"""d-tagpath — DIAGNOSTIC tag-path structural similarity (set Jaccard).

Scheme §4 + 附录 A (BYTE-EXACT, implemented verbatim, never simplified):

    d-tagpath | 诊断;root→leaf tag 路径集合 Jaccard

i.e. the channel value is the **Jaccard index of the two cards' sets of
root→leaf tag paths**.

    S = |P_a ∩ P_b| / |P_a ∪ P_b|

where ``P_x`` is the *set* (distinct, NOT a multiset — 附录 A pins "路径集合" =
path *set*) of root-to-leaf tag paths of card ``x``'s element tree. A single
root→leaf tag path is the ordered tuple of element **tag** names encountered on
the descent from a root element down to a leaf element (a leaf = an element with
no element children). Identical trees → identical path sets → S = 1.

This is a permanently **diagnostic** channel (v9 pin, restated in v12 §4): it
does NOT participate in the greedy channel-retention audit, the formal channel
set, the statistics, or the hypothesis family. It is computed and reported for
observability only.

Conventions shared with the other DOM (``d-*``) channels, applied here:

  * Node label = element **tag** name (``label=tag``, as in d-pqgram). Text
    nodes carry no tag and are therefore excluded from the tree; across the
    devset every element's parent is an element, so text nodes are never
    interior. The element-only tree captured in ``dom.json`` is used.
  * The scheme states NO visibility filter for d-tagpath (unlike d-geom's
    visibility rule or d-text's dynamic-field stripping), so the COMPLETE
    captured element tree is used, including
    ``html``/``head``/``meta``/``script``/``style`` — identical treatment to
    d-pqgram (FLAGGED in the report, same call as d-pqgram made).
  * Empty set on either side (double-empty included) → ``s = None`` (§4 unified
    rule "零向量/空集/空 bag: 任一侧 → 该 pair S=null"). A path set is empty only
    when a side has zero element nodes (a single lone element still yields one
    path — its own tag), or when the ``dom.json`` artifact is absent/unparseable.

Input artifact for a DOM (``d-*``) channel is the card's ``dom.json`` file.

The returned dict always carries:
    s                 : float in [0,1] | None  — the channel value (= Jaccard)
    label             : str                     — "tag"
    n_paths_a         : int                     — |P_a| (distinct root→leaf paths)
    n_paths_b         : int                     — |P_b|
    intersection      : int                     — |P_a ∩ P_b|
    union             : int                     — |P_a ∪ P_b|
    n_elements_a      : int                     — element-node count side a
    n_elements_b      : int                     — element-node count side b
    null_reason       : str | None              — why s is None (diagnostic)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

# 附录 A pinned constant for d-tagpath — implement verbatim, never simplify.
LABEL = "tag"  # node label source = element tag name

_UTF8 = "utf-8"


# --------------------------------------------------------------------------
# artifact resolution — dom.json for a d-* channel (identical to d-pqgram)
# --------------------------------------------------------------------------
def _read_dom(artifacts: Any) -> dict | None:
    """Resolve the parsed ``dom.json`` object from a channel artifact handle.

    Accepts, in order of preference:
      * ``dict``/``Mapping`` that already IS a dom dump (has a ``nodes`` key)
      * ``dict``/``Mapping`` with ``dom`` / ``dom_json`` (parsed dict or path)
      * ``dict``/``Mapping`` with ``card_dir`` / ``dir`` / ``path`` (dir or file)
      * ``str`` / ``os.PathLike`` pointing at a card directory (reads
        ``dom.json``) or directly at a ``.json`` file
    Returns the parsed dom dict, or ``None`` when no dom.json artifact exists or
    it cannot be parsed.
    """
    if artifacts is None:
        return None
    if isinstance(artifacts, (str, os.PathLike)):
        p = Path(artifacts)
        if p.is_dir():
            p = p / "dom.json"
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding=_UTF8))
        except (ValueError, OSError):
            return None
    if isinstance(artifacts, Mapping):
        if "nodes" in artifacts:
            return dict(artifacts)
        for key in ("dom", "dom_json"):
            if key in artifacts and artifacts[key] is not None:
                val = artifacts[key]
                if isinstance(val, Mapping):
                    return dict(val)
                return _read_dom(val)
        for key in ("card_dir", "dir", "path"):
            val = artifacts.get(key)
            if val is not None:
                return _read_dom(val)
        return None
    raise TypeError(
        f"d-tagpath: unsupported artifact type {type(artifacts)!r}; expected "
        "Mapping, str, os.PathLike, or None"
    )


# --------------------------------------------------------------------------
# element tree construction (label = tag) — identical to d-pqgram
# --------------------------------------------------------------------------
def _element_children(dom: dict | None) -> tuple[dict, list]:
    """Build ``parent_preorder -> [child element node, ...]`` in document order
    plus the list of root element nodes (parent is None / missing).

    Only ``type == "element"`` nodes participate (label = tag). Children are
    ordered by ``preorder`` (global DFS index = document order among siblings).
    Sibling order does NOT affect the path SET, but a deterministic traversal is
    kept for reproducibility.
    """
    children: dict = {}
    roots: list = []
    if not dom:
        return children, roots
    nodes = dom.get("nodes") or []
    elems = [n for n in nodes if n.get("type") == "element"]
    elems.sort(key=lambda n: n.get("preorder", 0))
    elem_ids = {n.get("preorder") for n in elems}
    for n in elems:
        par = n.get("parent")
        if par is None or par not in elem_ids:
            roots.append(n)
        else:
            children.setdefault(par, []).append(n)
    return children, roots


def tagpath_set(dom: dict | None) -> set:
    """The SET of distinct root→leaf tag paths of the element tree.

    Each path is the tuple of element tag names from a root element down to a
    leaf element (leaf = element with no element children). A lone element is
    itself a leaf and yields the singleton path ``(tag,)``. Returns a Python
    ``set`` of such tuples (distinct paths). Empty when there are no element
    nodes.

    Iterative DFS (no recursion-depth limit); duplicate paths from distinct
    subtrees collapse into one set member, per "路径集合" (set) semantics.
    """
    paths: set = set()
    children, roots = _element_children(dom)
    for root in roots:
        # stack of (node, prefix_tuple) — explicit to avoid deep recursion
        stack = [(root, (root.get("tag"),))]
        while stack:
            node, prefix = stack.pop()
            kids = children.get(node.get("preorder"), ())
            if not kids:
                paths.add(prefix)
                continue
            for c in kids:
                stack.append((c, prefix + (c.get("tag"),)))
    return paths


# --------------------------------------------------------------------------
# set Jaccard
# --------------------------------------------------------------------------
def jaccard(set_a: set, set_b: set):
    """Return ``(sim, n_a, n_b, inter, union)``.

    sim = |A ∩ B| / |A ∪ B|   (附录 A: 路径集合 Jaccard)
    Either set empty (double-empty included) → sim = None (§4).
    """
    n_a = len(set_a)
    n_b = len(set_b)
    if n_a == 0 or n_b == 0:
        return None, n_a, n_b, 0, 0
    inter = len(set_a & set_b)
    union = n_a + n_b - inter
    sim = inter / union
    return sim, n_a, n_b, inter, union


def _count_elements(dom: dict | None) -> int:
    if not dom:
        return 0
    return sum(1 for n in (dom.get("nodes") or []) if n.get("type") == "element")


def _null(reason: str, n_a: int, n_b: int, n_elem_a: int, n_elem_b: int) -> dict:
    return {
        "s": None,
        "label": LABEL,
        "n_paths_a": n_a,
        "n_paths_b": n_b,
        "intersection": 0,
        "union": 0,
        "n_elements_a": n_elem_a,
        "n_elements_b": n_elem_b,
        "null_reason": reason,
    }


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """Compute the d-tagpath similarity between two cards' DOM trees.

    See module docstring for the returned dict shape. ``s`` is the Jaccard index
    of the two cards' root→leaf tag-path sets (identical trees → 1.0); ``s`` is
    ``None`` when either side has an empty path set (no element nodes / absent
    dom.json), per §4.
    """
    dom_a = _read_dom(card_a_artifacts)
    dom_b = _read_dom(card_b_artifacts)

    n_elem_a = _count_elements(dom_a)
    n_elem_b = _count_elements(dom_b)

    set_a = tagpath_set(dom_a)
    set_b = tagpath_set(dom_b)

    sim, n_a, n_b, inter, union = jaccard(set_a, set_b)

    if sim is None:
        if n_a == 0 and n_b == 0:
            reason = "empty-set-both"
        elif n_a == 0:
            reason = "empty-set-a"
        else:
            reason = "empty-set-b"
        return _null(reason, n_a, n_b, n_elem_a, n_elem_b)

    return {
        "s": sim,
        "label": LABEL,
        "n_paths_a": n_a,
        "n_paths_b": n_b,
        "intersection": inter,
        "union": union,
        "n_elements_a": n_elem_a,
        "n_elements_b": n_elem_b,
        "null_reason": None,
    }
