"""c-ast-js — JavaScript AST node-type bigram similarity (scheme §4, 附录 A).

附录 A row (BYTE-EXACT intent, implemented verbatim):

    c-ast-js | acorn-loose 钉版, ecmaVersion=2023, 先 script 后 module;
              前序遍历相邻 node-type 对 bigram 直方图 cosine;
              解析失败 → token-bigram, 成对一致

Pipeline
--------
1. Extract the card's **inline JavaScript** from ``card.html`` (all inline
   ``<script>`` bodies whose type is a JS type, in document order, joined by
   newlines; external ``src`` scripts and non-JS ``type`` scripts are skipped).
2. Parse with pinned ``acorn-loose@8.4.0`` (``acorn@8.14.0``), ``ecmaVersion``
   2023, **script sourceType first, then module** (the pinned loose parser is
   error-tolerant and essentially never throws — the module fallback and the
   ``ast_ok=False`` path exist for pathological inputs only).
3. Preorder-traverse the AST into a flat node-type sequence; the histogram of
   **adjacent (consecutive-in-preorder) node-type pairs** is the AST bigram
   vector.
4. **Pairwise-consistent fallback (成对一致)**: if *either* side failed to parse
   (``ast_ok=False``), *both* sides use the token-category bigram histogram
   instead — never AST-vs-token across a pair (v3 scheme: 任一侧失败则该 pair
   双侧都用 token-bigram).
5. ``s`` = cosine between the two (same-kind) bigram histograms.

Null rule (§4 / 附录 A 统一规则)
-------------------------------
Zero-vector / empty on either side (double-empty included) → ``s = None``:
  * a card with no inline JS,
  * or JS so degenerate it yields no bigrams (e.g. comment-only, a single
    token), producing an empty histogram.

Input artifact resolution mirrors the other ``c-*`` channels: a card directory
path (reads ``card.html``), a direct ``.html`` path, or a mapping carrying
``card_html`` / ``html`` / ``card_dir`` / ``dir`` / ``path``.

The returned dict always carries::

    s            : float in [0,1] | None
    channel      : "c-ast-js"
    method       : "ast" | "token" | None       — which histogram kind was used
    ast_ok_a/_b  : bool | None                   — per-side acorn-loose success
    n_bigrams_a/_b : int                         — |chosen histogram| (distinct keys)
    n_js_bytes_a/_b : int                        — UTF-8 length of extracted JS
    null_reason  : str | None
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

# 附录 A pinned constants for c-ast-js (see also _ast_js_node/package.json).
CHANNEL = "c-ast-js"
ECMA_VERSION = 2023
ACORN_LOOSE_VERSION = "8.4.0"
ACORN_VERSION = "8.14.0"
SOURCE_ORDER = ("script", "module")  # 先 script 后 module

_UTF8 = "utf-8"
_HERE = Path(__file__).resolve().parent
_NODE_DIR = _HERE / "_ast_js_node"
_NODE_SCRIPT = _NODE_DIR / "ast_repr.mjs"

# Inline <script> types that carry JavaScript we should parse. An absent/empty
# type attribute defaults to JS. Non-JS types (application/json, importmap,
# text/x-template, speculationrules, …) and external (src=) scripts are skipped.
_JS_SCRIPT_TYPES = frozenset({
    "",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "text/ecmascript",
    "application/ecmascript",
    "module",
})

# Module-level repr cache keyed by sha256 of the extracted JS string. Lets the
# 861-pair devset sweep call node once per distinct card body, not per pair.
_REPR_CACHE: dict[str, dict] = {}


# --------------------------------------------------------------------------
# artifact resolution → card.html text
# --------------------------------------------------------------------------
def _read_card_html(artifacts: Any) -> str | None:
    if artifacts is None:
        return None
    if isinstance(artifacts, (str, os.PathLike)):
        p = Path(artifacts)
        if p.is_dir():
            p = p / "card.html"
        if not p.is_file():
            return None
        return p.read_text(encoding=_UTF8)
    if isinstance(artifacts, Mapping):
        for key in ("card_html", "html"):
            if key in artifacts:
                return artifacts[key]
        for key in ("card_dir", "dir", "path"):
            val = artifacts.get(key)
            if val is not None:
                return _read_card_html(val)
        return None
    raise TypeError(
        f"c-ast-js: unsupported artifact type {type(artifacts)!r}; expected "
        "Mapping, str, os.PathLike, or None"
    )


# --------------------------------------------------------------------------
# inline JS extraction from HTML
# --------------------------------------------------------------------------
class _ScriptExtractor(HTMLParser):
    """Collect inline JS <script> bodies. HTMLParser treats <script> content as
    raw CDATA, so handle_data receives the verbatim script text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.chunks: list[str] = []
        self._in_js = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "script":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if "src" in a:                      # external script: no inline body
            self._in_js = False
            return
        stype = a.get("type", "").strip().lower()
        self._in_js = stype in _JS_SCRIPT_TYPES
        self._buf = []

    def handle_data(self, data):
        if self._in_js:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._in_js:
            self.chunks.append("".join(self._buf))
            self._in_js = False
            self._buf = []


def extract_js(html: str | None) -> str:
    """Return the concatenated inline JavaScript of a card (doc order, '\\n'
    joined). Empty string when there is no inline JS."""
    if not html:
        return ""
    p = _ScriptExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed HTML — keep whatever we collected
        pass
    return "\n".join(c for c in p.chunks if c.strip() != "")


# --------------------------------------------------------------------------
# node helper → per-card bigram representations
# --------------------------------------------------------------------------
def _node_bin() -> str:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError(
            "c-ast-js requires Node.js (`node` on PATH) for pinned acorn-loose; "
            "none found."
        )
    return node


