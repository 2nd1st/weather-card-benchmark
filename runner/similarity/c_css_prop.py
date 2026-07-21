"""Similarity channel **c-css-prop** — CSS property-name distribution cosine
(scheme §4 / appendix A).

Appendix A row (verbatim):

    c-css-prop | postcss 钉版；property 名频次分布 cosine

i.e. parse a card's CSS with **pinned postcss**, count how often each CSS
*property name* is declared, and take the cosine similarity of the two cards'
property-count vectors.

    S = cos(v_a, v_b) = (v_a · v_b) / (‖v_a‖ · ‖v_b‖)

where ``v_x[p]`` = number of declarations of property ``p`` in card *x* (a
multiset / frequency vector, NOT a presence set — that distinguishes this channel
from ``c-feature``'s Jaccard).

Per §4 / appendix A "统一规则": a **zero vector / empty** distribution on *either*
side (double-empty included) → ``S = None`` (null); null never enters a mean.
Both sides non-empty ⇒ both norms > 0 ⇒ ``S`` is a well-defined value in ``[0, 1]``
(all counts are non-negative, so cosine ∈ [0, 1]).

The parse is delegated to real postcss (``_postcss/extract_props.mjs`` +
``_postcss/node_modules/postcss`` pinned in ``_postcss/package.json``) so that
declarations are identified exactly as postcss does — declarations inside
``@media`` / ``@keyframes`` / ``@supports`` / ``:root`` / nested rules are walked
(``root.walkDecls``), while selectors (``a:hover``) and at-rule preludes
(``@media (min-width: 600px)``) are NOT mistaken for properties. ``decl.prop`` is
emitted verbatim; custom properties (``--foo``) are declarations too and are kept.

The channel is a classical, deterministic, symmetric operation (§0 硬约束 1:
同输入必同输出): postcss parsing is deterministic and the cosine is summed over a
fixed sorted key order.

--------------------------------------------------------------------------------
APPENDIX-A CONSTANTS USED (byte-exact, implement verbatim):
  * parser            = **postcss**, pinned to **8.5.19** (``禁 latest``; see
                        ``_postcss/package.json`` + lockfile).
  * declaration walk  = ``postcss.parse(css).walkDecls`` (all nesting levels).
  * property name     = ``decl.prop`` verbatim (postcss preserves case; custom
                        properties kept).
  * distribution      = per-property **frequency** (declaration count).
  * similarity        = **cosine** of the two frequency vectors.
  * empty rule        = 统一规则: any-side zero-vector/empty (double-empty incl.)
                        → S = null.

SCHEME-SILENT CHOICES (FLAGGED — appendix A pins "postcss / property 名频次分布 /
cosine" but not the HTML→CSS extraction boundary; choices below stay maximally
consistent with the stated rules and are frozen under ``EXTRACTOR_VERSION``):

  P1. **CSS source = ``<style>`` blocks only.** postcss parses *stylesheets*; the
      channel input ``card.html`` is not CSS. We extract the text of every
      ``<style>`` element and hand each block to postcss. Inline ``style="..."``
      attributes are NOT included: they are HTML attribute values, not stylesheet
      text a postcss stylesheet parse would see. (This DIFFERS from ``c-feature``,
      which folds inline styles into its presence set; c-css-prop is a strict
      stylesheet reading of "postcss 钉版". If inline styles were intended, feeding
      each as a synthetic declaration list is a localized change + version bump.)
  P2. **Property name case = verbatim from postcss.** ``decl.prop`` is kept as
      postcss returns it (postcss preserves case). Standard CSS property names are
      case-insensitive per spec, but the LAW pins postcss as *the* implementation
      and postcss does not fold case, so verbatim is the faithful reading. Real
      cards author lowercase, so this is a no-op in practice; a ``COLOR`` vs
      ``color`` split would only appear on adversarial input.
  P3. **Multiple ``<style>`` blocks** are parsed independently and their property
      counts summed (document-order-independent; addition is commutative). A block
      that fails to parse is skipped (counted in ``blocks_failed``); other blocks
      still contribute. If ALL blocks fail / there are none → empty vector → null.
  P4. **Empty rule** follows appendix A 统一规则 (any-empty → null, double-empty →
      null). Note this differs from a bare cosine convention that returns 1.0 for
      two identical zero vectors — v12 is LAW here.

Directional agreement with the trial's legacy metrics: the closest legacy analog
is the CSS-property component of ``feature_set`` in
``trial-20260715/analysis/metrics.py`` (``css:<prop>`` tokens folded into a set
Jaccard, extracted by the naive regex ``([a-z-]+)\\s*:`` over lowercased ``<style>``
text). Agreement is expected to be POSITIVE but only moderate, because (a) legacy
is presence-Jaccard while c-css-prop is frequency-**cosine**, and (b) the legacy
regex miscounts non-declarations (it catches ``min-width`` inside
``@media (min-width:…)`` and any ``prop:`` lookalike in selectors/values) whereas
postcss only yields true declarations. This is a fresh implementation, NOT a port —
v12 附录 A pins differ.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from collections import Counter
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Version of the whole c-css-prop extractor (postcss pin + extraction boundary +
# normalization). Bump on ANY change, per scheme §11 (extractor_version locked).
EXTRACTOR_VERSION = "c-css-prop-extractor-v1"

# Pinned postcss version (mirrors _postcss/package.json; recorded for the lock).
POSTCSS_VERSION = "8.5.19"

_POSTCSS_DIR = Path(__file__).resolve().parent / "_postcss"
_EXTRACT_SCRIPT = _POSTCSS_DIR / "extract_props.mjs"


# --- <style> block extraction (tolerant, single pass) -----------------------

class _StyleExtractor(HTMLParser):
    """Collect the text content of every ``<style>`` element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._in_style = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "style":
            self._in_style = True
            self._buf = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <style/> self-closing: no body.
        if tag.lower() == "style":
            self.blocks.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._in_style:
            self.blocks.append("".join(self._buf))
            self._in_style = False
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._buf.append(data)

    def close(self) -> None:  # flush an unterminated trailing <style>
        if self._in_style:
            self.blocks.append("".join(self._buf))
            self._in_style = False
        super().close()


