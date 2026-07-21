"""d-pqgram — pq-gram structural similarity with multiplicity-Dice.

Scheme §4 + 附录 A (BYTE-EXACT, implemented verbatim, never simplified):

    d-pqgram | p=2, q=3, label=tag;
        sim = 2·Σ_g min(cnt₁(g), cnt₂(g)) / (|bag₁| + |bag₂|),
        |bag_i| := Σ_g cnt_i(g)  (total multiplicity mass);
        dist = 1 − sim;  empty bag → S = null

The pq-gram profile is the classic Augsten–Böhlen–Gamper index
("Approximate Matching of Hierarchical Data Using pq-Grams", TODS 2010):
each pq-gram is an ordered label tuple of ``p`` ancestor labels (the *stem*)
followed by ``q`` consecutive sibling labels (the *base*), with a null label
sentinel ``*`` padding both the ancestor spine above the root and the sibling
window at the left/right boundaries and for leaf nodes. The bag (multiset) of
all such tuples over the tree is the profile; the scheme pins its comparison
to the *multiplicity Sørensen–Dice* coefficient above (NOT the paper's default
symmetric-difference pq-gram distance — 附录 A pins Dice, so we implement Dice).

Node label = the element **tag** name (``label=tag``). The tree is the
element-only DOM tree captured in ``dom.json``: text nodes carry no tag and are
therefore excluded (they can never be interior nodes — verified across the
devset every element's parent is an element). The scheme states NO visibility
or subtree filter for d-pqgram (unlike d-geom's visibility rule or d-text's
dynamic-field stripping), so the COMPLETE captured element tree is used,
including ``html``/``head``/``meta``/``script``/``style`` (FLAGGED below).

Input artifact for a DOM (``d-*``) channel is the card's ``dom.json`` file.
Empty bag on either side (double-empty included) → ``s = None`` (§4). A bag is
empty only when a side has zero element nodes (a single lone element still
yields one pq-gram), or when the ``dom.json`` artifact is absent/unparseable.

The returned dict always carries:
    s                    : float in [0,1] | None  — the channel value (= sim)
    dist                 : float in [0,1] | None  — 1 − sim
    p, q                 : int                     — pinned 2, 3
    label                : str                     — "tag"
    bag_a_mass           : int                     — |bag₁| = Σ cnt₁
    bag_b_mass           : int                     — |bag₂| = Σ cnt₂
    bag_a_distinct       : int                     — #distinct pq-grams side a
    bag_b_distinct       : int                     — #distinct pq-grams side b
    intersection_mass    : int                     — Σ_g min(cnt₁, cnt₂)
    n_elements_a         : int                     — element-node count side a
    n_elements_b         : int                     — element-node count side b
    null_reason          : str | None              — why s is None (diagnostic)
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

# 附录 A pinned constants for d-pqgram — implement verbatim, never simplify.
P = 2          # p = number of ancestor labels in a pq-gram stem
Q = 3          # q = number of sibling labels in a pq-gram base
LABEL = "tag"  # node label source = element tag name
_NULL = None   # the pq-gram null label sentinel ("*")

_UTF8 = "utf-8"


# --------------------------------------------------------------------------
# artifact resolution — dom.json for a d-* channel
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
        # already a dom dump?
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
        f"d-pqgram: unsupported artifact type {type(artifacts)!r}; expected "
        "Mapping, str, os.PathLike, or None"
    )


# --------------------------------------------------------------------------
# element tree construction (label = tag)
# --------------------------------------------------------------------------
def _element_children(dom: dict | None) -> tuple[dict, list]:
    """Build ``parent_preorder -> [child element node, ...]`` in document order
    plus the list of root element nodes (parent is None / missing).

    Only ``type == "element"`` nodes participate (label = tag). Children are
    ordered by ``preorder`` (global DFS index = document order among siblings).
    """
    children: dict = {}
    roots: list = []
    if not dom:
        return children, roots
    nodes = dom.get("nodes") or []
    # stable document order: sort by preorder (defensive; dumps are already sorted)
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


class _ShiftRegister:
    """Fixed-size shift register (Augsten et al.). ``shift(x)`` appends ``x`` on
    the right and drops the leftmost entry, preserving length; initialized with
    ``size`` copies of the null sentinel."""

    __slots__ = ("_r",)

    def __init__(self, size: int):
        self._r = [_NULL] * size

    def shift(self, x) -> None:
        self._r.append(x)
        del self._r[0]

    def copy(self) -> "_ShiftRegister":
        c = _ShiftRegister.__new__(_ShiftRegister)
        c._r = list(self._r)
        return c

    def tuple(self) -> tuple:
        return tuple(self._r)


def pqgram_bag(dom: dict | None, p: int = P, q: int = Q) -> Counter:
    """The pq-gram profile (multiset of label tuples) of the element tree.

    Implements Augsten et al. ``getpqgrams`` verbatim:

      * ``anc`` = length-p shift register (ancestor stem), initialized all-null;
        shifting the current node's label in gives its p-ancestor spine (null
        above the root).
      * ``sib`` = length-q shift register (sibling base), reset all-null per
        node; a leaf emits one pq-gram ``(anc, all-null base)``; a node with n
        children emits n + (q−1) pq-grams as each child label is shifted through
        the window, then q−1 trailing nulls flush the right boundary.

    Each emitted pq-gram is the p ancestor labels followed by the q base labels
    (null = ``None``). Multiplicities are accumulated in the Counter.
    """
    bag: Counter = Counter()
    children, roots = _element_children(dom)

    def rec(node: dict, anc: _ShiftRegister) -> None:
        anc = anc.copy()
        anc.shift(node.get("tag"))
        kids = children.get(node.get("preorder"), ())
        sib = _ShiftRegister(q)
        if not kids:
            # leaf: one pq-gram, base = all null
            bag[anc.tuple() + sib.tuple()] += 1
            return
        for c in kids:
            sib.shift(c.get("tag"))
            bag[anc.tuple() + sib.tuple()] += 1
        for _ in range(q - 1):
            sib.shift(_NULL)
            bag[anc.tuple() + sib.tuple()] += 1
        for c in kids:
            rec(c, anc)

    for root in roots:
        rec(root, _ShiftRegister(p))
    return bag


# --------------------------------------------------------------------------
# multiplicity-Dice
# --------------------------------------------------------------------------
def multiplicity_dice(bag_a: Counter, bag_b: Counter) -> tuple:
    """Return ``(sim, mass_a, mass_b, distinct_a, distinct_b, inter_mass)``.

    sim = 2·Σ_g min(cnt_a(g), cnt_b(g)) / (|bag_a| + |bag_b|)   (附录 A)
    Either bag empty (double-empty included) → sim = None (§4).
    """
    mass_a = sum(bag_a.values())
    mass_b = sum(bag_b.values())
    distinct_a = len(bag_a)
    distinct_b = len(bag_b)
    if mass_a == 0 or mass_b == 0:
        return None, mass_a, mass_b, distinct_a, distinct_b, 0
    # iterate the smaller bag for the intersection mass
    small, large = (bag_a, bag_b) if distinct_a <= distinct_b else (bag_b, bag_a)
    inter = 0
    for g, c in small.items():
        o = large.get(g)
        if o:
            inter += c if c < o else o
    sim = (2 * inter) / (mass_a + mass_b)
    return sim, mass_a, mass_b, distinct_a, distinct_b, inter


def _null(reason: str, mass_a: int, mass_b: int,
          distinct_a: int, distinct_b: int,
          n_elem_a: int, n_elem_b: int) -> dict:
    return {
        "s": None,
        "dist": None,
        "p": P,
        "q": Q,
        "label": LABEL,
        "bag_a_mass": mass_a,
        "bag_b_mass": mass_b,
        "bag_a_distinct": distinct_a,
        "bag_b_distinct": distinct_b,
        "intersection_mass": 0,
        "n_elements_a": n_elem_a,
        "n_elements_b": n_elem_b,
        "null_reason": reason,
    }


def _count_elements(dom: dict | None) -> int:
    if not dom:
        return 0
    return sum(1 for n in (dom.get("nodes") or []) if n.get("type") == "element")


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """Compute the d-pqgram similarity between two cards' DOM trees.

    See module docstring for the returned dict shape. ``s`` is the multiplicity
    Sørensen–Dice of the two pq-gram bags (identical trees → 1.0); ``s`` is
    ``None`` when either side has an empty pq-gram bag (no element nodes /
    absent dom.json), per §4.
    """
    dom_a = _read_dom(card_a_artifacts)
    dom_b = _read_dom(card_b_artifacts)

    n_elem_a = _count_elements(dom_a)
    n_elem_b = _count_elements(dom_b)

    bag_a = pqgram_bag(dom_a)
    bag_b = pqgram_bag(dom_b)

    sim, mass_a, mass_b, distinct_a, distinct_b, inter = multiplicity_dice(bag_a, bag_b)

    if sim is None:
        if mass_a == 0 and mass_b == 0:
            reason = "empty-bag-both"
        elif mass_a == 0:
            reason = "empty-bag-a"
        else:
            reason = "empty-bag-b"
        return _null(reason, mass_a, mass_b, distinct_a, distinct_b, n_elem_a, n_elem_b)

    return {
        "s": sim,
        "dist": 1.0 - sim,
        "p": P,
        "q": Q,
        "label": LABEL,
        "bag_a_mass": mass_a,
        "bag_b_mass": mass_b,
        "bag_a_distinct": distinct_a,
        "bag_b_distinct": distinct_b,
        "intersection_mass": inter,
        "n_elements_a": n_elem_a,
        "n_elements_b": n_elem_b,
        "null_reason": None,
    }
