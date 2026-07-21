"""Qoder CLI harness adapter (qwen family, cli arm) — plan×harness, zero proxy.

Invokes qodercli (Alibaba Qoder, a Claude-Code-compatible fork; iFlow CLI's
official successor) headless (`-p --dangerously-skip-permissions`) in a throwaway
sandbox cwd. Probes 2026-07-19 (v1.0.48):
  * Global config is clean out of the box (~/.qoder carries no rules / CLAUDE.md
    / AGENTS / memory) so the user's login session is a stock harness as-is — no
    config-dir isolation needed (unlike kiro's fake HOME). We only sandbox the
    cwd (a fresh tempdir) so it can't read the repo's project instructions or
    write the card into the repo.
  * Delivery is FILE-ONLY: qodercli fs_writes the card to the cwd and prints only
    a prose summary to stdout (no HTML on stdout). We collect the sandbox file.
  * `--reasoning-effort` is silently ignored for qwen models (banana → no error,
    normal reply) — qwen's reasoning knob is thinking_budget, not a level, so the
    effort axis is NOT expanded here (default tier only), same shape as the kiro
    Chinese-model seats.
  * Permissions: `--dangerously-skip-permissions` auto-approves the fs_write tool
    (headless; without it the non-interactive run would hang on a permission
    prompt — the mimo lesson).

provider_model_id carries Qoder's exact CamelCase model name (site id
qwen3.8-max-preview → Qoder "Qwen3.8-Max-Preview").
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

_REAL_HOME = Path.home()
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")


def _qoder_exe() -> Optional[str]:
    real = _REAL_HOME / ".local" / "bin" / "qodercli"
    if real.exists():
        return str(real)
    return shutil.which("qodercli")


def call_qoder_cli(
    config: dict,
    prompt: str,
    timeout_s: int = 1500,
) -> CliResult:
    exe = _qoder_exe()
    if exe is None:
        return CliResult("infra_error", None, [], None, None, 0, None, "qodercli binary not found")
    model = config.get("provider_model_id") or config.get("model_id") or "Auto"
    effort = config.get("effort")  # ignored for qwen (see header); kept for future models
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="wcb-qoder-") as cwd:
        argv = [exe, "-p", "--model", model, "--dangerously-skip-permissions"]
        if effort:
            argv += ["--reasoning-effort", effort]
        argv.append(prompt)
        env = dict(os.environ)
        env["PWD"] = cwd
        env.pop("OLDPWD", None)
        try:
            p = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s,
                cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return CliResult("infra_error", None, [], None, None,
                             int((time.monotonic() - t0) * 1000), None,
                             f"qodercli -p timeout after {timeout_s}s")
        file_artifact = _collect_html_file(cwd)
    wall_ms = int((time.monotonic() - t0) * 1000)
    out = _ANSI_RE.sub("", p.stdout or "")
    err = _ANSI_RE.sub("", p.stderr or "")
    low = (out + "\n" + err).lower()
    # delivery: sandbox file first (that's where the card is), stdout only as a
    # fallback in case a future model inlines HTML instead of writing a file.
    if file_artifact is not None:
        ex = extract_output(file_artifact)
        if ex.valid_html:
            return CliResult("valid", ex.html, list(ex.flags), None, None, wall_ms, model, None)
    if out.strip():
        ex = extract_output(out)
        if ex.valid_html:
            return CliResult("valid", ex.html, list(ex.flags), None, None, wall_ms, model, None)
    if p.returncode != 0 or (file_artifact is None and not out.strip()):
        if "429" in low or "rate limit" in low or "too many requests" in low or "quota" in low:
            return CliResult("rate_limited", None, [], None, None, wall_ms, model,
                             f"rate limited: rc={p.returncode} {err[:300]}")
        if "unauthorized" in low or "not logged in" in low or "401" in low or "expired" in low:
            return CliResult("infra_error", None, [], None, None, wall_ms, model,
                             f"auth: rc={p.returncode} {err[:300]}")
        return CliResult("infra_error", None, [], None, None, wall_ms, model,
                         f"rc={p.returncode} {(err or out)[:300]}")
    return CliResult("model_error", None, [], None, None, wall_ms, model,
                     f"no valid HTML (file={'yes' if file_artifact else 'no'}, stdout_len={len(out)})",
                     raw_text=(file_artifact or out or None))
