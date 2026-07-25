#!/usr/bin/env python3
"""Regenerate every similarity figure the README quotes, from one definition.

The README used to carry hand-computed numbers that were a PLAIN MEAN of the 20
formal channel medians, while the site's matrix showed a contrast-stretched mean
under the same name, "merged". Both were internally consistent and they did not
agree. Worse, the plain mean is dominated by whichever channels have the widest
spread and the highest baseline -- which is why adding the x-* family in v13
lifted every published figure by ~0.07 without any card changing.

This tool computes the published figures from the SINGLE definition the site
now uses: the frozen per-channel stretch domain in similarity/merge-domain.lock.json
(runner/tools/build_merge_domain_lock.py), averaged over the 20 formal channels.
Run it after any recompute and paste the output into the READMEs, so alignment is
mechanical rather than remembered.

Usage:
  .venv/bin/python -m runner.tools.report_findings <batch_dir> [--variant P-min]
  .venv/bin/python -m runner.tools.report_findings <batch_dir> --raw   # old scale, for comparison
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

DIAGNOSTIC = frozenset({"c-ncd", "d-tagpath"})
N_EFF_MIN, M_CROSS_MIN = 4, 2
MERGE_MIN_CH = 6


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def load(batch: Path, variant: str, raw: bool):
    fac = {}
    for d in (batch / "configs").iterdir():
        f = d / "config.json"
        if f.is_file():
            c = json.loads(f.read_text())
            fac[c["config_id"]] = c

    dom = None
    if not raw:
        lock = json.loads((batch / "similarity" / "merge-domain.lock.json").read_text())
        dom = lock["variants"][variant]["channels"]

    doc = json.loads((batch / "similarity" / f"summary--{variant}.json").read_text())
    acc: dict[tuple[str, str, str], list[float]] = {}
    for cell in doc["cells"]:
        if cell["median"] is None or cell["n_eff"] < N_EFF_MIN:
            continue
        if cell["kind"] == "cross" and (cell["m_a"] < M_CROSS_MIN or cell["m_b"] < M_CROSS_MIN):
            continue
        ch = cell["channel"]
        if ch in DIAGNOSTIC:
            continue
        if dom is None:
            v = cell["median"]
        else:
            d = dom.get(ch)
            if d is None:
                continue
            v = clamp01((cell["median"] - d["lo"]) / (d["hi"] - d["lo"]))
        acc.setdefault((cell["kind"], cell["config_a"], cell["config_b"]), []).append(v)

    merged = {k: sum(v) / len(v) for k, v in acc.items() if len(v) >= MERGE_MIN_CH}
    return fac, merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_dir", type=Path)
    ap.add_argument("--variant", default="P-min")
    ap.add_argument("--raw", action="store_true",
                    help="plain mean of channel medians (the OLD README scale)")
    ap.add_argument("--configs", metavar="SUBSTR",
                    help="also dump every config whose id contains SUBSTR, with its "
                         "mean to its OWN family and to everything else (the effort-"
                         "drift bullet needs this)")
    args = ap.parse_args()
    batch = args.batch_dir.resolve()
    fac, merged = load(batch, args.variant, args.raw)
    scale = "RAW plain mean (old scale)" if args.raw else "FROZEN merged (locked stretch domain)"

    selfc = {a: v for (k, a, b), v in merged.items() if k == "self"}
    cross = {(a, b): v for (k, a, b), v in merged.items() if k == "cross"}
    within = [v for (a, b), v in cross.items() if fac[a]["family"] == fac[b]["family"]]
    between = [v for (a, b), v in cross.items() if fac[a]["family"] != fac[b]["family"]]

    print(f"═══ {args.variant} · {scale} · 20 formal channels ═══\n")
    print(f"self-consistency (diagonal)   n={len(selfc):5d}  mean {statistics.fmean(selfc.values()):.3f}")
    print(f"within-family cross           n={len(within):5d}  mean {statistics.fmean(within):.3f}")
    print(f"cross-family                  n={len(between):5d}  mean {statistics.fmean(between):.3f}")

    # per-config mean to everything else, and to its own family
    to_all, to_fam, to_other = defaultdict(list), defaultdict(list), defaultdict(list)
    for (a, b), v in cross.items():
        same = fac[a]["family"] == fac[b]["family"]
        for x, y in ((a, b), (b, a)):
            to_all[x].append(v)
            (to_fam if same else to_other)[x].append(v)

    print("\n-- most divergent configs (lowest mean to everything else, n>=30) --")
    ranked = sorted(((statistics.fmean(v), c) for c, v in to_all.items() if len(v) >= 30))
    for m, c in ranked[:8]:
        print(f"   {m:.3f}  {c}")

    print("\n-- family cohesion: mean WITHIN family --")
    fam_within = defaultdict(list)
    for (a, b), v in cross.items():
        if fac[a]["family"] == fac[b]["family"]:
            fam_within[fac[a]["family"]].append(v)
    for f, vs in sorted(fam_within.items(), key=lambda kv: -statistics.fmean(kv[1])):
        if len(vs) >= 10:
            print(f"   {statistics.fmean(vs):.3f}  {f:<12} (n={len(vs)})")

    print("\n-- cross-family reach: mean to OTHER families, by family --")
    fam_other = defaultdict(list)
    for c, vs in to_other.items():
        fam_other[fac[c]["family"]].extend(vs)
    for f, vs in sorted(fam_other.items(), key=lambda kv: -statistics.fmean(kv[1])):
        if len(vs) >= 100:
            print(f"   {statistics.fmean(vs):.3f}  {f:<12} (n={len(vs)})")

    print("\n-- lowest self-consistency (a different card most re-runs) --")
    for c, v in sorted(selfc.items(), key=lambda kv: kv[1])[:8]:
        print(f"   {v:.3f}  {c}")
    print(f"\n   {len(selfc)} configs have a self-consistency reading at all "
          f"(needs several re-runs of one config in one variant).")

    # the claude max-effort outlier claim, scoped to its own family
    print("\n-- max-effort frontier Claude vs its own family --")
    cl = {c: statistics.fmean(v) for c, v in to_fam.items()
          if fac[c]["family"] == "claude" and len(v) >= 10}
    if cl:
        base = statistics.fmean([v for (a, b), v in cross.items()
                                 if fac[a]["family"] == fac[b]["family"] == "claude"])
        print(f"   claude within-family overall: {base:.3f}")
        for c, v in sorted(cl.items(), key=lambda kv: kv[1])[:6]:
            print(f"   {v:.3f}  {c}")

    if args.configs:
        print(f"\n-- configs matching {args.configs!r}: mean to own family / to others --")
        rows = []
        for c in sorted(to_all):
            if args.configs not in c:
                continue
            fam_v = statistics.fmean(to_fam[c]) if to_fam.get(c) else None
            oth_v = statistics.fmean(to_other[c]) if to_other.get(c) else None
            rows.append((c, fam_v, oth_v, len(to_fam.get(c, []))))
        for c, fam_v, oth_v, n in rows:
            f1 = f"{fam_v:.3f}" if fam_v is not None else "  —  "
            f2 = f"{oth_v:.3f}" if oth_v is not None else "  —  "
            print(f"   own-family {f1}   others {f2}   (n_fam={n:3d})  {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