def _run_node(js_by_id: dict[str, str]) -> dict[str, dict]:
    """Batch-compute reprs for a {id: js} mapping via the node helper."""
    if not js_by_id:
        return {}
    if not _NODE_SCRIPT.is_file():
        raise RuntimeError(f"c-ast-js node helper missing: {_NODE_SCRIPT}")
    payload = json.dumps({"cards": js_by_id})
    proc = subprocess.run(
        [_node_bin(), str(_NODE_SCRIPT)],
        input=payload.encode(_UTF8),
        cwd=str(_NODE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"c-ast-js node helper failed (rc={proc.returncode}): "
            f"{proc.stderr.decode(_UTF8, 'replace')[:500]}"
        )
    return json.loads(proc.stdout.decode(_UTF8))


def _js_key(js: str) -> str:
    return hashlib.sha256(js.encode(_UTF8)).hexdigest()


def prime(js_values) -> None:
    """Warm ``_REPR_CACHE`` for an iterable of JS strings in ONE node call.

    Used by the devset matrix sweep so acorn-loose runs once per distinct card
    body instead of once per pair. Idempotent; safe to call repeatedly."""
    want: dict[str, str] = {}
    for js in js_values:
        k = _js_key(js)
        if k not in _REPR_CACHE and k not in want:
            want[k] = js
    if want:
        _REPR_CACHE.update(_run_node(want))


def _get_reprs(js_a: str, js_b: str) -> tuple[dict, dict]:
    """Reprs for two JS strings, using/filling the cache (≤1 node call)."""
    ka, kb = _js_key(js_a), _js_key(js_b)
    need = {k: js for k, js in ((ka, js_a), (kb, js_b)) if k not in _REPR_CACHE}
    if need:
        _REPR_CACHE.update(_run_node(need))
    return _REPR_CACHE[ka], _REPR_CACHE[kb]


# --------------------------------------------------------------------------
# cosine over sparse bigram histograms
# --------------------------------------------------------------------------
def _cosine(hist_a: Mapping[str, int], hist_b: Mapping[str, int]) -> float | None:
    """Cosine of two sparse count histograms. Zero-vector on either side → None."""
    na = math.sqrt(sum(v * v for v in hist_a.values()))
    nb = math.sqrt(sum(v * v for v in hist_b.values()))
    if na == 0.0 or nb == 0.0:
        return None
    if hist_a == hist_b:
        return 1.0  # cosine of a vector with itself is exactly 1 (avoid sqrt noise)
    lo, hi = (hist_a, hist_b) if len(hist_a) <= len(hist_b) else (hist_b, hist_a)
    dot = 0.0
    for k, v in lo.items():
        w = hi.get(k)
        if w is not None:
            dot += v * w
    s = dot / (na * nb)
    # counts are non-negative ⇒ s ∈ [0, 1] mathematically; clip float noise.
    return min(1.0, max(0.0, s))


def _null(reason: str, **extra) -> dict:
    base = {
        "s": None,
        "channel": CHANNEL,
        "method": None,
        "ast_ok_a": None,
        "ast_ok_b": None,
        "n_bigrams_a": 0,
        "n_bigrams_b": 0,
        "n_js_bytes_a": 0,
        "n_js_bytes_b": 0,
        "null_reason": reason,
    }
    base.update(extra)
    return base


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """Compute the c-ast-js similarity between two cards' inline JavaScript.

    See the module docstring for the returned dict shape and null semantics.
    ``s`` is a cosine in [0,1]; identical non-degenerate JS → 1.0.
    """
    html_a = _read_card_html(card_a_artifacts)
    html_b = _read_card_html(card_b_artifacts)
    js_a = extract_js(html_a)
    js_b = extract_js(html_b)
    nb_a, nb_b = len(js_a.encode(_UTF8)), len(js_b.encode(_UTF8))

    # §4: no inline JS on either side (double-empty included) → zero-vector → null.
    if js_a.strip() == "" or js_b.strip() == "":
        return _null("no-js", n_js_bytes_a=nb_a, n_js_bytes_b=nb_b)

    repr_a, repr_b = _get_reprs(js_a, js_b)
    ast_ok_a = bool(repr_a["ast_ok"])
    ast_ok_b = bool(repr_b["ast_ok"])

    # 成对一致: AST bigrams only if BOTH parsed; otherwise token bigrams for both.
    if ast_ok_a and ast_ok_b:
        method = "ast"
        hist_a = repr_a["ast_bigrams"]
        hist_b = repr_b["ast_bigrams"]
    else:
        method = "token"
        hist_a = repr_a["token_bigrams"]
        hist_b = repr_b["token_bigrams"]

    n_a, n_b = len(hist_a), len(hist_b)
    diag = {
        "channel": CHANNEL,
        "method": method,
        "ast_ok_a": ast_ok_a,
        "ast_ok_b": ast_ok_b,
        "n_bigrams_a": n_a,
        "n_bigrams_b": n_b,
        "n_js_bytes_a": nb_a,
        "n_js_bytes_b": nb_b,
        "null_reason": None,
    }

    # Degenerate JS → empty bigram histogram → zero-vector → null (§4).
    if n_a == 0 or n_b == 0:
        diag["s"] = None
        diag["null_reason"] = "empty-bigram-histogram"
        return diag

    diag["s"] = _cosine(hist_a, hist_b)
    if diag["s"] is None:  # defensive; unreachable given the guard above
        diag["null_reason"] = "zero-vector"
    return diag
