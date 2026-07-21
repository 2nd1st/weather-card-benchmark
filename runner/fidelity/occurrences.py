"""Occurrence matcher (scheme §3 候选身份 = occurrence).

Pipeline (all on NORMALIZED per-node visible text, codepoint offsets only):

  1. **raw hits** — each versioned pattern is run over each visible text node's
     normalized string, producing ``(node, start, end, pattern_id)`` hits with a
     ``category`` ∈ {temperature, max, min, hourly} (scheme §3 patterns 逐个版本化;
     start/end = 节点内 Unicode 码点偏移 — Python ``re`` on a ``str`` yields
     codepoint indices, so NO UTF-16 anywhere).
  2. **same-span merge** — hits sharing an exact ``(node,start,end)`` span merge
     into one occurrence whose ``merged_categories`` summarizes the classes; a
     label occurrence whose summary contains BOTH max and min is **voided**
     (scheme §3 同 span 合并汇总类别; 汇总含两类才作废).
  3. **leftmost-longest arbitration** — merged spans sorted by
     ``(node preorder, start asc, length desc, pattern_id lexicographic)`` and
     greedily kept when not overlapping an already-selected span in the same node
     (scheme §3 / 附录 A leftmost-longest 全句).
  4. **namespace pools** — survivors are pooled by namespace
     (temperature / max-label / min-label / hourly-label); occurrence
     ID = (node preorder, start, end); occurrence order = ID lexicographic.

The versioned patterns are the sole matcher authority; every scheme-silent
lexical choice is FLAGGED in the R5 report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .normalize import DomModel

# ---------------------------------------------------------------------------
# versioned patterns (scheme §3 pattern 逐个版本化)
# ---------------------------------------------------------------------------
# Temperature candidate: 数字 + 度符号/单位 (scheme §3). A signed decimal number
# immediately (optionally space-separated) followed by a degree indicator
# (° optionally + c/f, or ℃ / ℉). The number-with-no-degree case is NOT a
# candidate (literal §3 reading — see R5 report). Casefolded input, so c/f lower.
#
# SIGN GLYPH CLASS (R5 DEFECT 1 fix). §3 is silent on the sign glyph and 附录 A
# text-normalization (NFC+trim+空白折叠+casefold) does NOT fold the Unicode
# minus/dash glyphs (U+2212 MINUS, U+2013/2014 dashes, U+2010 HYPHEN, U+00AD soft
# hyphen) to ASCII '-'. The former ``-?`` recognized ASCII hyphen-minus ONLY, which
# was inconsistent BOTH ways (real typographic negatives read as positive; ASCII
# range separators fabricated negatives). Per "scheme-silent → choose the option
# most consistent with stated rules", the sign is treated as a NEGATIVE indicator
# for the whole minus/dash class, but ONLY when (a) directly adjacent to the leading
# digit and (b) NOT preceded by a digit. Guard (b) rejects range separators
# ("3-5°"/"3–5°" → +5, never a fabricated -5); guard (a) — no ``\s*`` between sign
# and digit — rejects spaced decorative dashes ("Today — 5°" → +5). The matched
# glyph is folded to ASCII '-' before Decimal parsing (below).
# leading ASCII '-' (literal in a char class) + U+2010 U+2013 U+2014 U+2212 U+00AD.
_MINUS_CLASS = "-‐–—−­"
_MINUS_TABLE = {ord(c): "-" for c in _MINUS_CLASS if c != "-"}
_SIGN = rf"(?:(?<!\d)[{_MINUS_CLASS}])?"
_TEMP_RE = re.compile(rf"{_SIGN}\d+(?:\.\d+)?\s*(?:°\s*[cf]?|℃|℉)")
_TEMP_NUM_RE = re.compile(rf"{_SIGN}\d+(?:\.\d+)?")
TEMP_PATTERN_ID = "temp-number-degree-v1"

# max / min daily labels (versioned word list; casefolded, word-bounded).
_MAX_RE = re.compile(r"\b(?:high|max|maximum|hi)\b")
_MIN_RE = re.compile(r"\b(?:low|min|minimum|lo)\b")
MAX_PATTERN_ID = "max-label-v1"
MIN_PATTERN_ID = "min-label-v1"

# hourly clock labels (versioned template table → UTC hour 0..23).
#   HH:mm / HH:00      e.g. "00:00", "15:30"
#   h AM/PM           e.g. "3 pm", "11 am"
#   HHh               e.g. "15h"
_HOUR_HHMM_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_HOUR_AMPM_RE = re.compile(r"\b(0?\d|1[0-2])\s*(am|pm)\b")
_HOUR_HH_RE = re.compile(r"\b([01]?\d|2[0-3])h\b")
HOUR_PATTERN_ID = "hourly-label-v1"

# categories
CAT_TEMP = "temperature"
CAT_MAX = "max"
CAT_MIN = "min"
CAT_HOURLY = "hourly"

NS_TEMP = "temperature"
NS_MAX = "max-label"
NS_MIN = "min-label"
NS_HOURLY = "hourly-label"


@dataclass(frozen=True)
class Occurrence:
    """A matched occurrence; ID = (node, start, end); order = ID lexicographic."""

    node: int
    start: int
    end: int
    pattern_id: str
    namespace: str
    parent: int  # enclosing element preorder
    merged_categories: tuple[str, ...]
    voided: bool
    voided_reason: str | None
    text_coord: int  # base + start
    # temperature extras
    value: Decimal | None = None
    display_decimals: int | None = None
    display_str: str | None = None
    font_size: float | None = None
    # hourly-label extra
    parsed_hour: int | None = None

    @property
    def oid(self) -> tuple[int, int, int]:
        return (self.node, self.start, self.end)


@dataclass
class _RawHit:
    node: int
    start: int
    end: int
    pattern_id: str
    category: str
    parent: int
    # extras captured at match time
    value: Decimal | None = None
    display_decimals: int | None = None
    display_str: str | None = None
    parsed_hour: int | None = None


@dataclass
class _Considered:
    node_preorder: int
    start: int
    length: int
    pattern_id: str
    selected: bool


@dataclass
class OccurrenceResult:
    """Everything downstream + the trace needs from the matcher."""

    occurrences: list[Occurrence]  # arbitration survivors, ID order
    considered: list[_Considered]  # arbitration log (schema arbitration.considered)
    pools: dict[str, list[Occurrence]] = field(default_factory=dict)

    def pool(self, ns: str) -> list[Occurrence]:
        return self.pools.get(ns, [])


def _parse_hour(m: re.Match, kind: str) -> int | None:
    if kind == "hhmm":
        return int(m.group(1))
    if kind == "hh":
        return int(m.group(1))
    if kind == "ampm":
        h = int(m.group(1)) % 12
        if m.group(2) == "pm":
            h += 12
        return h
    return None


def _raw_hits(model: DomModel) -> list[_RawHit]:
    hits: list[_RawHit] = []
    for vt in model.visible:
        text = vt.norm
        node, parent = vt.preorder, vt.parent

        for m in _TEMP_RE.finditer(text):
            num_m = _TEMP_NUM_RE.match(text, m.start())
            num_raw = num_m.group(0) if num_m else text[m.start() : m.end()]
            # fold any Unicode minus/dash sign glyph → ASCII '-' for Decimal parsing.
            num = num_raw.translate(_MINUS_TABLE)
            if "." in num:
                dd = len(num.split(".", 1)[1])
            else:
                dd = 0
            hits.append(
                _RawHit(
                    node, m.start(), m.end(), TEMP_PATTERN_ID, CAT_TEMP, parent,
                    value=Decimal(num), display_decimals=dd, display_str=num,
                )
            )
        for m in _MAX_RE.finditer(text):
            hits.append(_RawHit(node, m.start(), m.end(), MAX_PATTERN_ID, CAT_MAX, parent))
        for m in _MIN_RE.finditer(text):
            hits.append(_RawHit(node, m.start(), m.end(), MIN_PATTERN_ID, CAT_MIN, parent))
        for regex, kind in ((_HOUR_HHMM_RE, "hhmm"), (_HOUR_AMPM_RE, "ampm"), (_HOUR_HH_RE, "hh")):
            for m in regex.finditer(text):
                hits.append(
                    _RawHit(
                        node, m.start(), m.end(), HOUR_PATTERN_ID, CAT_HOURLY, parent,
                        parsed_hour=_parse_hour(m, kind),
                    )
                )
    return hits


def _namespace_for(cats: set[str]) -> tuple[str, bool, str | None]:
    """Namespace + void decision for a merged span's category summary."""
    if CAT_TEMP in cats:
        return NS_TEMP, False, None
    if CAT_MAX in cats and CAT_MIN in cats:
        # label span summarizing BOTH max and min → voided (scheme §3).
        return NS_MAX, True, "span-has-both-max-and-min"
    if CAT_MAX in cats:
        return NS_MAX, False, None
    if CAT_MIN in cats:
        return NS_MIN, False, None
    return NS_HOURLY, False, None


