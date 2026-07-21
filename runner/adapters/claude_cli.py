"""Claude Code CLI adapter — the first HARNESS-arm adapter (plan×harness).

Invokes the locally installed & authenticated `claude` binary in headless mode
(`claude -p … --output-format json`), which is Anthropic's OFFICIAL automation
entry (MODEL-MATRIX §4: US plan arms = official client direct login, ZERO proxy).

Harness identity notes (config annotation, scheme §6/§9):
  * The Claude Code default system prompt IS part of the harness arm — we do not
    override it (that difference vs api×raw is what this arm measures).
  * cwd = a fresh EMPTY temp dir so the CLI cannot ingest repo context
    (CLAUDE.md, files) into the oneshot.
  * --max-turns 1: single response, no agentic tool loop.
DEV note: telemetry token fields come from the CLI JSON `usage`; billing=plan
(subscription) so cost_usd stays null.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from .openai_compat import extract_output  # reuse the R3 fence/doctype extractor


from dataclasses import field


@dataclass
class CliResult:
    status: str  # valid | model_error | infra_error | rate_limited
    html: Optional[str]
    flags: list  # e.g. ["fence"] — mirrors openai_compat result shape
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    wall_ms: int
    served_model: Optional[str]
    error: Optional[str]
    # raw reply text for non-valid terminals (run_batch persists it for
    # diagnosis, mirroring openai_compat's raw_text)
    raw_text: Optional[str] = None


def call_claude_cli(
    config: dict,
    prompt: str,
    timeout_s: int = 1500,
) -> CliResult:
    exe = shutil.which("claude")
    if exe is None:
        return CliResult("infra_error", None, [], None, None, 0, None, "claude binary not found")
    model = config.get("model_id") or "claude-fable-5"
    effort = config.get("effort")  # CLI enum: low | medium | high | xhigh | max
    t0 = time.monotonic()
    # HARNESS SEMANTICS: the agent may deliver via stdout OR by Writing a file
    # in its (empty, sandboxed) cwd — both are legitimate cli-arm outputs
    # (trial collect_cli.py precedent). Tools limited to Write; generous turns.
    with tempfile.TemporaryDirectory(prefix="wcb-claude-") as cwd:
        argv = [exe, "-p", prompt, "--output-format", "json",
                "--model", model, "--max-turns", "24",
                "--allowedTools", "Write",
                # STOCK-harness isolation (reproducibility): no user-level
                # CLAUDE.md/skills/plugins/hooks, no MCP servers. Verified:
                # cache_creation 15.8K -> 5.0K with these flags (2026-07-17).
                "--setting-sources", "",
                "--strict-mcp-config"]
        if effort:
            argv += ["--effort", effort]
        try:
            p = subprocess.run(
                argv,
                capture_output=True, text=True, timeout=timeout_s, cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return CliResult("infra_error", None, [], None, None,
                             int((time.monotonic() - t0) * 1000), None,
                             f"claude -p timeout after {timeout_s}s")
        file_artifact = _collect_html_file(cwd)
    wall_ms = int((time.monotonic() - t0) * 1000)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    low = (out + "\n" + err).lower()
    j = None
    if out:
        try:
            j = json.loads(out)
        except json.JSONDecodeError:
            j = {"result": out}
    if j is None:
        if "limit" in low and ("session" in low or "usage" in low or "rate" in low):
            return CliResult("rate_limited", None, [], None, None, wall_ms, None,
                             f"plan/usage limit: rc={p.returncode} {err[:300]}")
        return CliResult("infra_error", None, [], None, None, wall_ms, None,
                         f"rc={p.returncode} {err[:300]}")
    text = j.get("result") or ""
    usage = j.get("usage") or {}
    mu = j.get("modelUsage") or {}
    served = (next(iter(mu)) if mu else None) or j.get("model") or model
    ti, to = usage.get("input_tokens"), usage.get("output_tokens")
    # delivery path 1: stdout result text
    if text.strip():
        ex = extract_output(text)
        if ex.valid_html:
            return CliResult("valid", ex.html, list(ex.flags), ti, to, wall_ms, served, None)
    # delivery path 2: file written by the agent in its sandbox cwd
    if file_artifact is not None:
        ex = extract_output(file_artifact)
        if ex.valid_html:
            return CliResult("valid", ex.html, list(ex.flags), ti, to, wall_ms, served, None)
    subtype = j.get("subtype") or ""
    return CliResult("model_error", None, [], ti, to, wall_ms, served,
                     f"no valid HTML (subtype={subtype}, is_error={j.get('is_error')}, "
                     f"result_len={len(text)}, file={'yes' if file_artifact else 'no'})",
                     raw_text=(text or file_artifact or None))


def _collect_html_file(cwd: str) -> Optional[str]:
    """Largest .html/.htm file the agent wrote in its sandbox, if any."""
    import os
    best, best_size = None, -1
    for root, _dirs, files in os.walk(cwd):
        for fn in files:
            if fn.lower().endswith((".html", ".htm")):
                fp = os.path.join(root, fn)
                sz = os.path.getsize(fp)
                if sz > best_size:
                    best, best_size = fp, sz
    if best is None:
        return None
    try:
        with open(best, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None
