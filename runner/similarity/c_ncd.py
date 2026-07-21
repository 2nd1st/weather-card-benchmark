"""c-ncd — Normalized Compression Distance similarity (DIAGNOSTIC channel).

Scheme §4 + 附录 A (BYTE-EXACT, implemented verbatim):

    c-ncd | 诊断; C = len(lzma.compress(FORMAT_XZ, preset=6, single stream));
    input UTF-8 NFC; concat = byte concatenation with NO separator;
    NCD(x,y) = (C(x∘y) − min(C(x),C(y))) / max(C(x),C(y));
    NCD_sym = mean of both orders;
    sim = 1 − clamp(NCD_sym, 0, 1), unclamped value kept alongside.

c-ncd is a PERMANENT diagnostic channel (v9): it never participates in the
greedy channel cull and never enters the formal channel set / statistics /
hypothesis family. It is emitted only for the channel audit display.

Input artifact for a code (``c-*``) channel is the card's ``card.html`` file.
Empty / absent HTML on either side (double-empty included) → ``s = None`` (§4).

The returned dict always carries:
    s                 : float in [0,1] | None   — the (clamped) channel value
    s_unclamped       : float | None            — 1 − NCD_sym (CAN be < 0 or > 1)
    ncd_sym           : float | None            — symmetric NCD (unclamped)
    ncd_xy, ncd_yx    : float | None            — the two directional NCDs
    c_x, c_y          : int | None              — C(x), C(y)
    c_xy, c_yx        : int | None              — C(x∘y), C(y∘x)
    len_x, len_y      : int                     — NFC-UTF-8 byte lengths of inputs
    null_reason       : str | None              — why s is None (diagnostic)
"""

from __future__ import annotations

import lzma
import os
import unicodedata
from pathlib import Path
from typing import Any, Mapping

# 附录 A pinned constants for c-ncd — implement verbatim, never simplify.
_LZMA_FORMAT = lzma.FORMAT_XZ  # C = len(lzma.compress(FORMAT_XZ, ...))
_LZMA_PRESET = 6               # preset=6, single stream
_NFC = "NFC"                   # input UTF-8 NFC
_UTF8 = "utf-8"


def _compressed_len(data: bytes) -> int:
    """C(data) = byte length of the single-stream xz compression (附录 A)."""
    return len(lzma.compress(data, format=_LZMA_FORMAT, preset=_LZMA_PRESET))


def _read_card_html(artifacts: Any) -> str | None:
    """Resolve the card.html text from a channel artifact handle.

    Accepts, in order of preference:
      * ``dict``/``Mapping`` with ``card_html`` or ``html`` (raw string, may be None)
      * ``dict``/``Mapping`` with ``card_dir`` / ``dir`` / ``path`` (dir or file path)
      * ``str`` / ``os.PathLike`` pointing at a card directory (reads ``card.html``)
        or directly at an ``.html`` file
    Returns the raw HTML string, or ``None`` when no card.html artifact exists.
    """
    if artifacts is None:
        return None
    if isinstance(artifacts, (str, os.PathLike)):
        p = Path(artifacts)
        if p.is_dir():
            p = p / "card.html"
        if not p.is_file():
            return None
        return p.read_text(encoding=_UTF8)
    if isinstance(artifacts, Mapping):
        for key in ("card_html", "html"):
            if key in artifacts:
                return artifacts[key]
        for key in ("card_dir", "dir", "path"):
            val = artifacts.get(key)
            if val is not None:
                return _read_card_html(val)
        return None
    raise TypeError(
        f"c-ncd: unsupported artifact type {type(artifacts)!r}; expected "
        "Mapping, str, os.PathLike, or None"
    )


def _nfc_utf8(html: str | None) -> bytes:
    """Encode HTML as UTF-8 NFC bytes (附录 A). None/'' → empty bytes."""
    if not html:
        return b""
    return unicodedata.normalize(_NFC, html).encode(_UTF8)


def _null(len_x: int, len_y: int, reason: str) -> dict:
    return {
        "s": None,
        "s_unclamped": None,
        "ncd_sym": None,
        "ncd_xy": None,
        "ncd_yx": None,
        "c_x": None,
        "c_y": None,
        "c_xy": None,
        "c_yx": None,
        "len_x": len_x,
        "len_y": len_y,
        "null_reason": reason,
    }


def compute(card_a_artifacts: Any, card_b_artifacts: Any) -> dict:
    """Compute the c-ncd similarity between two cards' HTML.

    See module docstring for the returned dict shape. ``s`` is the clamped,
    reportable channel value in [0,1]; ``s_unclamped`` is the diagnostic
    ``1 − NCD_sym`` which can fall below 0 (or above 1).
    """
    html_a = _read_card_html(card_a_artifacts)
    html_b = _read_card_html(card_b_artifacts)

    xa = _nfc_utf8(html_a)
    xb = _nfc_utf8(html_b)
    len_x, len_y = len(xa), len(xb)

    # §4: zero-vector / empty on either side (double-empty included) → S=null.
    if not xa or not xb:
        return _null(len_x, len_y, "empty-input")

    c_x = _compressed_len(xa)
    c_y = _compressed_len(xb)
    c_xy = _compressed_len(xa + xb)   # concat = no-separator byte concatenation
    c_yx = _compressed_len(xb + xa)

    c_min = min(c_x, c_y)
    c_max = max(c_x, c_y)
    # xz always emits a non-empty container for non-empty payloads, so c_max>0.
    if c_max == 0:  # defensive; unreachable for non-empty inputs
        return _null(len_x, len_y, "zero-compressed-size")

    ncd_xy = (c_xy - c_min) / c_max
    ncd_yx = (c_yx - c_min) / c_max
    ncd_sym = (ncd_xy + ncd_yx) / 2.0

    s_unclamped = 1.0 - ncd_sym                      # diagnostic; can be < 0
    s = 1.0 - min(1.0, max(0.0, ncd_sym))            # 1 − clamp(NCD_sym, 0, 1)

    return {
        "s": s,
        "s_unclamped": s_unclamped,
        "ncd_sym": ncd_sym,
        "ncd_xy": ncd_xy,
        "ncd_yx": ncd_yx,
        "c_x": c_x,
        "c_y": c_y,
        "c_xy": c_xy,
        "c_yx": c_yx,
        "len_x": len_x,
        "len_y": len_y,
        "null_reason": None,
    }
