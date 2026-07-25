"""Shared machinery for the **x-*** extended code-feature channels (scheme v13).

The x-* family is a NEW kind of code channel. Where the existing ``c-*`` channels
measure structural *overlap* (Jaccard / cosine of shared tokens, shingles,
fingerprints), the x-* channels extract an interpretable **feature-distribution
signature** from ``card.html`` — declared colors, CSS value vocabulary, layout
technique, class-naming style, HTML semantics — and compare distributions by
cosine. They read the HTML source only (no render), so like ``c-*`` they resolve
from ``card_html``.

Validated to add orthogonal family-discrimination signal (leaderboard: x-color
0.51 effect / 0.232 redundancy — a genuinely new axis; x-css-values 0.57,
x-layout 0.59; x-naming/x-semantics weaker). See the v13 channel-expansion notes.

Every x-* channel:  ``extract(html) -> dict[str, float]``  (sparse vector) and
``compute(art_a, art_b) -> {"s": float|None, ...diagnostics}`` with the §4 unified
rule (empty vector on either side, double-empty included → ``s = None``).
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# artifact resolution (shared convention with the c-* channels)
# ---------------------------------------------------------------------------
def load_html(artifacts: Any, channel: str) -> str:
    if isinstance(artifacts, dict):
        for key in ("html", "card_html"):
            if key in artifacts and artifacts[key] is not None:
                return str(artifacts[key])
        for key in ("card_dir", "dir", "path", "card_html_path"):
            if key in artifacts and artifacts[key] is not None:
                return _read_path(artifacts[key])
        raise KeyError(f"{channel}: artifacts dict must carry 'html' or a path key")
    if isinstance(artifacts, (str, Path)):
        s = str(artifacts)
        p = Path(s)
        if p.exists():
            return _read_path(p)
        if "<" in s:
            return s
        raise FileNotFoundError(f"{channel}: path does not exist: {s!r}")
    raise TypeError(f"{channel}: unsupported artifacts type {type(artifacts)!r}")


def _read_path(path: Any) -> str:
    p = Path(path)
    if p.is_dir():
        p = p / "card.html"
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# single-pass HTML collector feeding every x-* extractor
# ---------------------------------------------------------------------------
_VOID = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)


class Collector(HTMLParser):
    """One tolerant pass: <style> bodies, inline style= values, <script> text,
    tag counts, class tokens, aria/role/data attr counts, element depths."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.style_blocks: list[str] = []
        self.inline_styles: list[str] = []
        self.scripts: list[str] = []
        self.tags: Counter = Counter()
        self.classes: list[str] = []
        self.total_elems = 0
        self.aria = 0
        self.role = 0
        self.data_attr = 0
        self.alt = 0
        self.depth = 0
        self.depths: list[int] = []
        self._in_style = False
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        self.tags[t] += 1
        self.total_elems += 1
        self.depth += 1
        self.depths.append(self.depth)
        for name, val in attrs:
            ln = name.lower()
            if ln == "class" and val:
                self.classes.extend(tok for tok in val.split() if tok)
            elif ln == "style" and val:
                self.inline_styles.append(val)
            elif ln == "role":
                self.role += 1
            elif ln == "alt":
                self.alt += 1
            elif ln.startswith("aria-"):
                self.aria += 1
            elif ln.startswith("data-"):
                self.data_attr += 1
        if t == "style":
            self._in_style = True
        elif t == "script":
            self._in_script = True
        if t in _VOID:
            self.depth -= 1  # void element carries no body

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        t = tag.lower()
        if t not in _VOID:
            self.depth -= 1
        if t == "style":
            self._in_style = False
        if t == "script":
            self._in_script = False

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "style":
            self._in_style = False
        elif t == "script":
            self._in_script = False
        if t not in _VOID and self.depth > 0:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.style_blocks.append(data)
        elif self._in_script:
            self.scripts.append(data)


def collect(html: str) -> Collector:
    c = Collector()
    try:
        c.feed(html)
        c.close()
    except Exception:
        pass
    return c


