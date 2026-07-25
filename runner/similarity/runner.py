#!/usr/bin/env python3
"""L2 similarity ORCHESTRATOR — INCREMENTAL + PARALLEL (scheme §4/§5; DECISIONS-M0
§8, M1 ruling; INCREMENTAL-PLAN.md verdicts 1-7).

Runs all **17** L2 channels (15 formal + the 2 permanent diagnostics c-ncd /
d-tagpath) over a batch's VALID-slot pairs and writes, PER PROMPT VARIANT:

  * ``similarity/pairs/<a>--<b>--<variant>.json`` — per slot-pair, the 17-channel
    S vector (+ the c-ncd / v-ssim diagnostic companions). Each UNORDERED config
    pair is materialized EXACTLY ONCE (DECISIONS-M0 §8): self pairs ``{(i,j)|i<j}``
    per config, cross pairs ``A×B`` with ``config_a`` preceding ``config_b`` in the
    manifest's persisted order.
  * ``similarity/summary--<variant>.json`` — config×config×channel L3 descriptive
    aggregate (median / IQR / n_eff / m_a / m_b), **aggregated from the written pair
    files, never recomputed** (verdict 4). Eligibility ``n_eff≥4`` (+ cross needs
    ``m_a≥2 ∧ m_b≥2``); ineligible / all-null → null cell.

INCREMENTAL CACHE (verdict 1) — a pair document is reused iff its identity key
matches. Identity = (variant, ordered config ids, self/cross kind, pair schema,
engine fingerprint, and the config-variant INVENTORY DIGESTS of the involved
configs). Inventory digests fold ordered ``(slot_index, slot_digest)`` entries;
a slot digest folds per-component (html / shot+kind / dom) hashes with explicit
ABSENT markers. The engine fingerprint covers every channel's source hash, the
Node helper dirs + lockfiles + Node version, and Pillow / numpy / skimage / zlib /
Python versions. Pair-engine and summary-engine versions are kept SEPARATE so a
summary-only change never invalidates pair computations. All identity/serialization
primitives live in ``cache.py``.

The frozen pairs schema is ``additionalProperties:false``, so identity cannot be
embedded in the pair JSON (it would fail schema validation — see test_runner.py).
Instead each pair file has a co-located SIDECAR under ``similarity/.incremental/``
carrying the identity key + the pair file's SHA-256, written AFTER the pair
(verdict 2's blessed fallback). The ``.incremental/`` dir is invisible to the site
(copy-assets only descends into ``pairs/``; listPairFiles only reads ``*.json``).

ATOMICITY (verdict 2): every write is same-dir tmp + fsync + os.replace; a
batch-level advisory lock (fcntl) serializes runs; an INCOMPLETE marker is written
first and removed only after all expected pairs validate, summaries publish, config
m updates, and the (deterministic) completion manifest lands.

PARALLELISM (verdict 3): ``multiprocessing`` **spawn** (macOS-safe). The worker is
module-level, receives only a small path/spec record, holds a bounded per-worker
artifact LRU, and re-verifies each loaded slot's digest against the precomputed
inventory (TOCTOU guard). Task unit = one output pair document. Native numeric
thread pools are pinned to 1 to avoid oversubscription. Default workers =
``min(8, cpu-2)``; ``--workers N`` overrides. (Persistent per-worker Node helpers
are a documented TODO — today each pair recomputes Node reprs per worker process.)

DETERMINISM (verdict 5): results are keyed by task identity and the summary reads
pairs in MANIFEST order (not arrival order); one canonical serializer (sort_keys,
allow_nan=False, normalized -0.0, fixed whitespace, shortest-repr floats) makes
1-worker and N-worker runs byte-identical.

FAILURE SEMANTICS (verdict 7): a MISSING/degenerate artifact deterministically
yields channel-null (legitimate). An INFRASTRUCTURE failure — the c-ast-js /
c-css-prop Node subprocess failing, a worker-level or otherwise unexpected
exception, or a slot whose bytes changed mid-run — FAILS the task and aborts the
batch (leaving the INCOMPLETE marker). Such failures are NEVER cached as null.

FLAGGED scheme-silent choices (unchanged from the prior runner; also in the batch
CONFORMANCE-REPORT): one ``summary--<variant>.json`` per variant; shot.webp fed to
the visual channels when shot.png is absent (dev approximation); dom.json absence →
the 4 DOM channels resolve to S=null.

Usage:  .venv/bin/python -m runner.similarity.runner <batch_dir> [--workers N] [--force]
"""
from __future__ import annotations

