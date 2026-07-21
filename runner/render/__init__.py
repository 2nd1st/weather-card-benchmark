"""Render + screenshot subsystem (R4).

Frozen-channel deterministic rendering: Date freeze, mulberry32(0xC0FFEE),
CDP virtual time t_v=5000, animation pause currentTime=0, frame-change capture,
double-render determinism. See COMPARISON-SCHEME-v12.md §2.
"""

from .double_render import DoubleRenderResult, double_render
from .env_digest import build_descriptor, env_digest
from .harness import (
    DOM_DUMP_VERSION,
    RenderResult,
    dump_dom,
    render_card,
    render_once,
    write_outputs,
)
from .local_server import LocalCardServer, cache_key
from .mulberry32 import MULBERRY32_JS, SEED, make_mulberry32

__all__ = [
    "LocalCardServer",
    "cache_key",
    "RenderResult",
    "render_card",
    "render_once",
    "write_outputs",
    "dump_dom",
    "DOM_DUMP_VERSION",
    "DoubleRenderResult",
    "double_render",
    "env_digest",
    "build_descriptor",
    "make_mulberry32",
    "MULBERRY32_JS",
    "SEED",
]
