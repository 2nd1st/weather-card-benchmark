"""x-naming — class-naming-style cosine (scheme v13, extended code family).

A model's class-token "handwriting": the mix of naming conventions (BEM ``__``/``--``,
camelCase, kebab-case, Tailwind-ish utility, single words), plus class density
(classes per element), mean token length, and unique-class ratio. Cosine over the
profile. Weak family discriminator on its own (~0.32 effect) but orthogonal to the
structural channels — a distinct authored-style axis. §4: empty vector → S = None.
A card with no classes yields the singleton ``{noclass: 1}`` (a legitimate signal).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import _xcommon as X

EXTRACTOR_VERSION = "x-naming-extractor-v1"

_STYLES = ("bem", "camel", "kebab", "utility", "single", "other")


def extract(html: str) -> dict[str, float]:
    c = X.collect(html)
    if c.total_elems == 0:
        return {}  # degenerate / empty card → empty vector → §4 null
    cls = c.classes
    if not cls:
        return {"noclass": 1.0}  # real card, no classes — a legitimate signal
    tot = max(1, c.total_elems)
    styles = Counter(X.naming_style(t) for t in cls)
    n = len(cls)
    v: dict[str, float] = {}
    for st in _STYLES:
        val = styles.get(st, 0) / n
        if val:
            v["style:" + st] = val
    v["class_density"] = n / tot
    v["mean_len"] = X.mean([len(t) for t in cls]) / 20.0
    v["uniq_ratio"] = len(set(cls)) / n
    return v


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    va = extract(X.load_html(card_a_artifacts, "x-naming"))
    vb = extract(X.load_html(card_b_artifacts, "x-naming"))
    return X.channel_result(va, vb, EXTRACTOR_VERSION)
