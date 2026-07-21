"""GOLDEN — render anchor (scheme §2.2 / §11 lock item 3, env-tagged).

Renders one fixed card through the frozen-channel harness and checks its main
screenshot pixel hash against a frozen golden.

ENV-DEPENDENCE (documented, see GOLDEN-README.md): the pixel hash is only valid
under the ``env_digest`` it was captured with (chromium build + platform +
playwright + pinned flags). When the LIVE ``env_digest`` differs from the golden
the STRICT hash equality is SKIPPED — but the env-independent invariants
(ready-state machine reached, intra-render stability, double-render determinism)
are still asserted, so the render subsystem is exercised on every platform.

Fixtures (self-contained under ./fixtures/render/):
  card.html       the fixed, animation-free, deterministic card
  expected.json   {main_pixel_hash, env:{env_digest, chromium_version, …}, …}
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner.render import LocalCardServer, double_render, env_digest, render_once

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "render"
REPO = HERE.parents[2]
TRIAL_CACHE = REPO / "trial-20260715" / "api-cache"

CARD = FIX / "card.html"
GOLDEN = json.loads((FIX / "expected.json").read_text("utf-8"))
SNAPSHOT = GOLDEN["snapshot"]
PARAMS = GOLDEN["params"]
LAUNCH_ARGS = GOLDEN["launch_args"]


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(args=LAUNCH_ARGS)
        yield b
        b.close()


@pytest.fixture(scope="module")
def server():
    srv = LocalCardServer(cache_dirs=[TRIAL_CACHE], overlay_dir=None, allow_upstream=False)
    base = srv.start()
    srv.base_url = base
    yield srv
    srv.stop()


def test_render_golden_pixel_hash(browser, server):
    r = render_once(browser, server.base_url, server, CARD, SNAPSHOT, PARAMS, slug="golden-anchor")

    # env-independent invariants — must hold on any modern chromium.
    assert r.api_finished, f"target API request did not finish; flags={r.flags}"
    assert r.ready_normal, f"READY_NORMAL not met; flags={r.flags}"
    assert r.stable, f"unstable after freeze; flags={r.flags}"
    assert "content-not-ready" not in r.flags
    assert "unstable-after-freeze" not in r.flags
    assert r.main_pixel_hash is not None

    live_env = env_digest(browser.version, dev=True)
    if live_env != GOLDEN["env"]["env_digest"]:
        pytest.skip(
            "render env_digest differs from golden — strict pixel-hash is ENV-TAGGED "
            f"(golden chromium={GOLDEN['env']['chromium_version']} "
            f"env={GOLDEN['env']['env_digest'][:12]}…; live chromium={browser.version} "
            f"env={live_env[:12]}…). See GOLDEN-README.md."
        )
    # same env → the frozen pixel hash MUST reproduce exactly.
    assert r.main_pixel_hash == GOLDEN["main_pixel_hash"]


def test_render_golden_is_deterministic(browser, server):
    """Double-render determinism (§2.4) — env-independent."""
    dr = double_render(browser, server.base_url, server, CARD, SNAPSHOT, PARAMS, slug="golden-anchor")
    assert dr.equal, f"golden card must double-render identically; flags={dr.flags}"
    assert "non-deterministic-render" not in dr.flags
