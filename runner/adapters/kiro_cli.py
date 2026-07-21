"""kiro CLI harness adapter (multi-family, cli arm) — plan×harness, zero proxy.

Invokes kiro-cli (AWS Kiro, an Amazon Q CLI fork) headless
(`chat --no-interactive`) under an ISOLATED fake $HOME (runner/_kiro_home),
HARNESS-SETUP rule #6:
  * .local/bin/{kiro-cli,kiro-cli-chat,kiro-cli-term} symlinked to the real
    binaries — the dispatcher resolves its subcommand binaries via $HOME and
    dies with os error 2 otherwise;
  * "Library/Application Support/kiro-cli" symlinked to the real dir so the
    GitHub-login token (data.sqlite3 — NOT the macOS keychain) refreshes
    consistently with the user's session;
  * .kiro/ EMPTY skeleton — the real HOME carries ~/.kiro/prompts (spec-kitty
    slash commands); steering/skills stay absent. Workspace .kiro/ never loads
    because each call runs in a throwaway sandbox cwd.

Probes 2026-07-18/19:
  * 6-way parallel OK (10-11s each, zero retries) — the "low concurrency"
    reputation did not reproduce at this fan-out.
  * EFFORT (corrected after an initial sampling-bias miss): the backend
    declares per-model effort — claude sonnet-5/opus-4.8/4.7 take
    output_config.effort low..max (opus-4.7 defaults to XHIGH, everyone else
    high); opus-4.6/sonnet-4.6 the same minus xhigh; gpt sol/terra/luna take
    reasoning.effort none..max (default high) plus mode standard|pro. The
    remaining 9 models declare nothing. HOWEVER `--no-interactive` silently
    DROPS effort no matter how it is supplied (flag, chat.modelDefaults —
    luna low/xhigh/none behaviorally identical; illegal values swallowed),
    while the interactive TUI applies it (pty probe: luna @xhigh shows a
    Thinking spinner, 0.42cr/25s vs 0.27cr/39s headless). So effort configs
    are driven through a pty here (_tty_call); effort-null configs stay on
    the plain headless path.
  * TUI chrome around the reply: a leading "> " marker line, optional
    "WARNING:" lines, and a "▸ Credits: X • Time: Ys" footer AFTER </html>
    (which would fail extract_output's complete_html gate) — all stripped
    here before extraction so flags reflect MODEL behavior only. The credit
    figure is parsed and printed to the batch log (plan-credit audit trail).
    pty-mode calls additionally carry a "tty" flag; a "preamble" flag on a
    tty call may be residual TUI chrome (spinner frames), not model output.

Tool trust: --trust-tools=fs_read,fs_write (sandbox file delivery allowed, no
exec/network tools) — mirrors the claude/grok stock-harness tool surface.
Delivery is dual-path (rule #7): stdout reply first, sandbox-written *.html
fallback. Model names differ from site model ids for some entries (site
claude-opus-4-8 → kiro claude-opus-4.8), carried per-config in yaml as
`provider_model_id`.
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

_KIRO_HOME = Path(__file__).resolve().parent.parent / "_kiro_home"
_REAL_HOME = Path.home()
# full strip: CSI sequences, OSC strings, stray control toggles
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]|[\x0e\x0f]")
_CREDITS_RE = re.compile(r"Credits:\s*([0-9.]+)")
_CHROME_LINE_RE = re.compile(r"(?m)^(?:\s*WARNING:[^\n]*|\s*▸[^\n]*)\n?")


def _ensure_home() -> Path:
    """Build the fake HOME skeleton; symlink binaries + credential store."""
    (_KIRO_HOME / ".kiro").mkdir(parents=True, exist_ok=True)
    bin_dir = _KIRO_HOME / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("kiro-cli", "kiro-cli-chat", "kiro-cli-term"):
        link = bin_dir / name
        real = _REAL_HOME / ".local" / "bin" / name
        if not link.exists() and real.exists():
            link.symlink_to(real)
    app_support = _KIRO_HOME / "Library" / "Application Support"
    app_support.mkdir(parents=True, exist_ok=True)
    link = app_support / "kiro-cli"
    real = _REAL_HOME / "Library" / "Application Support" / "kiro-cli"
    if not link.exists() and real.exists():
        link.symlink_to(real)
    return _KIRO_HOME


def _kiro_exe() -> Optional[str]:
    real = _REAL_HOME / ".local" / "bin" / "kiro-cli"
    if real.exists():
        return str(real)
    return shutil.which("kiro-cli")


_DIFF_PREFIX_RE = re.compile(r"(?m)^\s*\+\s+\d+:")
_TUI_CHROME_MARKERS = ("Type to steer", "esc to cancel", "Ctrl+S to queue",
                       "ctrl+o to expand", "to queue a message")


def _looks_corrupted(text: str) -> bool:
    """Screen a TEXT delivery candidate for TUI rendering artifacts (render
    census 2026-07-19): fs_write echoes arrive as diff views with '+  N:' line
    prefixes, and idle-UI chrome can bleed into long captures. Such text parses
    as 'valid HTML' but ships broken JS — reject the candidate so extraction
    falls through to a clean source (or an honest failure)."""
    if _DIFF_PREFIX_RE.search(text):
        return True
    return any(m in text for m in _TUI_CHROME_MARKERS)


def _clean_tui(text: str) -> tuple[str, Optional[float]]:
    """Strip ANSI + kiro TUI chrome; return (clean_text, credits)."""
    t = _ANSI_RE.sub("", text or "")
    credits: Optional[float] = None
    m = _CREDITS_RE.search(t)
    if m:
        try:
            credits = float(m.group(1))
        except ValueError:
            pass
    t = _CHROME_LINE_RE.sub("", t)
    # the reply itself starts with a single "> " marker on its first line
    stripped = t.lstrip()
    if stripped.startswith("> "):
        t = stripped[2:]
    return t, credits


def _tty_call(argv: list[str], cwd: str, env: dict, timeout_s: int) -> tuple[str, bool]:
    """Drive kiro-cli through a pty (interactive TUI) and return (raw, clean).

    Rationale: --no-interactive drops --effort entirely (2.13.0), the TUI
    applies it. The positional INPUT auto-submits on TUI start; we watch the
    stream for the "Credits:" reply footer, then send /quit. The pty window
    is set very wide so long HTML lines never soft-wrap into the scrollback
    (wrapping would corrupt extraction)."""
    import fcntl
    import select
    import signal
    import struct
    import termios

    # openpty (NOT pty.fork): the winsize must be in place BEFORE the TUI
    # starts — with pty.fork the TUI can boot against a 0x0 window and wedge
    # (observed: idle kiro-cli-chat, no reply ever produced).
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 500, 10000, 0, 0))
    pid = os.fork()
    if pid == 0:  # child
        try:
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            os.close(master)
            if slave > 2:
                os.close(slave)
            os.chdir(cwd)
            os.execve(argv[0], argv, env)
        finally:  # pragma: no cover — exec failed
            os._exit(127)
    os.close(slave)
    fd = master
    buf = bytearray()
    quit_sent = False
    saw_eof = False
    winch_sent = False
    ctrld_tries = 0
    deadline = time.monotonic() + timeout_s
    last_data = time.monotonic()
    try:
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], 1.0)
            if r:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:  # pty closed by child exit
                    saw_eof = True
                    break
                if not chunk:
                    saw_eof = True
                    break
                buf += chunk
                last_data = time.monotonic()
                if not quit_sent and b"Credits:" in bytes(buf[-16384:]):
                    # graceful exit: a single fast write of "/quit\r" is DROPPED
                    # by the TUI (verified); slow-typed input after a settle
                    # pause exits cleanly.
                    time.sleep(2.0)
                    for ch in "/quit":
                        os.write(fd, ch.encode())
                        time.sleep(0.03)
                    time.sleep(0.4)
                    os.write(fd, b"\r")
                    quit_sent = True
            elif quit_sent and time.monotonic() - last_data > 12:
                if ctrld_tries >= 1:
                    break  # ladder exhausted — SIGTERM in finally
                os.write(fd, b"\x04")  # ctrl-d fallback
                last_data = time.monotonic()
                ctrld_tries += 1
            elif time.monotonic() - last_data > 120:
                break  # stalled TUI (thinking streams constantly; 2min silence = wedged)
            elif not winch_sent and time.monotonic() - last_data > 3:
                # belt-and-braces: nudge a size re-read in case the TUI probed
                # the window before our ioctl landed
                try:
                    os.kill(pid, signal.SIGWINCH)
                except ProcessLookupError:
                    pass
                winch_sent = True
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        os.close(fd)
    return bytes(buf).decode("utf-8", errors="replace"), (quit_sent or saw_eof)


def _session_reply_candidates(cwd: str) -> list[str]:
    """Pull raw reply text from the tty session store: interactive sessions
    save $HOME/.kiro/sessions/cli/<uuid>.json (cwd field + full turn state)
    the moment a turn completes — regardless of how the session later exits.
    Candidates are the turn result content strings, newest turn first; the
    session file triplet (.json/.jsonl/.history) is deleted afterwards so the
    isolated home stays clean."""
    import json as _json

    sess_dir = _KIRO_HOME / ".kiro" / "sessions" / "cli"
    if not sess_dir.is_dir():
        return []
    want = {cwd, os.path.realpath(cwd)}
    cands: list[str] = []
    for jf in sorted(sess_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            doc = _json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if doc.get("cwd") not in want:
            continue
        turns = (doc.get("session_state", {})
                    .get("conversation_metadata", {})
                    .get("user_turn_metadatas", []))
        for turn in reversed(turns):
            content = (turn.get("result", {}).get("Ok", {}) or {}).get("content", [])
            for block in content:
                data = block.get("data") if isinstance(block, dict) else None
                if isinstance(data, str) and data.strip():
                    cands.append(data)
        for ext in (".json", ".jsonl", ".history"):
            try:
                (sess_dir / (jf.stem + ext)).unlink(missing_ok=True)
            except OSError:
                pass
    return cands


def _db_reply_candidates(cwd: str) -> list[str]:
    """Pull raw reply text for a finished tty session from kiro's conversation
    store (conversations_v2, keyed by session cwd). The TUI byte stream is
    render-corrupted (partial redraws interleave, observed 1.1MB for a 13KB
    card) — the DB holds the pristine message text. Returns candidates newest
    first; the row is deleted afterwards so sandbox sessions do not pollute
    the user's session list."""
    import json as _json
    import sqlite3

    db = _REAL_HOME / "Library" / "Application Support" / "kiro-cli" / "data.sqlite3"
    if not db.is_file():
        return []
    keys = list({cwd, os.path.realpath(cwd)})
    row = None
    try:
        con = sqlite3.connect(str(db), timeout=10)
        for k in keys:
            row = con.execute(
                "SELECT value FROM conversations_v2 WHERE key = ?", (k,)
            ).fetchone()
            if row:
                break
        if row:
            for k in keys:
                con.execute("DELETE FROM conversations_v2 WHERE key = ?", (k,))
            con.commit()
        con.close()
    except Exception:
        return []
    if not row:
        return []
    try:
        doc = _json.loads(row[0])
    except Exception:
        return []
    cands: list[str] = []
    t = doc.get("transcript")
    if isinstance(t, list):
        cands.extend(x for x in reversed(t) if isinstance(x, str))

    def walk(o) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "content" and isinstance(v, str):
                    cands.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc.get("history"))
    return cands


