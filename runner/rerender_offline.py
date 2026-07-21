"""Offline deterministic re-render of a batch's stored cards (task W step 3).

Re-renders every VALID slot's already-captured ``card.html`` WITHOUT calling any
model, producing the artifacts the live runner now emits:

  * ``dom.json``, ``shot.png`` (retained), ``shot.webp`` / ``thumb.webp``,
  * completed ``meta.json.l1`` (bytes + structure + visual + palette_top8),
  * ``meta.json.fidelity`` terminal verdict + per-slot ``fidelity-trace.json``,

then re-validates every slot ``meta.json`` (all six terminal states) against
slot-meta.schema.json. Rendering is byte-deterministic (scheme §2: frozen Date +
mulberry32 + CDP virtual clock), so re-rendering stored cards is reproducible.

The expected-value table is DERIVED from the batch ``weather-snapshot.json`` and
the ``extractor_version`` is stamped ``<base>+<city>-<date>`` (scheme §1.2 / §11).

Usage:
    python -m runner.rerender_offline <batch_dir> [--allow-upstream] [--no-retain-png]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator

from .fidelity import expected_from_snapshot, extractor_version_for
from .render.env_digest import env_digest
from .render.local_server import LocalCardServer
from .slot_pipeline import produce_slot_artifacts

REPO = Path(__file__).resolve().parent.parent
TRIAL = REPO / "trial-20260715"
API_CACHE = TRIAL / "api-cache"
SCHEMA_DIR = REPO / "data" / "SCHEMA"

VARIANTS = ["min", "q"]  # directory tokens (DECISIONS-M0 §4)
_RENDER_FLAG_ENUM = {"content-not-ready", "unstable-after-freeze", "non-deterministic-render"}


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMA_DIR / name).read_text()))


def _log(*a: Any) -> None:
    print(*a, file=sys.stderr, flush=True)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Offline deterministic batch re-render")
    ap.add_argument("batch_dir")
    ap.add_argument("--allow-upstream", action="store_true",
                    help="permit cache-miss upstream fetch (default: offline, cache-only)")
    ap.add_argument("--no-retain-png", action="store_true",
                    help="do not keep shot.png (default: retain, gitignored under batches-dev)")
    args = ap.parse_args(argv)

    batch_dir = Path(args.batch_dir).resolve()
    manifest = json.loads((batch_dir / "manifest.json").read_text())
    snapshot = json.loads((batch_dir / "weather-snapshot.json").read_text())
    config_ids: list[str] = list(manifest["config_ids"])  # persisted order — never re-sort
    loc = snapshot["locations"][0]

    expected = expected_from_snapshot(snapshot)
    extractor_version = extractor_version_for(snapshot)
    render_params = {"lat": loc["lat"], "lon": loc["lon"],
                     "name": loc["city"], "date": loc["date"]}
    render_snapshot = {"fetched_at": snapshot["fetched_at"]}
    _log(f"[rerender] batch={batch_dir.name} extractor_version={extractor_version}")
    _log(f"[rerender] expected: name={expected.name} day={expected.day} "
         f"code={expected.weather_code} max={expected.temp_max} min={expected.temp_min} "
         f"hourly={len(expected.hourly)}")

    slot_meta_v = _validator("slot-meta.schema.json")
    trace_v = _validator("fidelity-trace.schema.json")

    from playwright.sync_api import sync_playwright

    overlay = batch_dir / "_runtime" / "cache-overlay"
    cache_dirs = [d for d in (overlay, API_CACHE) if d.exists()]

    produced: list[dict] = []
    validations: list[dict] = []
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-gpu", "--font-render-hinting=none",
              "--force-color-profile=srgb", "--hide-scrollbars"],
    )
    digest = env_digest(browser.version, dev=True)
    server = LocalCardServer(
        cache_dirs=cache_dirs,
        overlay_dir=(overlay if args.allow_upstream else None),
        allow_upstream=args.allow_upstream,
    )
    server.__enter__()
    try:
        configs_root = batch_dir / "configs"
        for cid in config_ids:
            for vdir in VARIANTS:
                slots_root = configs_root / cid / vdir / "slots"
                if not slots_root.is_dir():
                    continue
                for slot in sorted(slots_root.iterdir(), key=lambda p: int(p.name)):
                    meta_path = slot / "meta.json"
                    card_path = slot / "card.html"
                    if not meta_path.is_file():
                        continue
                    meta = json.loads(meta_path.read_text())
                    k = int(slot.name)
                    if meta.get("state") != "valid" or not card_path.is_file():
                        # Non-valid slots keep null l1/fidelity; just re-validate.
                        v = _revalidate(slot_meta_v, meta, cid, vdir, k)
                        validations.append(v)
                        continue

                    slug = f"{cid}--{vdir}--{k}"
                    _log(f"[rerender] {slug} ...")
                    art = produce_slot_artifacts(
                        browser=browser, base_url=server.base_url, server=server,
                        slot_dir=slot, card_html_path=card_path,
                        render_params=render_params, render_snapshot=render_snapshot,
                        expected=expected, extractor_version=extractor_version,
                        slot_index=k, slug=slug, retain_png=not args.no_retain_png,
                    )
                    # fold new artifacts into the existing meta (preserve state /
                    # telemetry / slot_index; merge render flags; keep fence mirror).
                    prior_flags = [f for f in meta.get("flags", []) if f not in _RENDER_FLAG_ENUM]
                    flags = list(dict.fromkeys(prior_flags + art.render_flags))
                    meta["flags"] = flags
                    meta["l1"] = art.l1
                    meta["fidelity"] = art.fidelity
                    if "fence" in flags:
                        meta["fence"] = True
                    elif "fence" in meta:
                        del meta["fence"]
                    meta_path.write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                    )

                    # validate the trace + the updated meta.
                    trace_doc = json.loads((slot / "fidelity-trace.json").read_text())
                    validations.append(_validate(trace_v, trace_doc,
                                                 f"configs/{cid}/{vdir}/slots/{k}/fidelity-trace.json",
                                                 "fidelity-trace.schema.json"))
                    validations.append(_revalidate(slot_meta_v, meta, cid, vdir, k))
                    produced.append({
                        "config_id": cid, "variant": vdir, "slot_index": k,
                        **art.render_report,
                        "fidelity_states": {
                            f: art.fidelity[f]["state"] for f in
                            ("name", "date", "condition", "max", "min", "hourly")
                        } if art.fidelity else None,
                    })
    finally:
        server.__exit__()
        browser.close()
        playwright.stop()

    n_fail = sum(1 for v in validations if not v["valid"])
    summary = {
        "batch_id": batch_dir.name,
        "extractor_version": extractor_version,
        "env_digest": digest,
        "n_slots_rerendered": len(produced),
        "n_validated": len(validations),
        "n_validation_fail": n_fail,
        "produced": produced,
        "validation": validations,
    }
    out_path = batch_dir / "_runtime" / "rerender-summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    _log(f"[rerender] {len(produced)} slots re-rendered; "
         f"{len(validations)} validations, {n_fail} failed. → {out_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _validate(v: Draft202012Validator, instance: Any, rel: str, schema: str) -> dict:
    errs = sorted(v.iter_errors(instance), key=lambda e: list(e.path))
    return {
        "file": rel, "schema": schema, "valid": not errs,
        "errors": [{"path": "/".join(str(p) for p in e.path) or "<root>", "message": e.message}
                   for e in errs],
    }


def _revalidate(v: Draft202012Validator, meta: dict, cid: str, vdir: str, k: int) -> dict:
    return _validate(v, meta, f"configs/{cid}/{vdir}/slots/{k}/meta.json", "slot-meta.schema.json")


if __name__ == "__main__":
    raise SystemExit(main())
