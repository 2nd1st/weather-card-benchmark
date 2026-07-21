"""Serialization + seed helpers for the L3 stats layer (scheme §5 / §8, appendix A;
CONTRACT-NOTES §4/§6/§8).

stats.json ENTERS the §8 verify rehash, so every number is emitted through ONE
fixed serialization. The stats schema types the statistical quantities
(median/mean/self_consistency/p/adjusted_p/…) as JSON **numbers** — so those keep
Python's shortest round-tripping ``float.__repr__`` via ``json.dumps`` (the exact
same discipline the already-frozen ``similarity/summary--*.json`` uses; reusing it
keeps sibling rehashed files consistent).

FLAG (scheme-silent): CONTRACT-NOTES §8 *recommends* reusing the §3 decimal-string
grammar for every rehashed file, but the stats schema types these fields as JSON
numbers — a decimal STRING would fail schema validation. The schema wins: numeric
fields are JSON numbers. Only ``coverage`` is schema-typed as a canonical decimal
STRING and uses the §3 grammar (see ``coverage_string``).
"""
from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

import rfc8785

from ..manifest import canonical_decimal, nfc

# stats-layer PRNG seed domain (scheme §5 / appendix A; CONTRACT-NOTES §6).
_PERM_XOR = 20260716

# FLAG (scheme-silent): the coverage decimal-string grammar (CONTRACT-NOTES §3)
# cannot express a NON-terminating ratio (e.g. 2/3 when a batch has 4 configs →
# denominator 3). The <80% gate always uses the EXACT Fraction, never this string,
# so display rounding never changes eligibility. When the exact value does not
# terminate we ROUND_HALF_UP to this many fractional digits (matching §3's numeric
# rounding discipline) then strip trailing zeros. Locked here, FLAGGED for review.
_COVERAGE_NONTERMINATING_SCALE = 6


def seed_h(hypothesis_id: str) -> int:
    """Per-hypothesis pooled-permutation PRNG seed (scheme §5 / appendix A):

        seed_h = uint64_le(SHA256(b"perm\\0" ‖ NFC(hypothesis_id).utf8)[0:8]) XOR 20260716

    Independent stream per hypothesis, zero sharing (other random processes use
    disjoint domains such as ``b"boot\\0"``).
    """
    payload = b"perm\x00" + nfc(hypothesis_id).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[0:8], byteorder="little", signed=False) ^ _PERM_XOR


def mask_hash(config_ids: Sequence[str]) -> str:
    """Lowercase-hex SHA256 of the config_id array's canonical JSON (JCS), the same
    discipline as manifest ``mask_h`` (CONTRACT-NOTES §4). Strings are NFC-first."""
    normalized = [nfc(c) for c in config_ids]
    jcs = rfc8785.dumps(normalized)
    return hashlib.sha256(jcs).hexdigest()


def coverage_string(b_set_size: int, registered_total: int) -> str | None:
    """``|B| / (registered_total − 1)`` as a canonical decimal STRING (scheme §5).

    Returns ``None`` when the denominator is 0 (registered_total == 1), matching the
    schema's ``coverage`` null case. Terminating ratios serialize exactly; a
    non-terminating ratio is ROUND_HALF_UP'd to a locked scale (see module FLAG).
    """
    denom = registered_total - 1
    if denom <= 0:
        return None
    frac = Fraction(b_set_size, denom)
    reduced_den = frac.denominator
    residual = reduced_den
    for p in (2, 5):
        while residual % p == 0:
            residual //= p
    if residual == 1:
        # Terminating: exact decimal (fractional length ≤ max(v2, v5), tiny).
        dec = Decimal(frac.numerator) / Decimal(frac.denominator)
    else:
        quant = Decimal(1).scaleb(-_COVERAGE_NONTERMINATING_SCALE)
        dec = (Decimal(frac.numerator) / Decimal(frac.denominator)).quantize(
            quant, rounding=ROUND_HALF_UP
        )
    return canonical_decimal(dec)


def coverage_meets_threshold(b_set_size: int, registered_total: int) -> bool:
    """EXACT (Fraction) ``|B|/(registered−1) >= 0.8`` gate (scheme §5). Never uses
    the rounded display string. False when the denominator is 0."""
    denom = registered_total - 1
    if denom <= 0:
        return False
    return Fraction(b_set_size, denom) >= Fraction(4, 5)


def dumps(doc: Any) -> str:
    """Fixed canonical serialization for the rehashed stats.json — shortest
    round-tripping float repr via json, indent=1, trailing newline (mirrors the
    frozen ``similarity`` writer)."""
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def write_stats_json(path: Path, doc: Any) -> None:
    Path(path).write_text(dumps(doc), encoding="utf-8")
