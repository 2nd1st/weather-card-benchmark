"""x-layout — layout-technique cosine (scheme v13, extended code family).

Counts the layout vocabulary a model reaches for: ``display`` values (flex / grid /
inline-block / …), ``position`` (absolute / fixed / sticky / …), ``float``, at-rules
(``@media`` / ``@keyframes`` / ``@supports``), and layout-bearing properties (gap,
grid-template-*, flex-direction, justify/align, aspect-ratio, transition, animation,
transform, box-shadow). Cosine over the counts. The strongest family discriminator
of the v13 additions; partially overlaps c-feature (shares css tokens) but resolves
flex-vs-grid, which name-presence cannot. §4: empty vector either side → S = None.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from . import _xcommon as X

EXTRACTOR_VERSION = "x-layout-extractor-v1"

_DISPLAY = ("flex", "grid", "inline-flex", "inline-block", "inline-grid", "none", "block", "contents")
_POSITION = ("absolute", "fixed", "relative", "sticky", "static")
_FLOAT = ("left", "right")
_PROPS = (
    "gap", "grid-template-columns", "grid-template-rows", "grid-template-areas",
    "flex-direction", "justify-content", "align-items", "align-content",
    "place-items", "aspect-ratio", "transition", "animation", "transform", "box-shadow",
)


def extract(html: str) -> dict[str, float]:
    css = X.css_text(X.collect(html)).lower()
    v: Counter = Counter()
    for prop, vals in (("display", _DISPLAY), ("position", _POSITION), ("float", _FLOAT)):
        for val in vals:
            n = len(re.findall(prop + r"\s*:\s*" + val + r"\b", css))
            if n:
                v["%s:%s" % (prop, val)] += n
    for at in ("@media", "@keyframes", "@supports"):
        n = len(re.findall(at + r"\b", css))
        if n:
            v[at] = n
    for k in _PROPS:
        n = len(re.findall(r"\b" + re.escape(k) + r"\s*:", css))
        if n:
            v[k] = n
    return dict(v)


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    va = extract(X.load_html(card_a_artifacts, "x-layout"))
    vb = extract(X.load_html(card_b_artifacts, "x-layout"))
    return X.channel_result(va, vb, EXTRACTOR_VERSION)
