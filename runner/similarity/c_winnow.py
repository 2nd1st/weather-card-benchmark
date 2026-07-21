"""Similarity channel **c-winnow** — token-level winnowing fingerprint Jaccard.

Appendix A row (scheme §4 / COMPARISON-SCHEME-v12, verbatim):

    c-winnow | 词法化(标识符→ID、class→CLS、数字→NUM、字符串→STR);
               k=5 token-gram,winnow w=4;指纹 Jaccard

Pipeline (all deterministic — §0 硬约束 1: 同输入必同输出):

    1. LEXICALIZE ``card.html`` into an ordered token-type stream. Four token
       classes are folded to canonical placeholders (this folding removes the
       surface variation winnowing is designed to see through):
           标识符 → ``ID``      (identifier words)
           class  → ``CLS``     (CSS class-name tokens, see F1 below)
           数字   → ``NUM``     (numeric literals)
           字符串 → ``STR``     (quoted string literals)
       Every other single non-whitespace character is kept verbatim as its own
       token (``<`` ``>`` ``{`` ``}`` ``:`` ``;`` ``(`` ``)`` ``+`` ``.`` …),
       carrying the structural skeleton. Whitespace is a separator, dropped.
    2. k=5 contiguous **token-grams**; each 5-gram is hashed (SHA-256 → 64-bit).
    3. **Winnowing** with window w=4 over the hash sequence: in every window of 4
       consecutive hashes select the minimum. The set of selected values is the
       document fingerprint set (see W1 for the exact set/tie semantics).
    4. ``S = Jaccard(FP_a, FP_b) = |FP_a ∩ FP_b| / |FP_a ∪ FP_b|``.

Per §4 / appendix A 统一规则: an **empty fingerprint set** on *either* side
(double-empty included) → ``S = None`` (null); null never enters a mean. A
fingerprint set is empty when the token stream yields no 5-gram, i.e. fewer than
5 tokens (this subsumes the empty-html case).

Identical non-empty inputs → identical fingerprint sets → ``S = 1.0`` (the
channel maximum), so the self-similarity / matrix diagonal is 1.0 for any card
with ≥5 tokens.

--------------------------------------------------------------------------------
SCHEME-SILENT CHOICES (FLAGGED — appendix A pins the four foldings, ``k=5``,
``w=4`` and "指纹 Jaccard" but NOT the base lexer, the k-gram hash, the winnow
tie/set semantics, or comment handling. The choices below stay maximally
consistent with the stated rules and are frozen under ``EXTRACTOR_VERSION``):

  F1. **"class" = CSS class-name tokens, captured as class selectors ``.name``.**
      The four foldings are a parallel list of lexical *categories* (identifier /
      class / number / string), each collapsed to a placeholder so cosmetic
      variation stops blocking structural matches. Read literally as "fold the
      keyword ``class``" the mapping would be a no-op (that word is identical in
      every card), so the category reading is the one consistent with winnowing's
      purpose. Class names are captured **parse-free** as the CSS selector form
      ``.name`` (a ``.`` immediately followed by an identifier) in ``<style>``
      blocks. Class names carried inside ``class="…"`` HTML attribute *values*
      fall inside a string literal and therefore fold to ``STR`` (see F2); this is
      accepted — the CSS-selector capture is the deterministic, single-pass reading
      of "class → CLS". A JS member access ``a.b`` also matches ``.b`` → ``CLS``;
      this is a deterministic false-positive we accept over introducing an
      HTML/CSS/JS-context parser. NUMBER is tried before CLS so ``.5`` → ``NUM``.
  F2. **Base lexer.** A single leftmost-longest pass over the raw UTF-8 source,
      alternation order STRING → NUMBER → CLS → IDENT → PUNCT. STRING covers
      double/single-quoted literals with backslash escapes (JS template literals
      via backticks are NOT special-cased → the backtick is PUNCT and inner tokens
      lex normally — deterministic). This lexer is intentionally language-agnostic
      (it spans the card's mixed HTML+CSS+JS text in one stream), matching the
      terseness of the appendix row.
  F3. **k-gram hash.** ``int.from_bytes(sha256("\\n".join(5 token strings))[:8])``
      — a stable, salt-free 64-bit hash (Python's builtin ``hash`` is salted and
      would break determinism across processes). ``\\n`` is a safe joiner: no token
      is whitespace, so it cannot occur inside a token string.
  F4. **Winnow set + tie semantics.** The fingerprint is the SET of per-window
      minima: ``{ min(H[i : i+w]) : all windows i }``. Standard (Schleimer et al.)
      winnowing records the *rightmost*-minimum position per window with adjacent-
      window de-duplication; because the recorded value is always the window
      minimum, the set of recorded VALUES is provably identical to the set of
      window minima — so the tie rule does not affect this value set, and Jaccard
      runs over the value set directly.
  F5. **Short-document window.** When the number of 5-grams ``n`` is in ``1..w-1``
      (fewer hashes than a full window), the effective window is ``min(w, n)`` so a
      non-empty k-gram sequence always yields ≥1 fingerprint (the global minimum).
      ``n = 0`` (fewer than 5 tokens) → empty fingerprint set → ``S = None``.
  F6. **No comment stripping.** Unlike c-shingle (appendix: "去注释+压空白+小写"),
      the c-winnow row lists ONLY the four foldings — comment removal is a
      c-shingle-specific normalization the winnow row deliberately omits, so it is
      NOT applied here (the literal reading of the LAW). HTML/CSS/JS comment
      delimiters lex as PUNCT and comment words as ``ID``. If the scheme owner
      wants comments stripped for c-winnow too, that is a one-line change + a
      ``EXTRACTOR_VERSION`` bump.

Directional agreement with the trial's legacy code metrics
(``trial-20260715/analysis/metrics.py``): the legacy pipeline has a **character**
5-shingle Jaccard (``shingle``) and a feature Jaccard, no token-winnowing metric.
c-winnow is the token-level structural cousin of the legacy ``shingle`` and of the
v12 ``c-shingle`` channel; its pair ordering is EXPECTED to correlate POSITIVELY
and fairly strongly with both (all three read code-structure similarity) but NOT
be identical — winnowing is token-level with placeholder folding, subsampled to
fingerprints, whereas ``shingle`` is raw-character overlap. Algorithm NOT copied:
v12 附录 A pins differ.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

# Version of the whole c-winnow extractor (lexer + foldings + k/w + hash +
# winnow semantics). Bump on ANY change, per scheme §11 (extractor_version locked).
EXTRACTOR_VERSION = "c-winnow-extractor-v1"

# Appendix A constants (BYTE-EXACT — never simplify):
K = 5  # token-gram length ("k=5 token-gram")
W = 4  # winnowing window   ("winnow w=4")

# --- F2/F1: single leftmost-longest lexer -----------------------------------
# Alternation order is significant (STRING → NUMBER → CLS → IDENT → PUNCT):
#   * NUMBER before CLS so ".5" lexes as NUM, not a class selector.
#   * CLS before IDENT so a "." + word lexes as CLS (its own category).
# DOTALL lets an escaped char / string body span newlines.
_TOKEN_RE = re.compile(
    r"""
      (?P<STR> "(?:\\.|[^"\\])*"  |  '(?:\\.|[^'\\])*' )
    | (?P<NUM> \d+\.\d+ | \.\d+ | \d+ )
    | (?P<CLS> \.[A-Za-z_-][A-Za-z0-9_-]* )
    | (?P<ID>  [A-Za-z_][A-Za-z0-9_-]* )
    | (?P<PUNCT> \S )
    """,
    re.VERBOSE | re.DOTALL,
)

# Folded categories → placeholder token string. PUNCT is emitted as its literal
# character (the matched text), so structural punctuation is preserved.
_FOLD = {"STR": "STR", "NUM": "NUM", "CLS": "CLS", "ID": "ID"}


def lexicalize(source: str) -> list[str]:
    """Lexicalize raw card source into the ordered token-type stream (F1/F2).

    Each element is a placeholder (``"ID"``/``"CLS"``/``"NUM"``/``"STR"``) or a
    single literal punctuation character. Whitespace is a separator (dropped).
    """
    out: list[str] = []
    for m in _TOKEN_RE.finditer(source):
        kind = m.lastgroup
        out.append(_FOLD.get(kind, m.group()))
    return out


def _kgram_hash(gram: tuple[str, ...]) -> int:
    """F3: stable 64-bit SHA-256 hash of a k-gram of token strings."""
    joined = "\n".join(gram).encode("utf-8")
    return int.from_bytes(hashlib.sha256(joined).digest()[:8], "big")


def kgram_hashes(tokens: list[str], k: int = K) -> list[int]:
    """Hash every contiguous ``k``-token-gram (empty when < k tokens)."""
    n = len(tokens)
    if n < k:
        return []
    return [_kgram_hash(tuple(tokens[i : i + k])) for i in range(n - k + 1)]


def winnow(hashes: list[int], w: int = W) -> frozenset[int]:
    """F4/F5: winnowing fingerprint SET = {min of each window of size w}.

    Effective window is ``min(w, len(hashes))`` so any non-empty hash sequence
    yields ≥1 fingerprint; an empty sequence yields the empty set.
    """
    n = len(hashes)
    if n == 0:
        return frozenset()
    we = w if w < n else n
    return frozenset(min(hashes[i : i + we]) for i in range(n - we + 1))


def fingerprint(source: str) -> frozenset[int]:
    """Full lexer → k-gram → winnow pipeline for one card's source."""
    return winnow(kgram_hashes(lexicalize(source)))