import argparse
import io
import json
import multiprocessing as mp
import os
import sys
import time
from collections import OrderedDict
from itertools import combinations
from pathlib import Path
from typing import Any

from PIL import Image

from . import (
    c_ast_js,
    c_css_prop,
    c_feature,
    c_ncd,
    c_shingle,
    c_winnow,
    d_geom,
    d_pqgram,
    d_tagpath,
    d_text,
    v_color,
    v_dhash,
    v_edge,
    v_layout,
    v_palette,
    v_phash,
    v_ssim,
    x_color,
    x_css_values,
    x_layout,
    x_naming,
    x_semantics,
)
from . import cache as C

# Channel registry in the schema's required key order (scheme §4). c-ncd and
# d-tagpath are the 2 permanent DIAGNOSTIC channels (displayed, never pruned/statted).
CHANNELS: list[tuple[str, Any]] = [
    ("v-phash", v_phash),
    ("v-dhash", v_dhash),
    ("v-color", v_color),
    ("v-palette", v_palette),
    ("v-layout", v_layout),
    ("v-edge", v_edge),
    ("v-ssim", v_ssim),
    ("c-shingle", c_shingle),
    ("c-winnow", c_winnow),
    ("c-feature", c_feature),
    ("c-ast-js", c_ast_js),
    ("c-css-prop", c_css_prop),
    ("c-ncd", c_ncd),
    ("d-geom", d_geom),
    ("d-text", d_text),
    ("d-pqgram", d_pqgram),
    ("d-tagpath", d_tagpath),
    # v13 extended code-feature family (§4 v13): interpretable feature-distribution
    # signatures from card.html — declared color / CSS values / layout technique /
    # class naming / HTML semantics. Formal (non-diagnostic); cosine over vectors.
    ("x-color", x_color),
    ("x-css-values", x_css_values),
    ("x-layout", x_layout),
    ("x-naming", x_naming),
    ("x-semantics", x_semantics),
]
CHANNEL_NAMES: list[str] = [name for name, _ in CHANNELS]

# The ONLY channels that shell out to a Node subprocess. Their data-null paths
# (no inline JS / no CSS) RETURN cleanly; any RAISED exception from them is an
# infrastructure failure (Node launch/crash, helper missing) — verdict 7.
NODE_CHANNELS: frozenset[str] = frozenset({"c-ast-js", "c-css-prop"})
NODE_HELPER_DIRS: dict[str, Path] = {
    "ast_js": c_ast_js._NODE_DIR,
    "postcss": c_css_prop._POSTCSS_DIR,
}

# variant directory name → schema variant value (scheme §9).
VARIANTS: list[tuple[str, str]] = [("min", "P-min"), ("q", "P-q")]

N_EFF_MIN = 4  # summary eligibility (scheme §5)

# Pin native numeric thread pools to 1 (verdict 3/5): avoids Python-worker ×
# BLAS-thread oversubscription and removes reduction-order nondeterminism. Set in
# the parent BEFORE the spawn Pool so children inherit it at numpy import time.
_THREAD_ENV = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

# Bounded per-worker artifact LRU (verdict 3). Module-global → one per worker
# process under spawn; never shared with the parent.
_WORKER_ART_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_WORKER_ART_LRU = 64


class ChannelInfraError(RuntimeError):
    """A Node-backed channel raised — treated as infrastructure failure, not null."""


class InputChangedError(RuntimeError):
    """A slot's bytes changed between inventory hashing and worker load (TOCTOU)."""


def _limit_native_threads() -> None:
    for key in _THREAD_ENV:
        os.environ.setdefault(key, "1")


