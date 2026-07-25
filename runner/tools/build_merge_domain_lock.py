#!/usr/bin/env python3
"""Freeze the `merged` pseudo-channel's per-channel contrast-stretch domain.

WHY THIS EXISTS
---------------
`merged` is not a measurement — it is derived in the client from the real
channels. Channels are not on a common scale (c-winnow's cross-pair median is
0.22, x-semantics' is 0.95), so a plain mean of channel medians is really
"whichever channel has the widest spread and the highest baseline". That is not
hypothetical: adding the 5-channel x-* family in v13 lifted every published
figure by ~0.07 purely because the family's raw cosines sit near 0.84.

So each channel is contrast-stretched to [0,1] over its own p5-p95 before the
mean is taken, which gives every channel an equal vote. The catch is WHICH
population defines p5-p95. The client used to recompute it over whatever configs
the current view happened to show, which made `merged` change when you filtered
the matrix -- fine as a display, useless as a number you can cite, and the reason
the site and the README disagreed while both calling their number "merged".

This tool computes the domain ONCE over the full production set and freezes it
into a lock file. After that `merged` is view-independent, citable, and identical
in the matrix, the README and any downstream analysis.

The lock is keyed by variant: P-min and P-q have genuinely different channel
distributions and must not share a domain.

Usage:
  .venv/bin/python -m runner.tools.build_merge_domain_lock <batch_dir> [--report]

--report prints the recipe comparison (population and percentile choices, with
the resulting clamp rates) and writes nothing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path

SCHEMA_ID = "merge-domain-lock/1"

# The 2 permanent diagnostic channels are "displayed, never statted" (scheme §4
# v9) -- folding them into a score would contradict that.
DIAGNOSTIC = frozenset({"c-ncd", "d-tagpath"})

# Eligibility, mirroring site/lib/neutral.ts isSufficient (scheme §5/§7).
N_EFF_MIN = 4
M_CROSS_MIN = 2

# A channel whose stretched span would be degenerate contributes nothing but
# noise; it is dropped from the merge entirely (same threshold the client used).
MIN_SPAN = 0.02

# Frozen recipe. --report regenerates the evidence for both choices.
#
# POPULATION: cross vs cross+self moves every figure by <=0.1pp (51 self cells
# against 20100 cross), so cross is chosen as the simpler statement -- and the
# honest one: the diagonal is the quantity being measured, not part of the scale
# that measures it.
#
# PERCENTILES: p1-p99 over the p5-p95 the client used to apply. p5-p95 saturated
# 33.4% of SELF cells at the top of the domain (P-min), i.e. a third of the
# self-consistency diagonal -- a headline quantity -- collapsed to a flat 1.0,
# and clamped 13.7% of cross cells. p1-p99 cuts those to 16.5% and 5.9%. The cost
# is 0.009 of merged family-discrimination (AUC 0.7424 -> 0.7331 P-min,
# 0.6971 -> 0.6833 P-q), which is the right trade: a value that is clamped is not
# a measurement of that pair at all.
LO_P, HI_P = 0.01, 0.99
POPULATION = "cross"

VARIANTS = ("P-min", "P-q")


def pct(sorted_vals: list[float], p: float) -> float:
    i = (len(sorted_vals) - 1) * p
    f, c = math.floor(i), math.ceil(i)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (i - f)


def eligible(cell: dict) -> bool:
    if cell["median"] is None:
        return False
    if cell["n_eff"] < N_EFF_MIN:
        return False
    if cell["kind"] == "cross" and (cell["m_a"] < M_CROSS_MIN or cell["m_b"] < M_CROSS_MIN):
        return False
    return True


def collect(batch: Path, variant: str) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """(cross values, self values) per channel, eligible cells only."""
    doc = json.loads((batch / "similarity" / f"summary--{variant}.json").read_text())
    cross: dict[str, list[float]] = {}
    selfv: dict[str, list[float]] = {}
    for cell in doc["cells"]:
        if not eligible(cell):
            continue
        bucket = cross if cell["kind"] == "cross" else selfv
        bucket.setdefault(cell["channel"], []).append(cell["median"])
    for d in (cross, selfv):
        for v in d.values():
            v.sort()
    return cross, selfv


def domain_for(cross: dict[str, list[float]], selfv: dict[str, list[float]],
               population: str, lo_p: float, hi_p: float) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for ch in sorted(cross):
        if ch in DIAGNOSTIC:
            continue
        vals = cross[ch] if population == "cross" else sorted(cross[ch] + selfv.get(ch, []))
        if len(vals) < 8:
            continue
        lo, hi = pct(vals, lo_p), pct(vals, hi_p)
        if hi - lo < MIN_SPAN:
            continue
        out[ch] = {"lo": lo, "hi": hi}
    return out


def clamp_rate(vals: list[float], lo: float, hi: float) -> float:
    if not vals:
        return 0.0
    return sum(1 for v in vals if v <= lo or v >= hi) / len(vals)


def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def _auc(pos: list[float], neg: list[float]) -> float | None:
    """P(random pos ranks above random neg), midrank ties."""
    if not pos or not neg:
        return None
    r = _rank(pos + neg)
    n1, n2 = len(pos), len(neg)
    return (sum(r[:n1]) - n1 * (n1 + 1) / 2) / (n1 * n2)


def _merged_auc(batch: Path, variant: str, dom: dict[str, dict[str, float]]) -> float | None:
    """Family discrimination of the MERGED value under a candidate domain.

    The clamp rates say how much information the stretch throws away; this says
    whether throwing it away costs anything that matters. Recipe choice follows
    this number, not the clamp rate alone.
    """
    fam: dict[str, str] = {}
    for d in (batch / "configs").iterdir():
        f = d / "config.json"
        if f.is_file():
            c = json.loads(f.read_text())
            fam[c["config_id"]] = c.get("family")
    doc = json.loads((batch / "similarity" / f"summary--{variant}.json").read_text())
    acc: dict[tuple[str, str], list[float]] = {}
    for cell in doc["cells"]:
        if cell["kind"] != "cross" or not eligible(cell):
            continue
        d = dom.get(cell["channel"])
        if d is None:
            continue
        t = (cell["median"] - d["lo"]) / (d["hi"] - d["lo"])
        acc.setdefault((cell["config_a"], cell["config_b"]), []).append(min(1.0, max(0.0, t)))
    within, between = [], []
    for (a, b), vs in acc.items():
        if len(vs) < 6:
            continue
        (within if fam.get(a) == fam.get(b) else between).append(sum(vs) / len(vs))
    return _auc(within, between)


def report(batch: Path) -> None:
    for variant in VARIANTS:
        p = batch / "similarity" / f"summary--{variant}.json"
        if not p.is_file():
            continue
        cross, selfv = collect(batch, variant)
        print(f"\n═══ {variant} ═══")
        print(f"{'recipe':<28}{'channels':>9}{'cross clamp%':>14}{'self clamp%':>13}"
              f"{'self@1.0%':>11}{'merged AUCfam':>15}")
        for population in ("cross", "cross+self"):
            for lo_p, hi_p in ((0.05, 0.95), (0.02, 0.98), (0.01, 0.99)):
                dom = domain_for(cross, selfv, population, lo_p, hi_p)
                cr, sr, sat = [], [], []
                for ch, d in dom.items():
                    cr.append(clamp_rate(cross[ch], d["lo"], d["hi"]))
                    sv = selfv.get(ch, [])
                    sr.append(clamp_rate(sv, d["lo"], d["hi"]))
                    sat.append(sum(1 for v in sv if v >= d["hi"]) / len(sv) if sv else 0.0)
                mean = lambda xs: sum(xs) / len(xs) * 100 if xs else 0.0  # noqa: E731
                auc = _merged_auc(batch, variant, dom)
                print(f"{population + f'  p{lo_p*100:g}-p{hi_p*100:g}':<28}{len(dom):>9}"
                      f"{mean(cr):>13.1f}%{mean(sr):>12.1f}%{mean(sat):>10.1f}%"
                      f"{(f'{auc:.4f}' if auc is not None else '—'):>15}")
        print("\n  self@1.0% = share of SELF (diagonal) cells that saturate at the top of the\n"
              "  domain. High saturation flattens self-consistency, the one quantity the\n"
              "  diagonal exists to show. merged AUCfam = does the resulting merged value\n"
              "  still separate same-family from cross-family pairs — the check that the\n"
              "  wider domain is not bought with lost discrimination.")


def build(batch: Path) -> dict:
    variants: dict[str, dict] = {}
    for variant in VARIANTS:
        p = batch / "similarity" / f"summary--{variant}.json"
        if not p.is_file():
            continue
        cross, selfv = collect(batch, variant)
        dom = domain_for(cross, selfv, POPULATION, LO_P, HI_P)
        variants[variant] = {
            "channels": dom,
            "n_cross_cells": {ch: len(cross[ch]) for ch in dom},
        }
    return {
        "schema": SCHEMA_ID,
        "batch_id": batch.name,
        "recipe": {
            "percentiles": [LO_P, HI_P],
            "population": POPULATION,
            "min_span": MIN_SPAN,
            "diagnostic_excluded": sorted(DIAGNOSTIC),
            "n_eff_min": N_EFF_MIN,
            "m_cross_min": M_CROSS_MIN,
            "note": (
                "merged(pair) = mean over these channels of "
                "clamp01((median_ch - lo) / (hi - lo)); a pair needs >= merge_min "
                "contributing channels or it reads as insufficient. Frozen over the "
                "full production set so the value does not move when the view is "
                "filtered."
            ),
        },
        "variants": variants,
    }


def write_atomic(path: Path, payload: dict) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
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
    ap.add_argument("batch_dir", type=Path)
    ap.add_argument("--report", action="store_true", help="compare recipes, write nothing")
    args = ap.parse_args()
    batch = args.batch_dir.resolve()
    if not (batch / "similarity").is_dir():
        print(f"no similarity/ under {batch}", file=sys.stderr)
        return 2
    if args.report:
        report(batch)
        return 0
    payload = build(batch)
    out = batch / "similarity" / "merge-domain.lock.json"
    write_atomic(out, payload)
    for variant, v in payload["variants"].items():
        print(f"{variant}: {len(v['channels'])} channels locked")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