def match_occurrences(model: DomModel) -> OccurrenceResult:
    hits = _raw_hits(model)

    # -- same-span merge -----------------------------------------------------
    by_span: dict[tuple[int, int, int], list[_RawHit]] = {}
    for h in hits:
        by_span.setdefault((h.node, h.start, h.end), []).append(h)

    merged: list[Occurrence] = []
    for (node, start, end), group in by_span.items():
        cats = {h.category for h in group}
        ns, voided, vreason = _namespace_for(cats)
        # representative pattern id = lexicographically smallest in the span.
        pid = min(h.pattern_id for h in group)
        parent = group[0].parent
        # temperature extras (take the temp hit if present; a span is temp XOR label).
        temp = next((h for h in group if h.category == CAT_TEMP), None)
        hourly = next((h for h in group if h.category == CAT_HOURLY), None)
        merged.append(
            Occurrence(
                node=node, start=start, end=end, pattern_id=pid, namespace=ns,
                parent=parent, merged_categories=tuple(sorted(cats)),
                voided=voided, voided_reason=vreason,
                text_coord=model.text_coord(node, start),
                value=temp.value if temp else None,
                display_decimals=temp.display_decimals if temp else None,
                display_str=temp.display_str if temp else None,
                font_size=model.font_size(parent),
                parsed_hour=hourly.parsed_hour if hourly else None,
            )
        )

    # -- leftmost-longest arbitration ---------------------------------------
    # sort by (node, start asc, length desc, pattern_id lexicographic).
    ordered = sorted(merged, key=lambda o: (o.node, o.start, -(o.end - o.start), o.pattern_id))
    selected: list[Occurrence] = []
    considered: list[_Considered] = []
    kept_by_node: dict[int, list[tuple[int, int]]] = {}
    for o in ordered:
        overlap = any(
            o.start < ke and ks < o.end for (ks, ke) in kept_by_node.get(o.node, [])
        )
        keep = not overlap
        considered.append(
            _Considered(o.node, o.start, o.end - o.start, o.pattern_id, keep)
        )
        if keep:
            selected.append(o)
            kept_by_node.setdefault(o.node, []).append((o.start, o.end))

    # -- occurrence order + namespace pools ---------------------------------
    selected.sort(key=lambda o: o.oid)
    pools: dict[str, list[Occurrence]] = {NS_TEMP: [], NS_MAX: [], NS_MIN: [], NS_HOURLY: []}
    for o in selected:
        if o.voided:
            continue  # dropped from effective pools (still in occurrences trace)
        pools.setdefault(o.namespace, []).append(o)

    return OccurrenceResult(occurrences=selected, considered=considered, pools=pools)
