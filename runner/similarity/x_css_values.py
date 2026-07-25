"""x-css-values — CSS value-vocabulary cosine (scheme v13, extended code family).

Where ``c-css-prop`` reads only property *names*, this reads the *values*: the
unit distribution (px / rem / em / % / vw / vh / fr / deg / ms …), the CSS-function
vocabulary (calc / clamp / var / gradients / transforms / filters …), and the
color-notation mix (hex-3/6/8, rgb(a), hsl(a), currentColor, transparent). Cosine
over the frequency vector. Captures "how a model scales and styles", an axis the
name-only channel is blind to. §4: empty vector either side → S = None.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from . import _xcommon as X

EXTRACTOR_VERSION = "x-css-values-extractor-v1"

_UNIT = re.compile(
    r"(?<![\w.])-?\d*\.?\d+(px|rem|em|vw|vh|vmin|vmax|%|pt|fr|ch|ex|deg|turn|ms|s)\b",
    re.I,
)
_FUNC = re.compile(
    r"\b(calc|clamp|min|max|var|env|linear-gradient|radial-gradient|conic-gradient|"
    r"translate3?d?|translatex|translatey|rotate|scale|skew|matrix|perspective|"
    r"rgb|rgba|hsl|hsla|blur|brightness|contrast|drop-shadow|url|cubic-bezier|"
    r"repeat|minmax|fit-content)\s*\(",
    re.I,
)
_HEX = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")


def extract(html: str) -> dict[str, float]:
    css = X.css_text(X.collect(html))
    v: Counter = Counter()
    for m in _UNIT.finditer(css):
        v["unit:" + m.group(1).lower()] += 1
    for m in _FUNC.finditer(css):
        v["fn:" + m.group(1).lower()] += 1
    for m in _HEX.finditer(css):
        v["colfmt:hex%d" % len(m.group(1))] += 1
    low = css.lower()
    for kw in ("rgb(", "rgba(", "hsl(", "hsla(", "currentcolor", "transparent"):
        n = low.count(kw)
        if n:
            v["colfmt:" + kw.replace("(", "")] += n
    return dict(v)


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    va = extract(X.load_html(card_a_artifacts, "x-css-values"))
    vb = extract(X.load_html(card_b_artifacts, "x-css-values"))
    return X.channel_result(va, vb, EXTRACTOR_VERSION)
