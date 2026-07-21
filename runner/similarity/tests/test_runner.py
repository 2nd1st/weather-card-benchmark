"""Orchestration tests for runner.similarity.runner (scheme §4/§5, DECISIONS-M0 §8).

Builds a synthetic 2-config × 2-variant batch in a tmp dir (card.html + a real PNG
per valid slot, no dom.json) and asserts:
  * pairs files are materialized once per unordered config-pair × variant, self =
    C(m,2), cross = A×B;
  * every pairs / summary doc validates against the frozen schema;
  * the 4 DOM channels resolve to null (no dom.json) while code+visual channels do not;
  * config.json m is rewritten to the per-variant object and validates against the
    bumped config.schema (1.1.0).
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import jsonschema
import pytest
from PIL import Image

from runner.similarity import runner as R

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "data" / "SCHEMA"
DOM_CHANNELS = {"d-geom", "d-text", "d-pqgram", "d-tagpath"}


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1280, 800), color).save(buf, format="PNG")
    return buf.getvalue()


_HTML_A = "<!doctype html><html><head><style>.c{color:red;margin:4px}</style></head>" \
          "<body><div class='c'>Berlin 28°</div><script>const x=[1,2,3];x.map(v=>v+1)</script></body></html>"
_HTML_B = "<!doctype html><html><head><style>.d{color:blue;padding:9px}</style></head>" \
          "<body><section class='d'>Paris 19°</section><script>let y={a:1};Object.keys(y)</script></body></html>"


def _make_batch(root: Path) -> Path:
    batch = root / "2026-07-16--synthetic"
    configs = ["cfg-a--api--raw--dev", "cfg-b--api--raw--dev"]
    (batch).mkdir(parents=True)
    (batch / "manifest.json").write_text(json.dumps({"config_ids": configs, "N": 3}))
    palette = {"cfg-a--api--raw--dev": [(220, 40, 40), (200, 60, 60)],
               "cfg-b--api--raw--dev": [(40, 40, 220), (60, 60, 200)]}
    html = {"cfg-a--api--raw--dev": _HTML_A, "cfg-b--api--raw--dev": _HTML_B}
    for cid in configs:
        cdir = batch / "configs" / cid
        (cdir).mkdir(parents=True)
        cdir.joinpath("config.json").write_text(json.dumps({
            "config_id": cid, "family": "x", "model_id": "x", "effort": None,
            "protocol": "api", "billing": "metered",
            "auth": {"method": "api-key", "credential_ref": "ENV"},
            "transport": "harness", "served_model": None, "vendor_sanction_ref": None,
            "telemetry": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                          "wall_ms": 1, "cost_usd": None},
            "m": 0, "N": 3,
        }))
        for vdir in ("min", "q"):
            for k in range(2):  # 2 valid slots per variant
                sd = cdir / vdir / "slots" / str(k)
                sd.mkdir(parents=True)
                sd.joinpath("card.html").write_text(html[cid])
                sd.joinpath("shot.png").write_bytes(_png(palette[cid][k]))
                sd.joinpath("meta.json").write_text(json.dumps({"state": "valid", "slot_index": k}))
    return batch


def test_orchestrator_end_to_end(tmp_path):
    batch = _make_batch(tmp_path)
    result = R.run(batch)

    # 2 self (one per config) + 1 cross, per variant × 2 variants = 6 pair files.
    assert len(result["pairs_written"]) == 6
    assert set(result["summaries_written"]) == {"summary--P-min.json", "summary--P-q.json"}

    pairs_schema = json.loads((SCHEMA_DIR / "similarity-pairs.schema.json").read_text())
    sum_schema = json.loads((SCHEMA_DIR / "similarity-summary.schema.json").read_text())
    cfg_schema = json.loads((SCHEMA_DIR / "config.schema.json").read_text())

    pairs_dir = batch / "similarity" / "pairs"
    for f in pairs_dir.glob("*.json"):
        doc = json.loads(f.read_text())
        jsonschema.validate(doc, pairs_schema)
        assert doc["channels"] == R.CHANNEL_NAMES
        if doc["kind"] == "self":
            assert len(doc["pairs"]) == 1  # C(2,2)
        else:
            assert len(doc["pairs"]) == 4  # 2×2
        for entry in doc["pairs"]:
            for ch in DOM_CHANNELS:  # no dom.json → null
                assert entry["s"][ch] is None
            # code + visual channels present & non-null on this synthetic input.
            assert entry["s"]["c-shingle"] is not None
            assert entry["s"]["v-color"] is not None

    for f in (batch / "similarity").glob("summary--*.json"):
        doc = json.loads(f.read_text())
        jsonschema.validate(doc, sum_schema)
        assert doc["configs"] == result["config_ids"]

    for cid in result["config_ids"]:
        doc = json.loads((batch / "configs" / cid / "config.json").read_text())
        assert doc["m"] == {"min": 2, "q": 2}
        # config.json still fails ONLY on N (enum 3/4/5) here N=3 → fully valid.
        jsonschema.validate(doc, cfg_schema)


def test_percentile_linear_matches_numpy():
    np = pytest.importorskip("numpy")
    xs = [0.1, 0.4, 0.5, 0.9, 0.95]
    for q in (25.0, 50.0, 75.0):
        assert R._percentile_linear(xs, q) == pytest.approx(float(np.percentile(xs, q)))


def test_corrupt_png_guard_yields_null_not_crash(tmp_path):
    """M2 verify note: a corrupt / truncated shot.png must drop to channel-null +
    a decode flag, NEVER crash _build_artifacts or the pair computation."""
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "card.html").write_text("<html><body>hi 20°C</body></html>")
    (bad / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")

    art = R._build_artifacts(bad)  # must not raise
    assert "image" not in art and "shot_png" not in art
    assert art.get("shot_decode_failed") is True

    good = tmp_path / "good"
    good.mkdir()
    Image.new("RGB", (40, 30), (10, 20, 30)).save(good / "shot.png")
    (good / "card.html").write_text("<html><body>hi 21°C</body></html>")

    s_map, _diag = R._compute_pair(art, R._build_artifacts(good))
    # visual channels null (missing shot on one side); code channels still compute.
    assert s_map["v-color"] is None
    assert s_map["v-palette"] is None
    assert s_map["c-shingle"] is not None
