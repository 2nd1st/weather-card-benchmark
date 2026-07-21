"""Shared per-valid-slot artifact production (task W).

ONE code path produces a FULLY conformant slot, used by BOTH:
  * ``run_batch.py``       — the live (model-calling) batch runner, and
  * ``rerender_offline.py`` — the deterministic offline re-render driver.

Given a rendered ``card.html`` it emits, into the slot directory:
  * ``dom.json``            — frozen-instant DOM dump (dom-dump-v1),
  * ``shot.png``            — frozen main screenshot (retained only when
                              ``retain_png``; gitignored under data/batches-dev/),
  * ``shot.webp`` / ``thumb.webp`` — in-repo derivatives,
  * ``fidelity-trace.json`` — the load-bearing "全部落盘" fidelity audit record,
and RETURNS the pieces the caller folds into ``meta.json``:
  * ``l1``            — full L1 object (bytes + structure + visual + palette_top8),
  * ``fidelity``      — terminal fidelity verdict object (or None if no DOM),
  * ``render_flags``  — slot-flag-enum-valid render flags (content-not-ready /
                        unstable-after-freeze / non-deterministic-render),
  * ``render_report`` — diagnostics for the run summary.

Determinism (scheme §2): Date frozen, mulberry32 RNG, CDP virtual clock — the
re-render of a stored card is byte-stable (verified: 3×/10× re-render all-equal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Browser

from .fidelity import ExpectedSnapshot, extract
from .render import _pixels
from .render.harness import capture_frame_change, render_once
from .render.l1_visual import compute_l1
from .render.local_server import LocalCardServer

# slot-meta flags enum (closed) — render can only legitimately raise these three.
_RENDER_FLAG_ENUM = {"content-not-ready", "unstable-after-freeze", "non-deterministic-render"}


@dataclass
class SlotArtifacts:
    l1: Optional[dict] = None
    fidelity: Optional[dict] = None
    render_flags: list[str] = field(default_factory=list)
    render_report: dict = field(default_factory=dict)
    main_pixel_hash: Optional[str] = None
    double_render_equal: bool = False


def _dedup(seq) -> list[str]:
    return list(dict.fromkeys(seq))


def produce_slot_artifacts(
    *,
    browser: Browser,
    base_url: str,
    server: LocalCardServer,
    slot_dir: Path,
    card_html_path: Path,
    render_params: dict,
    render_snapshot: dict,
    expected: ExpectedSnapshot,
    extractor_version: str,
    slot_index: int,
    slug: str,
    retain_png: bool = True,
) -> SlotArtifacts:
    """Render one stored card and emit its full conformant artifact set."""
    slot_dir.mkdir(parents=True, exist_ok=True)

    # (A) frozen main render + DOM dump.
    ra = render_once(
        browser, base_url, server, card_html_path, render_snapshot, render_params,
        slug, dump_dom_step=True,
    )
    # (B) determinism second render (scheme §2.4).
    rb = render_once(
        browser, base_url, server, card_html_path, render_snapshot, render_params, slug,
    )
    equal = ra.main_pixel_hash is not None and ra.main_pixel_hash == rb.main_pixel_hash
    dr_flags = [] if equal else ["non-deterministic-render"]
    # (C) frame-change render (scheme §2.3).
    fc = capture_frame_change(
        browser, base_url, server, card_html_path, render_snapshot, render_params, slug,
    )

    dom = ra.dom if isinstance(ra.dom, dict) and "error" not in ra.dom else None

    # ---- write pixel + dom artifacts -------------------------------------- #
    if dom is not None:
        # Determinism: the served card runs on an EPHEMERAL localhost port, so the
        # raw dump ``url`` carries a volatile ``127.0.0.1:<port>`` that changes every
        # process. Normalize the origin to a stable token so the persisted dom.json
        # is byte-deterministic across runs (the port is non-semantic — no consumer
        # reads the url; nodes/boxes/text drive every §3/§4 channel).
        _url = dom.get("url")
        if isinstance(_url, str) and _url:
            _p = urlsplit(_url)
            dom["url"] = urlunsplit(("http", "card.local", _p.path, _p.query, ""))
        _write_json(slot_dir / "dom.json", dom)
    if ra.main_png is not None:
        if retain_png:
            (slot_dir / "shot.png").write_bytes(ra.main_png)
        (slot_dir / "shot.webp").write_bytes(_pixels.to_webp(ra.main_png))
        (slot_dir / "thumb.webp").write_bytes(_pixels.to_thumb_webp(ra.main_png))

    # ---- L1 (bytes + structure + visual + palette_top8) ------------------- #
    html = card_html_path.read_text(encoding="utf-8")
    l1: Optional[dict] = None
    if ra.main_png is not None:
        l1 = compute_l1(html, ra.main_png, fc.frames)

    # ---- fidelity verdict + trace ----------------------------------------- #
    fidelity: Optional[dict] = None
    if dom is not None:
        out = extract(
            dom, expected, slot_index=slot_index, extractor_version=extractor_version,
        )
        fidelity = out["meta"]
        _write_json(slot_dir / "fidelity-trace.json", out["trace"])

    render_flags = [f for f in _dedup(ra.flags + dr_flags + fc.flags) if f in _RENDER_FLAG_ENUM]

    return SlotArtifacts(
        l1=l1,
        fidelity=fidelity,
        render_flags=render_flags,
        main_pixel_hash=ra.main_pixel_hash,
        double_render_equal=equal,
        render_report={
            "slot_index": slot_index,
            "main_pixel_hash": ra.main_pixel_hash,
            "double_render_equal": equal,
            "stable": ra.stable,
            "stability_attempts": ra.stability_attempts,
            "frame_hashes": fc.frame_hashes,
            "wall_ms": ra.wall_ms,
            "flags": render_flags,
            "dom_dumped": dom is not None,
            "shot_png_retained": retain_png and ra.main_png is not None,
        },
    )


def _write_json(path: Path, obj: Any) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
