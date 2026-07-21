"""Golden tests for the task-W pipeline wiring (L1 completion + fidelity + expected).

Covers the pieces run_batch / rerender_offline now emit per VALID slot:

  * full L1 (bytes + structure + visual + palette_top8) on a frozen devset
    ``shot.png`` — golden scalar values + slot-meta subschema conformance;
  * frame-change reducer (§2.3) on synthetic frames;
  * expected-value table DERIVED from a batch weather-snapshot.json (not hardcoded
    Berlin) with the extractor_version stamp;
  * fidelity meta verdict validates against the slot-meta fidelity subschema.

Golden card = ``r1__gpt-5.6-sol`` (devset-42): the frozen shot.png + dom.json ship
in-repo, so these goldens are reproducible without a render.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from runner.fidelity import (
    DEVSET_EXPECTED,
    EXTRACTOR_BASE,
    expected_from_snapshot,
    extract,
    extractor_version_for,
)
from runner.render import l1_visual

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "data" / "SCHEMA"
GOLDEN_CARD = REPO / "data" / "batches-dev" / "devset-42" / "cards" / "r1__gpt-5.6-sol"
# m1-smoke was physically deleted in the 2026-07-19 sub2 cleanup; the unified
# batch carries the same frozen Berlin fixture (semantic-identity verified).
M1_SNAPSHOT = (
    REPO / "data" / "batches-dev" / "2026-07-19--unified" / "weather-snapshot.json"
)


def _slot_meta_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMA / "slot-meta.schema.json").read_text()))


# --------------------------------------------------------------------------- #
# L1 visual scalars — golden on one devset card
# --------------------------------------------------------------------------- #
def test_l1_visual_golden() -> None:
    png = (GOLDEN_CARD / "shot.png").read_bytes()
    html = (GOLDEN_CARD / "card.html").read_text()
    l1 = l1_visual.compute_l1(html, png, [])

    v = l1["visual"]
    # Frozen goldens (128×80 LANCZOS, appendix A formulas). rel tol absorbs any
    # sub-ULP resize/BLAS drift; the palette weight is an exact rational.
    assert v["colorfulness"] == pytest.approx(16.16433506631405, rel=1e-6)
    assert v["brightness"] == pytest.approx(73.3722462890625, rel=1e-6)
    assert v["contrast"] == pytest.approx(20.26921914625986, rel=1e-6)
    assert v["whitespace_ratio"] == pytest.approx(0.41083984375, rel=1e-9)
    assert v["frame_change"] == {"median": 0.0, "max": 0.0}

    # palette_top8: non-zero bins only, ≤8, sort key (−weight, bin index).
    pal = l1["palette_top8"]
    assert 0 < len(pal) <= 8
    weights = [p["weight"] for p in pal]
    assert weights == sorted(weights, reverse=True)  # descending weight
    assert all(w > 0 for w in weights)
    assert pal[0]["bin_index"] == 56
    assert pal[0]["weight"] == pytest.approx(0.5068359375, rel=1e-9)
    assert set(pal[0]["lab"]) == {"L", "a", "b"}


def test_l1_conforms_to_slot_meta_subschema() -> None:
    png = (GOLDEN_CARD / "shot.png").read_bytes()
    html = (GOLDEN_CARD / "card.html").read_text()
    dom = json.loads((GOLDEN_CARD / "dom.json").read_text())
    l1 = l1_visual.compute_l1(html, png, [])
    fidelity = extract(dom, DEVSET_EXPECTED, slot_index=0)["meta"]

    meta = {
        "slot_index": 0,
        "state": "valid",
        "flags": [],
        "telemetry": {
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
            "wall_ms": 10, "cost_usd": None, "request_id": None,
        },
        "l1": l1,
        "fidelity": fidelity,
    }
    errors = sorted(_slot_meta_validator().iter_errors(meta), key=lambda e: list(e.path))
    assert errors == [], [f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors]


# --------------------------------------------------------------------------- #
# frame-change reducer (§2.3)
# --------------------------------------------------------------------------- #
def _png(arr: np.ndarray) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), "RGB").save(buf, format="PNG")
    return buf.getvalue()


def test_frame_change_identical_frames_zero() -> None:
    a = np.full((8, 10, 3), 120, dtype=np.uint8)
    frames = [_png(a), _png(a), _png(a), _png(a)]
    assert l1_visual.frame_change(frames) == {"median": 0.0, "max": 0.0}


def test_frame_change_partial_motion() -> None:
    base = np.zeros((10, 10, 3), dtype=np.uint8)
    f2 = base.copy()
    f2[0, :, :] = 255  # 10 of 100 pixels change (one row)
    # frames: base, f2, f2, f2 → adjacent ratios [0.1, 0.0, 0.0]
    frames = [_png(base), _png(f2), _png(f2), _png(f2)]
    fc = l1_visual.frame_change(frames)
    assert fc["max"] == pytest.approx(0.1)
    assert fc["median"] == pytest.approx(0.0)


def test_frame_change_fewer_than_two_frames() -> None:
    assert l1_visual.frame_change([]) == {"median": 0.0, "max": 0.0}
    a = np.zeros((4, 4, 3), dtype=np.uint8)
    assert l1_visual.frame_change([_png(a)]) == {"median": 0.0, "max": 0.0}


# --------------------------------------------------------------------------- #
# expected-value table derived from the batch snapshot (not hardcoded Berlin)
# --------------------------------------------------------------------------- #
def test_expected_from_snapshot_m1() -> None:
    snap = json.loads(M1_SNAPSHOT.read_text())
    exp = expected_from_snapshot(snap)
    assert exp.name == "Berlin"
    assert str(exp.day) == "2026-07-15"
    assert exp.weather_code == 51
    assert str(exp.temp_max) == "25.3"
    assert str(exp.temp_min) == "16"
    assert len(exp.hourly) == 24
    assert str(exp.hourly[0]) == "17.4"

    ev = extractor_version_for(snap)
    assert ev == f"{EXTRACTOR_BASE}+Berlin-2026-07-15"
    assert ev.startswith(EXTRACTOR_BASE + "+")


def test_extractor_version_stamped_into_verdict() -> None:
    snap = json.loads(M1_SNAPSHOT.read_text())
    exp = expected_from_snapshot(snap)
    ev = extractor_version_for(snap)
    dom = json.loads((GOLDEN_CARD / "dom.json").read_text())
    out = extract(dom, exp, slot_index=0, extractor_version=ev)
    assert out["meta"]["extractor_version"] == ev
    assert out["trace"]["extractor_version"] == ev