# ---------------------------------------------------------------------------
# artifact loading  (bytes-based; identical text semantics to Path.read_text)
# ---------------------------------------------------------------------------
def _decode_text(data: bytes) -> str:
    """Decode UTF-8 with universal-newline translation — byte-for-byte identical to
    ``Path.read_text(encoding='utf-8')`` (the form the channels were validated on)."""
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8").read()


def _artifacts_from_bytes(sb: dict[str, Any]) -> dict[str, Any]:
    """Build the universal artifact handle from pre-read slot bytes.

    Same contract as the historical ``_build_artifacts``: keys are added only when
    the underlying artifact exists (so DOM channels see a missing ``dom`` and null
    out), and a corrupt/undecodable shot drops every shot key + sets
    ``shot_decode_failed`` (null-not-crash), decoded EAGERLY at this guarded
    boundary. The shot is offered three ways (raw bytes under ``shot_png`` /
    ``shot_png_bytes`` and an open PIL image under ``image``) to satisfy the visual
    resolvers' differing key expectations. PIL auto-detects the webp container.
    """
    art: dict[str, Any] = {}
    if sb["html"] is not None:
        art["card_html"] = _decode_text(sb["html"])
    if sb["shot"] is not None:
        raw = sb["shot"]
        try:
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception:  # noqa: BLE001 — any decode fault → null-not-crash
            print(
                "[similarity] WARN corrupt/undecodable shot — channels → null",
                file=sys.stderr,
                flush=True,
            )
            art["shot_decode_failed"] = True
        else:
            art["shot_png"] = raw
            art["shot_png_bytes"] = raw
            art["image"] = img
    if sb["dom"] is not None:
        art["dom"] = json.loads(_decode_text(sb["dom"]))
    return art


def _build_artifacts(slot_dir: Path) -> dict[str, Any]:
    """Path-based artifact builder (kept for tests / golden fixtures)."""
    return _artifacts_from_bytes(C.read_slot_bytes(Path(slot_dir)))


# ---------------------------------------------------------------------------
# per-pair channel evaluation
# ---------------------------------------------------------------------------
def _compute_pair(art_a: dict, art_b: dict) -> tuple[dict, dict]:
    """Return ``(s_map, diagnostics)`` for one slot-pair across all 17 channels.

    Failure classification (verdict 7): a Node-backed channel raising is an
    INFRASTRUCTURE failure → ``ChannelInfraError`` (propagates, aborts the batch;
    never cached as null). Any other channel raising is a missing/degenerate
    artifact → S=null (legitimate, e.g. d-geom on absent dom.json). Diagnostic
    companions (unclamped c-ncd NCD_sym, raw v-ssim) are stored beside a non-null
    mapped value, exactly as the schema requires.
    """
    s_map: dict[str, float | None] = {}
    diag: dict[str, float] = {}
    for name, mod in CHANNELS:
        result: dict[str, Any]
        try:
            result = mod.compute(art_a, art_b)
            sv = result.get("s")
        except Exception as exc:  # noqa: BLE001
            if name in NODE_CHANNELS:
                raise ChannelInfraError(
                    f"channel {name} raised (infrastructure): {type(exc).__name__}: {exc}"
                ) from exc
            result, sv = {}, None
        s_map[name] = None if sv is None else float(sv)
        if name == "c-ncd" and s_map[name] is not None and result.get("ncd_sym") is not None:
            diag["c_ncd_ncd_sym"] = float(result["ncd_sym"])
        if name == "v-ssim" and s_map[name] is not None and result.get("raw") is not None:
            diag["v_ssim_raw"] = float(result["raw"])
    return s_map, diag


