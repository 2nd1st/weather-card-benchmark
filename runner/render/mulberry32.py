"""mulberry32 PRNG — the exact seed the render init-script pins Math.random to.

Scheme §2.2 / appendix A: ``Math.random = mulberry32(0xC0FFEE)``. The JS below is
the canonical mulberry32 (Tommy Ettinger's public-domain generator) transcribed
verbatim; :func:`make_mulberry32` is a bit-exact Python reference used only for
tests / golden derivation (never injected). ``test_render`` asserts the two
agree on the first values under seed 0xC0FFEE via a live page eval.
"""

from __future__ import annotations

SEED = 0xC0FFEE

# Verbatim mulberry32. Installed by the init-script over the whole page BEFORE
# any card script runs, so Math.random() is deterministic (scheme §2.2).
MULBERRY32_JS = """
function mulberry32(a) {
  return function () {
    a |= 0;
    a = (a + 0x6D2B79F5) | 0;
    var t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
""".strip()


def _imul(a: int, b: int) -> int:
    return ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF


def make_mulberry32(seed: int = SEED):
    """Return a zero-arg callable yielding the same float64 sequence as the JS.

    Mirrors JS ToUint32 / Math.imul semantics with explicit 32-bit masking.
    """
    state = seed & 0xFFFFFFFF

    def rng() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = state
        t = _imul(t ^ (t >> 15), 1 | t)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        t &= 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return rng
