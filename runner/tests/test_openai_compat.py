"""Unit tests for the OpenAI-compatible adapter (R3).

Pure extraction / classification / stream-folding logic only — NO network.
Streaming is exercised by feeding canned SSE lines through the same parse +
state-machine + classifier the live path uses.
"""

import json

import pytest
import requests

from runner.adapters import openai_compat as oc

# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

DOC = "<!doctype html>\n<html><head></head><body>ok</body></html>"


def test_extract_plain_doctype_is_valid():
    ex = oc.extract_output(DOC)
    assert ex.flags == []
    assert ex.looks_like_html and ex.complete_html and ex.valid_html


def test_extract_strips_fence_and_flags():
    ex = oc.extract_output(f"```html\n{DOC}\n```")
    assert "fence" in ex.flags
    assert ex.valid_html
    assert ex.html == DOC


def test_extract_bare_fence_no_lang():
    ex = oc.extract_output(f"```\n{DOC}\n```")
    assert "fence" in ex.flags
    assert ex.valid_html


def test_extract_preamble_before_doctype_is_dropped():
    ex = oc.extract_output(f"Here is your card:\n\n{DOC}")
    assert "preamble" in ex.flags
    assert ex.valid_html
    assert ex.html.startswith("<!doctype")


def test_extract_html_open_without_doctype():
    src = "<html><body>hi</body></html>"
    ex = oc.extract_output(src)
    assert ex.looks_like_html and ex.complete_html and ex.valid_html


def test_extract_refusal_is_not_html():
    ex = oc.extract_output("I cannot help with that request.")
    assert not ex.looks_like_html
    assert not ex.valid_html


def test_extract_truncated_no_closing_tag():
    ex = oc.extract_output("<!doctype html>\n<html><body>partial")
    assert ex.looks_like_html
    assert not ex.complete_html
    assert not ex.valid_html


def test_extract_empty():
    ex = oc.extract_output("")
    assert not ex.valid_html
    assert ex.flags == []


# ---------------------------------------------------------------------------
# body construction / effort mapping
# ---------------------------------------------------------------------------


def test_endpoint_selection():
    assert oc.endpoint_for("gpt") == "responses"
    assert oc.endpoint_for("grok") == "chat"
    assert oc.endpoint_for("gpt", {"endpoint": "chat"}) == "chat"
    assert oc.endpoint_for("grok", {"endpoint": "responses"}) == "responses"


def test_build_body_responses_effort():
    b = oc.build_body(endpoint="responses", model="gpt-5.6-sol", prompt="P", effort="xhigh")
    assert b["input"] == "P" and b["stream"] is True
    assert b["reasoning"] == {"effort": "xhigh", "summary": "auto"}
    assert "messages" not in b


def test_build_body_responses_no_effort():
    b = oc.build_body(endpoint="responses", model="gpt-5.6-sol", prompt="P", effort=None)
    assert "reasoning" not in b


def test_build_body_chat_effort():
    b = oc.build_body(endpoint="chat", model="grok-4.5", prompt="P", effort="medium")
    assert b["messages"] == [{"role": "user", "content": "P"}]
    assert b["reasoning_effort"] == "medium"
    assert b["stream_options"] == {"include_usage": True}


def test_build_body_chat_no_effort():
    b = oc.build_body(endpoint="chat", model="grok-4.5", prompt="P", effort=None)
    assert "reasoning_effort" not in b


# ---------------------------------------------------------------------------
# SSE parsing + stream folding
# ---------------------------------------------------------------------------


def _chat_lines(events):
    return [f"data: {json.dumps(e)}" for e in events] + ["data: [DONE]"]


def test_iter_sse_skips_noise_and_stops_on_done():
    lines = [": keepalive", "", "data: {\"a\":1}", "event: x", "data: [DONE]", "data: {\"b\":2}"]
    got = list(oc.iter_sse(lines))
    assert got == [{"a": 1}]


def test_iter_sse_bytes_input():
    got = list(oc.iter_sse([b"data: {\"x\":5}", b"data: [DONE]"]))
    assert got == [{"x": 5}]


