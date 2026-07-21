"""Similarity channel **c-feature** — feature-set Jaccard (scheme §4 / appendix A).

Appendix A row (verbatim):

    c-feature | 特征集 Jaccard(CSS 属性/tag/JS API 词表版本化/class token)

i.e. the feature set of a card is the **union** of four namespaced token families
extracted from its ``card.html``:

    * ``tag:<name>``    — every HTML element tag name (ASCII-lowercased)
    * ``css:<prop>``    — every CSS *property name* declared, from ``<style>`` blocks
                          **and** every inline ``style="..."`` attribute
    * ``class:<token>`` — every whitespace token of every ``class="..."`` attribute
    * ``jsapi:<token>`` — every token of the **versioned** JS-API vocabulary
                          (``JSAPI_VOCAB`` @ ``EXTRACTOR_VERSION``) occurring as a
                          whole word inside ``<script>`` text

Similarity ``S = Jaccard(A, B) = |A ∩ B| / |A ∪ B|``.

Per §4 / appendix A "统一规则": an **empty** feature set on *either* side (the
double-empty case included) → ``S = None`` (null); null never enters a mean.

The channel is a pure classical set operation, hence deterministic and symmetric
(§0 硬约束 1: 同输入必同输出).

--------------------------------------------------------------------------------
SCHEME-SILENT CHOICES (FLAGGED — appendix A pins the *four families* and "Jaccard"
but not the byte-level extraction; the options below are chosen to stay maximally
consistent with the stated rules and are frozen under ``EXTRACTOR_VERSION``):

  F1. **JS-API vocabulary contents.** Appendix says "JS API 词表版本化" (versioned
      vocabulary) but does not enumerate it. ``JSAPI_VOCAB`` below is that frozen
      list, pinned to ``EXTRACTOR_VERSION``. A different vocabulary ⇒ version bump.
  F2. **CSS property source.** "CSS 属性" is read from BOTH ``<style>`` blocks and
      inline ``style=`` attributes (both are authored CSS properties). Property
      names are taken only from *declaration bodies* (brace depth ≥ 1 for style
      blocks; the whole value for inline attrs), so selector pseudo-classes
      (``a:hover``) and at-rule media features (``@media (max-width: …)``, at depth
      0) are NOT mistaken for properties. Custom properties (``--foo``) are kept.
  F3. **Case handling.** Tag names and CSS property names are ASCII-lowercased
      (HTML/CSS are case-insensitive there). Class tokens are kept verbatim (CSS
      class selectors are case-sensitive in HTML standard mode). JS-API tokens are
      matched case-sensitively (JS is case-sensitive).
  F4. **Namespacing.** Each family is prefixed so a ``div`` tag never collides with
      a ``div`` class; the Jaccard therefore runs over one flat namespaced set.
  F5. **Empty rule.** Follows appendix A "统一规则" (any-empty → null, double-empty
      → null) — this DIFFERS from the trial's legacy ``feature`` metric, which
      returned 1.0 for the double-empty case. v12 is LAW here.

Directional agreement with the trial's legacy ``feature`` metric
(``trial-20260715/analysis/metrics.py::feature_set`` + ``jaccard``) is expected to
be HIGH (same feature-Jaccard shape, same ``css:``/``tag:``/``js(api):``/``class``
namespacing) but NOT identical: the legacy metric adds a ``js:backticks~N`` bucket
hack, hard-codes a different JS token list, reads CSS props from style blocks only
(no inline ``style=``), splits class tokens on whitespace-or-hyphen (v12 splits on whitespace
only), and returns 1.0 for double-empty. v12 附录 A pins differ — this is a fresh
implementation, not a port.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

# Version of the whole c-feature extractor (families + vocabulary + normalization).
# Bump on ANY change to extraction, per scheme §11 (extractor_version locked).
EXTRACTOR_VERSION = "c-feature-extractor-v1"

# --- F1: frozen JS-API vocabulary (versioned) -------------------------------
# Whole-word tokens searched (case-sensitively) in <script> text. Kept sorted &
# de-duplicated for auditability; contents are pinned to EXTRACTOR_VERSION.
JSAPI_VOCAB: tuple[str, ...] = (
    "AbortController",
    "Array",
    "Date",
    "IntersectionObserver",
    "JSON",
    "Map",
    "Math",
    "MutationObserver",
    "Number",
    "Object",
    "Promise",
    "Set",
    "URL",
    "URLSearchParams",
    "WebSocket",
    "XMLHttpRequest",
    "addEventListener",
    "appendChild",
    "async",
    "await",
    "cancelAnimationFrame",
    "classList",
    "clearInterval",
    "clearTimeout",
    "console",
    "createElement",
    "dataset",
    "document",
    "fetch",
    "getAttribute",
    "getContext",
    "getElementById",
    "getElementsByClassName",
    "getElementsByTagName",
    "innerHTML",
    "insertAdjacentHTML",
    "localStorage",
    "matchMedia",
    "navigator",
    "querySelector",
    "querySelectorAll",
    "removeChild",
    "removeEventListener",
    "requestAnimationFrame",
    "sessionStorage",
    "setAttribute",
    "setInterval",
    "setTimeout",
    "textContent",
    "then",
    "window",
)

# Precompiled whole-word patterns for the JS vocabulary (case-sensitive).
_JSAPI_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = tuple(
    (tok, re.compile(r"\b" + re.escape(tok) + r"\b")) for tok in JSAPI_VOCAB
)

# CSS property-name shape (after trimming a declaration's left-of-colon part).
_CSS_PROP_RE = re.compile(r"^-{0,2}[A-Za-z][A-Za-z0-9-]*$")
# CSS comment stripper (/* ... */).
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


class _CardParser(HTMLParser):
    """Tolerant single-pass HTML reader collecting the four c-feature families."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.classes: set[str] = set()
        # raw CSS text needing property extraction: <style> bodies + inline style attrs
        self._style_blocks: list[str] = []
        self._inline_styles: list[str] = []
        self._script_texts: list[str] = []
        self._in_style = False
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag.lower())
        for name, value in attrs:
            if value is None:
                continue
            lname = name.lower()
            if lname == "class":
                for tok in value.split():
                    if tok:
                        self.classes.add(tok)
            elif lname == "style":
                self._inline_styles.append(value)
        if tag.lower() == "style":
            self._in_style = True
        elif tag.lower() == "script":
            self._in_script = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # self-closing / void element: capture tag + class/style, but no body.
        self.handle_starttag(tag, attrs)
        if tag.lower() in ("style", "script"):
            self._in_style = False
            self._in_script = False

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style":
            self._in_style = False
        elif tag.lower() == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._style_blocks.append(data)
        elif self._in_script:
            self._script_texts.append(data)

    # --- derived family sets ---
    def css_props(self) -> set[str]:
        props: set[str] = set()
        for block in self._style_blocks:
            props |= _css_props_from_block(block, brace_scoped=True)
        for inline in self._inline_styles:
            props |= _css_props_from_block(inline, brace_scoped=False)
        return props

    def jsapi(self) -> set[str]:
        if not self._script_texts:
            return set()
        joined = "\n".join(self._script_texts)
        return {tok for tok, pat in _JSAPI_PATTERNS if pat.search(joined)}