# --- artifact loading (mirrors the c-feature/c-ncd flexible resolver) --------

def _load_source(artifacts: Any) -> str:
    """Resolve ``card.html`` text from a flexible ``artifacts`` argument.

    Accepts: ``None`` → ``""``; a mapping with ``card_html``/``html`` (``None`` →
    ``""``) or a path key (``card_dir``/``dir``/``path``/``card_html_path``); a
    ``str``/``PathLike`` pointing at a card directory (reads ``card.html``) or an
    ``.html`` file; or a raw HTML string. A path whose file does not exist (e.g. a
    card directory that is missing ``card.html``) resolves to ``""`` → ``S=None``.
    """
    if artifacts is None:
        return ""
    if isinstance(artifacts, dict):
        for key in ("card_html", "html"):
            if key in artifacts:
                v = artifacts[key]
                return "" if v is None else str(v)
        for key in ("card_dir", "dir", "path", "card_html_path"):
            if key in artifacts and artifacts[key] is not None:
                return _read_path(artifacts[key])
        return ""
    if isinstance(artifacts, (str, Path)):
        s = str(artifacts)
        p = Path(s)
        if p.exists():
            return _read_path(p)
        # Not an existing path: treat as raw HTML if it looks like markup, else "".
        return s if "<" in s else ""
    raise TypeError(f"c-winnow: unsupported artifacts type {type(artifacts)!r}")


