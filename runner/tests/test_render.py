"""R4 render subsystem tests (scheme §2).

Fast unit tests (no browser) + browser integration tests that render a real
trial card through the local cache-proxy server.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from runner.render import (
    MULBERRY32_JS,
    SEED,
    LocalCardServer,
    build_descriptor,
    cache_key,
    double_render,
    env_digest,
    make_mulberry32,
    render_card,
)

REPO = Path(__file__).resolve().parents[2]
TRIAL = REPO / "trial-20260715"
TRIAL_CACHE = TRIAL / "api-cache"
CARDS = TRIAL / "outputs-v2r2"

SNAPSHOT = {"fetched_at": "2026-07-16T12:00:00Z"}
BERLIN = {"lat": "52.52", "lon": "13.405", "name": "Berlin", "date": "2026-07-16"}

# grok-4.5 forecast query for Berlin @ 2026-07-16 is known-present in the frozen
# trial cache (reconstructed offline).
_GROK_BERLIN_URL = (
    "https://api.open-meteo.com/v1/forecast?"
    "latitude=52.52&longitude=13.405&start_date=2026-07-16&end_date=2026-07-16"
    "&timezone=auto&daily=temperature_2m_max%2Ctemperature_2m_min%2Cweathercode"
    "%2Cwindspeed_10m_max&hourly=temperature_2m%2Cweathercode&current_weather=true"
)


# ---------------------------------------------------------------- unit tests
def test_mulberry32_python_reference_is_deterministic():
    a = make_mulberry32(SEED)
    b = make_mulberry32(SEED)
    seq_a = [a() for _ in range(20)]
    seq_b = [b() for _ in range(20)]
    assert seq_a == seq_b
    assert all(0.0 <= x < 1.0 for x in seq_a)
    assert len(set(seq_a)) > 1  # not a constant stream


def test_env_digest_format_and_stability():
    d1 = env_digest("149.0.7827.55", dev=True)
    d2 = env_digest("149.0.7827.55", dev=True)
    assert d1 == d2
    assert re.fullmatch(r"[0-9a-f]{64}", d1)
    # a different chromium version changes the digest
    assert env_digest("150.0.0.0", dev=True) != d1
    desc = build_descriptor("149.0.7827.55", dev=True)
    assert desc["dev"] is True
    assert desc["viewport"] == {"width": 1280, "height": 800}


def test_cache_key_matches_trial_serve():
    # trial serve.py: sha256(url).hexdigest()[:32]
    import hashlib

    assert cache_key(_GROK_BERLIN_URL) == hashlib.sha256(
        _GROK_BERLIN_URL.encode()
    ).hexdigest()[:32]
    assert (TRIAL_CACHE / f"{cache_key(_GROK_BERLIN_URL)}.json").exists()


def test_local_server_serves_from_cache_no_upstream():
    server = LocalCardServer(
        cache_dirs=[TRIAL_CACHE], overlay_dir=None, allow_upstream=False
    )
    # /api/om/forecast with grok-4.5's Berlin query, from cache only
    query = _GROK_BERLIN_URL.split("?", 1)[1]
    body, status = server.serve_api("/api/om/forecast", query)
    assert status == 200
    data = json.loads(body)
    assert data["daily"]["temperature_2m_max"]  # non-empty temps
    # a query that is NOT cached must 502 (no upstream allowed)
    body2, status2 = server.serve_api("/api/om/forecast", "latitude=0&longitude=0")
    assert status2 == 502


# --------------------------------------------------- browser integration
@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(
            args=[
                "--font-render-hinting=none",
                "--disable-gpu",
                "--force-color-profile=srgb",
                "--hide-scrollbars",
            ]
        )
        yield b
        b.close()


@pytest.fixture(scope="module")
def server():
    srv = LocalCardServer(
        cache_dirs=[TRIAL_CACHE],
        overlay_dir=REPO / "data" / "batches-dev" / "smoke-r4" / "api-cache",
        allow_upstream=True,
    )
    base = srv.start()
    srv.base_url = base
    yield srv
    srv.stop()


def test_mulberry32_js_matches_python(browser, server):
    page = browser.new_page()
    js = (
        "(() => { "
        + MULBERRY32_JS
        + f" const r = mulberry32({SEED}); const out = []; "
        "for (let i = 0; i < 12; i++) out.push(r()); return out; })"
    )
    seq_js = page.evaluate(js)
    page.close()
    rng = make_mulberry32(SEED)
    seq_py = [rng() for _ in range(12)]
    assert seq_js == seq_py


def test_render_and_double_render_grok(browser, server):
    card = CARDS / "grok-4.5.html"
    out_dir = REPO / "data" / "batches-dev" / "smoke-r4" / "shots"
    result = render_card(
        browser,
        server.base_url,
        server,
        card,
        SNAPSHOT,
        BERLIN,
        slug="grok-4.5",
        out_dir=out_dir,
        variant="min",
    )
    assert result.api_finished
    assert result.ready_normal, f"predicate not met; flags={result.flags}"
    assert result.stable, f"unstable; flags={result.flags}"
    assert result.main_pixel_hash
    for kind in ("png", "webp", "thumb"):
        assert Path(result.files[kind]).exists()

    dr = double_render(
        browser, server.base_url, server, card, SNAPSHOT, BERLIN, slug="grok-4.5"
    )
    # 这张 fixture 卡有 ~1/20 的残余非确定性(§2.2 冻结通道消不掉的那类)。
    # scheme §2.4 对这种情况的规定是打 non-deterministic-render 旗标而非报错——
    # 所以测试断言的是"旗标机制正确工作",不是"必须相等"。
    if not dr.equal:
        assert "non-deterministic-render" in dr.flags, (
            f"unequal double-render MUST carry the non-deterministic-render flag; "
            f"flags={dr.flags} hashes={dr.hash_a}!={dr.hash_b}"
        )
