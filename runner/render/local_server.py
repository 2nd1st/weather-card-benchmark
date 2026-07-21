"""Local card + Open-Meteo cache proxy server (scheme §2, §4.3, §6).

Serves one card HTML file at ``/card.html`` (and ``/``) and answers the two
whitelisted same-origin endpoints ``/api/om/forecast`` and ``/api/om/archive``
from the trial weather cache. Response bodies carry ``Access-Control-Allow-Origin: *``.

Cache keying replicates trial-20260715/serve.py EXACTLY: the upstream URL is
``UPSTREAM[path] + "?" + query`` and the on-disk key is
``sha256(url).hexdigest()[:32]`` — so the frozen trial cache (populated by that
server) resolves without a re-fetch.

Read order: an optional writable *overlay* dir first, then each read-only cache
dir (the trial cache). On a miss, if ``allow_upstream`` is set (DEV convenience)
the upstream is fetched once and written into the overlay; otherwise a 502 is
returned. Production shooting must run with ``allow_upstream=False`` against a
pre-populated snapshot cache.
"""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

UPSTREAM = {
    "/api/om/forecast": "https://api.open-meteo.com/v1/forecast",
    "/api/om/archive": "https://archive-api.open-meteo.com/v1/archive",
}


def cache_key(url: str) -> str:
    """sha256(url)[:32] — identical to trial serve.py."""
    return hashlib.sha256(url.encode()).hexdigest()[:32]


class LocalCardServer:
    """A threaded HTTP server serving one card + the cache proxy.

    Use as a context manager or call :meth:`start` / :meth:`stop`. The served
    card can be swapped between renders with :meth:`set_card` (the server stays
    up, so double-render reuses the same origin).
    """

    def __init__(
        self,
        cache_dirs: list[Path],
        overlay_dir: Optional[Path] = None,
        allow_upstream: bool = True,
        host: str = "127.0.0.1",
    ) -> None:
        self.cache_dirs = [Path(d) for d in cache_dirs]
        self.overlay_dir = Path(overlay_dir) if overlay_dir else None
        if self.overlay_dir is not None:
            self.overlay_dir.mkdir(parents=True, exist_ok=True)
        self.allow_upstream = allow_upstream
        self.host = host
        self._card_bytes: bytes = b"<!doctype html><title>no card</title>"
        self._card_lock = threading.Lock()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._upstream_sem = threading.Semaphore(3)

    # -- card management --------------------------------------------------
    def set_card(self, card_path: Path) -> None:
        with self._card_lock:
            self._card_bytes = Path(card_path).read_bytes()

    def _read_card(self) -> bytes:
        with self._card_lock:
            return self._card_bytes

    # -- cache lookup -----------------------------------------------------
    def _lookup(self, url: str) -> Optional[bytes]:
        key = cache_key(url)
        dirs: list[Path] = []
        if self.overlay_dir is not None:
            dirs.append(self.overlay_dir)
        dirs.extend(self.cache_dirs)
        for d in dirs:
            f = d / f"{key}.json"
            if f.exists():
                return f.read_bytes()
        return None

    def _fetch_upstream(self, url: str) -> tuple[bytes, int]:
        try:
            with self._upstream_sem:
                with urllib.request.urlopen(url, timeout=30) as r:
                    body, status = r.read(), r.status
            if status == 200 and self.overlay_dir is not None:
                (self.overlay_dir / f"{cache_key(url)}.json").write_bytes(body)
            return body, status
        except urllib.error.HTTPError as e:
            return e.read(), e.code
        except Exception as e:  # noqa: BLE001 - surfaced to the card as JSON error
            return json.dumps({"error": str(e)}).encode(), 502

    def serve_api(self, path: str, query: str) -> tuple[bytes, int]:
        url = UPSTREAM[path] + (("?" + query) if query else "")
        body = self._lookup(url)
        if body is not None:
            return body, 200
        if self.allow_upstream:
            return self._fetch_upstream(url)
        return json.dumps({"error": "cache-miss", "url": url}).encode(), 502

    # -- lifecycle --------------------------------------------------------
    def start(self) -> str:
        server = self  # closure handle

        class Handler(BaseHTTPRequestHandler):
            def _send(self, body: bytes, status: int, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - stdlib signature
                path, _, query = self.path.partition("?")
                if path in UPSTREAM:
                    body, status = server.serve_api(path, query)
                    self._send(body, status, "application/json")
                    return
                if path in ("/", "/card.html", "/index.html"):
                    self._send(server._read_card(), 200, "text/html; charset=utf-8")
                    return
                self._send(b'{"error":"not found"}', 404, "application/json")

            def log_message(self, *_args) -> None:  # silence
                return

        self._httpd = ThreadingHTTPServer((self.host, 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        port = self._httpd.server_address[1]
        return f"http://{self.host}:{port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "LocalCardServer":
        self.base_url = self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()