# ---------------------------------------------------------------------------
# aggregation (scheme §5 / 附录 A 分位数)
# ---------------------------------------------------------------------------
def _percentile_linear(values: list[float], q: float) -> float:
    """Linear-interpolation percentile (numpy 'linear' method), q in [0,100]."""
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = (q / 100.0) * (len(xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def _aggregate_cell(
    s_values: list[float | None], kind: str, m_a: int, m_b: int
) -> dict[str, Any]:
    """median / iqr / n_eff for one (config_a, config_b, channel) pair set."""
    non_null = [v for v in s_values if v is not None]
    n_eff = len(non_null)
    eligible = n_eff >= N_EFF_MIN
    if kind == "cross":
        eligible = eligible and m_a >= 2 and m_b >= 2
    if not eligible or not non_null:
        median: float | None = None
        iqr: dict[str, float] | None = None
    else:
        median = _percentile_linear(non_null, 50.0)
        iqr = {
            "p25": _percentile_linear(non_null, 25.0),
            "p75": _percentile_linear(non_null, 75.0),
        }
    return {"median": median, "iqr": iqr, "n_eff": n_eff, "m_a": m_a, "m_b": m_b}


# ---------------------------------------------------------------------------
# batch discovery / inventory
# ---------------------------------------------------------------------------
def _slot_dir(config_dir: Path, variant_dir: str, k: int) -> Path:
    return config_dir / variant_dir / "slots" / str(k)


def _valid_slots(config_dir: Path, variant_dir: str) -> list[int]:
    """Ascending valid slot indices for one config × variant."""
    return [idx for idx, _ in _slot_inventory(config_dir, variant_dir)]


def _slot_inventory(config_dir: Path, variant_dir: str) -> list[tuple[int, str]]:
    """Ordered ``(slot_index, slot_digest)`` for the VALID slots of one config ×
    variant. Reads each valid slot's artifact bytes once and hashes them (no PIL
    decode — identity is over raw bytes)."""
    slots_root = config_dir / variant_dir / "slots"
    if not slots_root.is_dir():
        return []
    out: list[tuple[int, str]] = []
    for slot in slots_root.iterdir():
        meta = slot / "meta.json"
        if not slot.is_dir() or not meta.is_file():
            continue
        if json.loads(meta.read_text(encoding="utf-8")).get("state") == "valid":
            sb = C.read_slot_bytes(slot)
            out.append((int(slot.name), C.slot_digest(sb)))
    out.sort(key=lambda t: t[0])
    return out


# ---------------------------------------------------------------------------
# spawn worker (module-level; receives only a small picklable spec)
# ---------------------------------------------------------------------------
def _worker_init() -> None:
    _limit_native_threads()


def _worker_load(
    configs_root: str, cid: str, vdir: str, idx: int, expected_digest: str
) -> dict:
    """Load a slot's artifacts through the bounded worker LRU, re-verifying the
    slot digest against the precomputed inventory (TOCTOU guard, verdict 3)."""
    key = (cid, vdir, idx)
    cached = _WORKER_ART_CACHE.get(key)
    if cached is not None:
        _WORKER_ART_CACHE.move_to_end(key)
        return cached
    slot_dir = Path(configs_root) / cid / vdir / "slots" / str(idx)
    sb = C.read_slot_bytes(slot_dir)
    got = C.slot_digest(sb)
    if got != expected_digest:
        raise InputChangedError(
            f"{cid}/{vdir}/slot {idx}: slot bytes changed mid-run "
            f"(expected {expected_digest[:12]}…, got {got[:12]}…)"
        )
    art = _artifacts_from_bytes(sb)
    _WORKER_ART_CACHE[key] = art
    _WORKER_ART_CACHE.move_to_end(key)
    while len(_WORKER_ART_CACHE) > _WORKER_ART_LRU:
        _WORKER_ART_CACHE.popitem(last=False)
    return art


def _pair_entries(spec: dict) -> list[dict]:
    kind = spec["kind"]
    croot = spec["configs_root"]
    vdir = spec["vdir"]
    cid_a = spec["config_a"]
    cid_b = spec["config_b"]
    entries: list[dict] = []
    if kind == "self":
        dig = {idx: d for idx, d in spec["slots_a"]}
        idxs = [idx for idx, _ in spec["slots_a"]]
        for i, j in combinations(idxs, 2):  # i<j
            art_i = _worker_load(croot, cid_a, vdir, i, dig[i])
            art_j = _worker_load(croot, cid_a, vdir, j, dig[j])
            s_map, d = _compute_pair(art_i, art_j)
            entry = {"slot_a": i, "slot_b": j, "s": s_map}
            if d:
                entry["diagnostics"] = d
            entries.append(entry)
    else:
        dig_a = {idx: d for idx, d in spec["slots_a"]}
        dig_b = {idx: d for idx, d in spec["slots_b"]}
        for i, _ in spec["slots_a"]:
            for j, _ in spec["slots_b"]:
                art_i = _worker_load(croot, cid_a, vdir, i, dig_a[i])
                art_j = _worker_load(croot, cid_b, vdir, j, dig_b[j])
                s_map, d = _compute_pair(art_i, art_j)
                entry = {"slot_a": i, "slot_b": j, "s": s_map}
                if d:
                    entry["diagnostics"] = d
                entries.append(entry)
    return entries


def _worker_pair(spec: dict) -> dict:
    """Compute + atomically write ONE pair document and its identity sidecar.

    Returns a small status record. Infrastructure failures (Node channel, TOCTOU,
    or any unexpected exception) come back as ``status='fail'`` so the parent aborts
    the whole batch WITHOUT publishing summaries — never caching a null (verdict 7).
    """
    _limit_native_threads()
    fname = spec.get("pair_filename")
    try:
        entries = _pair_entries(spec)
        doc = {
            "batch_id": spec["batch_id"],
            "variant": spec["variant"],
            "config_a": spec["config_a"],
            "config_b": spec["config_b"],
            "kind": spec["kind"],
            "channels": CHANNEL_NAMES,
            "pairs": entries,
        }
        data = C.dump_doc(doc)
        pair_path = Path(spec["pairs_dir"]) / fname
        C.atomic_write_if_changed(pair_path, data)
        # Sidecar AFTER the pair, bound to its live SHA-256 (verdict 2).
        C.write_sidecar(
            Path(spec["cache_pairs_dir"]), fname, pair_path, spec["sidecar_record"]
        )
        return {"status": "ok", "pair_filename": fname, "n_pairs": len(entries)}
    except (ChannelInfraError, InputChangedError) as exc:
        return {"status": "fail", "pair_filename": fname, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001 — unexpected → infrastructure fail
        return {
            "status": "fail",
            "pair_filename": fname,
            "error": f"UNEXPECTED {type(exc).__name__}: {exc}",
        }


# ---------------------------------------------------------------------------
# spec construction / scheduling
# ---------------------------------------------------------------------------
def _make_spec(
    kind, cid_a, cid_b, vdir, vlabel, fname, valid, inv, pdig, key,
    batch_id, pairs_dir, cache_pairs_dir, configs_root,
) -> dict:
    slots_a = [[idx, dig] for idx, dig in valid[(cid_a, vdir)]]
    slots_b = [[idx, dig] for idx, dig in valid[(cid_b, vdir)]]
    if kind == "self":
        invs = [inv[(cid_a, vdir)]]
    else:
        invs = [inv[(cid_a, vdir)], inv[(cid_b, vdir)]]
    record = {
        "pair_key": key,
        "pair_engine_digest": pdig,
        "variant": vlabel,
        "kind": kind,
        "config_a": cid_a,
        "config_b": cid_b,
        "inventories": invs,
    }
    return {
        "pair_filename": fname,
        "batch_id": batch_id,
        "variant": vlabel,
        "kind": kind,
        "config_a": cid_a,
        "config_b": cid_b,
        "vdir": vdir,
        "configs_root": str(configs_root),
        "pairs_dir": str(pairs_dir),
        "cache_pairs_dir": str(cache_pairs_dir),
        "slots_a": slots_a,
        "slots_b": slots_b,
        "sidecar_record": record,
    }


def _effective_workers(workers: int | None, n_pending: int) -> int:
    if n_pending <= 0:
        return 1
    if workers is not None:
        return max(1, min(int(workers), n_pending))
    cpu = os.cpu_count() or 2
    default = min(8, max(1, cpu - 2))
    return min(default, n_pending)


def _run_pending(pending: list[dict], eff_workers: int, verbose: bool) -> None:
    if not pending:
        return
    if eff_workers <= 1:
        for spec in pending:
            res = _worker_pair(spec)
            if res["status"] != "ok":
                raise RuntimeError(
                    f"similarity infrastructure failure on {res.get('pair_filename')}: "
                    f"{res.get('error')}"
                )
        return
    ctx = mp.get_context("spawn")
    # Chunk consecutive (config-adjacent) tasks to the same worker for LRU locality
    # while keeping enough chunks for balance.
    chunk = max(1, min(16, (len(pending) // (eff_workers * 4)) or 1))
    done = 0
    with ctx.Pool(processes=eff_workers, initializer=_worker_init) as pool:
        for res in pool.imap_unordered(_worker_pair, pending, chunksize=chunk):
            if res["status"] != "ok":
                # Pool.__exit__ terminates the remaining workers.
                raise RuntimeError(
                    f"similarity infrastructure failure on {res.get('pair_filename')}: "
                    f"{res.get('error')}"
                )
            done += 1
            if verbose and done % 2000 == 0:
                print(f"[similarity]   computed {done}/{len(pending)} pair-docs",
                      file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# summary aggregation from written pair files (verdict 4 — no recompute)
# ---------------------------------------------------------------------------
def _read_validate_pair(
    path: Path, batch_id, vlabel, cid_a, cid_b, kind, valid, vdir
) -> dict:
    """Load an expected pair file and validate identity / envelope / channels /
    exact slot-pair set (verdict 4). Any mismatch aborts summary publication."""
    if not path.is_file():
        raise RuntimeError(f"summary: expected pair file missing: {path.name}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    for field, exp in (
        ("batch_id", batch_id),
        ("variant", vlabel),
        ("config_a", cid_a),
        ("config_b", cid_b),
        ("kind", kind),
    ):
        if doc.get(field) != exp:
            raise RuntimeError(
                f"summary: {path.name} envelope mismatch: {field}={doc.get(field)!r} "
                f"expected {exp!r}"
            )
    if doc.get("channels") != CHANNEL_NAMES:
        raise RuntimeError(f"summary: {path.name} channels mismatch")
    if kind == "self":
        idxs = [idx for idx, _ in valid[(cid_a, vdir)]]
        expected_pairs = set(combinations(idxs, 2))
    else:
        ia = [idx for idx, _ in valid[(cid_a, vdir)]]
        ib = [idx for idx, _ in valid[(cid_b, vdir)]]
        expected_pairs = {(i, j) for i in ia for j in ib}
    got = {(e["slot_a"], e["slot_b"]) for e in doc["pairs"]}
    if got != expected_pairs:
        raise RuntimeError(
            f"summary: {path.name} slot-pair set mismatch "
            f"(got {len(got)}, expected {len(expected_pairs)})"
        )
    for e in doc["pairs"]:
        if set(e["s"].keys()) != set(CHANNEL_NAMES):
            raise RuntimeError(
                f"summary: {path.name} entry ({e.get('slot_a')},{e.get('slot_b')}) "
                "channel key set mismatch"
            )
    return doc


def _aggregate_variant(
    batch_id, vlabel, vdir, config_ids, valid, pairs_dir, expected
) -> dict:
    """Fold the written pair files (enumerated in manifest order) into the
    config×config×channel summary cells — WITHOUT recomputing any channel."""
    cells: list[dict] = []
    for kind, cid_a, cid_b, fname in expected:
        m_a = len(valid[(cid_a, vdir)])
        m_b = len(valid[(cid_b, vdir)])
        doc = _read_validate_pair(
            pairs_dir / fname, batch_id, vlabel, cid_a, cid_b, kind, valid, vdir
        )
        per: dict[str, list] = {n: [] for n in CHANNEL_NAMES}
        for entry in doc["pairs"]:
            s = entry["s"]
            for n in CHANNEL_NAMES:
                per[n].append(s[n])
        for n in CHANNEL_NAMES:
            agg = _aggregate_cell(per[n], kind, m_a, m_b)
            cells.append({
                "config_a": cid_a,
                "config_b": cid_b,
                "channel": n,
                "kind": kind,
                "median": agg["median"],
                "iqr": agg["iqr"],
                "n_eff": agg["n_eff"],
                "m_a": m_a,
                "m_b": m_b,
            })
    return {
        "batch_id": batch_id,
        "variant": vlabel,
        "channels": CHANNEL_NAMES,
        "configs": config_ids,
        "cells": cells,
    }


# ---------------------------------------------------------------------------
# summary / config sidecar helpers
# ---------------------------------------------------------------------------
def _summary_sidecar_path(cache_dir: Path, sname: str) -> Path:
    return cache_dir / (sname + C.SIDECAR_SUFFIX)


def _summary_cache_ok(cache_dir: Path, sname: str, spath: Path, skey: str) -> bool:
    scp = _summary_sidecar_path(cache_dir, sname)
    if not scp.is_file() or not spath.is_file():
        return False
    try:
        rec = json.loads(scp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if rec.get("summary_key") != skey:
        return False
    try:
        return C.sha256_file(spath) == rec.get("summary_sha256")
    except OSError:
        return False


def _write_summary_sidecar(cache_dir, sname, spath, skey, sdig, vlabel) -> None:
    rec = {
        "summary_key": skey,
        "summary_engine_digest": sdig,
        "variant": vlabel,
        "summary_sha256": C.sha256_file(spath),
    }
    C.atomic_write_bytes(
        _summary_sidecar_path(cache_dir, sname),
        (json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )


def _update_config_m(path: Path, m_obj: dict[str, int]) -> bool:
    """Rewrite ``m`` to the per-variant object, preserving config.json's key order
    and indent; skip the write entirely if the bytes are unchanged (warm-run mtime
    stability)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["m"] = m_obj
    data = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return C.atomic_write_if_changed(path, data)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def run(
    batch_dir: Path,
    *,
    workers: int | None = None,
    force: bool = False,
    verbose: bool = True,
) -> dict[str, Any]:
    t0 = time.monotonic()
    _limit_native_threads()

    batch_dir = Path(batch_dir)
    batch_id = batch_dir.name
    manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
    config_ids: list[str] = list(manifest["config_ids"])  # persisted order — never re-sort
    configs_root = batch_dir / "configs"

    sim_dir = batch_dir / "similarity"
    pairs_dir = sim_dir / "pairs"
    cache_dir = sim_dir / C.CACHE_DIRNAME
    cache_pairs_dir = cache_dir / "pairs"
    pairs_dir.mkdir(parents=True, exist_ok=True)
    cache_pairs_dir.mkdir(parents=True, exist_ok=True)

    with C.BatchLock(cache_dir):
        # -- engine fingerprint (pair vs summary kept separate) ----------------
        eng = C.build_engine_fingerprint(CHANNELS, CHANNEL_NAMES, N_EFF_MIN, NODE_HELPER_DIRS)
        pdig = eng["pair_engine_digest"]
        sdig = eng["summary_engine_digest"]

        # -- inventory: valid slots + per-slot digest + inventory digest -------
        valid: dict[tuple[str, str], list[tuple[int, str]]] = {}
        inv: dict[tuple[str, str], str] = {}
        for cid in config_ids:
            for vdir, _ in VARIANTS:
                entries = _slot_inventory(configs_root / cid, vdir)
                valid[(cid, vdir)] = entries
                inv[(cid, vdir)] = C.inventory_digest(entries)

        if C.incomplete_marker_present(sim_dir) and verbose:
            print(
                "[similarity] WARN prior INCOMPLETE marker present — resuming; "
                "valid cached pairs are reused, the rest recompute",
                file=sys.stderr,
                flush=True,
            )
        C.write_incomplete_marker(sim_dir, {"batch_id": batch_id, "pid": os.getpid()})

        # -- enumerate expected pairs (manifest order) + split hit/pending -----
        pending: list[dict] = []
        expected_by_variant: dict[str, list[tuple]] = {}
        all_expected_names: list[str] = []
        n_hit = 0
        for vdir, vlabel in VARIANTS:
            exp: list[tuple] = []
            for cid in config_ids:  # self
                fname = f"{cid}--{cid}--{vlabel}.json"
                exp.append(("self", cid, cid, fname))
                all_expected_names.append(fname)
                key = C.pair_key(pdig, vlabel, "self", [cid], [inv[(cid, vdir)]])
                if (not force) and C.validate_pair_cache(
                    cache_pairs_dir, pairs_dir, fname, key
                ):
                    n_hit += 1
                else:
                    pending.append(_make_spec(
                        "self", cid, cid, vdir, vlabel, fname, valid, inv, pdig, key,
                        batch_id, pairs_dir, cache_pairs_dir, configs_root,
                    ))
            for cid_a, cid_b in combinations(config_ids, 2):  # cross
                fname = f"{cid_a}--{cid_b}--{vlabel}.json"
                exp.append(("cross", cid_a, cid_b, fname))
                all_expected_names.append(fname)
                key = C.pair_key(
                    pdig, vlabel, "cross", [cid_a, cid_b],
                    [inv[(cid_a, vdir)], inv[(cid_b, vdir)]],
                )
                if (not force) and C.validate_pair_cache(
                    cache_pairs_dir, pairs_dir, fname, key
                ):
                    n_hit += 1
                else:
                    pending.append(_make_spec(
                        "cross", cid_a, cid_b, vdir, vlabel, fname, valid, inv, pdig, key,
                        batch_id, pairs_dir, cache_pairs_dir, configs_root,
                    ))
            expected_by_variant[vlabel] = exp

        n_expected = len(all_expected_names)
        eff_workers = _effective_workers(workers, len(pending))
        if verbose:
            print(
                f"[similarity] {batch_id}: {n_expected} expected pair-docs, "
                f"{n_hit} cache-hit, {len(pending)} to compute, workers={eff_workers}"
                + (" (--force)" if force else ""),
                file=sys.stderr,
                flush=True,
            )

        # -- compute pending (serial for workers<=1, else spawn pool) ----------
        _run_pending(pending, eff_workers, verbose)

        # -- summaries: aggregate from written pair files, per variant ---------
        summaries_written: list[str] = []
        for vdir, vlabel in VARIANTS:
            sname = f"summary--{vlabel}.json"
            spath = sim_dir / sname
            skey = C.summary_key(
                sdig, vlabel, config_ids, {cid: inv[(cid, vdir)] for cid in config_ids}
            )
            if (not force) and _summary_cache_ok(cache_dir, sname, spath, skey):
                summaries_written.append(sname)
                continue
            summary_doc = _aggregate_variant(
                batch_id, vlabel, vdir, config_ids, valid, pairs_dir,
                expected_by_variant[vlabel],
            )
            C.atomic_write_if_changed(spath, C.dump_doc(summary_doc))
            _write_summary_sidecar(cache_dir, sname, spath, skey, sdig, vlabel)
            summaries_written.append(sname)

        # -- rewrite per-variant m in each config.json (skip if unchanged) -----
        m_updates: dict[str, dict[str, int]] = {}
        for cid in config_ids:
            m_obj = {"min": len(valid[(cid, "min")]), "q": len(valid[(cid, "q")])}
            _update_config_m(configs_root / cid / "config.json", m_obj)
            m_updates[cid] = m_obj

        # -- completion manifest (deterministic, LAST) + drop marker -----------
        completion = {
            "batch_id": batch_id,
            "config_ids": config_ids,
            "pair_engine_digest": pdig,
            "summary_engine_digest": sdig,
            "n_expected_pairs": n_expected,
            "inventory": {
                f"{cid}/{vdir}": inv[(cid, vdir)]
                for cid in config_ids
                for vdir, _ in VARIANTS
            },
        }
        C.write_completion_manifest(cache_dir, completion)
        C.remove_incomplete_marker(sim_dir)

    wall = time.monotonic() - t0
    return {
        "batch_id": batch_id,
        "config_ids": config_ids,
        "pairs_written": all_expected_names,
        "summaries_written": summaries_written,
        "m_updates": m_updates,
        "pairs_expected": n_expected,
        "pairs_cache_hit": n_hit,
        "pairs_computed": len(pending),
        "workers": eff_workers,
        "wall_s": round(wall, 3),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m runner.similarity.runner",
        description="Incremental + parallel L2 similarity runner.",
    )
    ap.add_argument("batch_dir", help="path to the batch directory")
    ap.add_argument("--workers", type=int, default=None,
                    help="worker processes (default min(8, cpu-2))")
    ap.add_argument("--force", action="store_true",
                    help="ignore the incremental cache and recompute everything")
    args = ap.parse_args(argv[1:])
    result = run(Path(args.batch_dir), workers=args.workers, force=args.force)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
