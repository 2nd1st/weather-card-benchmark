"""Scalar fidelity fields + salience + display-precision quantize (scheme §3).

  * ``name``      — 2-state {match, not-found} (scheme §3: name 无 mismatch 态).
  * ``date``      — 4-state; ISO + versioned locale template table → UTC day.
  * ``condition`` — 4-state; WMO multilingual vocab text match.
  * ``salience``  — temperature-candidate set E ((f_max−f)/f_max < 0.10).
  * ``quantize_half_up`` / ``canonical_decimal`` — display-precision helpers
    (scheme §3 数值: decimal ROUND_HALF_UP, ties away from zero; 禁裸 round).

Every scheme-silent lexical/template choice is FLAGGED in the R5 report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .normalize import DomModel, normalize_text
from .occurrences import Occurrence

SALIENCE_BAND = Decimal("0.10")  # (f_max−f)/f_max < 0.10 (scheme §3)


# ---------------------------------------------------------------------------
# display-precision (scheme §3 数值匹配)
# ---------------------------------------------------------------------------
def quantize_half_up(value: Decimal, decimals: int) -> Decimal:
    """ROUND_HALF_UP (ties away from zero) quantize to ``decimals`` places."""
    q = Decimal(1).scaleb(-decimals)  # 10**-decimals
    return value.quantize(q, rounding=ROUND_HALF_UP)


def canonical_decimal(value: Decimal) -> str:
    """Canonical decimal string (no trailing-zero, no leading zero, no −0).

    Matches the slot-meta / fidelity-trace ``value`` pattern.
    """
    v = value.normalize()
    # Decimal.normalize collapses e.g. 28.50→2.85E+1; expand to plain notation.
    s = format(v, "f")
    if s.startswith("-") and Decimal(s) == 0:
        s = s[1:]
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def value_state(displayed: Decimal, decimals: int, expected: Decimal) -> str:
    """match iff displayed == ROUND_HALF_UP(expected, decimals) (scheme §3)."""
    return "match" if displayed == quantize_half_up(expected, decimals) else "mismatch"


# ---------------------------------------------------------------------------
# name (scheme §3) — 2-state
# ---------------------------------------------------------------------------
@dataclass
class FieldVerdict:
    state: str
    reason: str | None
    path: list[tuple[str, str | None]]


def judge_name(model: DomModel, expected_name: str) -> FieldVerdict:
    """Any visible text node whose normalized text CONTAINS the normalized
    expected name (direct substring) → match; else not-found (no mismatch)."""
    target = normalize_text(expected_name)
    if not model.visible:
        return FieldVerdict("not-found", "no-visible-text", [("name-no-visible-text", None)])
    for vt in model.visible:
        if target and target in vt.norm:
            return FieldVerdict("match", None, [("name-substring-hit", f"node={vt.preorder}")])
    return FieldVerdict(
        "not-found", "text-present-no-name-hit", [("name-no-substring-hit", None)]
    )


# ---------------------------------------------------------------------------
# date (scheme §3) — 4-state
# ---------------------------------------------------------------------------
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# "16 july 2026" / "16 jul. 2026"
_DMY_RE = re.compile(r"\b(\d{1,2})\s+([a-z]+)\.?\s+(\d{4})\b")
# "july 16, 2026" / "jul 16 2026"
_MDY_RE = re.compile(r"\b([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")

DATE_TEMPLATE_VERSION = "date-templates-v1"


def _try_date(text: str) -> date | None:
    m = _ISO_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = _DMY_RE.search(text)
    if m and m.group(2) in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:
            pass
    m = _MDY_RE.search(text)
    if m and m.group(1) in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            pass
    return None


def judge_date(model: DomModel, expected_day: date) -> FieldVerdict:
    """Parse ISO + locale templates → UTC day. hit→match, other→mismatch,
    unparseable→not-found, conflicting multi-candidate→ambiguous (scheme §3)."""
    parsed: dict[date, int] = {}  # day → first-hit node preorder
    for vt in model.visible:
        d = _try_date(vt.norm)
        if d is not None and d not in parsed:
            parsed[d] = vt.preorder
    if not parsed:
        return FieldVerdict("not-found", "no-parseable-date", [("date-unparseable", None)])
    if len(parsed) > 1:
        detail = ",".join(sorted(d.isoformat() for d in parsed))
        return FieldVerdict("ambiguous", "multi-candidate-conflict", [("date-ambiguous", detail)])
    only = next(iter(parsed))
    if only == expected_day:
        return FieldVerdict("match", None, [("date-hit", only.isoformat())])
    return FieldVerdict("mismatch", "other-day", [("date-other-day", only.isoformat())])


# ---------------------------------------------------------------------------
# condition (scheme §3) — WMO vocab, 4-state
# ---------------------------------------------------------------------------
# WMO code → canonical condition phrase(s) (Open-Meteo weather_code table).
# English canonical vocabulary (versioned); multilingual extension is a future
# vocab bump. Phrases are matched as normalized substrings, longest-first.
WMO_VOCAB_VERSION = "wmo-vocab-v1"
_WMO: dict[int, list[str]] = {
    0: ["clear sky", "clear", "sunny"],
    1: ["mainly clear", "mostly clear", "mostly sunny"],
    2: ["partly cloudy", "partly sunny"],
    3: ["overcast", "cloudy"],
    45: ["fog", "foggy"],
    48: ["depositing rime fog", "rime fog"],
    51: ["light drizzle", "drizzle"],
    53: ["moderate drizzle"],
    55: ["dense drizzle", "heavy drizzle"],
    56: ["light freezing drizzle"],
    57: ["dense freezing drizzle", "heavy freezing drizzle"],
    61: ["slight rain", "light rain", "rain"],
    63: ["moderate rain"],
    65: ["heavy rain"],
    66: ["light freezing rain"],
    67: ["heavy freezing rain"],
    71: ["slight snow", "light snow", "snow"],
    73: ["moderate snow"],
    75: ["heavy snow"],
    77: ["snow grains"],
    80: ["slight rain showers", "light rain showers", "rain showers"],
    81: ["moderate rain showers"],
    82: ["violent rain showers", "heavy rain showers"],
    85: ["slight snow showers", "light snow showers", "snow showers"],
    86: ["heavy snow showers"],
    95: ["thunderstorm"],
    96: ["thunderstorm with slight hail", "thunderstorm with hail"],
    99: ["thunderstorm with heavy hail"],
}
# reverse index: phrase → code, longest phrases first for greedy matching.
_WMO_PHRASES: list[tuple[str, int]] = sorted(
    ((normalize_text(p), c) for c, ps in _WMO.items() for p in ps),
    key=lambda kv: -len(kv[0]),
)

_ICON_TAGS = {"svg", "img", "canvas", "path", "use", "symbol"}


def judge_condition(model: DomModel, expected_code: int) -> FieldVerdict:
    """Match if any visible text carries the expected code's WMO vocab; a
    recognized OTHER code → mismatch; only an icon / no text → not-found (+
    icon-only note) (scheme §3)."""
    matched_codes: set[int] = set()
    hit_expected_node: int | None = None
    for vt in model.visible:
        for phrase, code in _WMO_PHRASES:
            if phrase and phrase in vt.norm:
                matched_codes.add(code)
                if code == expected_code and hit_expected_node is None:
                    hit_expected_node = vt.preorder
                break  # longest phrase for this node wins
    if expected_code in matched_codes:
        return FieldVerdict("match", None, [("condition-wmo-hit", f"node={hit_expected_node}")])
    if matched_codes:
        detail = ",".join(str(c) for c in sorted(matched_codes))
        return FieldVerdict("mismatch", "other-wmo-code", [("condition-other-code", detail)])
    # no condition text at all: icon-only note when the DOM carries icon elements.
    has_icon = any(t in _ICON_TAGS for t in model.tag_of.values())
    reason = "icon-only" if has_icon else "no-condition-text"
    return FieldVerdict("not-found", reason, [("condition-not-found", reason)])


# ---------------------------------------------------------------------------
# temperature salience (scheme §3)
# ---------------------------------------------------------------------------
@dataclass
class Salience:
    f_max: float | None
    candidates: list[Occurrence]  # all temperature occurrences
    e: list[Occurrence]  # same-salience set E (ID order)
    ratios: dict[tuple[int, int, int], Decimal]  # occ id → (f_max−f)/f_max


def compute_salience(temp_occurrences: list[Occurrence]) -> Salience:
    """f_max = max parent font-size over temperature candidates; E = candidates
    with (f_max−f)/f_max < 0.10 (scheme §3). E=∅ when there is no candidate."""
    cands = sorted(temp_occurrences, key=lambda o: o.oid)
    fonts = [o.font_size for o in cands if o.font_size is not None]
    if not fonts:
        return Salience(None, cands, [], {})
    f_max = max(fonts)
    ratios: dict[tuple[int, int, int], Decimal] = {}
    e: list[Occurrence] = []
    fmax_dec = Decimal(str(f_max))
    for o in cands:
        f = Decimal(str(o.font_size)) if o.font_size is not None else Decimal(0)
        ratio = (fmax_dec - f) / fmax_dec if fmax_dec != 0 else Decimal(0)
        ratios[o.oid] = ratio
        if ratio < SALIENCE_BAND:
            e.append(o)
    return Salience(f_max, cands, e, ratios)
