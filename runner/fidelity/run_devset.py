#!/usr/bin/env python3
"""Run the R5 fidelity extractor over the devset-42 corpus (informational).

For every card with a ``dom.json`` under ``data/batches-dev/devset-42/cards/`` this
emits ``data/batches-dev/devset-42/fidelity/<slug>.trace.json`` (validated against
fidelity-trace.schema.json) plus a ``SUMMARY.md`` of per-field terminal-verdict
distributions and informational match rates.

The expected ground truth is the frozen ``DEVSET_EXPECTED`` snapshot (Berlin
2026-07-16; see expected.py). Match rates are DESCRIPTIVE — similarity ≠ quality,
and the dev fixture carries a few cache-overlay inconsistencies (documented in
expected.py) that legitimately read out as mismatch.

Usage:  .venv/bin/python -m runner.fidelity.run_devset
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import jsonschema

from runner.fidelity import DEVSET_EXPECTED, EXTRACTOR_VERSION, extract

REPO = Path(__file__).resolve().parents[2]
CARDS = REPO / "data" / "batches-dev" / "devset-42" / "cards"
OUT = REPO / "data" / "batches-dev" / "devset-42" / "fidelity"
TRACE_SCHEMA = json.loads((REPO / "data/SCHEMA/fidelity-trace.schema.json").read_text())

FIELDS = ["name", "date", "condition", "max", "min", "hourly"]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    slugs = sorted(
        p.name for p in CARDS.iterdir() if p.is_dir() and (p / "dom.json").exists()
    )
    dist: dict[str, Counter] = {f: Counter() for f in FIELDS}
    rows: list[tuple[str, dict]] = []
    hourly_cov: list[tuple[str, int, int, int]] = []

    for slug in slugs:
        dom = json.loads((CARDS / slug / "dom.json").read_text())
        out = extract(dom, DEVSET_EXPECTED, slot_index=0)
        jsonschema.validate(out["trace"], TRACE_SCHEMA)
        (OUT / f"{slug}.trace.json").write_text(
            json.dumps(out["trace"], ensure_ascii=False, indent=2)
        )
        meta = out["meta"]
        for f in FIELDS:
            dist[f][meta[f]["state"]] += 1
        rows.append((slug, meta))
        h = meta["hourly"]
        hourly_cov.append((slug, h["points_match"], h["points_mismatch"], h["points_not_shown"]))

    _write_summary(slugs, dist, rows, hourly_cov)
    print(f"[fidelity] wrote {len(slugs)} traces + SUMMARY.md to {OUT}", file=sys.stderr)
    return 0


def _write_summary(slugs, dist, rows, hourly_cov) -> None:
    n = len(slugs)
    lines: list[str] = []
    lines.append("# Devset-42 fidelity verdicts (informational)\n")
    lines.append(f"- extractor_version: `{EXTRACTOR_VERSION}`")
    lines.append(f"- cards with `dom.json`: **{n}** (of 37 dirs; 1 model-failed card has no dom)")
    lines.append("- expected snapshot: Berlin 2026-07-16 — max 28.5°, min 18.6°, "
                 "code 3 (Overcast), 24 hourly UTC values")
    lines.append("- **similarity ≠ quality**; these are descriptive per-field state "
                 "distributions, not a score.\n")

    states_order = ["match", "mismatch", "ambiguous", "not-found"]
    lines.append("## Per-field terminal-verdict distribution\n")
    lines.append("| field | match | mismatch | ambiguous | not-found |")
    lines.append("|---|---|---|---|---|")
    for f in FIELDS:
        c = dist[f]
        cells = " | ".join(str(c.get(s, 0)) for s in states_order)
        lines.append(f"| {f} | {cells} |")
    lines.append("")

    lines.append("## Per-card verdicts\n")
    lines.append("| card | name | date | condition | max | min | hourly | h M/X/NS |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for slug, meta in rows:
        h = meta["hourly"]
        mxns = f"{h['points_match']}/{h['points_mismatch']}/{h['points_not_shown']}"

        def cell(fld):
            m = meta[fld]
            v = m.get("value")
            return m["state"] + (f" ({v})" if v else "")

        lines.append(
            f"| {slug} | {meta['name']['state']} | {meta['date']['state']} | "
            f"{meta['condition']['state']} | {cell('max')} | {cell('min')} | "
            f"{h['state']} | {mxns} |"
        )
    lines.append("")

    lines.append("## Notes / FLAGGED ambiguities\n")
    lines.append("- Cards rendered against cache-overlay responses (live max=30.1 / shifted "
                 "hourly, see expected.py) read out as `mismatch` — a real dev-fixture "
                 "property, not an extractor defect.")
    lines.append("- High `ambiguous`/`not-found` rates are expected: §3 is deliberately "
                 "conservative (strict degree-symbol temperature gate; clock-time hourly "
                 "template matches spurious sunrise/sunset labels → repeated-hour conflict).")

    (OUT / "SUMMARY.md").write_text("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