def _css_props_from_block(css_text: str, *, brace_scoped: bool) -> set[str]:
    """Extract CSS property names (lowercased) from CSS text.

    ``brace_scoped=True``  → a full ``<style>`` body: only declarations at brace
    depth ≥ 1 count (selectors + at-rule preludes at depth 0 are ignored, so
    ``a:hover`` / ``@media (max-width:…)`` are not mistaken for properties).
    ``brace_scoped=False`` → an inline ``style="..."`` value: the whole string is a
    declaration list at depth 1.
    """
    text = _CSS_COMMENT_RE.sub(" ", css_text)
    props: set[str] = set()

    if not brace_scoped:
        _collect_props_from_decl_list(text, props)
        return props

    # Walk the block, harvesting declaration text only while inside braces.
    depth = 0
    buf: list[str] = []
    for ch in text:
        if ch == "{":
            # entering a rule body; flush any depth-0 prelude (a selector) unused
            depth += 1
            buf = []
        elif ch == "}":
            if depth >= 1:
                _collect_props_from_decl_list("".join(buf), props)
            depth = max(0, depth - 1)
            buf = []
        elif depth >= 1:
            buf.append(ch)
    # trailing unterminated body
    if depth >= 1 and buf:
        _collect_props_from_decl_list("".join(buf), props)
    return props


