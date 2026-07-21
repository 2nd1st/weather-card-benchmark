"""Pixel-hash + image-encoding helpers (scheme §2.2/§2.4, appendix A imaging).

Pixel hash = SHA256 over the raw RGB bytes of the decoded PNG (NOT the PNG file
bytes, which carry non-pixel chunks). Two screenshots with identical pixels hash
equal regardless of PNG encoder noise.
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image

THUMB_WIDTH = 480
WEBP_QUALITY = 85


def pixel_hash(png_bytes: bytes) -> str:
    """SHA256 of decoded RGB pixel bytes (lowercase hex)."""
    with Image.open(io.BytesIO(png_bytes)) as im:
        rgb = im.convert("RGB")
        return hashlib.sha256(rgb.tobytes()).hexdigest()


def to_webp(png_bytes: bytes, quality: int = WEBP_QUALITY) -> bytes:
    with Image.open(io.BytesIO(png_bytes)) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="WEBP", quality=quality, method=6)
        return buf.getvalue()


def to_thumb_webp(png_bytes: bytes, width: int = THUMB_WIDTH, quality: int = WEBP_QUALITY) -> bytes:
    with Image.open(io.BytesIO(png_bytes)) as im:
        rgb = im.convert("RGB")
        w, h = rgb.size
        new_h = max(1, round(h * width / w))
        thumb = rgb.resize((width, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        thumb.save(buf, format="WEBP", quality=quality, method=6)
        return buf.getvalue()
