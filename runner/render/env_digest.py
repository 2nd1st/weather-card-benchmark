"""Canonical render-environment descriptor + digest (scheme §2.4, appendix A).

The scheme names an ``env_digest`` field (manifest.schema.json) but leaves its
exact construction to an appendix-A follow-up (DECISIONS-M0: "env_digest 精确
输入字节 ... 随渲染环境钉死一起定"). This module fixes a *provisional* contract:

  env_digest = lowercase-hex SHA256 over the RFC 8785 (JCS) serialization of a
  canonical descriptor object (NFC on every string).

DEV mode: the descriptor carries ``"dev": true`` and may describe a local
(non-Docker) Chromium, per the task brief. When appendix A pins the final byte
layout this module's ``build_descriptor`` shape is the single place to change.
"""

from __future__ import annotations

import hashlib
import platform
import unicodedata
from importlib import metadata
from typing import Any

import rfc8785

# Render flags pinned by scheme appendix A (environment & timing table).
# Kept sorted; order is irrelevant to the hash (JCS sorts object keys, and this
# is a value array we keep canonical by construction).
PINNED_CHROMIUM_FLAGS: list[str] = [
    "--disable-gpu",
    "--font-render-hinting=none",
    "--force-color-profile=srgb",
    "--hide-scrollbars",
]

VIEWPORT = {"width": 1280, "height": 800}
DEVICE_SCALE_FACTOR = 1
LOCALE = "en-US"
TIMEZONE = "UTC"


def _nfc(value: Any) -> Any:
    """NFC-normalize every string reachable in the descriptor tree."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {_nfc(k): _nfc(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_nfc(v) for v in value]
    return value


def _playwright_version() -> str:
    try:
        return metadata.version("playwright")
    except metadata.PackageNotFoundError:  # pragma: no cover - always installed
        return "unknown"


def build_descriptor(chromium_version: str, *, dev: bool = True) -> dict[str, Any]:
    """Build the canonical env descriptor object.

    ``chromium_version`` is the running browser version (e.g. Playwright's
    ``browser.version``); the caller supplies it because it requires a live
    browser handle.
    """
    descriptor: dict[str, Any] = {
        "chromium_version": chromium_version,
        "playwright_version": _playwright_version(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "viewport": dict(VIEWPORT),
        "device_scale_factor": DEVICE_SCALE_FACTOR,
        "locale": LOCALE,
        "timezone": TIMEZONE,
        "flags": list(PINNED_CHROMIUM_FLAGS),
        "dev": bool(dev),
    }
    return _nfc(descriptor)


def digest_bytes(descriptor: dict[str, Any]) -> bytes:
    """RFC 8785 (JCS) canonical byte stream for the descriptor."""
    return rfc8785.dumps(descriptor)


def env_digest(chromium_version: str, *, dev: bool = True) -> str:
    """Lowercase-hex SHA256 of the canonical env descriptor."""
    descriptor = build_descriptor(chromium_version, dev=dev)
    return hashlib.sha256(digest_bytes(descriptor)).hexdigest()
