"""Fidelity extractor (R5) — COMPARISON-SCHEME-v12.md §3.

The data-fidelity judge: consumes a card's ``dom.json`` (dom-dump-v1) + a frozen
expected-value snapshot and emits

  * a per-slot ``fidelity-trace.json`` (fidelity-trace.schema.json) — the
    load-bearing "全部落盘" audit artifact (candidates, salience, arbitration,
    binding decisions, decision paths), and
  * the terminal ``fidelity`` verdict object for ``meta.json`` (slot-meta schema).

Everything here is PURE PYTHON codepoint arithmetic — NO UTF-16 indices anywhere
(scheme §3 实现注记). The public entry point is ``extract`` (trace.py).

Module map (scheme §3 sub-parts):

  normalize.py    NFC+trim+whitespace-fold+casefold; visible-text-node model +
                  text-coordinate system (cumulative normalized codepoints, no
                  inter-node separator); DOM parent / depth / LCA / font model.
  occurrences.py  raw-hit matcher (versioned patterns) → same-span merge (+
                  category summary / label voiding) → leftmost-longest overlap
                  arbitration → per-namespace occurrence pools.
  fields.py       name (2-state), date (4-state), condition (WMO, 4-state),
                  temperature candidates + salience set E; ROUND_HALF_UP quantize.
  binding.py      max/min global one-to-one binding, all v12 branches incl.
                  E=∅ pre-rule, dual/single/no-label, canonical publication.
  hourly.py       pre-gate + max-cardinality injective binding (fixed-length
                  per-label key vector) / no-label exactly-24 alignment.
  trace.py        assemble fidelity-trace.json + meta.json terminal verdicts.
  expected.py     the frozen devset expected-value snapshot + extractor_version.
"""

from .expected import (
    DEVSET_EXPECTED,
    EXTRACTOR_BASE,
    EXTRACTOR_VERSION,
    ExpectedSnapshot,
    expected_from_snapshot,
    extractor_version_for,
)
from .trace import extract

__all__ = [
    "extract",
    "EXTRACTOR_VERSION",
    "EXTRACTOR_BASE",
    "DEVSET_EXPECTED",
    "ExpectedSnapshot",
    "expected_from_snapshot",
    "extractor_version_for",
]