def call_kiro_cli(
    config: dict,
    prompt: str,
    timeout_s: int = 1500,
) -> CliResult:
    exe = _kiro_exe()
    if exe is None:
        return CliResult("infra_error", None, [], None, None, 0, None, "kiro-cli binary not found")
    model = config.get("provider_model_id") or config.get("model_id") or "auto"
    effort = config.get("effort")
    use_tty = bool(effort)  # headless drops --effort; see module header
    home = _ensure_home()
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="wcb-kiro-") as cwd:
        argv = [exe, "chat", "--trust-tools=fs_read,fs_write", "--model", model]
        if not use_tty:
            argv.insert(2, "--no-interactive")
        if effort:
            argv += ["--effort", effort]
        argv.append(prompt)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["PWD"] = cwd
        env.setdefault("TERM", "xterm-256color")
        env.pop("OLDPWD", None)
        if use_tty:
            raw_out, clean_exit = _tty_call(argv, cwd, env, timeout_s)
            stdout_text, stderr_text, rc = raw_out, "", (0 if clean_exit else 1)
        else:
            try:
                p = subprocess.run(
                    argv, capture_output=True, text=True, timeout=timeout_s,
                    cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                return CliResult("infra_error", None, [], None, None,
                                 int((time.monotonic() - t0) * 1000), None,
                                 f"kiro-cli chat timeout after {timeout_s}s")
            stdout_text, stderr_text, rc = p.stdout or "", p.stderr or "", p.returncode
        file_artifact = _collect_html_file(cwd)
        # tty sessions persist to session files, headless to conversations_v2 —
        # gather (and clean up) both stores.
        db_candidates = _session_reply_candidates(cwd) + _db_reply_candidates(cwd)
    wall_ms = int((time.monotonic() - t0) * 1000)
    out, credits = _clean_tui(stdout_text)
    err, _ = _clean_tui(stderr_text)
    if use_tty:
        # The TUI re-renders the streaming reply many times, so the raw stream
        # holds dozens of partial copies of the document (observed: 1.2MB for a
        # 13KB card). Keep ONLY the final complete render: the last <!doctype
        # (or <html) that still has a </html> after it. Anything after that
        # </html> is idle-prompt chrome (the Credits footer was parsed above).
        low_text = out.lower()
        end = low_text.rfind("</html>")
        if end != -1:
            start = low_text.rfind("<!doctype", 0, end)
            if start == -1:
                start = low_text.rfind("<html", 0, end)
            out = out[(start if start != -1 else 0): end + len("</html>")]
    if credits is not None:
        print(f"[kiro-cli] {model}{' @' + effort if effort else ''} credits={credits} "
              f"wall={wall_ms}ms{' tty' if use_tty else ''}", flush=True)
    extra_flags = ["tty"] if use_tty else []
    low = (out + "\n" + err).lower()
    # delivery order (render-census fix 2026-07-19): the SANDBOX FILE comes
    # FIRST in BOTH modes. When the model fs_writes the card, the chat reply /
    # session text carries the TUI's *rendering* of that write — a diff view
    # with "+  N:" line-number prefixes, markdown-eaten backticks, or chrome —
    # which extract_output happily "validates" (23 corrupted cards shipped).
    # Text candidates are additionally screened by _looks_corrupted.
    sources: list[str] = []
    if file_artifact is not None:
        sources.append(file_artifact)
    text_cands = (db_candidates + ([out] if out.strip() else [])) if use_tty \
        else (([out] if out.strip() else []) + db_candidates)
    sources.extend(t for t in text_cands if not _looks_corrupted(t))
    for src in sources:
        ex = extract_output(src)
        if ex.valid_html and not _looks_corrupted(ex.html):
            return CliResult("valid", ex.html, extra_flags + list(ex.flags), None, None, wall_ms, model, None)
    if rc != 0 or not out.strip():
        if ("429" in low or "rate limit" in low or "too many requests" in low
                or "throttl" in low or "quota" in low or "credit" in low and "insufficient" in low):
            return CliResult("rate_limited", None, [], None, None, wall_ms, model,
                             f"rate/credit limited: rc={rc} {(err or out)[:300]}")
        if "not authenticated" in low or "unauthorized" in low or "expired" in low or "login" in low:
            return CliResult("infra_error", None, [], None, None, wall_ms, model,
                             f"auth: rc={rc} {(err or out)[:300]}")
        return CliResult("infra_error", None, [], None, None, wall_ms, model,
                         f"rc={rc} {(err or out)[:300]}")
    return CliResult("model_error", None, [], None, None, wall_ms, model,
                     f"no valid HTML (stdout_len={len(out)}, file={'yes' if file_artifact else 'no'}, "
                     f"db_cands={len(db_candidates)})",
                     raw_text=((db_candidates[0] if db_candidates else None) or out or file_artifact or None))
