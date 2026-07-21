"""grok CLI harness adapter (grok family, cli arm) — plan×harness, zero proxy.

Invokes the OFFICIAL grok binary (~/.grok/bin/grok, NOT the user's guard
wrapper) headless (`-p … --always-approve`) under an ISOLATED fake $HOME
(runner/_grok_home) so none of the user's config leaks into the harness
(HARNESS-SETUP rule #6). Verified 2026-07-17 via `grok inspect`:
  * real HOME: ~/.claude/Claude.md as project instructions (~563 tok) +
    150 permissions from ~/.claude/settings.local.json + 91 user skills;
  * fake HOME: 0 instructions / 0 permissions / 0 user skills.
auth.json + bundled/ (official stock skills) are symlinked from ~/.grok so
oauth token refresh stays consistent with the user's login.

Extra hygiene flags: --no-memory --no-plan --no-subagents (user-state
features, not part of the stock oneshot harness).
Delivery is dual-path (rule #7): stdout reply first, sandbox-written *.html
fallback. Stock binary keeps stdout clean (guard-wrapper banner lines were
stdout-polluting — another reason to bypass the wrapper).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from .openai_compat import extract_output
from .claude_cli import _collect_html_file, CliResult

_GROK_HOME = Path(__file__).resolve().parent.parent / "_grok_home"
_REAL_GROK_DIR = Path.home() / ".grok"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _ensure_home() -> Path:
    d = _GROK_HOME / ".grok"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("auth.json", "bundled"):
        link = d / name
        real = _REAL_GROK_DIR / name
        if not link.exists() and real.exists():
            link.symlink_to(real)
    return _GROK_HOME


def _grok_exe() -> Optional[str]:
    official = _REAL_GROK_DIR / "bin" / "grok"
    if official.exists():
        return str(official)
    return shutil.which("grok")


def call_grok_cli(
    config: dict,
    prompt: str,
    timeout_s: int = 1500,
) -> CliResult:
    exe = _grok_exe()
    if exe is None:
        return CliResult("infra_error", None, [], None, None, 0, None, "grok binary not found")
    model = config.get("model_id") or "grok-4.5"
    effort = config.get("effort")  # CLI enum: low | medium | high
    home = _ensure_home()
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="wcb-grok-") as cwd:
        argv = [exe, "-p", prompt, "--always-approve",
                "-m", model,
                "--no-memory", "--no-plan", "--no-subagents"]
        if effort:
            argv += ["--reasoning-effort", effort]
        env = dict(os.environ)
        env["HOME"] = str(home)
        try:
            p = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s,
                cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return CliResult("infra_error", None, [], None, None,
                             int((time.monotonic() - t0) * 1000), None,
                             f"grok -p timeout after {timeout_s}s")
        file_artifact = _collect_html_file(cwd)
    wall_ms = int((time.monotonic() - t0) * 1000)
    out = _ANSI_RE.sub("", p.stdout or "")
    err = _ANSI_RE.sub("", p.stderr or "")
    low = (out + "\n" + err).lower()
    # delivery path 1: stdout reply
    if out.strip():
        ex = extract_output(out)
        if ex.valid_html:
            return CliResult("valid", ex.html, list(ex.flags), None, None, wall_ms, model, None)
    # delivery path 2: file written by the agent in its sandbox cwd
    if file_artifact is not None:
        ex = extract_output(file_artifact)
        if ex.valid_html:
            return CliResult("valid", ex.html, list(ex.flags), None, None, wall_ms, model, None)
    if p.returncode != 0 or not out.strip():
        if "429" in low or "rate limit" in low or "quota" in low or "too many requests" in low:
            return CliResult("rate_limited", None, [], None, None, wall_ms, model,
                             f"rate limited: rc={p.returncode} {err[:300]}")
        if "not authenticated" in low or "unauthorized" in low or "401" in low:
            return CliResult("infra_error", None, [], None, None, wall_ms, model,
                             f"auth: rc={p.returncode} {err[:300]}")
        return CliResult("infra_error", None, [], None, None, wall_ms, model,
                         f"rc={p.returncode} {(err or out)[:300]}")
    return CliResult("model_error", None, [], None, None, wall_ms, model,
                     f"no valid HTML (stdout_len={len(out)}, file={'yes' if file_artifact else 'no'})",
                     raw_text=(out or file_artifact or None))