def _collect_props_from_decl_list(decls: str, props: set[str]) -> None:
    """From ``prop: value; prop: value`` add each valid lowercased property name.

    A nested ``{`` (e.g. an ``@media`` prelude captured inside the body) resets the
    current declaration so an at-rule feature name is not read as a property.
    """
    for piece in decls.split(";"):
        # a stray '{' means what precedes it is a selector/prelude, not a decl
        if "{" in piece or "}" in piece:
            continue
        head, sep, _ = piece.partition(":")
        if not sep:
            continue
        name = head.strip()
        if _CSS_PROP_RE.match(name):
            props.add(name.lower())


def extract_features(html: str) -> "frozenset[str]":
    """Return the flat namespaced c-feature set for one card's HTML.

    Families: ``tag:`` / ``css:`` / ``class:`` / ``jsapi:`` (see module docstring).
    """
    parser = _CardParser()
    parser.feed(html)
    parser.close()

    feats: set[str] = set()
    feats.update("tag:" + t for t in parser.tags)
    feats.update("css:" + p for p in parser.css_props())
    feats.update("class:" + c for c in parser.classes)
    feats.update("jsapi:" + j for j in parser.jsapi())
    return frozenset(feats)


def feature_breakdown(feats: Iterable[str]) -> dict[str, int]:
    """Per-family counts for diagnostics."""
    out = {"tag": 0, "css": 0, "class": 0, "jsapi": 0}
    for f in feats:
        fam = f.split(":", 1)[0]
        if fam in out:
            out[fam] += 1
    return out


# --- artifact loading -------------------------------------------------------

def _load_html(artifacts: Any) -> str:
    """Resolve ``card.html`` text from a flexible ``artifacts`` argument.

    Accepts:
      * ``str`` already containing HTML (heuristic: contains ``<`` and no NUL and
        is not an existing path)  — mainly for tests,
      * a mapping with ``"html"`` / ``"card_html"`` (raw string),
      * a mapping with ``"card_dir"`` / ``"dir"`` / ``"path"`` (dir or file path),
      * a ``str`` / ``os.PathLike`` path to a card directory (reads ``card.html``)
        or directly to an ``.html`` file.
    """
    if isinstance(artifacts, dict):
        for key in ("html", "card_html"):
            if key in artifacts and artifacts[key] is not None:
                return str(artifacts[key])
        for key in ("card_dir", "dir", "path", "card_html_path"):
            if key in artifacts and artifacts[key] is not None:
                return _read_path(artifacts[key])
        raise KeyError(
            "c-feature: artifacts dict must carry 'html' or a path "
            "('card_dir'/'dir'/'path')"
        )
    if isinstance(artifacts, (str, Path)):
        s = str(artifacts)
        if s == "":
            # Empty raw HTML (degenerate fixture). NOT a path — Path("") == ".".
            return ""
        p = Path(s)
        # Prefer filesystem interpretation when it resolves; else treat as raw HTML.
        if p.exists():
            return _read_path(p)
        if "<" in s:
            return s
        raise FileNotFoundError(f"c-feature: path does not exist: {s!r}")
    raise TypeError(f"c-feature: unsupported artifacts type {type(artifacts)!r}")


def _read_path(path: Any) -> str:
    p = Path(path)
    if p.is_dir():
        p = p / "card.html"
    return p.read_text(encoding="utf-8", errors="replace")


# --- public channel entry point --------------------------------------------

def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """Feature-set Jaccard between two cards.

    Returns ``{"s": float | None, ...diagnostics}``. ``s`` is ``None`` when either
    (or both) feature sets are empty (§4 / appendix A 统一规则).
    """
    feats_a = extract_features(_load_html(card_a_artifacts))
    feats_b = extract_features(_load_html(card_b_artifacts))

    n_a = len(feats_a)
    n_b = len(feats_b)

    diagnostics: dict[str, Any] = {
        "extractor_version": EXTRACTOR_VERSION,
        "n_a": n_a,
        "n_b": n_b,
        "by_category_a": feature_breakdown(feats_a),
        "by_category_b": feature_breakdown(feats_b),
    }

    # Empty on either side (double-empty included) → null.
    if n_a == 0 or n_b == 0:
        diagnostics["intersection"] = 0
        diagnostics["union"] = n_a or n_b or 0
        diagnostics["reason"] = "empty-feature-set"
        return {"s": None, **diagnostics}

    inter = len(feats_a & feats_b)
    union = len(feats_a | feats_b)
    s = inter / union  # union ≥ 1 here since both sides non-empty

    diagnostics["intersection"] = inter
    diagnostics["union"] = union
    return {"s": s, **diagnostics}