def css_text(c: Collector) -> str:
    return "\n".join(c.style_blocks) + "\n" + "\n".join(c.inline_styles)


# ---------------------------------------------------------------------------
# cosine over sparse non-negative vectors (matches c-css-prop / v-layout)
# ---------------------------------------------------------------------------
def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if a == b:
        return 1.0  # exact self-identity, no sqrt noise
    # sorted, not set-order: str hashing is seed-randomized per process, so an
    # unordered accumulation makes S vary in the last ULP between runs
    # (measured: x-semantics landed on ...685/686/687 across PYTHONHASHSEED
    # 0-3). A benchmark whose premise is reproducibility cannot have that.
    keys = sorted(set(a) | set(b))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for k in keys:
        va = a.get(k, 0.0)
        vb = b.get(k, 0.0)
        dot += va * vb
        na += va * va
        nb += vb * vb
    s = dot / (math.sqrt(na) * math.sqrt(nb))
    if s < 0.0:
        return 0.0
    if s > 1.0:
        return 1.0
    return s


def channel_result(
    vec_a: dict[str, float], vec_b: dict[str, float], version: str
) -> dict:
    """Shared compute tail: cosine + §4 unified null rule + diagnostics."""
    n_a = len(vec_a)
    n_b = len(vec_b)
    diag: dict[str, Any] = {"extractor_version": version, "n_a": n_a, "n_b": n_b}
    if n_a == 0 or n_b == 0:
        diag["reason"] = "empty-vector"
        return {"s": None, **diag}
    return {"s": cosine(vec_a, vec_b), **diag}


# ---------------------------------------------------------------------------
# color helpers (x-color)
# ---------------------------------------------------------------------------
NAMED_COLORS: dict[str, str] = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080",
    "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "navy": "#000080",
    "teal": "#008080", "gold": "#ffd700", "pink": "#ffc0cb", "cyan": "#00ffff",
    "magenta": "#ff00ff", "lime": "#00ff00", "indigo": "#4b0082", "violet": "#ee82ee",
    "brown": "#a52a2a", "maroon": "#800000", "olive": "#808000", "coral": "#ff7f50",
    "salmon": "#fa8072", "khaki": "#f0e68c", "crimson": "#dc143c", "skyblue": "#87ceeb",
    "tomato": "#ff6347", "slateblue": "#6a5acd", "darkblue": "#00008b",
    "lightblue": "#add8e6", "steelblue": "#4682b4", "dodgerblue": "#1e90ff",
    "royalblue": "#4169e1", "midnightblue": "#191970",
}


def rgb_to_hsl(r: float, g: float, b: float) -> tuple[float, float, float]:
    r, g, b = r / 255, g / 255, b / 255
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = (g - b) / d + (6 if g < b else 0)
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60, s, l


def hex_to_rgb(h: str) -> tuple[int, int, int] | None:
    h = h.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(ch * 2 for ch in h[:3])
    if len(h) >= 6:
        try:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            return None
    return None


def bucket_color(h: float, s: float, l: float) -> str:
    if s < 0.12 or l < 0.06 or l > 0.94:
        return "achr:%d" % min(2, int(l * 3))
    hb = int((h % 360) // 30)
    lb = min(2, int(l * 3))
    return "h%02d_l%d" % (hb, lb)


# ---------------------------------------------------------------------------
# naming-style classifier (x-naming)
# ---------------------------------------------------------------------------
def naming_style(tok: str) -> str:
    if "__" in tok or "--" in tok:
        return "bem"
    if re.search(r"[a-z][A-Z]", tok):
        return "camel"
    if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", tok):
        segs = tok.split("-")
        if any(re.fullmatch(r"\d+", s) or len(s) <= 3 for s in segs):
            return "utility"
        return "kebab"
    if re.fullmatch(r"[a-z0-9]+", tok):
        return "single"
    return "other"


# shared for x-semantics
def mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0
