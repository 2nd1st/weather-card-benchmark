"""c-shingle — character k-shingle Jaccard similarity (formal channel).

Scheme §4 + 附录 A (BYTE-EXACT, implemented verbatim):

    c-shingle | normalize = 去注释 + 压空白 + 小写; k = 5 字符 shingle, Jaccard

i.e. normalize the card's HTML by (1) removing comments, (2) collapsing
whitespace, (3) lowercasing, then form the SET of all length-5 character
substrings (shingles) and take the Jaccard index of the two sets.

Input artifact for a code (``c-*``) channel is the card's ``card.html`` file.

§4 "统一规则" (unified rule): 零向量/空集/空 bag → 任一侧 → 该 pair S=null
(双空亦 null). Here the "empty set" is an empty shingle set (either side, and
the double-empty case) → ``s = None``.

── SCHEME-SILENT judgment calls (FLAGGED — see module report) ─────────────
Appendix A pins only "remove comments / collapse whitespace / lowercase" and
"k=5 char shingle, Jaccard". It does NOT pin the comment grammar, the exact
whitespace class, or whether Unicode NFC is applied. This module fixes:

  * COMMENTS: a single left-to-right scan removes, at each position, whichever
    of these opens first —
        - HTML comment   ``<!--`` … ``-->``
        - block comment  ``/*`` … ``*/``          (CSS + JS)
        - line comment   ``//`` … end-of-line     (JS) — BUT ONLY when the
          ``//`` is NOT immediately preceded by ``:`` in the raw source, so
          URL schemes (``https://`` / ``http://`` / ``file://``) survive.
      An unterminated comment runs to end-of-string. This is the choice most
      consistent with "去注释" over an HTML document that embeds CSS + JS while
      staying deterministic and reproducible across implementations.
  * WHITESPACE: runs of ASCII whitespace ``[ \\t\\n\\r\\f\\v]+`` collapse to a
    single U+0020 space; leading/trailing space stripped. (ASCII-only avoids
    Unicode-whitespace ambiguity across implementations.)
  * LOWERCASE: Python ``str.lower()`` — 小写 as written (NOT ``casefold``,
    which the scheme reserves for the fidelity text-normalize).
  * NFC: NOT applied. Appendix A enumerates exactly three ops for c-shingle
    and omits NFC (contrast c-ncd / fidelity, which name NFC explicitly);
    "implement verbatim, never simplify" → no extra normalization added.

Order of ops: comments → lowercase → whitespace-collapse → strip. (lowercase
and whitespace-collapse commute; comment removal must run on the raw source.)

Returned dict:
    s              : float in [0,1] | None   — Jaccard index (channel value)
    channel        : "c-shingle"
    k              : 5
    n_shingles_a   : int                     — |shingle set A|
    n_shingles_b   : int                     — |shingle set B|
    intersection   : int                     — |A ∩ B|
    union          : int                     — |A ∪ B|
    norm_len_a     : int                     — len of normalized text A
    norm_len_b     : int                     — len of normalized text B
    empty_a        : bool                     — shingle set A is empty
    empty_b        : bool                     — shingle set B is empty
    null_reason    : str | None               — why s is None (diagnostic)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping

# 附录 A pinned constant for c-shingle — implement verbatim, never simplify.
K = 5  # k = 5 字符 shingle

_UTF8 = "utf-8"
_WS_RUN = re.compile(r"[ \t\n\r\f\v]+")  # ASCII whitespace class (see FLAG)


# --------------------------------------------------------------------------
# artifact resolution (shared convention with the other c-* channels)
# --------------------------------------------------------------------------
def _read_card_html(artifacts: Any) -> str | None:
    """Resolve the card.html text from a channel artifact handle.

    Accepts, in order of preference:
      * ``Mapping`` with ``card_html`` or ``html`` (raw string, may be None)
      * ``Mapping`` with ``card_dir`` / ``dir`` / ``path`` (dir or file path)
      * ``str`` / ``os.PathLike`` at a card directory (reads ``card.html``)
        or directly at an ``.html`` file
    Returns the raw HTML string, or ``None`` when no card.html artifact exists.
    """
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
        f"c-shingle: unsupported artifact type {type(artifacts)!r}; expected "
        "Mapping, str, os.PathLike, or None"
    )


# --------------------------------------------------------------------------
# normalization (附录 A: 去注释 + 压空白 + 小写)
# --------------------------------------------------------------------------
def strip_comments(src: str) -> str:
    """Remove HTML / block / guarded-line comments in a single L→R scan.

    See module docstring for the exact (FLAGGED) grammar. Deterministic and
    order-free: at each index the earliest-opening comment kind wins; an
    unterminated comment runs to the end of the string.
    """
    n = len(src)
    out: list[str] = []
    i = 0
    while i < n:
        c = src[i]
        # HTML comment: <!-- ... -->
        if c == "<" and src.startswith("<!--", i):
            end = src.find("-->", i + 4)
            i = n if end == -1 else end + 3
            continue
        if c == "/" and i + 1 < n:
            nxt = src[i + 1]
            # block comment: /* ... */
            if nxt == "*":
                end = src.find("*/", i + 2)
                i = n if end == -1 else end + 2
                continue
            # line comment: // ... EOL — but NOT part of a URL scheme (`://`)
            if nxt == "/" and (i == 0 or src[i - 1] != ":"):
                end = src.find("\n", i + 2)
                i = n if end == -1 else end  # keep the newline (WS-collapsed later)
                continue
        out.append(c)
        i += 1
    return "".join(out)


def normalize(html: str | None) -> str:
    """去注释 → 小写 → 压空白 → trim. None/'' → ''."""
    if not html:
        return ""
    text = strip_comments(html)
    text = text.lower()                 # 小写 (str.lower, not casefold — FLAG)
    text = _WS_RUN.sub(" ", text)       # 压空白 → single U+0020
    return text.strip()


# --------------------------------------------------------------------------
# shingles + Jaccard
# --------------------------------------------------------------------------
def shingles(text: str, k: int = K) -> set[str]:
    """Set of all length-k character substrings of ``text`` (empty if <k)."""
    if len(text) < k:
        return set()
    return {text[i : i + k] for i in range(len(text) - k + 1)}


def _jaccard(a: set[str], b: set[str]) -> float | None:
    """|a∩b| / |a∪b|; None if either side empty (double-empty included)."""
    if not a or not b:
        return None
    inter = len(a & b)
    union = len(a | b)
    return inter / union  # union > 0 here (both non-empty)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """c-shingle similarity between two cards' ``card.html``.

    Returns ``{"s": float | None, ...diagnostics}``. ``s`` is None when either
    side's k-shingle set is empty (missing / trivially short HTML), per §4.
    """
    na = normalize(_read_card_html(card_a_artifacts))
    nb = normalize(_read_card_html(card_b_artifacts))

    sa = shingles(na)
    sb = shingles(nb)

    empty_a = not sa
    empty_b = not sb
    s = _jaccard(sa, sb)

    if s is None:
        if empty_a and empty_b:
            reason: str | None = "empty-shingle-set-both"
        elif empty_a:
            reason = "empty-shingle-set-a"
        else:
            reason = "empty-shingle-set-b"
        inter = union = 0
    else:
        reason = None
        inter = len(sa & sb)
        union = len(sa | sb)

    return {
        "s": s,
        "channel": "c-shingle",
        "k": K,
        "n_shingles_a": len(sa),
        "n_shingles_b": len(sb),
        "intersection": inter,
        "union": union,
        "norm_len_a": len(na),
        "norm_len_b": len(nb),
        "empty_a": empty_a,
        "empty_b": empty_b,
        "null_reason": reason,
    }