def _read_path(path: Any) -> str:
    p = Path(path)
    if p.is_dir():
        p = p / "card.html"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


# --- public channel entry point ---------------------------------------------

def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """Token-winnowing fingerprint Jaccard between two cards.

    Returns ``{"s": float | None, ...diagnostics}``. ``s`` is ``None`` when either
    (or both) fingerprint sets are empty — i.e. a card with < 5 tokens (§4 /
    appendix A 统一规则). Deterministic and symmetric.
    """
    src_a = _load_source(card_a_artifacts)
    src_b = _load_source(card_b_artifacts)

    tokens_a = lexicalize(src_a)
    tokens_b = lexicalize(src_b)
    fp_a = fingerprint(src_a)
    fp_b = fingerprint(src_b)

    n_a, n_b = len(fp_a), len(fp_b)
    diagnostics: dict[str, Any] = {
        "extractor_version": EXTRACTOR_VERSION,
        "k": K,
        "w": W,
        "n_tokens_a": len(tokens_a),
        "n_tokens_b": len(tokens_b),
        "n_fingerprints_a": n_a,
        "n_fingerprints_b": n_b,
    }

    # Empty fingerprint set on either side (double-empty included) → null.
    if n_a == 0 or n_b == 0:
        diagnostics["intersection"] = 0
        diagnostics["union"] = n_a or n_b or 0
        diagnostics["reason"] = "empty-fingerprint-set"
        return {"s": None, **diagnostics}

    inter = len(fp_a & fp_b)
    union = len(fp_a | fp_b)  # ≥ 1 since both non-empty
    diagnostics["intersection"] = inter
    diagnostics["union"] = union
    diagnostics["reason"] = None
    return {"s": inter / union, **diagnostics}
