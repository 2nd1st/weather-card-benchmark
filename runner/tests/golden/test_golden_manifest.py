"""GOLDEN — manifest anchor (scheme §1.2 / §11 lock item 2).

Reads the frozen fixture manifest + its expected manifest_hash / per-config
seed_assign / assignment ranks and asserts the CURRENT implementation reproduces
them byte/int-exact. This is a cross-implementation reproduction anchor: any
reimplementation of the JCS byte stream, the hash, or the seed derivation that
drifts from these pinned values trips here.

Fixtures (self-contained under ./fixtures/manifest/):
  manifest.json      the fixed canonical manifest (N=5, TWO config ids)
  manifest.jcs.bin   its exact RFC 8785 (JCS) byte stream
  expected.json      manifest_hash, seed_assign{cid}, assignment_table, ω values
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import rfc8785
from jsonschema import Draft202012Validator

from runner.manifest import build_manifest, compute_manifest_hash
from runner.seed import assign_config, build_assignment_table, enumerate_omega

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "manifest"
SCHEMA_DIR = HERE.parents[2] / "data" / "SCHEMA"

MANIFEST = json.loads((FIX / "manifest.json").read_text("utf-8"))
JCS_BIN = (FIX / "manifest.jcs.bin").read_bytes()
EXPECTED = json.loads((FIX / "expected.json").read_text("utf-8"))


def _rebuild():
    """Rebuild the manifest from the fixture's own field values (regression net)."""
    return build_manifest(
        prompt_min_sha256=MANIFEST["prompt_min_sha256"],
        prompt_q_sha256=MANIFEST["prompt_q_sha256"],
        snapshot_sha256=MANIFEST["snapshot_sha256"],
        cities=MANIFEST["cities"],
        config_ids=MANIFEST["config_ids"],
        N=MANIFEST["N"],
        env_digest=MANIFEST["env_digest"],
        pipeline_commit=MANIFEST["pipeline_commit"],
    )


def test_manifest_jcs_bytes_are_byte_exact():
    res = _rebuild()
    assert res.jcs_bytes == JCS_BIN
    # the persisted .bin IS the canonical serialization of manifest.json.
    assert rfc8785.dumps(MANIFEST) == JCS_BIN


def test_manifest_hash_matches_golden():
    res = _rebuild()
    assert res.manifest_hash == EXPECTED["manifest_hash"]
    assert compute_manifest_hash(JCS_BIN) == EXPECTED["manifest_hash"]
    assert hashlib.sha256(JCS_BIN).hexdigest() == EXPECTED["manifest_hash"]
    assert len(res.manifest_hash) == 64 and res.manifest_hash == res.manifest_hash.lower()


def test_manifest_validates_against_frozen_schema():
    schema = json.loads((SCHEMA_DIR / "manifest.schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(MANIFEST)


def test_config_ids_persisted_order_is_nfc_utf8_byte_order():
    res = _rebuild()
    assert res.manifest["config_ids"] == EXPECTED["config_ids_persisted"]
    assert res.manifest["config_ids"] == sorted(res.manifest["config_ids"])  # ASCII ids


def test_seed_assign_per_config_matches_golden():
    mh = EXPECTED["manifest_hash"]
    N = EXPECTED["N"]
    for cid, expected_seed in EXPECTED["seed_assign"].items():
        a = assign_config(mh, cid, N)
        assert a.seed_assign == expected_seed


def test_assignment_table_ranks_match_golden():
    mh = EXPECTED["manifest_hash"]
    order = EXPECTED["config_ids_persisted"]
    N = EXPECTED["N"]
    table = build_assignment_table(mh, order, N)
    assert [row["config_id"] for row in table] == order  # persisted traversal order
    omega = enumerate_omega(N)
    assert omega == EXPECTED["omega_values"]
    for row, exp in zip(table, EXPECTED["assignment_table"], strict=True):
        assert row["config_id"] == exp["config_id"]
        assert row["rank"] == exp["rank"]
        assert row["omega_value"] == exp["omega_value"]
        assert omega[row["rank"]] == row["omega_value"]


def test_assignment_artifacts_never_leak_into_manifest():
    res = _rebuild()
    assert set(res.manifest) == {
        "prompt_min_sha256", "prompt_q_sha256", "snapshot_sha256", "cities",
        "config_ids", "N", "env_digest", "pipeline_commit",
    }
