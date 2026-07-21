"""Playwright render harness — frozen-channel deterministic capture (scheme §2).

Implements the §2.1 ready state machine and §2.2 frozen main screenshot:

  init script (BEFORE load): Date frozen to snapshot ``fetched_at``;
  ``Math.random = mulberry32(0xC0FFEE)``; a MutationObserver feeding the
  wall-clock quiet window.

  ready: (1) target /api/om response finished → (2) document.fonts.ready →
  (3) 500 ms wall-clock DOM-mutation quiet → (4) READY_NORMAL predicate
  (≥1 visible temperature candidate — DEV PREDICATE, see note) → else
  2×rAF+250 ms re-check → ``content-not-ready``.

  freeze (§2.2): CDP Emulation.setVirtualTimePolicy advance to t_v=5000 ms;
  pause CSS/Web Animations + currentTime=0. Stability: two instants 250 ms
  VIRTUAL apart, pixel-hash equal, retry 3, else ``unstable-after-freeze``.
  Hard cap 15 s wall.

DEV-PREDICATE NOTE: READY_NORMAL here is the scheme's explicitly-weak "simple
version" — a visible text node matching a temperature regex. The versioned
extractor predicate arrives with R5; this module tags every result with
``ready_predicate="dev-temperature-regex"`` so downstream can tell.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from playwright.sync_api import Browser

from ._pixels import pixel_hash, to_thumb_webp, to_webp
from .local_server import LocalCardServer
from .mulberry32 import MULBERRY32_JS, SEED

VIEWPORT = {"width": 1280, "height": 800}
HARD_CAP_S = 15.0
QUIET_MS = 500
VIRTUAL_FREEZE_MS = 5000
STABILITY_INTERVAL_MS = 250
STABILITY_RETRIES = 3
READY_PREDICATE_ID = "dev-temperature-regex"
# scheme §2.3 frame-change: second render, animations NOT paused, virtual clock
# advanced to t_v+{700,1300,2100,3400} ms; adjacent-frame pixel change → L1.
FRAME_CHANGE_OFFSETS_MS = (700, 1300, 2100, 3400)

# DOM dump (see data/batches-dev/devset-42/DOM-DUMP-FORMAT.md). Captured at the
# frozen instant (same page, after freeze + stability) so it corresponds exactly
# to the frozen main screenshot. Versioned; raw (un-normalized) text is dumped —
# the fidelity extractor (scheme §3) does its own normalization downstream.
DOM_DUMP_VERSION = "dom-dump-v1"

# DEV predicate: number + degree/unit indicator in a visible text node.
READY_NORMAL_JS = r"""
() => {
  const re = /-?\d+(?:[.,]\d+)?\s*(?:°|℃|℉)/;
  if (!document.body) return false;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = n.nodeValue;
    if (!t || !re.test(t)) continue;
    const el = n.parentElement;
    if (!el) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (parseFloat(st.opacity) === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (el.offsetParent === null && st.position !== 'fixed') continue;
    return true;
  }
  return false;
}
"""

FREEZE_JS = r"""
() => {
  // v12.3 freeze phase: finite animations freeze at the END of their active
  // interval (endTime - 1ms, fill-independent so the final styles stay applied
  // == what a user sees at t_v=5s); infinite/looping animations freeze at
  // phase 0 (fixed, deterministic). The old uniform currentTime=0 rewound
  // entrance animations to their not-yet-visible state (blank screenshots).
  let count = 0;
  const anims = (document.getAnimations ? document.getAnimations() : []);
  for (const a of anims) {
    try {
      a.pause();
      let end = Infinity;
      if (a.effect && a.effect.getComputedTiming) {
        const t = a.effect.getComputedTiming();
        if (t && Number.isFinite(t.endTime)) end = t.endTime;
      }
      a.currentTime = Number.isFinite(end) ? Math.max(0, end - 1) : 0;
      count++;
    } catch (e) {}
  }
  const s = document.createElement('style');
  s.textContent =
    '*,*::before,*::after{animation-play-state:paused !important;' +
    'transition:none !important;' +
    'caret-color:transparent !important;}';
  (document.head || document.documentElement).appendChild(s);
  return count;
}
"""

RAF_SETTLE_JS = (
    "() => new Promise(r => requestAnimationFrame(() => "
    "requestAnimationFrame(() => setTimeout(r, 250))))"
)

# Full-document preorder DOM dump. Single monotonic `preorder` counter over
# element + text nodes (siblings by child index; scheme §3 "DOM 先序"). Provides
# EVERYTHING both consumers need — the fidelity extractor (raw visible text +
# per-node preorder + parent computed style) AND the L2 DOM channels (element
# boxes for d-geom, tag tree via parent pointers for d-pqgram/d-tagpath, visible
# text for d-text). Visibility booleans (`element_visible` / `text_visible`) are
# CONVENIENCE determinations at rule id `dom-dump-v1`; every raw input they were
# derived from is also dumped, so a downstream extractor may recompute its own.
DOM_DUMP_JS = r"""
(vw, vh) => {
  const nodes = [];
  let visTextIdx = 0;
  const vis = (el) => {
    const st = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const opacity = parseFloat(st.opacity);
    const displayNone = st.display === 'none';
    const hidden = st.visibility === 'hidden' || st.visibility === 'collapse';
    const box = {x: r.x, y: r.y, width: r.width, height: r.height};
    const intersects = r.right > 0 && r.left < vw && r.bottom > 0 && r.top < vh;
    // d-geom candidate gate (scheme §4 d-geom): rect∩viewport ∧ display/visibility
    // not hidden ∧ opacity>0.05. width/height>0 also catches ancestor display:none
    // (getBoundingClientRect collapses to 0). NOTE: opacity does NOT inherit in
    // computed style, so an ancestor opacity:0 is NOT reflected here — downstream
    // may walk parent pointers to apply a cumulative rule (see format doc).
    const element_visible = !displayNone && !hidden && (isNaN(opacity) || opacity > 0.05)
      && intersects && r.width > 0 && r.height > 0;
    // fidelity visible-text gate (scheme §3, mirrors READY_NORMAL): parent not
    // display:none / not visibility hidden / opacity>0 / non-zero box / laid out.
    const text_parent_visible = !displayNone && !hidden
      && (isNaN(opacity) || opacity > 0)
      && r.width > 0 && r.height > 0
      && (el.offsetParent !== null || st.position === 'fixed');
    return {st, box, intersects, opacity, element_visible, text_parent_visible};
  };
  const walk = (node, parentPre, childIndex, depth) => {
    const pre = nodes.length;
    if (node.nodeType === 1) { // ELEMENT_NODE
      const el = node;
      const v = vis(el);
      const st = v.st;
      const attrs = {};
      if (el.id) attrs.id = el.id;
      if (el.getAttribute && el.getAttribute('class')) attrs.class = el.getAttribute('class');
      const ah = el.getAttribute ? el.getAttribute('aria-hidden') : null;
      if (ah !== null) attrs['aria-hidden'] = ah;
      nodes.push({
        preorder: pre,
        type: 'element',
        tag: el.tagName.toLowerCase(),
        parent: parentPre,
        child_index: childIndex,
        depth: depth,
        attrs: attrs,
        box: v.box,
        intersects_viewport: v.intersects,
        style: {
          display: st.display,
          visibility: st.visibility,
          opacity: isNaN(v.opacity) ? null : v.opacity,
          font_size: parseFloat(st.fontSize),
        },
        element_visible: v.element_visible,
      });
      let ci = 0;
      for (const child of el.childNodes) {
        if (child.nodeType === 1 || child.nodeType === 3) {
          walk(child, pre, ci, depth + 1);
          ci++;
        }
      }
    } else if (node.nodeType === 3) { // TEXT_NODE
      const raw = node.nodeValue || '';
      // parent-based visibility: reuse the parent element's determination.
      const parentEl = node.parentElement;
      let text_visible = false;
      if (parentEl) {
        const v = vis(parentEl);
        text_visible = v.text_parent_visible;
      }
      const entry = {
        preorder: pre,
        type: 'text',
        parent: parentPre,
        child_index: childIndex,
        depth: depth,
        text_raw: raw,
        codepoint_length: Array.from(raw).length,
        text_visible: text_visible,
        visible_text_index: null,
      };
      if (text_visible) { entry.visible_text_index = visTextIdx; visTextIdx++; }
      nodes.push(entry);
    }
  };
  const root = document.documentElement;
  walk(root, null, 0, 0);
  let elements = 0, texts = 0, visTexts = 0;
  for (const n of nodes) {
    if (n.type === 'element') elements++;
    else { texts++; if (n.text_visible) visTexts++; }
  }
  return {
    viewport: {width: vw, height: vh},
    counts: {nodes: nodes.length, elements: elements, text_nodes: texts,
             visible_text_nodes: visTexts},
    nodes: nodes,
  };
}
"""


def dump_dom(page, *, url: Optional[str] = None) -> dict:
    """Capture a full-document preorder DOM dump from ``page`` at its current
    (frozen) instant. See DOM-DUMP-FORMAT.md. Pure read — no page mutation."""
    raw = page.evaluate(DOM_DUMP_JS, [VIEWPORT["width"], VIEWPORT["height"]])
    return {
        "dump_format_version": DOM_DUMP_VERSION,
        "visibility_rule": DOM_DUMP_VERSION,
        "captured_at_virtual_ms": VIRTUAL_FREEZE_MS,
        "url": url,
        "viewport": raw["viewport"],
        "counts": raw["counts"],
        "nodes": raw["nodes"],
    }


def _init_script(frozen_epoch_ms: int) -> str:
    return f"""
