"""opencode CLI harness adapter (cli arm, opencode-go subscription models).

`opencode run -m opencode-go/<model> "<prompt>"` headless under an ISOLATED
XDG home (runner/_opencode_home) — HARNESS-SETUP rule #6:
  * XDG_DATA_HOME  → _opencode_home/share   (auth.json: ONLY the go key)
  * XDG_CONFIG_HOME → _opencode_home/config (opencode.json: ONLY the
    opencode-go provider — no user plugins/MCP/providers/model default)
  * leaked *_API_KEY env vars are stripped (opencode auto-adopts them as
    providers — observed with GEMINI_API_KEY).
Verified 2026-07-18: isolated `opencode auth list` shows no user creds; the
user config (superpowers plugin + 5 custom providers + MCP) does not load.

The CLI's zen billing line rejects the go workspace ("Insufficient balance"),
so the provider block points at the FIRST-PARTY go subscription endpoint
(https://opencode.ai/zen/go/v1) — same channel as the api arm, reproducible
by any go subscriber via the official provider-config mechanism.

Permissions: edit=allow, bash=deny (aligns with the claude arm's Write-only
tool surface). Delivery is dual-path (rule #7): stdout reply first,
sandbox-written *.html fallback.
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

_OC_HOME = Path(__file__).resolve().parent.parent / "_opencode_home"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def call_opencode_cli(
    config: dict,
    prompt: str,
    timeout_s: int = 1800,
) -> CliResult:
    exe = shutil.which("opencode")
    if exe is None:
        return CliResult("infra_error", None, [], None, None, 0, None, "opencode binary not found")
    model = config.get("model_id") or "minimax-m3"
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="wcb-opencode-") as cwd:
        argv = [exe, "run", "-m", f"opencode-go/{model}", prompt]
        env = dict(os.environ)
        env["XDG_DATA_HOME"] = str(_OC_HOME / "share")
        env["XDG_CONFIG_HOME"] = str(_OC_HOME / "config")
        # subprocess cwd= does NOT rewrite inherited $PWD — opencode (bun) reads
        # it for project-root discovery and once wrote its card into the REPO
        # root (and snapshotted the repo). Pin both to the sandbox.
        env["PWD"] = cwd
        env.pop("OLDPWD", None)
        # opencode auto-adopts ambient provider keys — strip them (stock isolation).
        for k in [k for k in env if k.endswith("_API_KEY") or k in ("GEMINI_API_KEY", "GOOGLE_API_KEY")]:
            env.pop(k, None)
        try:
            p = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s,
                cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return CliResult("infra_error", None, [], None, None,
                             int((time.monotonic() - t0) * 1000), None,
                             f"opencode run timeout after {timeout_s}s")
        file_artifact = _collect_html_file(cwd)
    wall_ms = int((time.monotonic() - t0) * 1000)
    out = _ANSI_RE.sub("", p.stdout or "")
    err = _ANSI_RE.sub("", p.stderr or "")
    low = (out + "\n" + err).lower()
    if out.strip():
        ex = extract_output(out)
        if ex.valid_html:
            return CliResult("valid", ex.html, list(ex.flags), None, None, wall_ms, model, None)
    if file_artifact is not None:
        ex = extract_output(file_artifact)
        if ex.valid_html:
            return CliResult("valid", ex.html, list(ex.flags), None, None, wall_ms, model, None)
    if p.returncode != 0 or not out.strip():
        if "429" in low or "rate limit" in low or "quota" in low or "insufficient balance" in low:
            return CliResult("rate_limited", None, [], None, None, wall_ms, model,
                             f"rate/credit limited: rc={p.returncode} {err[:300]}")
        return CliResult("infra_error", None, [], None, None, wall_ms, model,
                         f"rc={p.returncode} {(err or out)[:300]}")
    return CliResult("model_error", None, [], None, None, wall_ms, model,
                     f"no valid HTML (stdout_len={len(out)}, file={'yes' if file_artifact else 'no'})",
                     raw_text=(out or file_artifact or None))
