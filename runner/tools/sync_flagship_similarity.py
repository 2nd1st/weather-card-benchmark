#!/usr/bin/env python3
"""Regenerate the public flagship subset's similarity/ from the unified batch.

The public repo ships a flagship subset (one canonical config per frontier lab)
so the site renders out of the box. Its similarity data was hand-assembled once
and then drifted, in three ways all found on 2026-07-25:

  * still **17 channels** — never refreshed after the v13 x-* expansion, while the
    public README advertised "22 visual and structural similarity channels";
  * ``summary.configs`` listed **192** config ids inside a subset with 11 config
    dirs — the roster was copied wholesale from the unified batch, and 192 was
    already stale (the set is 201);
  * pair documents carried ``batch_id: <...>--unified`` inside a directory named
    ``--flagship``.

Hand-assembly is why. This makes it one command in the ship sequence.

The flagship ROSTER is read from the target's own configs/ directory — this tool
syncs data, it never decides which models are flagship.

Usage:
  .venv/bin/python -m runner.tools.sync_flagship_similarity <unified_batch> <flagship_batch>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

VARIANTS = ("P-min", "P-q")


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("unified", type=Path)
    ap.add_argument("flagship", type=Path)
    args = ap.parse_args()
    uni, flag = args.unified.resolve(), args.flagship.resolve()
    for p in (uni / "similarity", flag / "configs"):
        if not p.is_dir():
            print(f"missing {p}", file=sys.stderr)
            return 2

    roster = sorted(d.name for d in (flag / "configs").iterdir() if (d / "config.json").is_file())
    keep = set(roster)
    flag_id = flag.name
    print(f"flagship roster: {len(roster)} configs -> batch_id {flag_id}")

    dst_sim = flag / "similarity"
    dst_pairs = dst_sim / "pairs"
    if dst_pairs.exists():
        shutil.rmtree(dst_pairs)          # stale pairs must not survive a re-cut
    dst_pairs.mkdir(parents=True, exist_ok=True)

    # ---- pairs: only those whose BOTH endpoints are in the roster ----
    copied = 0
    for src in sorted((uni / "similarity" / "pairs").glob("*.json")):
        doc = json.loads(src.read_text())
        if doc["config_a"] not in keep or doc["config_b"] not in keep:
            continue
        doc["batch_id"] = flag_id
        write_atomic(dst_pairs / src.name, json.dumps(doc, indent=2, sort_keys=True,
                                                     allow_nan=False) + "\n")
        copied += 1

    # ---- summaries: restrict cells AND the config roster ----
    for variant in VARIANTS:
        src = uni / "similarity" / f"summary--{variant}.json"
        if not src.is_file():
            continue
        doc = json.loads(src.read_text())
        doc["batch_id"] = flag_id
        doc["configs"] = roster
        doc["cells"] = [
            c for c in doc["cells"] if c["config_a"] in keep and c["config_b"] in keep
        ]
        write_atomic(dst_sim / f"summary--{variant}.json",
                     json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n")
        print(f"  summary--{variant}: {len(doc['cells'])} cells, "
              f"{len(doc['channels'])} channels, {len(roster)} configs")

    # ---- the frozen merge domain, if the unified batch has one ----
    lock = uni / "similarity" / "merge-domain.lock.json"
    if lock.is_file():
        doc = json.loads(lock.read_text())
        doc["batch_id"] = flag_id
        doc.setdefault("recipe", {})["domain_source"] = uni.name
        write_atomic(dst_sim / "merge-domain.lock.json",
                     json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n")
        print("  merge-domain.lock.json copied (domain stays the FULL-SET domain, "
              "so subset numbers remain comparable to the published ones)")

    print(f"  pairs: {copied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
