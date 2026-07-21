"""Load a batch's frozen artifacts into the descriptive + randomization inputs
(scheme §5). Reuses the L2 similarity channel resolvers for the cross-variant S the
randomization test needs (similarity/pairs only stores WITHIN-variant pairs).

Sources:
  * manifest.json            → config_ids (persisted order), N.
  * _runtime/assignment.json → observed bits + rank per config (ω_obs).
  * _runtime/run-summary.json→ slot_outcomes: (config, block, variant) → slot + state.
  * configs/<cid>/config.json→ per-variant m.
  * similarity/pairs/*.json  → raw per-pair S (descriptive input).
  * configs/<cid>/<v>/slots/<k>/{card.html,shot,dom.json} → randomization S (reuses
    similarity._build_artifacts / _compute_pair) and meta.json l1 (scalar_of).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ..seed import enumerate_omega
from ..similarity.runner import _build_artifacts, _compute_pair, _slot_dir
from .randomization import ConfigRunModel, Run

VARIANT_DIR = {"P-min": "min", "P-q": "q"}
VARIANT_M_KEY = {"P-min": "min", "P-q": "q"}

# L1 scalar id → meta.json l1 access path (proposed list, hypotheses.L1_SCALARS).
_SCALAR_PATH: dict[str, tuple[str, ...]] = {
    "colorfulness": ("visual", "colorfulness"),
    "brightness": ("visual", "brightness"),
    "contrast": ("visual", "contrast"),
    "whitespace_ratio": ("visual", "whitespace_ratio"),
    "frame_change_median": ("visual", "frame_change", "median"),
}


@dataclass
class StatsInputs:
    batch_id: str
    config_ids: list[str]
    N: int
    omega_size: int
    omega_values: list[int]
    variants_present: list[str]
    m_map: dict[str, dict[str, int]]
    valid_slots: dict[str, dict[str, list[int]]]
    self_pairs: dict[str, dict[str, list[dict]]]
    cross_pairs: dict[str, dict[tuple[str, str], list[dict]]]
    run_models: dict[str, ConfigRunModel]
    s_between: Callable[[str, Run, Run, str], Optional[float]]
    scalar_of: Callable[[str, Run, str], Optional[float]]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_batch(batch_dir: Path) -> StatsInputs:
    batch_dir = Path(batch_dir)
    batch_id = batch_dir.name
    manifest = _read_json(batch_dir / "manifest.json")
    config_ids: list[str] = list(manifest["config_ids"])  # persisted order — never re-sort
    N = int(manifest["N"])
    omega_values = enumerate_omega(N)
    omega_size = len(omega_values)

    assignment = _read_json(batch_dir / "_runtime" / "assignment.json")
    obs_bits = {c["config_id"]: list(c["bits"]) for c in assignment["configs"]}
    obs_rank = {c["config_id"]: int(c["rank"]) for c in assignment["configs"]}

    run_summary = _read_json(batch_dir / "_runtime" / "run-summary.json")
    slot_outcomes = run_summary["slot_outcomes"]

    # variants present, in canonical order.
    present = {so["variant"] for so in slot_outcomes}
    variants_present = [v for v in ("P-min", "P-q") if v in present]

    # per (config, block) → variant → (slot, valid); and valid_slots per variant.
    blocks: dict[str, dict[int, dict[str, tuple[int, bool]]]] = {c: {} for c in config_ids}
    valid_slots: dict[str, dict[str, list[int]]] = {v: {c: [] for c in config_ids} for v in variants_present}
    for so in slot_outcomes:
        cid, blk, var = so["config_id"], int(so["block_index"]), so["variant"]
        slot, valid = int(so["slot_index"]), so["state"] == "valid"
        blocks.setdefault(cid, {}).setdefault(blk, {})[var] = (slot, valid)
        if valid and var in valid_slots:
            valid_slots[var][cid].append(slot)
    for v in variants_present:
        for c in config_ids:
            valid_slots[v][c].sort()

    # per-variant m from config.json.
    m_map: dict[str, dict[str, int]] = {v: {} for v in variants_present}
    for cid in config_ids:
        cfg = _read_json(batch_dir / "configs" / cid / "config.json")
        m_obj = cfg.get("m", {})
        for v in variants_present:
            key = VARIANT_M_KEY[v]
            m_map[v][cid] = int(m_obj[key]) if isinstance(m_obj, dict) and key in m_obj else len(valid_slots[v][cid])

    # ConfigRunModel per config (block-ordered; missing/invalid run → None).
    run_models: dict[str, ConfigRunModel] = {}
    for cid in config_ids:
        blk_map = blocks.get(cid, {})
        ordered_blocks = []
        for k in range(N):
            b = blk_map.get(k, {})

            def _run(var: str) -> Optional[Run]:
                cell = b.get(var)
                if cell is None or not cell[1]:  # missing or not valid
                    return None
                return (VARIANT_DIR[var], cell[0])

            ordered_blocks.append({"min": _run("P-min"), "q": _run("P-q")})
        run_models[cid] = ConfigRunModel(
            config_id=cid,
            blocks=tuple(ordered_blocks),
            obs_bits=tuple(obs_bits.get(cid, [0] * N)),
            obs_rank=obs_rank.get(cid, 0),
        )

    # descriptive raw pairs from similarity/pairs.
    self_pairs: dict[str, dict[str, list[dict]]] = {v: {} for v in variants_present}
    cross_pairs: dict[str, dict[tuple[str, str], list[dict]]] = {v: {} for v in variants_present}
    pairs_dir = batch_dir / "similarity" / "pairs"
    if pairs_dir.is_dir():
        for pf in pairs_dir.glob("*.json"):
            doc = _read_json(pf)
            var = doc["variant"]
            if var not in self_pairs:
                continue
            rows = doc.get("pairs", [])
            if doc["kind"] == "self":
                self_pairs[var][doc["config_a"]] = rows
            else:
                cross_pairs[var][(doc["config_a"], doc["config_b"])] = rows

    s_between = _make_s_between(batch_dir, config_ids)
    scalar_of = _make_scalar_of(batch_dir)

    return StatsInputs(
        batch_id=batch_id, config_ids=config_ids, N=N, omega_size=omega_size,
        omega_values=omega_values, variants_present=variants_present, m_map=m_map,
        valid_slots=valid_slots, self_pairs=self_pairs, cross_pairs=cross_pairs,
        run_models=run_models, s_between=s_between, scalar_of=scalar_of,
    )


def _make_s_between(batch_dir: Path, config_ids: list[str]) -> Callable[[str, Run, Run, str], Optional[float]]:
    """Cross-variant S provider for the randomization test. Reuses the L2 channel
    resolvers via similarity._compute_pair; caches artifacts per (cid, variant, slot)
    and S vectors per unordered run pair (S is symmetric — materialize once)."""
    configs_root = batch_dir / "configs"
    art_cache: dict[tuple[str, str, int], dict] = {}
    s_cache: dict[tuple[str, frozenset], dict[str, Optional[float]]] = {}

    def artifacts(cid: str, run: Run) -> dict:
        vdir, slot = run
        key = (cid, vdir, slot)
        if key not in art_cache:
            art_cache[key] = _build_artifacts(_slot_dir(configs_root / cid, vdir, slot))
        return art_cache[key]

    def s_between(cid: str, a: Run, b: Run, channel: str) -> Optional[float]:
        key = (cid, frozenset((a, b)))
        if key not in s_cache:
            s_map, _ = _compute_pair(artifacts(cid, a), artifacts(cid, b))
            s_cache[key] = s_map
        return s_cache[key].get(channel)

    return s_between


def _make_scalar_of(batch_dir: Path) -> Callable[[str, Run, str], Optional[float]]:
    """L1 scalar provider from slot meta.json ``l1``; None when absent (dev batches
    ship webp-only slots without the visual scalars — documented, not a defect)."""
    configs_root = batch_dir / "configs"
    meta_cache: dict[tuple[str, str, int], Optional[dict]] = {}

    def scalar_of(cid: str, run: Run, scalar: str) -> Optional[float]:
        vdir, slot = run
        key = (cid, vdir, slot)
        if key not in meta_cache:
            meta_path = _slot_dir(configs_root / cid, vdir, slot) / "meta.json"
            meta_cache[key] = _read_json(meta_path) if meta_path.is_file() else None
        meta = meta_cache[key]
        if not meta:
            return None
        node: Any = meta.get("l1")
        for step in _SCALAR_PATH.get(scalar, ()):  # unknown scalar → None
            if not isinstance(node, dict) or step not in node:
                return None
            node = node[step]
        return float(node) if isinstance(node, (int, float)) and not isinstance(node, bool) else None

    return scalar_of
