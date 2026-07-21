"""Render-determinism check (scheme §2.4).

Render the same card TWICE in fresh browser contexts and compare the frozen
main-screenshot pixel hash. Unequal → ``non-deterministic-render``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser

from .harness import RenderResult, render_once
from .local_server import LocalCardServer


@dataclass
class DoubleRenderResult:
    slug: str
    equal: bool
    hash_a: Optional[str]
    hash_b: Optional[str]
    flags: list[str] = field(default_factory=list)
    render_a: Optional[RenderResult] = None
    render_b: Optional[RenderResult] = None


def double_render(
    browser: Browser,
    base_url: str,
    server: LocalCardServer,
    card_path: Path,
    snapshot: dict,
    params: dict,
    slug: str,
    variant: str = "min",
) -> DoubleRenderResult:
    a = render_once(browser, base_url, server, card_path, snapshot, params, slug, variant)
    b = render_once(browser, base_url, server, card_path, snapshot, params, slug, variant)
    equal = a.main_pixel_hash is not None and a.main_pixel_hash == b.main_pixel_hash
    flags: list[str] = [] if equal else ["non-deterministic-render"]
    return DoubleRenderResult(
        slug=slug,
        equal=equal,
        hash_a=a.main_pixel_hash,
        hash_b=b.main_pixel_hash,
        flags=flags,
        render_a=a,
        render_b=b,
    )
