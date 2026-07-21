"""d-text — visible-text similarity channel (COMPARISON-SCHEME-v12.md §4 + 附录 A).

Appendix-A definition (the single implementation authority):

    d-text | 可见文本剔除动态字段;k=5 token shingle,Jaccard

i.e. take the card's **visible text**, **excise the dynamic fields**, tokenize,
build **k = 5 token shingles**, and score two cards by the **Jaccard** of their
shingle sets.

Input artifact (a DOM ``d-*`` channel) is the card's ``dom.json`` (dom-dump-v1,
see devset-42/DOM-DUMP-FORMAT.md). The visible-text-node set is exactly the
fidelity §3 set: nodes with ``type == "text"`` and ``text_visible == true``,
walked in DOM preorder (``preorder`` order, == ``visible_text_index`` order).

--------------------------------------------------------------------------------
IMPLEMENTATION DECISIONS — appendix A is terse; these are the choices this module
pins. Every scheme-silent choice is FLAGGED in the implementation report.
--------------------------------------------------------------------------------

1. **"可见文本" (visible text).** The dom-dump ``text_visible`` gate (dom-dump-v1)
   is the §3 fidelity visible-text gate; DOM-DUMP-FORMAT.md explicitly names this
   same set as the d-text input. We take ``text_raw`` verbatim from those nodes.

2. **Normalization.** The scheme's canonical text normalization (附录 A 保真:
   "NFC + trim + 空白折叠 + casefold") is applied. Because tokens are extracted with
   a Unicode word-token regex (``\\w+``), the *trim* and *whitespace-fold* steps are
   subsumed by tokenization (all inter-token whitespace is a delimiter); only NFC
   and casefold change token CONTENT. We apply NFC → casefold, in the order the
   scheme lists them. [FLAG: scheme does not spell out the d-text tokenizer; we use
   the standard §3 normalization + a ``\\w+`` Unicode word tokenizer.]

3. **"剔除动态字段" (excise dynamic fields).** The scheme does NOT enumerate what a
   "dynamic field" is. Since every devset / batch card renders the SAME frozen
   weather snapshot, the dynamic *data* is precisely the numeric values that vary
   with the data (temperatures, hourly clock labels, dates, percentages, wind
   speeds…). We define a token as dynamic iff it CONTAINS ANY UNICODE DIGIT and
   REMOVE it entirely ("剔除" = excise, not placeholder-substitute). This excises the
   weather data while retaining the card's design vocabulary (labels like "today",
   "max", "min", "humidity", "wind", condition words). [FLAG — scheme-silent: the
   exact dynamic-field definition is a judgment call; a WMO condition string (e.g.
   "cloudy") is arguably dynamic too but is textual, not numeric, and is not
   enumerated, so it is retained. Chosen rule = digit-bearing tokens, being the most
   deterministic, language-independent reading most consistent with the stated
   rules.]

4. **Token stream & shingling across node boundaries.** Tokens are extracted
   PER visible text node (so two adjacent nodes never merge into one token — cf. §3
   "节点间无分隔符" is about text COORDINATES, not token joining), then the per-node
   token lists are CONCATENATED in preorder into one global token sequence, and
   k=5 shingles slide over the concatenated sequence (shingles MAY span node
   boundaries). This is required for the channel to produce data at all: weather
   cards are dominated by short 1–2-token text nodes, so within-node 5-token
   shingles would be empty almost everywhere → S=null everywhere. [FLAG —
   scheme-silent on node-boundary handling; cross-node shingling chosen.]

5. **Shingle set & Jaccard.** Shingles are DISTINCT 5-token tuples (a set, as
   Jaccard is a set operation — matching the sibling c-shingle "Jaccard"). Jaccard
   = |A ∩ B| / |A ∪ B|.

6. **Null semantics (§4).** A side's shingle set is empty when it has < 5 retained
   tokens (or no dom / no visible text). Per §4 ("零向量/空集/空 bag:任一侧 → S=null;
   双空亦 null") either empty side → ``s = None`` (double-empty included). An
   identity pair with a non-empty shingle set scores exactly 1.0.

The returned dict always carries::

    s            : float in [0,1] | None   — Jaccard of the 5-token shingle sets
    channel      : "d-text"
    k            : 5
    tokens_a     : int   — retained (post-excision) token count, side a
    tokens_b     : int   — retained token count, side b
    shingles_a   : int   — |shingle set a|
    shingles_b   : int   — |shingle set b|
    intersection : int | None
    union        : int | None
    note         : str | None   — why s is None (diagnostic)
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

CHANNEL = "d-text"

# 附录 A pinned constant.
K = 5  # k = 5 token shingle

# Unicode word tokenizer (letters / digits / underscore runs) and the
# dynamic-field predicate (any Unicode decimal digit).
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_DIGIT_RE = re.compile(r"\d", re.UNICODE)

_NFC = "NFC"
_UTF8 = "utf-8"


# --------------------------------------------------------------------------
# artifact resolution — mirror the sibling channels' flexible handles
# --------------------------------------------------------------------------
def _resolve_dom(artifacts: Any) -> dict | None:
    """Resolve a parsed ``dom.json`` object from a channel artifact handle.

    Accepts, in order of precedence:
      * ``None`` → ``None``;
      * ``Mapping`` carrying the dom directly (has a ``"nodes"`` key) → itself;
      * ``Mapping`` with ``"dom"`` (parsed dom dict, may be ``None``) → that;
      * ``Mapping`` with ``"dom_json"`` (bytes / JSON str / path) → parsed;
      * ``Mapping`` with ``"dir"`` / ``"card_dir"`` / ``"path"`` → load ``dom.json``;
      * ``os.PathLike`` / ``str`` → a card directory containing ``dom.json`` or a
        ``.json`` file directly.

    Returns the parsed dom object, or ``None`` when nothing usable / file missing.
    """
    if artifacts is None:
        return None

    if isinstance(artifacts, Mapping):
        if "nodes" in artifacts:  # the dom object itself
            return dict(artifacts)
        if "dom" in artifacts:
            dom = artifacts["dom"]
            return dom if isinstance(dom, Mapping) else None
        val = artifacts.get("dom_json")
        if val is not None:
            return _parse_dom(val)
        for key in ("dir", "card_dir", "path"):
            d = artifacts.get(key)
            if d is not None:
                return _load_dom_file(os.path.join(os.fspath(d), "dom.json"))
        return None

    if isinstance(artifacts, (str, os.PathLike)):
        p = os.fspath(artifacts)
        if os.path.isdir(p):
            return _load_dom_file(os.path.join(p, "dom.json"))
        return _load_dom_file(p)

    raise TypeError(
        f"d-text: unsupported artifact type {type(artifacts)!r}; expected "
        "Mapping, str, os.PathLike, or None"
    )


def _parse_dom(val: Any) -> dict | None:
    """Parse a dom object from bytes / a JSON string / a filesystem path."""
    if isinstance(val, Mapping):
        return dict(val)
    if isinstance(val, (bytes, bytearray)):
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return None
    if isinstance(val, str):
        # A JSON document string, else a path.
        stripped = val.lstrip()
        if stripped[:1] in ("{", "["):
            try:
                return json.loads(val)
            except ValueError:
                return None
        return _load_dom_file(val)
    if isinstance(val, os.PathLike):
        return _load_dom_file(os.fspath(val))
    return None


def _load_dom_file(path: str) -> dict | None:
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read())
    except (OSError, ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# tokenization
# --------------------------------------------------------------------------
def _normalize(text: str) -> str:
    """§3/附录 A text normalization relevant to \\w+ tokenization: NFC → casefold.

    (trim + whitespace-fold are subsumed by \\w+ tokenization; only NFC and
    casefold change token content.)
    """
    return unicodedata.normalize(_NFC, text).casefold()


def _is_dynamic(token: str) -> bool:
    """A token is a dynamic field iff it bears any Unicode digit (§4, see docstring)."""
    return _DIGIT_RE.search(token) is not None


def visible_text_tokens(dom: dict | None) -> list[str]:
    """Retained (post dynamic-field excision) token sequence for one card.

    Visible text nodes (``type == "text"`` ∧ ``text_visible``) in preorder →
    NFC+casefold → ``\\w+`` tokens → drop digit-bearing (dynamic) tokens →
    concatenated global sequence.
    """
    if not isinstance(dom, Mapping):
        return []
    nodes = dom.get("nodes")
    if not isinstance(nodes, list):
        return []
    text_nodes = [
        n
        for n in nodes
        if isinstance(n, Mapping)
        and n.get("type") == "text"
        and n.get("text_visible")
    ]
    # dom-dump-v1 already emits preorder, but sort defensively on the explicit key.
    text_nodes.sort(key=lambda n: n.get("preorder", 0))

    tokens: list[str] = []
    for n in text_nodes:
        raw = n.get("text_raw")
        if not isinstance(raw, str) or not raw:
            continue
        for tok in _TOKEN_RE.findall(_normalize(raw)):
            if not _is_dynamic(tok):
                tokens.append(tok)
    return tokens


def shingles(tokens: list[str], k: int = K) -> set[tuple[str, ...]]:
    """Set of DISTINCT k-token shingles (tuples) over the token sequence."""
    if len(tokens) < k:
        return set()
    return {tuple(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


# --------------------------------------------------------------------------
# channel entry point
# --------------------------------------------------------------------------
def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """Compute the d-text (visible-text 5-token-shingle Jaccard) similarity.

    See the module docstring for the returned dict shape and the FLAGGED
    scheme-silent decisions. ``s`` is ``None`` when either side has an empty
    shingle set (< 5 retained tokens / no dom / no visible text), double-empty
    included (§4).
    """
    dom_a = _resolve_dom(card_a_artifacts)
    dom_b = _resolve_dom(card_b_artifacts)

    tokens_a = visible_text_tokens(dom_a)
    tokens_b = visible_text_tokens(dom_b)

    sh_a = shingles(tokens_a, K)
    sh_b = shingles(tokens_b, K)

    if not sh_a or not sh_b:
        return {
            "s": None,
            "channel": CHANNEL,
            "k": K,
            "tokens_a": len(tokens_a),
            "tokens_b": len(tokens_b),
            "shingles_a": len(sh_a),
            "shingles_b": len(sh_b),
            "intersection": None,
            "union": None,
            "note": "empty-shingle-set",
        }

    inter = len(sh_a & sh_b)
    union = len(sh_a | sh_b)
    s = inter / union  # union > 0 guaranteed (both sides non-empty)

    return {
        "s": s,
        "channel": CHANNEL,
        "k": K,
        "tokens_a": len(tokens_a),
        "tokens_b": len(tokens_b),
        "shingles_a": len(sh_a),
        "shingles_b": len(sh_b),
        "intersection": inter,
        "union": union,
        "note": None,
    }
