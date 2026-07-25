"""x-color — declared-color histogram cosine (scheme v13, extended code family).

A code-side color signal with NO render: parse every color literal from the card's
CSS (``<style>`` blocks + inline ``style=``) — hex, ``rgb()/rgba()``, ``hsl()/hsla()``,
and a frozen named-color vocabulary — convert each to HSL, bucket into 12 hue × 3
lightness bins (+ an achromatic bin for near-grey/black/white), and cosine-compare
the resulting weighted histograms. The most orthogonal of the v13 additions
(nearest existing channel ~0.23): it captures palette intent that no structural
channel sees. §4 unified rule: empty histogram either side → S = None.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from . import _xcommon as X

EXTRACTOR_VERSION = "x-color-extractor-v1"

_HEX = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
_RGB = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", re.I)
_HSL = re.compile(r"hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%", re.I)


def extract(html: str) -> dict[str, float]:
    css = X.css_text(X.collect(html))
    v: Counter = Counter()
    for m in _HEX.finditer(css):
        rgb = X.hex_to_rgb(m.group(1))
        if rgb:
            v[X.bucket_color(*X.rgb_to_hsl(*rgb))] += 1
    for m in _RGB.finditer(css):
        try:
            rgb = tuple(min(255, int(float(x))) for x in m.groups())
        except ValueError:
            continue
        v[X.bucket_color(*X.rgb_to_hsl(*rgb))] += 1
    for m in _HSL.finditer(css):
        try:
            h, s, l = float(m.group(1)), float(m.group(2)) / 100, float(m.group(3)) / 100
        except ValueError:
            continue
        v[X.bucket_color(h, s, l)] += 1
    low = css.lower()
    for name, hexv in X.NAMED_COLORS.items():
        n = len(re.findall(r"[:\s(]" + name + r"[;\s)!,]", low))
        if n:
            rgb = X.hex_to_rgb(hexv)
            if rgb:
                v[X.bucket_color(*X.rgb_to_hsl(*rgb))] += n
    return dict(v)


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    va = extract(X.load_html(card_a_artifacts, "x-color"))
    vb = extract(X.load_html(card_b_artifacts, "x-color"))
    return X.channel_result(va, vb, EXTRACTOR_VERSION)