def _style_blocks(html: str) -> list[str]:
    parser = _StyleExtractor()
    parser.feed(html)
    parser.close()
    return parser.blocks


# --- postcss declaration extraction (subprocess) ----------------------------

@lru_cache(maxsize=1)
def _node_bin() -> str:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError(
            "c-css-prop: 'node' not found on PATH; required to run pinned postcss "
            f"({POSTCSS_VERSION})."
        )
    return node


def _postcss_props(blocks: list[str]) -> dict[str, Any]:
    """Run pinned postcss over ``blocks`` → {'props': [...], 'parsed', 'failed'}."""
    if not (_POSTCSS_DIR / "node_modules" / "postcss").exists():
        raise RuntimeError(
            f"c-css-prop: pinned postcss not installed under {_POSTCSS_DIR}/node_modules; "
            "run `npm install` in that directory."
        )
    proc = subprocess.run(
        [_node_bin(), str(_EXTRACT_SCRIPT)],
        input=json.dumps(blocks),
        cwd=str(_POSTCSS_DIR),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


@lru_cache(maxsize=256)
def _distribution_for_html(html: str) -> tuple[tuple[str, int], ...]:
    """CSS property-name frequency distribution for one card's HTML.

    Returns a sorted tuple of ``(prop, count)`` pairs (sorted by NFC UTF-8 bytes of
    the property name → deterministic, hashable, cache-friendly).
    """
    blocks = _style_blocks(html)
    if not blocks:
        return ()
    result = _postcss_props(blocks)
    counts = Counter(result["props"])
    return tuple(
        sorted(counts.items(), key=lambda kv: kv[0].encode("utf-8"))
    )


def extract_distribution(html: str) -> dict[str, int]:
    """Public: property-name → declaration-count map for one card's HTML."""
    return {prop: n for prop, n in _distribution_for_html(html)}


# --- cosine over frequency vectors ------------------------------------------

def _cosine(dist_a: dict[str, int], dist_b: dict[str, int]) -> float:
    if dist_a == dist_b:
        return 1.0  # cosine of a vector with itself is exactly 1 (avoid sqrt noise)
    keys = sorted(set(dist_a) | set(dist_b), key=lambda k: k.encode("utf-8"))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for k in keys:
        a = dist_a.get(k, 0)
        b = dist_b.get(k, 0)
        dot += a * b
        na += a * a
        nb += b * b
    s = dot / (math.sqrt(na) * math.sqrt(nb))
    # Counts are non-negative ⇒ cosine ∈ [0, 1] mathematically; clip float noise
    # (identical vectors otherwise yield 1.0000000000000002). Matches v-layout.
    if s < 0.0:
        return 0.0
    if s > 1.0:
        return 1.0
    return s


# --- artifact loading (shared convention with sibling channels) -------------

def _load_html(artifacts: Any) -> str:
    """Resolve ``card.html`` text from a flexible ``artifacts`` argument.

    Accepts a raw HTML ``str``; a mapping with ``"html"``/``"card_html"`` or a path
    key (``"card_dir"``/``"dir"``/``"path"``/``"card_html_path"``); or a ``str`` /
    ``Path`` pointing at a card directory (reads ``card.html``) or an ``.html`` file.
    """
    if isinstance(artifacts, dict):
        for key in ("html", "card_html"):
            if key in artifacts and artifacts[key] is not None:
                return str(artifacts[key])
        for key in ("card_dir", "dir", "path", "card_html_path"):
            if key in artifacts and artifacts[key] is not None:
                return _read_path(artifacts[key])
        raise KeyError(
            "c-css-prop: artifacts dict must carry 'html' or a path "
            "('card_dir'/'dir'/'path')"
        )
    if isinstance(artifacts, (str, Path)):
        s = str(artifacts)
        p = Path(s)
        if p.exists():
            return _read_path(p)
        if "<" in s:
            return s
        raise FileNotFoundError(f"c-css-prop: path does not exist: {s!r}")
    raise TypeError(f"c-css-prop: unsupported artifacts type {type(artifacts)!r}")


def _read_path(path: Any) -> str:
    p = Path(path)
    if p.is_dir():
        p = p / "card.html"
    return p.read_text(encoding="utf-8", errors="replace")


# --- public channel entry point ---------------------------------------------

def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """CSS property-name frequency cosine between two cards.

    Returns ``{"s": float | None, ...diagnostics}``. ``s`` is ``None`` when either
    (or both) property distributions are empty (§4 / appendix A 统一规则).
    """
    dist_a = extract_distribution(_load_html(card_a_artifacts))
    dist_b = extract_distribution(_load_html(card_b_artifacts))

    total_a = sum(dist_a.values())
    total_b = sum(dist_b.values())

    diagnostics: dict[str, Any] = {
        "extractor_version": EXTRACTOR_VERSION,
        "postcss_version": POSTCSS_VERSION,
        "n_decl_a": total_a,
        "n_decl_b": total_b,
        "n_unique_a": len(dist_a),
        "n_unique_b": len(dist_b),
    }

    # Zero vector / empty on either side (double-empty included) → null.
    if total_a == 0 or total_b == 0:
        diagnostics["shared_props"] = 0
        diagnostics["reason"] = "empty-distribution"
        return {"s": None, **diagnostics}

    diagnostics["shared_props"] = len(set(dist_a) & set(dist_b))
    s = _cosine(dist_a, dist_b)
    return {"s": s, **diagnostics}
