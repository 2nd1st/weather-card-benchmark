"""GOLDEN — L2 channel anchors (scheme §4 / §11 lock item 2).

For EACH of the 17 L2 channels (15 formal + the 2 permanent diagnostics
c-ncd / d-tagpath) the fixture pins:

  * one real devset card PAIR (input refs) + the EXACT S value (full float repr),
    recomputed here from the referenced cards' frozen artifacts and asserted
    bit-identical; and
  * one degenerate input that must yield S=null (scheme §4 零向量/空集/空 bag →
    该 pair S=null), materialized from a per-channel ``degenerate`` kind.

Input source: ``data/batches-dev/devset-42/cards/<slug>/`` — visual channels read
``shot.png`` (frozen original), DOM channels read ``dom.json``, code channels read
``card.html``. The same universal artifact handle the batch orchestrator builds
(``runner.similarity.runner._build_artifacts``) is reused so this exercises the
production resolution path.

Fixture: ./fixtures/channels/channels.json
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from runner.similarity.runner import CHANNELS, _build_artifacts

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "channels" / "channels.json"
REPO = HERE.parents[2]
CARDS = REPO / "data" / "batches-dev" / "devset-42" / "cards"

SPEC = json.loads(FIX.read_text("utf-8"))
ENTRIES = SPEC["channels"]
MOD = {name: mod for name, mod in CHANNELS}

# every channel in the registry must have exactly one golden entry.
assert {e["channel"] for e in ENTRIES} == set(MOD), "channel set drift"
assert len(ENTRIES) == 17


def _art(slug: str) -> dict:
    return _build_artifacts(CARDS / slug)


def _degenerate_art(kind: str) -> dict:
    """Materialize a §4 degenerate artifact handle for the given kind."""
    if kind == "empty-html":
        return {"card_html": ""}
    if kind == "no-shot":  # absent / zero-area screenshot
        return {}
    if kind == "black-image":  # zero grayscale/edge vector (§4 zero-vector)
        buf = io.BytesIO()
        Image.new("RGB", (1280, 800), (0, 0, 0)).save(buf, format="PNG")
        raw = buf.getvalue()
        return {"shot_png": raw, "shot_png_bytes": raw, "image": Image.open(io.BytesIO(raw))}
    if kind == "empty-dom":  # zero nodes → empty bag / all-zero occupancy
        return {"dom": {
            "dump_format_version": "dom-dump-v1", "visibility_rule": "dom-dump-v1",
            "captured_at_virtual_ms": 5000, "url": "x",
            "viewport": {"width": 1280, "height": 800},
            "counts": {"nodes": 0, "elements": 0, "text_nodes": 0, "visible_text_nodes": 0},
            "nodes": [],
        }}
    raise AssertionError(f"unknown degenerate kind {kind!r}")


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["channel"] for e in ENTRIES])
def test_channel_pair_value_is_bit_exact(entry):
    mod = MOD[entry["channel"]]
    result = mod.compute(_art(entry["card_a"]), _art(entry["card_b"]))
    s = result.get("s")
    assert s is not None, f"{entry['channel']}: golden pair unexpectedly null"
    # exact float identity (repr round-trips through the fixture JSON).
    assert float(s) == entry["s"]
    assert repr(float(s)) == repr(entry["s"])
    assert 0.0 <= float(s) <= 1.0


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["channel"] for e in ENTRIES])
def test_channel_degenerate_is_null(entry):
    mod = MOD[entry["channel"]]
    art = _degenerate_art(entry["degenerate"])
    result = mod.compute(art, art)
    assert result.get("s") is None, (
        f"{entry['channel']}: degenerate ({entry['degenerate']}) must yield S=null (§4)"
    )