(() => {{
  const FROZEN = {frozen_epoch_ms};
  const _Date = Date;
  function FakeDate(...args) {{
    if (args.length === 0) return new _Date(FROZEN);
    return new _Date(...args);
  }}
  FakeDate.now = () => FROZEN;
  FakeDate.parse = _Date.parse;
  FakeDate.UTC = _Date.UTC;
  FakeDate.prototype = _Date.prototype;
  Object.setPrototypeOf(FakeDate, _Date);
  try {{ window.Date = FakeDate; }} catch (e) {{}}

  {MULBERRY32_JS}
  const _rng = mulberry32({SEED});
  Math.random = () => _rng();

  window.__mutCount = 0;
  try {{
    const mo = new MutationObserver(() => {{ window.__mutCount++; }});
    mo.observe(document, {{subtree: true, childList: true,
                           attributes: true, characterData: true}});
  }} catch (e) {{}}
}})();
"""


@dataclass
class RenderResult:
    slug: str
    variant: str
    main_pixel_hash: Optional[str] = None
    main_png: Optional[bytes] = field(default=None, repr=False)
    api_finished: bool = False
    fonts_ready: bool = False
    mutation_quiet: bool = False
    ready_normal: bool = False
    ready_predicate: str = READY_PREDICATE_ID
    animations_frozen: int = 0
    stable: bool = False
    stability_attempts: int = 0
    stability_hashes: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    wall_ms: int = 0
    files: dict[str, str] = field(default_factory=dict)
    dom: Optional[dict] = field(default=None, repr=False)


def _epoch_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _advance_virtual_time(cdp, page, budget_ms: int) -> None:
    """Advance CDP virtual time by ``budget_ms`` and block until it expires."""
    flag = {"done": False}

    def _on_expired(_evt) -> None:
        flag["done"] = True

    cdp.on("Emulation.virtualTimeBudgetExpired", _on_expired)
    try:
        cdp.send(
            "Emulation.setVirtualTimePolicy",
            {"policy": "advance", "budget": budget_ms},
        )
        deadline = time.monotonic() + (budget_ms / 1000.0) + 5.0
        while not flag["done"] and time.monotonic() < deadline:
            page.wait_for_timeout(20)
    finally:
        cdp.remove_listener("Emulation.virtualTimeBudgetExpired", _on_expired)


def _screenshot(page) -> bytes:
    return page.screenshot(clip={"x": 0, "y": 0, **VIEWPORT})


def render_once(
    browser: Browser,
    base_url: str,
    server: LocalCardServer,
    card_path: Path,
    snapshot: dict,
    params: dict,
    slug: str,
    variant: str = "min",
    dump_dom_step: bool = False,
) -> RenderResult:
    """Render one card in a FRESH browser context; return frozen main capture.

    ``dump_dom_step`` (default False → existing behavior unchanged): when set,
    a full-document preorder DOM dump is captured on the SAME page at the frozen
    instant (right after the frozen main screenshot) and stored on
    ``RenderResult.dom``. See DOM-DUMP-FORMAT.md.
    """
    server.set_card(card_path)
    frozen_ms = _epoch_ms(snapshot["fetched_at"])
    res = RenderResult(slug=slug, variant=variant)
    t0 = time.monotonic()
    deadline = t0 + HARD_CAP_S

    context = browser.new_context(
        viewport=dict(VIEWPORT),
        device_scale_factor=1,
        locale="en-US",
        timezone_id="UTC",
        service_workers="block",
    )
    try:
        page = context.new_page()
        api_finished: set[str] = set()
        page.on(
            "requestfinished",
            lambda req: api_finished.add(req.url) if "/api/om/" in req.url else None,
        )
        page.add_init_script(_init_script(frozen_ms))
        cdp = context.new_cdp_session(page)

        url = f"{base_url}/card.html?{urlencode(params)}"
        page.goto(url, wait_until="commit", timeout=int(HARD_CAP_S * 1000))

        # (1) target API response finished
        while time.monotonic() < deadline and not api_finished:
            page.wait_for_timeout(50)
        res.api_finished = bool(api_finished)

        # (2) document.fonts.ready
        try:
            res.fonts_ready = bool(
                page.evaluate("() => document.fonts.ready.then(() => true)")
            )
        except Exception:
            res.fonts_ready = False

        # (3) 500 ms wall-clock DOM-mutation quiet
        last = page.evaluate("() => window.__mutCount || 0")
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            page.wait_for_timeout(50)
            cur = page.evaluate("() => window.__mutCount || 0")
            if cur != last:
                last = cur
                stable_since = time.monotonic()
            elif (time.monotonic() - stable_since) * 1000 >= QUIET_MS:
                res.mutation_quiet = True
                break

        # (4) READY_NORMAL predicate (dev)
        res.ready_normal = bool(page.evaluate(READY_NORMAL_JS))
        if not res.ready_normal:
            try:
                page.evaluate(RAF_SETTLE_JS)
            except Exception:
                pass
            res.ready_normal = bool(page.evaluate(READY_NORMAL_JS))
            if not res.ready_normal:
                res.flags.append("content-not-ready")

        # freeze (§2.2)
        _advance_virtual_time(cdp, page, VIRTUAL_FREEZE_MS)
        res.animations_frozen = int(page.evaluate(FREEZE_JS))

        # stability confirm
        hash_a = pixel_hash(_screenshot(page))
        res.stability_hashes.append(hash_a)
        main_png = _screenshot(page)
        for attempt in range(STABILITY_RETRIES):
            res.stability_attempts = attempt + 1
            _advance_virtual_time(cdp, page, STABILITY_INTERVAL_MS)
            png_b = _screenshot(page)
            hash_b = pixel_hash(png_b)
            res.stability_hashes.append(hash_b)
            if hash_b == hash_a:
                res.stable = True
                break
            hash_a = hash_b
            main_png = png_b  # last comparison's second instant
        if not res.stable:
            res.flags.append("unstable-after-freeze")

        res.main_png = main_png
        res.main_pixel_hash = pixel_hash(main_png)

        # DOM dump at the frozen instant (same protocol, same page). Additive:
        # skipped entirely unless requested, so existing callers are untouched.
        if dump_dom_step:
            try:
                res.dom = dump_dom(page, url=url)
            except Exception as exc:  # noqa: BLE001 - surfaced as a flag, never fatal
                res.flags.append("dom-dump-failed")
                res.dom = {"error": str(exc)}
    finally:
        context.close()
        res.wall_ms = int((time.monotonic() - t0) * 1000)
    return res


@dataclass
class FrameChangeResult:
    slug: str
    frames: list[bytes] = field(default_factory=list, repr=False)
    frame_hashes: list[str] = field(default_factory=list)
    offsets_ms: tuple[int, ...] = FRAME_CHANGE_OFFSETS_MS
    api_finished: bool = False
    ready_normal: bool = False
    flags: list[str] = field(default_factory=list)
    wall_ms: int = 0


def capture_frame_change(
    browser: Browser,
    base_url: str,
    server: LocalCardServer,
    card_path: Path,
    snapshot: dict,
    params: dict,
    slug: str,
) -> FrameChangeResult:
    """§2.3 frame-change render: a SEPARATE render where animations are NOT paused
    and the virtual clock is advanced to t_v+{700,1300,2100,3400} ms, sampling one
    frame at each instant. Returns the 4 frame PNGs (adjacent per-pixel change → L1).

    Mirrors ``render_once`` up to the ready state machine, then — instead of the
    §2.2 freeze/pause — advances virtual time to t_v and samples the four offset
    instants live. Deterministic: Date frozen + mulberry32 + virtual clock, so
    animations driven by the virtual clock replay identically."""
    server.set_card(card_path)
    frozen_ms = _epoch_ms(snapshot["fetched_at"])
    res = FrameChangeResult(slug=slug)
    t0 = time.monotonic()
    deadline = t0 + HARD_CAP_S

    context = browser.new_context(
        viewport=dict(VIEWPORT),
        device_scale_factor=1,
        locale="en-US",
        timezone_id="UTC",
        service_workers="block",
    )
    try:
        page = context.new_page()
        api_finished: set[str] = set()
        page.on(
            "requestfinished",
            lambda req: api_finished.add(req.url) if "/api/om/" in req.url else None,
        )
        page.add_init_script(_init_script(frozen_ms))
        cdp = context.new_cdp_session(page)

        url = f"{base_url}/card.html?{urlencode(params)}"
        page.goto(url, wait_until="commit", timeout=int(HARD_CAP_S * 1000))

        while time.monotonic() < deadline and not api_finished:
            page.wait_for_timeout(50)
        res.api_finished = bool(api_finished)
        try:
            page.evaluate("() => document.fonts.ready.then(() => true)")
        except Exception:
            pass

        last = page.evaluate("() => window.__mutCount || 0")
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            page.wait_for_timeout(50)
            cur = page.evaluate("() => window.__mutCount || 0")
            if cur != last:
                last = cur
                stable_since = time.monotonic()
            elif (time.monotonic() - stable_since) * 1000 >= QUIET_MS:
                break

        res.ready_normal = bool(page.evaluate(READY_NORMAL_JS))
        if not res.ready_normal:
            res.flags.append("content-not-ready")

        # Advance to t_v, then sample each offset WITHOUT pausing animations.
        _advance_virtual_time(cdp, page, VIRTUAL_FREEZE_MS)
        prev = 0
        for off in FRAME_CHANGE_OFFSETS_MS:
            _advance_virtual_time(cdp, page, off - prev)
            prev = off
            png = _screenshot(page)
            res.frames.append(png)
            res.frame_hashes.append(pixel_hash(png))
    finally:
        context.close()
        res.wall_ms = int((time.monotonic() - t0) * 1000)
    return res


def write_outputs(result: RenderResult, out_dir: Path) -> dict[str, str]:
    """Write main PNG + webp(q85) + thumb webp (~480px). Returns file map."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assert result.main_png is not None, "no main_png to write"
    png_path = out_dir / f"{result.slug}.png"
    webp_path = out_dir / f"{result.slug}.webp"
    thumb_path = out_dir / f"{result.slug}.thumb.webp"
    png_path.write_bytes(result.main_png)
    webp_path.write_bytes(to_webp(result.main_png))
    thumb_path.write_bytes(to_thumb_webp(result.main_png))
    result.files = {
        "png": str(png_path),
        "webp": str(webp_path),
        "thumb": str(thumb_path),
    }
    return result.files


def render_card(
    browser: Browser,
    base_url: str,
    server: LocalCardServer,
    card_path: Path,
    snapshot: dict,
    params: dict,
    slug: str,
    out_dir: Path,
    variant: str = "min",
) -> RenderResult:
    """render_once + persist outputs."""
    result = render_once(
        browser, base_url, server, card_path, snapshot, params, slug, variant
    )
    write_outputs(result, out_dir)
    return result
