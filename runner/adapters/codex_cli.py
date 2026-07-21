"""codex CLI harness adapter (gpt family, cli arm).

`codex exec --skip-git-repo-check` in an EMPTY sandbox cwd with an ISOLATED
CODEX_HOME (runner/_codex_home: no user plugins/marketplaces/notify — stock
harness, HARNESS-SETUP rule #6). Model/effort per call via `-m` and
`-c model_reasoning_effort=...` (sub2 ceiling = "max"; "ultra" clamps).
Delivery is dual-path like claude_cli (rule #7): --output-last-message file
first, sandbox-written *.html fallback. Known codex flakiness: intermittent
"stream disconnected" (network layer) → infra_error, slot engine records it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .openai_compat import extract_output
from .claude_cli import _collect_html_file, CliResult

_CODEX_HOME = Path(__file__).resolve().parent.parent / "_codex_home"
_CODEX_CONFIG = """\
model_provider = "sub2api"
preferred_auth_method = "apikey"
sandbox_mode = "workspace-write"
approval_policy = "never"

[model_providers.sub2api]
name = "Sub2API"
base_url = "http://localhost:8080/v1"
wire_api = "responses"
env_key = "SUB2API_API_KEY"
"""

# oauth (ChatGPT plan direct login) variant — auth.json is a SYMLINK to
# ~/.codex/auth.json so token refresh stays consistent with the user's login.
# No custom provider: codex talks to its native ChatGPT backend (zero proxy).
_CODEX_HOME_OAUTH = Path(__file__).resolve().parent.parent / "_codex_home_oauth"
_CODEX_CONFIG_OAUTH = """\
preferred_auth_method = "chatgpt"
sandbox_mode = "workspace-write"
approval_policy = "never"
"""

_TOKENS_RE = re.compile(r"tokens used[:\s]+([\d,]+)", re.IGNORECASE)


def _ensure_home(oauth: bool = False) -> Path:
    home = _CODEX_HOME_OAUTH if oauth else _CODEX_HOME
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.toml"
    if not cfg.exists():
        cfg.write_text(_CODEX_CONFIG_OAUTH if oauth else _CODEX_CONFIG)
    if oauth:
        auth = home / "auth.json"
        if not auth.exists():
            real = Path.home() / ".codex" / "auth.json"
            if real.exists():
                auth.symlink_to(real)
    return home


def call_codex_cli(
    config: dict,
    prompt: str,
    api_key: str = "",
    oauth: bool = False,
    timeout_s: int = 2400,
) -> CliResult:
    exe = shutil.which("codex")
    if exe is None:
        return CliResult("infra_error", None, [], None, None, 0, None, "codex binary not found")
    model = config.get("model_id") or "gpt-5.6-sol"
    effort = config.get("effort")
    home = _ensure_home(oauth=oauth)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="wcb-codex-") as cwd:
        last_msg = os.path.join(cwd, "_wcb_last_message.txt")
        argv = [exe, "exec", "--skip-git-repo-check",
                "-m", model,
                "--output-last-message", last_msg]
        if effort:
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        argv.append(prompt)
        env = dict(os.environ)
        env["CODEX_HOME"] = str(home)
        if not oauth:
            env["SUB2API_API_KEY"] = api_key
        try:
            p = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s,
                cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return CliResult("infra_error", None, [], None, None,
                             int((time.monotonic() - t0) * 1000), None,
                             f"codex exec timeout after {timeout_s}s")
        wall_ms = int((time.monotonic() - t0) * 1000)
        out = (p.stdout or "")
        err = (p.stderr or "")
        low = (out + "\n" + err).lower()
        # opportunistic token parse from the exec transcript
        tok = None
        m = _TOKENS_RE.search(out) or _TOKENS_RE.search(err)
        if m:
            tok = int(m.group(1).replace(",", ""))
        last_text = ""
        if os.path.exists(last_msg):
            try:
                last_text = Path(last_msg).read_text(encoding="utf-8", errors="replace")
            except OSError:
                last_text = ""
        # delivery path 1: last agent message
        if last_text.strip():
            ex = extract_output(last_text)
            if ex.valid_html:
                return CliResult("valid", ex.html, list(ex.flags), None, tok, wall_ms, model, None)
        # delivery path 2: sandbox file (exclude our own last-message artifact)
        html_file = _collect_html_file(cwd)
        if html_file is not None:
            ex = extract_output(html_file)
            if ex.valid_html:
                return CliResult("valid", ex.html, list(ex.flags), None, tok, wall_ms, model, None)
        if p.returncode != 0 or not (out or last_text):
            if "429" in low or "rate limit" in low or "quota" in low:
                return CliResult("rate_limited", None, [], None, tok, wall_ms, model,
                                 f"rate limited: rc={p.returncode} {err[:300]}")
            return CliResult("infra_error", None, [], None, tok, wall_ms, model,
                             f"rc={p.returncode} {err[:300] or out[-300:]}")
    return CliResult("model_error", None, [], None, tok, wall_ms, model,
                     f"no valid HTML (last_len={len(last_text)}, rc={p.returncode})",
                     raw_text=(last_text or html_file or None))
