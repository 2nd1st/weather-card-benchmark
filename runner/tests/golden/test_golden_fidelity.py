"""GOLDEN — fidelity trace anchor (scheme §3 / §11 lock item 2).

Two real devset cards' ``dom.json`` are shipped alongside the EXACT extractor
output (trace + meta) produced by the current verified R5 extractor against the
frozen ``DEVSET_EXPECTED`` snapshot. The test re-runs ``extract`` and asserts the
full trace + meta are byte-identical to the golden, and that both artifacts still
validate against their frozen schemas.

Any reimplementation of the §3 pipeline (occurrence identity, salience, distance
二元组, max/min binding, hourly injection, decision-path serialization) that
diverges on these two inputs trips here.

Fixtures (self-contained under ./fixtures/fidelity/):
  <slug>.dom.json        the exact captured dom-dump-v1 input
  <slug>.expected.json   {"trace": <full fidelity-trace>, "meta": <verdict object>}
  index.json             extractor_version + slug list
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from runner.fidelity import DEVSET_EXPECTED, EXTRACTOR_VERSION, extract

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "fidelity"
SCHEMA_DIR = HERE.parents[2] / "data" / "SCHEMA"

TRACE_SCHEMA = json.loads((SCHEMA_DIR / "fidelity-trace.schema.json").read_text())
_SLOT = json.loads((SCHEMA_DIR / "slot-meta.schema.json").read_text())
META_SCHEMA = {"$defs": _SLOT["$defs"], **_SLOT["properties"]["fidelity"]}

INDEX = json.loads((FIX / "index.json").read_text("utf-8"))
SLUGS = INDEX["slugs"]


def _canon(obj) -> str:
    """Stable canonical JSON for byte-exact comparison of the whole structure."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_extractor_version_pinned():
    assert EXTRACTOR_VERSION == INDEX["extractor_version"]


@pytest.mark.parametrize("slug", SLUGS)
def test_devset_card_trace_is_byte_exact(slug):
    dom = json.loads((FIX / f"{slug}.dom.json").read_text("utf-8"))
    expected = json.loads((FIX / f"{slug}.expected.json").read_text("utf-8"))
    out = extract(dom, DEVSET_EXPECTED, slot_index=0)

    # schema-valid, then byte-exact against the golden.
    jsonschema.validate(out["trace"], TRACE_SCHEMA)
    jsonschema.validate(out["meta"], META_SCHEMA)
    assert _canon(out["trace"]) == _canon(expected["trace"]), f"trace drift for {slug}"
    assert _canon(out["meta"]) == _canon(expected["meta"]), f"meta drift for {slug}"


@pytest.mark.parametrize("slug", SLUGS)
def test_golden_trace_and_meta_are_schema_valid(slug):
    expected = json.loads((FIX / f"{slug}.expected.json").read_text("utf-8"))
    jsonschema.validate(expected["trace"], TRACE_SCHEMA)
    jsonschema.validate(expected["meta"], META_SCHEMA)