def test_consume_chat_valid_stream():
    events = [
        {"model": "grok-4.5", "choices": [{"delta": {"content": "<!doctype html><html>"}}]},
        {"choices": [{"delta": {"content": "<body>ok</body></html>"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"usage": {"prompt_tokens": 10, "completion_tokens": 42}, "choices": []},
    ]
    state = oc.consume_events(events, "chat")
    assert state.terminal and not state.failed
    assert state.finish_reason == "stop"
    assert state.served_model == "grok-4.5"
    assert state.usage["completion_tokens"] == 42
    ex = oc.extract_output(state.text)
    assert oc.classify_terminal(failed=state.failed, terminal=state.terminal,
                                valid_html=ex.valid_html) == "valid"


def test_consume_chat_length_truncation_is_model_error():
    events = [
        {"choices": [{"delta": {"content": "<!doctype html><html><body>partial"}}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
    ]
    state = oc.consume_events(events, "chat")
    assert state.terminal and state.finish_reason == "length"
    ex = oc.extract_output(state.text)
    assert oc.classify_terminal(failed=state.failed, terminal=state.terminal,
                                valid_html=ex.valid_html) == "model_error"


def test_consume_chat_refusal_is_model_error():
    events = [
        {"choices": [{"delta": {"content": "Sorry, I can't do that."}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    state = oc.consume_events(events, "chat")
    ex = oc.extract_output(state.text)
    assert oc.classify_terminal(failed=state.failed, terminal=state.terminal,
                                valid_html=ex.valid_html) == "model_error"


def test_consume_chat_no_finish_is_infra_error():
    # stream carried content but ended with no finish_reason -> mid-stream death
    events = [{"choices": [{"delta": {"content": "<!doctype html><html></html>"}}]}]
    state = oc.consume_events(events, "chat")
    assert not state.terminal
    ex = oc.extract_output(state.text)
    assert oc.classify_terminal(failed=state.failed, terminal=state.terminal,
                                valid_html=ex.valid_html) == "infra_error"


def test_consume_responses_completed_valid():
    events = [
        {"type": "response.created", "response": {"model": "gpt-5.6-sol"}},
        {"type": "response.output_text.delta", "delta": "<!doctype html><html>"},
        {"type": "response.output_text.delta", "delta": "<body>x</body></html>"},
        {"type": "response.completed",
         "response": {"model": "gpt-5.6-sol", "status": "completed",
                      "usage": {"input_tokens": 100, "output_tokens": 500}}},
    ]
    state = oc.consume_events(events, "responses")
    assert state.terminal and not state.failed
    assert state.served_model == "gpt-5.6-sol"
    assert state.finish_reason == "completed"
    tin, tout = oc._usage_tokens(state.usage)
    assert (tin, tout) == (100, 500)
    ex = oc.extract_output(state.text)
    assert oc.classify_terminal(failed=state.failed, terminal=state.terminal,
                                valid_html=ex.valid_html) == "valid"


def test_consume_responses_incomplete_is_model_error():
    events = [
        {"type": "response.output_text.delta", "delta": "<!doctype html><html><body>cut"},
        {"type": "response.incomplete", "response": {"status": "incomplete"}},
    ]
    state = oc.consume_events(events, "responses")
    assert state.terminal and state.finish_reason == "incomplete"
    ex = oc.extract_output(state.text)
    assert oc.classify_terminal(failed=state.failed, terminal=state.terminal,
                                valid_html=ex.valid_html) == "model_error"


def test_consume_responses_failed_is_infra_error():
    events = [
        {"type": "response.output_text.delta", "delta": "<!doctype html><html></html>"},
        {"type": "response.failed", "response": {"status": "failed"}},
    ]
    state = oc.consume_events(events, "responses")
    assert state.failed
    ex = oc.extract_output(state.text)
    # even with technically-complete html, an explicit failure => infra_error
    assert oc.classify_terminal(failed=state.failed, terminal=state.terminal,
                                valid_html=ex.valid_html) == "infra_error"


# ---------------------------------------------------------------------------
# HTTP status classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code,expected", [
    (200, None),
    (429, "rate_limited"),
    (503, "rate_limited"),
    (402, "rate_limited"),
    (401, "infra_error"),
    (400, "infra_error"),
    (500, "infra_error"),
])
def test_classify_http_status(code, expected):
    assert oc.classify_http_status(code) == expected


def test_classify_http_status_quota_body_overrides():
    assert oc.classify_http_status(400, "Quota exceeded for org") == "rate_limited"
    assert oc.classify_http_status(500, "server overloaded, retry") == "rate_limited"


# ---------------------------------------------------------------------------
# send-phase exception classification
# ---------------------------------------------------------------------------


def test_classify_send_exception_connect_refused_is_unsent():
    exc = requests.exceptions.ConnectionError("Connection refused")
    assert oc.classify_send_exception(exc) == "unsent"


def test_classify_send_exception_connect_timeout_is_unsent():
    exc = requests.exceptions.ConnectTimeout("connect timed out")
    assert oc.classify_send_exception(exc) == "unsent"


def test_classify_send_exception_read_timeout_is_acceptance_unknown():
    exc = requests.exceptions.ReadTimeout("read timed out")
    assert oc.classify_send_exception(exc) == "acceptance_unknown"


def test_classify_terminal_matrix():
    assert oc.classify_terminal(failed=False, terminal=True, valid_html=True) == "valid"
    assert oc.classify_terminal(failed=False, terminal=True, valid_html=False) == "model_error"
    assert oc.classify_terminal(failed=False, terminal=False, valid_html=True) == "infra_error"
    assert oc.classify_terminal(failed=True, terminal=True, valid_html=True) == "infra_error"


# ---------------------------------------------------------------------------
# call_model wiring (no real network): fake session covering send failure,
# http error, and a full valid stream.
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code=200, lines=None, text=""):
        self.status_code = status_code
        self._lines = lines or []
        self.text = text

    def iter_lines(self, decode_unicode=False):
        for ln in self._lines:
            yield ln if not decode_unicode else (ln.decode() if isinstance(ln, bytes) else ln)

    def close(self):
        pass


class _FakeSession:
    def __init__(self, resp=None, raise_exc=None):
        self._resp = resp
        self._raise = raise_exc

    def post(self, *a, **k):
        if self._raise is not None:
            raise self._raise
        return self._resp

    def close(self):
        pass


def test_call_model_send_failure_unsent():
    sess = _FakeSession(raise_exc=requests.exceptions.ConnectionError("refused"))
    r = oc.call_model({"family": "grok", "model_id": "grok-4.5"}, "P",
                      api_key="k", base_url="http://x/v1", session=sess)
    assert r.status == "unsent"
    assert r.http_status is None


def test_call_model_http_429_rate_limited():
    sess = _FakeSession(resp=_FakeResp(status_code=429, text="Too Many Requests"))
    r = oc.call_model({"family": "grok", "model_id": "grok-4.5"}, "P",
                      api_key="k", base_url="http://x/v1", session=sess)
    assert r.status == "rate_limited"
    assert r.http_status == 429


def test_call_model_full_valid_stream():
    lines = [f"data: {json.dumps(e)}".encode() for e in [
        {"model": "grok-4.5", "choices": [{"delta": {"content": DOC[:20]}}]},
        {"choices": [{"delta": {"content": DOC[20:]}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"usage": {"prompt_tokens": 5, "completion_tokens": 30}, "choices": []},
    ]] + [b"data: [DONE]"]
    sess = _FakeSession(resp=_FakeResp(status_code=200, lines=lines))
    r = oc.call_model({"family": "grok", "model_id": "grok-4.5"}, "P",
                      api_key="k", base_url="http://x/v1", session=sess)
    assert r.status == "valid"
    assert r.served_model == "grok-4.5"
    assert r.tokens_out == 30 and r.tokens_in == 5
    assert r.finish_reason == "stop"
    assert r.html and r.html.endswith("</html>")
    assert r.http_status == 200


def test_call_model_endpoint_selection_gpt_responses():
    lines = [f"data: {json.dumps(e)}".encode() for e in [
        {"type": "response.output_text.delta", "delta": DOC},
        {"type": "response.completed",
         "response": {"model": "gpt-5.6-sol", "status": "completed",
                      "usage": {"input_tokens": 3, "output_tokens": 9}}},
    ]] + [b"data: [DONE]"]
    sess = _FakeSession(resp=_FakeResp(status_code=200, lines=lines))
    r = oc.call_model({"family": "gpt", "model_id": "gpt-5.6-sol", "effort": "medium"}, "P",
                      api_key="k", base_url="http://x/v1", session=sess)
    assert r.endpoint == "responses"
    assert r.status == "valid"
    assert r.tokens_in == 3 and r.tokens_out == 9
