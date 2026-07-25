"""x-semantics — HTML semantic-profile cosine (scheme v13, extended code family).

A "div-soup index" and structural-sophistication signature: the ratio of semantic
elements (header/nav/main/section/article/… /headings/lists/table) to total, div and
span ratios, svg/path/img/canvas ratios, aria/role/data-attr density, and DOM-depth
statistics (max + mean, normalized). Cosine over the ratio vector. The weakest v13
addition on family discrimination (~0.23) but a genuine authored-structure axis. §4:
an all-zero vector (degenerate / empty card) → S = None.
"""
from __future__ import annotations

from typing import Any

from . import _xcommon as X

EXTRACTOR_VERSION = "x-semantics-extractor-v1"

_SEMANTIC = frozenset(
    "header nav main section article aside footer figure figcaption h1 h2 h3 h4 h5 h6 "
    "ul ol li dl dt dd table thead tbody tr td th button label time address mark "
    "details summary fieldset legend output progress meter".split()
)


def extract(html: str) -> dict[str, float]:
    c = X.collect(html)
    tot = max(1, c.total_elems)
    g = lambda t: c.tags.get(t, 0)  # noqa: E731
    sem = sum(g(t) for t in _SEMANTIC)
    v: dict[str, float] = {
        "sem_ratio": sem / tot,
        "div_ratio": g("div") / tot,
        "span_ratio": g("span") / tot,
        "svg_ratio": g("svg") / tot,
        "path_ratio": g("path") / tot,
        "img_ratio": g("img") / tot,
        "canvas_ratio": g("canvas") / tot,
        "aria_ratio": c.aria / tot,
        "role_ratio": c.role / tot,
        "data_ratio": c.data_attr / tot,
        "heading_ratio": sum(g("h%d" % i) for i in range(1, 7)) / tot,
        "list_ratio": (g("ul") + g("ol") + g("li")) / tot,
        "max_depth": (max(c.depths) / 25.0) if c.depths else 0.0,
        "mean_depth": X.mean(c.depths) / 15.0,
        "distinct_tags": len(c.tags) / 40.0,
    }
    # drop exact zeros → sparse vector; an all-zero (degenerate) card → empty → null.
    return {k: val for k, val in v.items() if val}


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    va = extract(X.load_html(card_a_artifacts, "x-semantics"))
    vb = extract(X.load_html(card_b_artifacts, "x-semantics"))
    return X.channel_result(va, vb, EXTRACTOR_VERSION)
