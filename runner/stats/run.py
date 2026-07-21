#!/usr/bin/env python3
"""L3 stats ORCHESTRATOR (scheme §5; DECISIONS-M0 / M0.5 / M1).

Assembles ``stats.json`` (stats.schema.json):
  (1) DESCRIPTIVE — one entry per prompt variant present (within-variant, scheme §9):
      self_consistency / cross / separation / distinctiveness over the 15 formal
      channels, LORO the only sensitivity量 (descriptive.py).
  (2) RANDOMIZATION_TEST — the single §5 P-min/P-q paired randomization test:
      per-config exact Ω_c enumeration (exploratory) + pooled B_perm=10000 MC with
      exhaustive null pre-check + Holm (randomization.py).

Usage:  .venv/bin/python -m runner.stats.run <batch_dir> [--dev]

``--dev`` keeps going and REPORTS schema-validation errors instead of failing hard
(e.g. an N=2 dev batch's ``omega_size`` violates the [6,20] enum — expected).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .descriptive import descriptive_for_variant
from .hypotheses import FORMAL_CHANNELS, pooled_family
from .hypotheses import exploratory_hypothesis_id
from .load import StatsInputs, load_batch
from .randomization import build_T_table, exploratory, holm, pooled
from .serialize import mask_hash, write_stats_json

B_PERM = 10000


def _target_key(target: dict) -> tuple:
    return (target["kind"], target.get("channel"), target.get("l1_scalar"))


def build_stats_doc(inputs: StatsInputs, b_perm: int = B_PERM) -> dict[str, Any]:
    """Pure builder: StatsInputs → stats.json document (scheme §5)."""
    # ---- DESCRIPTIVE (per variant) -----------------------------------------
    descriptive = [
        descriptive_for_variant(
            variant=v,
            config_ids=inputs.config_ids,
            m_map=inputs.m_map[v],
            valid_slots=inputs.valid_slots[v],
            self_pairs=inputs.self_pairs[v],
            cross_pairs=inputs.cross_pairs[v],
        )
        for v in inputs.variants_present
    ]

    # ---- RANDOMIZATION: precompute T_{config,h}(ω) once per (config, target) -
    family = pooled_family()
    T_tables: dict[tuple, list] = {}
    for cid in inputs.config_ids:
        model = inputs.run_models[cid]
        for e in family:
            T_tables[(cid, _target_key(e["target"]))] = build_T_table(
                model, inputs.N, e["target"], inputs.s_between, inputs.scalar_of
            )

    obs_ranks = {cid: inputs.run_models[cid].obs_rank for cid in inputs.config_ids}

    # ---- per-config EXPLORATORY (exact Ω_c enumeration) --------------------
    per_config: list[dict] = []
    for cid in inputs.config_ids:
        for e in family:
            tkey = _target_key(e["target"])
            T_obs, p = exploratory(T_tables[(cid, tkey)], obs_ranks[cid])
            per_config.append({
                "hypothesis_id": exploratory_hypothesis_id(cid, e["target"]),
                "config_id": cid,
                "target": e["target"],
                "T_obs": T_obs,
                "p": p,
                "omega_size": inputs.omega_size,
                "p_granularity": f"1/{inputs.omega_size}",
            })

    # ---- pooled MAIN test + Holm -------------------------------------------
    mask_ids = list(inputs.config_ids)  # frozen complete config list (persisted order)
    mask_h = {"config_ids": mask_ids, "hash": mask_hash(mask_ids)}
    pooled_results = []
    for e in family:
        tkey = _target_key(e["target"])
        tables = {cid: T_tables[(cid, tkey)] for cid in mask_ids}
        pooled_results.append(
            pooled(e["hypothesis_id"], e["target"], mask_ids, tables, obs_ranks,
                   inputs.omega_size, b_perm)
        )
    holm_map = holm(pooled_results, family_size_k=len(family))

    pooled_entries: list[dict] = []
    for r in pooled_results:
        pooled_entries.append({
            "hypothesis_id": r.hypothesis_id,
            "target": r.target,
            "mask_h": mask_h,
            "seed_h": r.seed_h,
            "omega_size": inputs.omega_size,
            "precheck_null": r.precheck_null,
            "T_pool_obs": r.T_pool_obs,
            "b": r.b,
            "p": r.p,
            "holm": holm_map[r.hypothesis_id],
        })

    return {
        "batch_id": inputs.batch_id,
        "configs": inputs.config_ids,
        "formal_channels": FORMAL_CHANNELS,
        "descriptive": descriptive,
        "randomization_test": {
            "B_perm": b_perm,
            "p_formula": "(b+1)/(B_perm+1)",
            "holm_family": [e["hypothesis_id"] for e in family],
            "family_size_k": len(family),
            "per_config": per_config,
            "pooled": pooled_entries,
        },
    }


def _validate(doc: dict, schema_path: Path) -> list[str]:
    try:
        import jsonschema
    except Exception:  # noqa: BLE001
        return ["jsonschema not available"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(doc)]


def run(batch_dir: Path, dev: bool = False, b_perm: int = B_PERM) -> dict[str, Any]:
    batch_dir = Path(batch_dir)
    inputs = load_batch(batch_dir)
    doc = build_stats_doc(inputs, b_perm=b_perm)
    out_path = batch_dir / "stats.json"
    write_stats_json(out_path, doc)

    schema_path = Path(__file__).resolve().parents[2] / "data" / "SCHEMA" / "stats.schema.json"
    errors = _validate(doc, schema_path) if schema_path.is_file() else ["schema not found"]
    return {
        "batch_id": inputs.batch_id,
        "stats_path": str(out_path),
        "schema_errors": errors,
        "dev": dev,
    }


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    dev = "--dev" in argv
    if len(args) != 1:
        print("usage: python -m runner.stats.run <batch_dir> [--dev]", file=sys.stderr)
        return 2
    result = run(Path(args[0]), dev=dev)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if result["schema_errors"] and not dev:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
