"""OpenAI-compatible adapter (R3) covering BOTH the /chat/completions and the
/responses protocols behind one call surface.

Why two protocols: the sub2api dev gateway serves the GPT family via
``POST /responses`` (the Responses API survives GPT-5.6-Sol's long silent
reasoning phase) and the Grok family via ``POST /chat/completions``. This module
picks the endpoint per family (overridable) and normalises both wire formats
into a single :class:`AdapterResult`.

Effort mapping (MODEL-MATRIX §3):
  * GPT via /responses  -> ``{"reasoning": {"effort": <e>, "summary": "auto"}}``
  * GPT/Grok via /chat  -> ``{"reasoning_effort": <e>}``  (OpenAI-compat field)
Grok 4.5's effort axis is a coarse reasoning toggle and is normally ``None`` in
the dev matrix; when a value is supplied it rides the ``reasoning_effort`` field.

Terminal-state classification maps EXACTLY onto the scheme §1.1 / R2 slot
vocabulary:
  * valid              -- complete response + valid HTML (fence-stripped ok too)
  * model_error        -- complete response but no valid HTML (refusal / self
                          truncation with no </html> / empty / incomplete)
  * infra_error        -- vendor accepted the request (headers/request-id seen)
                          but the stream died mid-flight, or a response.failed /
                          non-429 HTTP error came back
  * acceptance_unknown -- request bytes were sent but acceptance is unprovable
                          (read timeout before any response headers)
  * unsent             -- provably not sent (connect refused / DNS / TLS / connect
                          timeout, i.e. failure before the request was written)
  * rate_limited       -- vendor refused BEFORE model processing with a
                          deterministic un-billed signal (HTTP 429 / 503 overloaded
                          / 402 / explicit quota text)

Retry budgets (5x for unsent, exponential backoff for rate_limited, no retry for
the slot-consuming states) belong to the R2 slot engine, NOT here: this module
classifies a single attempt.

No API keys are hardcoded; the caller passes ``api_key`` + ``base_url`` resolved
from runner/.env.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

import requests

# ---------------------------------------------------------------------------
# Output extraction  (adapted from trial-20260715/extract_agent.py)
# ---------------------------------------------------------------------------

# A whole-message markdown fence: ```html\n...\n``` (language tag optional).
FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*)\n```\s*$", re.DOTALL)
DOCTYPE_RE = re.compile(r"<!doctype\s+html", re.IGNORECASE)
HTML_OPEN_RE = re.compile(r"<html[\s>]", re.IGNORECASE)
HTML_CLOSE_RE = re.compile(r"</html\s*>", re.IGNORECASE)
# Reasoning blocks emitted around (and sometimes inside) the answer. minimax and
# the deepseek-style models wrap their chain of thought in <think>…</think>; left
# in place it renders as a wall of prose across the card.
THINK_RE = re.compile(r"(?is)<think\b[^>]*>.*?</think\s*>")
THINK_OPEN_RE = re.compile(r"(?i)<think\b[^>]*>")

# Residual-pollution markers (render census 2026-07-20). These are things that
# can only come from the CAPTURE, never from a model writing a weather card:
# harness TUI chrome and our own sandbox temp paths. A card carrying one is
# garbage no matter how well-formed its HTML is — the earlier scan missed two
# 1.5–3.3MB kiro cards precisely because its marker list was too narrow, and both
# shipped as "valid".
POLLUTION_MARKERS = (
    "kiro is working",
    "type to steer",
    "ctrl+s to queue",
    "kiro_default",
    "wcb-kiro",
    "esc to interrupt",
    "⏎ send",
    "tokens used",
)
TMP_PATH_RE = re.compile(r"/private/var/folders/[^\s\"'<>]+")


@dataclass
class Extraction:
    """Result of pulling the HTML document out of a raw model message."""

    html: str
    flags: list[str] = field(default_factory=list)
    looks_like_html: bool = False
    complete_html: bool = False

    @property
    def valid_html(self) -> bool:
        """Slot-engine HTML validity gate: a complete document that both looks
        like HTML (opens with <!doctype/<html) and closes with </html>."""
        return self.looks_like_html and self.complete_html


def extract_output(raw_text: str) -> Extraction:
    """Strip an outer markdown fence (flag ``fence``), then locate the HTML
    document by finding ``<!doctype html>`` (or an opening ``<html>``); any
    preamble before it is dropped (flag ``preamble``)."""
    flags: list[str] = []
    text = (raw_text or "").strip()

    m = FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
        flags.append("fence")

    # Reasoning blocks first: <think>…</think> can sit before the document AND
    # between two attempts, so stripping it up front keeps the doctype scan below
    # from anchoring inside a discarded draft. An unterminated <think> (stream cut
    # mid-thought) drops everything from the tag onward.
    if THINK_RE.search(text):
        text = THINK_RE.sub("", text).strip()
        flags.append("think")
    stray = THINK_OPEN_RE.search(text)
    if stray:
        text = text[: stray.start()].strip()
        flags.append("think-unterminated")

    # Multi-document replies (render census 2026-07-19): self-correcting models
    # sometimes emit attempt #1 + reasoning + a rewritten attempt #2 in one
    # message. Anchoring on the FIRST doctype used to concatenate every attempt
    # into a single corrupt "document". Anchor on the LAST doctype — the
    # model's final answer — and flag it so the choice stays visible.
    doc_anchors = list(DOCTYPE_RE.finditer(text))
    if len(doc_anchors) > 1:
        flags.append("multidoc")
        anchor = doc_anchors[-1]
    else:
        anchor = (doc_anchors[0] if doc_anchors else None) or HTML_OPEN_RE.search(text)
    if anchor and anchor.start() != 0:
        flags.append("preamble")
        text = text[anchor.start():]

    # Trailing commentary: a model that keeps talking after </html> used to have
    # that prose stored inside the card and rendered on the page. Cut at the LAST
    # close tag so the artifact is exactly one document.
    closes = list(HTML_CLOSE_RE.finditer(text))
    if closes and closes[-1].end() < len(text.rstrip()):
        flags.append("epilogue")
        text = text[: closes[-1].end()]

    html = text
    head = html.lstrip()[:200].lower()
    looks_like_html = head.startswith("<!doctype") or head.startswith("<html")
    complete_html = html.rstrip().lower().endswith("</html>")

    # Capture pollution is never salvageable — refuse it outright rather than let
    # a structurally-valid wrapper carry harness chrome onto the site.
    low = html.lower()
    if any(mk in low for mk in POLLUTION_MARKERS) or TMP_PATH_RE.search(html):
        flags.append("capture-polluted")
        looks_like_html = False

    return Extraction(html, flags, looks_like_html, complete_html)


# ---------------------------------------------------------------------------
# Request body construction (effort mapping)
# ---------------------------------------------------------------------------


def provider_model(config: dict) -> str:
    """The exact model string to put on the wire.

    ``model_id`` is OUR label for a measured model; ``provider_model_id`` is what
    the vendor's API actually accepts. They diverge whenever a vendor changes the
    model behind a fixed API string — e.g. DeepSeek re-post-trained
    ``deepseek-v4-flash`` on 2026-07-31 without renaming it, so the two epochs
    need distinct model_ids here while both send `deepseek-v4-flash` upstream.

    kiro_cli and qoder_cli already honoured this; this adapter did not, and sent
    the internal label upstream — every slot came back infra-failed with
    served=None. Same precedence as the siblings: provider override, else label.
    """
    return config.get("provider_model_id") or config["model_id"]


def endpoint_for(family: Optional[str], config: Optional[dict] = None) -> str:
    """Return "responses" or "chat" for a family. GPT -> /responses, everything
    else -> /chat/completions. An explicit config ``endpoint`` key wins."""
    if config:
        ep = config.get("endpoint")
        if ep in ("chat", "responses"):
            return ep
    return "responses" if (family or "").lower() == "gpt" else "chat"


def _apply_effort(body: dict[str, Any], effort: str, effort_param: Optional[str]) -> None:
    """Map the effort token onto the vendor's thinking/reasoning field on a chat
    body. ``effort_param`` names the vendor's knob (config.yaml effort_param);
    default (None / "reasoning_effort") is the OpenAI-compat field used by
    gpt/grok/deepseek/mimo/hunyuan. Others take a differently-shaped field:

      thinking.type    → {"thinking": {"type": "enabled"|"disabled"}}  (doubao/ark, kimi)
      thinking         → {"thinking": {"type": ...}}  (on/off style)
      thinking_budget  → {"thinking_budget": <int>}   (qwen dashscope; off→0)
      enable_thinking  → {"enable_thinking": <bool>}  (intern)

    Only reasoning_effort and thinking.type are behavior-verified (2026-07: gpt/
    grok/deepseek/mimo via reasoning_effort; doubao-seed-2.1-pro via thinking.type,
    curl-probed). The rest are shaped from vendor docs and get verified when their
    key lands."""
    p = (effort_param or "reasoning_effort")
    tok = str(effort).lower()
    truthy = tok in ("on", "enabled", "true", "1", "yes")
    if p in ("reasoning_effort", "reasoning.effort"):
        body["reasoning_effort"] = effort
    elif p == "thinking.type":
        t = effort if tok in ("enabled", "disabled") else ("enabled" if truthy else "disabled")
        body["thinking"] = {"type": t}
    elif p == "thinking":
        body["thinking"] = {"type": "enabled" if truthy else "disabled"}
    elif p == "thinking_budget":
        if tok in ("off", "none", "0"):
            body["thinking_budget"] = 0
        else:
            try:
                body["thinking_budget"] = int(effort)
            except (TypeError, ValueError):
                body["reasoning_effort"] = effort
    elif p == "enable_thinking":
        body["enable_thinking"] = truthy
    else:
        body["reasoning_effort"] = effort  # unknown knob → prior default


def build_body(
    *,
    endpoint: str,
    model: str,
    prompt: str,
    effort: Optional[str] = None,
    effort_param: Optional[str] = None,
) -> dict[str, Any]:
    """Construct the streaming request body for the chosen endpoint, mapping the
    effort knob onto the right per-protocol field (MODEL-MATRIX §3)."""
    if endpoint == "responses":
        body: dict[str, Any] = {"model": model, "input": prompt, "stream": True}
        if effort:
            body["reasoning"] = {"effort": effort, "summary": "auto"}
        return body

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if effort:
        _apply_effort(body, effort, effort_param)
    return body


# ---------------------------------------------------------------------------
# SSE parsing + streaming state (pure, network-free -> unit-testable)
# ---------------------------------------------------------------------------


def iter_sse(lines: Iterable[Any]) -> Iterator[dict]:
    """Yield parsed JSON objects from an SSE ``data:`` line stream. Non-data and
    keepalive lines are skipped; ``[DONE]`` terminates; unparseable payloads are
    dropped (matches run_wave.sse_events)."""
    for raw in lines:
        if raw is None:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("", "[DONE]"):
            if payload == "[DONE]":
                return
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


@dataclass
class StreamState:
    """Accumulated result of consuming a streamed completion."""

    content: list[str] = field(default_factory=list)
    served_model: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Optional[dict] = None
    terminal: bool = False      # a clean protocol-level completion signal arrived
    failed: bool = False        # an explicit response.failed event arrived

    @property
    def text(self) -> str:
        return "".join(self.content)


def update_state(state: StreamState, ev: dict, endpoint: str) -> bool:
    """Fold one SSE event into ``state``. Returns True if this event contributed
    output-text content (used to time TTFT)."""
    produced_content = False

    if endpoint == "chat":
        if isinstance(ev.get("usage"), dict):
            state.usage = ev["usage"]
        if ev.get("model"):
            state.served_model = ev["model"]
        for ch in ev.get("choices") or []:
            delta = (ch.get("delta") or {}).get("content")
            if delta:
                state.content.append(delta)
                produced_content = True
            if ch.get("finish_reason"):
                state.finish_reason = ch["finish_reason"]
                state.terminal = True
        return produced_content

    # responses protocol
    etype = ev.get("type", "")
    resp_obj = ev.get("response") or {}
    if etype == "response.output_text.delta":
        delta = ev.get("delta") or ""
        if delta:
            state.content.append(delta)
            produced_content = True
    elif etype in ("response.created", "response.in_progress"):
        if resp_obj.get("model"):
            state.served_model = resp_obj["model"]
    elif etype == "response.completed":
        state.usage = resp_obj.get("usage") or state.usage
        state.served_model = resp_obj.get("model") or state.served_model
        state.finish_reason = resp_obj.get("status") or state.finish_reason
        state.terminal = True
    elif etype == "response.incomplete":
        state.usage = resp_obj.get("usage") or state.usage
        state.served_model = resp_obj.get("model") or state.served_model
        state.finish_reason = resp_obj.get("status") or "incomplete"
        state.terminal = True
    elif etype == "response.failed":
        state.usage = resp_obj.get("usage") or state.usage
        state.served_model = resp_obj.get("model") or state.served_model
        state.finish_reason = resp_obj.get("status") or "failed"
        state.failed = True
    return produced_content


def consume_events(events: Iterable[dict], endpoint: str) -> StreamState:
    """Fold an event iterable into a :class:`StreamState` (test helper)."""
    state = StreamState()
    for ev in events:
        update_state(state, ev, endpoint)
    return state


# ---------------------------------------------------------------------------
# Classification (pure)
# ---------------------------------------------------------------------------


def classify_terminal(*, failed: bool, terminal: bool, valid_html: bool) -> str:
    """Decide the terminal state once a stream has been fully consumed without a
    transport exception."""
    if failed:
        return "infra_error"          # vendor-signalled failure after acceptance
    if not terminal:
        return "infra_error"          # stream ended with no completion signal = mid-stream death
    return "valid" if valid_html else "model_error"


def classify_http_status(code: int, body_text: str = "") -> Optional[str]:
    """Map a non-streaming HTTP error response to a slot state. Returns None for
    a 2xx (no error)."""
    if 200 <= code < 300:
        return None
    if code in (429, 503, 402):
        return "rate_limited"
    low = (body_text or "").lower()
    if "quota" in low or "rate limit" in low or "overloaded" in low or "too many requests" in low:
        return "rate_limited"
    # Headers were received (server responded) but produced no usable model
    # output; the least-wrong slot bucket is infra_error and http_status is
    # surfaced on the result for debugging (auth/bad-request land here too).
    return "infra_error"


def classify_send_exception(exc: BaseException) -> str:
    """Classify a transport exception raised BEFORE any response headers were
    received (i.e. during connection / request send / wait-for-headers)."""
    # Connection could not be established -> request bytes never left = unsent.
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "unsent"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "unsent"
    # Bytes were sent, headers never arrived within the read window -> we cannot
    # prove the vendor did not accept/charge it.
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "acceptance_unknown"
    if isinstance(exc, requests.exceptions.Timeout):
        return "acceptance_unknown"
    # Unknown pre-header failure: conservatively treat as unprovable.
    return "acceptance_unknown"


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class AdapterResult:
    status: str                        # valid|model_error|infra_error|acceptance_unknown|unsent|rate_limited
    requested_model: str
    endpoint: str
    served_model: Optional[str] = None
    html: Optional[str] = None         # extracted document (only populated when valid)
    raw_text: str = ""                 # full concatenated model output
    flags: list[str] = field(default_factory=list)  # e.g. ["fence", "preamble"]
    finish_reason: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    wall_ms: int = 0
    ttft_ms: Optional[int] = None
    http_status: Optional[int] = None
    error: Optional[str] = None
    looks_like_html: bool = False
    complete_html: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        return d


def _usage_tokens(usage: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
    if not usage:
        return None, None
    tin = usage.get("prompt_tokens")
    if tin is None:
        tin = usage.get("input_tokens")
    tout = usage.get("completion_tokens")
    if tout is None:
        tout = usage.get("output_tokens")
    return tin, tout


# ---------------------------------------------------------------------------
# Background mode (/responses only) — create + poll instead of SSE.
#
# The official OpenAI /responses SSE stream drops long-reasoning runs
# mid-flight (observed 2026-07-18: keepalive events then a clean server-side
# close with no response.completed — gpt-5.4@high / 5.2-codex@xhigh). OpenAI's
# documented remedy for long tasks is background mode: POST {"background":
# true} then GET /responses/{id} until a terminal status. Same classification
# and result shape as the streaming path.
# ---------------------------------------------------------------------------


def _bg_extract_text(resp_obj: dict) -> str:
    """Aggregate output_text parts from a full /responses object."""
    parts: list[str] = []
    for item in resp_obj.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text" and c.get("text"):
                    parts.append(c["text"])
    return "".join(parts)


def call_model_background(
    config: dict,
    prompt: str,
    *,
    api_key: str,
    base_url: str,
    connect_timeout: float = 15.0,
    read_timeout: float = 1800.0,
    poll_interval_s: float = 10.0,
    session: Optional[requests.Session] = None,
) -> AdapterResult:
    """Run one /responses completion in background mode and poll to a terminal
    status. Only meaningful for the responses endpoint (gpt family)."""
    model = provider_model(config)
    effort = config.get("effort")
    url = base_url.rstrip("/") + "/responses"
    body = build_body(endpoint="responses", model=model, prompt=prompt, effort=effort)
    body.pop("stream", None)
    body["background"] = True
    body["store"] = True
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    own_session = session is None
    sess = session or requests.Session()
    t0 = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    def done(result: AdapterResult) -> AdapterResult:
        if own_session:
            sess.close()
        return result

    # --- create -------------------------------------------------------------
    try:
        resp = sess.post(url, json=body, headers=headers,
                         timeout=(connect_timeout, 120.0))
    except requests.exceptions.RequestException as exc:
        return done(AdapterResult(status=classify_send_exception(exc),
                                  requested_model=model, endpoint="responses",
                                  wall_ms=elapsed_ms(), error=repr(exc)[:500]))
    if resp.status_code not in (200, 202):
        body_text = resp.text[:2000]
        status = classify_http_status(resp.status_code, body_text) or "infra_error"
        return done(AdapterResult(status=status, requested_model=model,
                                  endpoint="responses", wall_ms=elapsed_ms(),
                                  http_status=resp.status_code,
                                  error=body_text[:500] or None))
    created = resp.json()
    rid = created.get("id")
    if not rid:
        return done(AdapterResult(status="infra_error", requested_model=model,
                                  endpoint="responses", wall_ms=elapsed_ms(),
                                  http_status=resp.status_code,
                                  error=f"background create returned no id: {str(created)[:300]}"))

    # --- poll ---------------------------------------------------------------
    poll_url = url + "/" + rid
    poll_failures = 0
    obj = created
    while True:
        st = obj.get("status")
        if st in ("completed", "failed", "incomplete", "cancelled"):
            break
        if time.monotonic() - t0 > read_timeout:
            return done(AdapterResult(status="infra_error", requested_model=model,
                                      endpoint="responses", wall_ms=elapsed_ms(),
                                      error=f"background poll timeout after {read_timeout}s (last status {st})"))
        time.sleep(poll_interval_s)
        try:
            pr = sess.get(poll_url, headers=headers, timeout=(connect_timeout, 60.0))
            if pr.status_code != 200:
                poll_failures += 1
                if poll_failures >= 5:
                    return done(AdapterResult(status="infra_error", requested_model=model,
                                              endpoint="responses", wall_ms=elapsed_ms(),
                                              http_status=pr.status_code,
                                              error=f"poll http {pr.status_code}: {pr.text[:300]}"))
                continue
            poll_failures = 0
            obj = pr.json()
        except requests.exceptions.RequestException as exc:
            poll_failures += 1
            if poll_failures >= 5:
                return done(AdapterResult(status="infra_error", requested_model=model,
                                          endpoint="responses", wall_ms=elapsed_ms(),
                                          error=f"poll: {repr(exc)[:300]}"))

    raw = _bg_extract_text(obj)
    ex = extract_output(raw)
    usage = obj.get("usage") or {}
    tokens_in = usage.get("input_tokens")
    tokens_out = usage.get("output_tokens")
    st = obj.get("status")
    status = classify_terminal(failed=(st == "failed"),
                               terminal=(st in ("completed", "incomplete")),
                               valid_html=ex.valid_html)
    err = None
    if st == "failed":
        err = str((obj.get("error") or {}))[:500] or "response.failed"
    elif st == "incomplete":
        err = f"incomplete: {str(obj.get('incomplete_details') or {})[:300]}"
    elif st == "cancelled":
        status = "infra_error"
        err = "background response cancelled"

    return done(AdapterResult(
        status=status,
        requested_model=model,
        endpoint="responses",
        served_model=obj.get("model"),
        html=ex.html if status == "valid" else None,
        raw_text=raw,
        flags=ex.flags,
        finish_reason=st,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        wall_ms=elapsed_ms(),
        ttft_ms=None,
        http_status=200,
        error=err,
    ))


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def call_model(
    config: dict,
    prompt: str,
    *,
    api_key: str,
    base_url: str,
    connect_timeout: float = 15.0,
    read_timeout: float = 1800.0,
    session: Optional[requests.Session] = None,
) -> AdapterResult:
    """Run one streaming completion for ``config`` and classify the outcome.

    ``config`` mirrors config.schema.json axes; the keys used here are
    ``family``, ``model_id`` / ``provider_model_id``, ``effort`` and optional
    ``endpoint``. ``base_url`` already includes the ``/v1`` route root (e.g. the
    dev gateway).
    """
    family = config.get("family")
    model = provider_model(config)
    effort = config.get("effort")
    endpoint = endpoint_for(family, config)

    url = base_url.rstrip("/") + ("/responses" if endpoint == "responses" else "/chat/completions")
    body = build_body(endpoint=endpoint, model=model, prompt=prompt, effort=effort,
                      effort_param=config.get("effort_param"))
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    own_session = session is None
    sess = session or requests.Session()
    t0 = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    # --- send + wait for headers -------------------------------------------
    try:
        resp = sess.post(url, json=body, headers=headers, stream=True,
                         timeout=(connect_timeout, read_timeout))
    except requests.exceptions.RequestException as exc:
        status = classify_send_exception(exc)
        if own_session:
            sess.close()
        return AdapterResult(status=status, requested_model=model, endpoint=endpoint,
                             wall_ms=elapsed_ms(), error=repr(exc)[:500])

    # --- HTTP-level error (headers received, non-2xx) ----------------------
    if resp.status_code != 200:
        try:
            body_text = resp.text[:2000]
        except Exception:
            body_text = ""
        status = classify_http_status(resp.status_code, body_text) or "infra_error"
        resp.close()
        if own_session:
            sess.close()
        return AdapterResult(status=status, requested_model=model, endpoint=endpoint,
                             wall_ms=elapsed_ms(), http_status=resp.status_code,
                             error=body_text[:500] or None)

    # --- stream body -------------------------------------------------------
    state = StreamState()
    ttft_ms: Optional[int] = None
    mid_stream_error: Optional[str] = None
    try:
        for ev in iter_sse(resp.iter_lines(decode_unicode=False)):
            produced = update_state(state, ev, endpoint)
            if produced and ttft_ms is None:
                ttft_ms = elapsed_ms()
    except requests.exceptions.RequestException as exc:
        mid_stream_error = repr(exc)[:500]
    finally:
        resp.close()
        if own_session:
            sess.close()

    raw = state.text
    ex = extract_output(raw)
    tokens_in, tokens_out = _usage_tokens(state.usage)

    if mid_stream_error is not None:
        # Headers were already received -> vendor accepted the request but the
        # stream died mid-flight.
        status = "infra_error"
        err = mid_stream_error
    else:
        status = classify_terminal(failed=state.failed, terminal=state.terminal,
                                   valid_html=ex.valid_html)
        err = None

    return AdapterResult(
        status=status,
        requested_model=model,
        endpoint=endpoint,
        served_model=state.served_model,
        html=ex.html if status == "valid" else None,
        raw_text=raw,
        flags=ex.flags,
        finish_reason=state.finish_reason,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        wall_ms=elapsed_ms(),
        ttft_ms=ttft_ms,
        http_status=200,
        error=err,
        looks_like_html=ex.looks_like_html,
        complete_html=ex.complete_html,
    )
