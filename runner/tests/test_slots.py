"""R2 slot-engine tests — every §1.1 terminal-state transition + backoff
exhaustion, plus send-log schema validation.

Terminal states exercised: valid / model-failed / infra-failed /
acceptance-unknown / unreachable / rate-limited-exhausted.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest

from runner.slots import (
    AdapterResult,
    RetryPolicy,
    build_send_log,
    count_valid,
    run_position,
    slot_terminal_states,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "SCHEMA" / "send-log.schema.json"
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeAdapter:
    """Returns a scripted sequence of AdapterResults, one per ``call``."""

    def __init__(self, script: list[AdapterResult]):
        self._script = list(script)
        self.calls: list[tuple[str, dict]] = []

    def call(self, prompt, config) -> AdapterResult:
        self.calls.append((prompt, config))
        if not self._script:
            raise AssertionError("FakeAdapter script exhausted (engine over-called)")
        return self._script.pop(0)


class FakeClock:
    """Monotonic deterministic UTC clock; +1s per tick."""

    def __init__(self):
        self._t = datetime(2026, 7, 16, 0, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        cur = self._t
        self._t = self._t + timedelta(seconds=1)
        return cur


class SleepRecorder:
    def __init__(self):
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def _policy(**kw) -> RetryPolicy:
    base = dict(rate_limit_max_attempts=4, rate_limit_max_total_ms=1_000_000)
    base.update(kw)
    return RetryPolicy(**base)


def _drive(script, *, policy=None, sleep=None, variant="P-min", slot_index=0, block_index=0):
    adapter = FakeAdapter(script)
    sleep = sleep or SleepRecorder()
    pos = run_position(
        adapter,
        "PROMPT",
        {"config_id": "x"},
        variant=variant,
        slot_index=slot_index,
        block_index=block_index,
        policy=policy or _policy(),
        sleep=sleep,
        clock=FakeClock(),
    )
    return pos, adapter, sleep


# ---------------------------------------------------------------------------
# Single-attempt model-reaching terminals
# ---------------------------------------------------------------------------


def test_valid_single_attempt():
    pos, adapter, _ = _drive([AdapterResult(status="ok", html="<html></html>")])
    assert pos.terminal_state == "valid"
    assert pos.model_reaching_attempt_index == 0
    assert len(pos.attempts) == 1
    a = pos.attempts[0]
    assert a["outcome"] == "valid"
    assert a["reached_model"] is True
    assert a["charged"] is True
    assert a["reason"] == "ok"
    assert a["backoff_ms"] is None
    assert len(adapter.calls) == 1  # exactly one model-reaching request


@pytest.mark.parametrize("reason", ["no-valid-html", "refusal", "self-truncated"])
def test_model_failed(reason):
    pos, _, _ = _drive([AdapterResult(status="model_error", reason=reason)])
    assert pos.terminal_state == "model-failed"
    assert pos.model_reaching_attempt_index == 0
    a = pos.attempts[0]
    assert a["outcome"] == "model-failed"
    assert a["reached_model"] is True
    assert a["charged"] is True
    assert a["reason"] == reason


def test_infra_failed():
    pos, _, _ = _drive(
        [AdapterResult(status="infra_error", request_id="req-123", http_status=200)]
    )
    assert pos.terminal_state == "infra-failed"
    a = pos.attempts[0]
    assert a["outcome"] == "infra-failed"
    assert a["reached_model"] is True
    assert a["charged"] is True  # accepted → billed
    assert a["reason"] == "mid-stream-death"
    assert a["request_id"] == "req-123"


def test_acceptance_unknown_consumes_position_no_retry():
    # ambiguous must NOT be retried even though a rate_limited follows in script
    script = [
        AdapterResult(status="ambiguous"),
        AdapterResult(status="ok"),  # should never be consumed
    ]
    pos, adapter, _ = _drive(script)
    assert pos.terminal_state == "acceptance-unknown"
    assert len(adapter.calls) == 1  # no retry
    a = pos.attempts[0]
    assert a["reached_model"] is True
    assert a["charged"] is None  # unprovable — conservative
    assert a["reason"] == "pre-header-timeout"


# ---------------------------------------------------------------------------
# Unsent (unreachable) path — non-slot-consuming retries, cap 5
# ---------------------------------------------------------------------------


def test_unsent_then_valid_no_budget_consumed():
    script = [
        AdapterResult(status="unsent", reason="connection-refused"),
        AdapterResult(status="unsent", reason="dns-failure"),
        AdapterResult(status="ok"),
    ]
    pos, _, sleep = _drive(script)
    assert pos.terminal_state == "valid"
    assert pos.model_reaching_attempt_index == 2
    assert [a["outcome"] for a in pos.attempts] == ["unreachable", "unreachable", "valid"]
    assert all(a["reached_model"] is False for a in pos.attempts[:2])
    assert all(a["charged"] is False for a in pos.attempts[:2])
    assert sleep.waits == []  # unsent retries have no backoff


def test_unreachable_exhaustion_default_cap_5():
    script = [AdapterResult(status="unsent", reason="tls-failure") for _ in range(5)]
    pos, adapter, _ = _drive(script)
    assert pos.terminal_state == "unreachable"
    assert pos.model_reaching_attempt_index is None
    assert len(pos.attempts) == 5
    assert len(adapter.calls) == 5
    assert all(a["outcome"] == "unreachable" for a in pos.attempts)
    assert all(a["backoff_ms"] is None for a in pos.attempts)


def test_unreachable_cap_is_configurable_lower():
    policy = _policy(max_unreachable_retries=2)
    script = [AdapterResult(status="unsent") for _ in range(2)]
    pos, _, _ = _drive(script, policy=policy)
    assert pos.terminal_state == "unreachable"
    assert len(pos.attempts) == 2


# ---------------------------------------------------------------------------
# Rate-limited path — exponential backoff, dual budget
# ---------------------------------------------------------------------------


def test_rate_limited_then_valid_records_backoff():
    policy = _policy(rate_limit_max_attempts=4, backoff_base_ms=500, backoff_factor=2.0)
    script = [
        AdapterResult(status="rate_limited", reason="http-429", http_status=429),
        AdapterResult(status="rate_limited", reason="http-503-overloaded", http_status=503),
        AdapterResult(status="ok"),
    ]
    pos, _, sleep = _drive(script, policy=policy)
    assert pos.terminal_state == "valid"
    assert pos.model_reaching_attempt_index == 2
    # backoff on each rate-limited attempt that is followed by a retry:
    assert pos.attempts[0]["backoff_ms"] == 500  # 500 * 2**0
    assert pos.attempts[1]["backoff_ms"] == 1000  # 500 * 2**1
    assert pos.attempts[2]["backoff_ms"] is None  # the valid attempt
    # sleep called with seconds (ms/1000), in order:
    assert sleep.waits == [0.5, 1.0]


def test_rate_limited_exhaustion_by_attempt_count():
    policy = _policy(rate_limit_max_attempts=3, backoff_base_ms=100, rate_limit_max_total_ms=10**9)
    script = [
        AdapterResult(status="rate_limited", reason="http-429", http_status=429)
        for _ in range(3)
    ]
    pos, _, sleep = _drive(script, policy=policy)
    assert pos.terminal_state == "rate-limited-exhausted"
    assert pos.model_reaching_attempt_index is None
    assert len(pos.attempts) == 3
    # two backoffs happened (before attempts 1 and 2); the 3rd (exhausting) has none
    assert pos.attempts[0]["backoff_ms"] == 100
    assert pos.attempts[1]["backoff_ms"] == 200
    assert pos.attempts[2]["backoff_ms"] is None
    assert sleep.waits == [0.1, 0.2]


def test_rate_limited_exhaustion_by_total_time():
    # attempt-count budget is generous; total-time budget forces exhaustion.
    policy = _policy(
        rate_limit_max_attempts=100,
        rate_limit_max_total_ms=700,  # allows 500 then would need +1000 → stop
        backoff_base_ms=500,
        backoff_factor=2.0,
    )
    script = [
        AdapterResult(status="rate_limited", reason="quota-exceeded", http_status=429)
        for _ in range(10)
    ]
    pos, adapter, sleep = _drive(script, policy=policy)
    assert pos.terminal_state == "rate-limited-exhausted"
    # attempt 0: backoff 500 (total 500 <= 700, waits). attempt 1: next would be
    # 1000, total 500+1000=1500 > 700 → exhausted, no wait.
    assert len(pos.attempts) == 2
    assert pos.attempts[0]["backoff_ms"] == 500
    assert pos.attempts[1]["backoff_ms"] is None
    assert sleep.waits == [0.5]
    assert len(adapter.calls) == 2


def test_rate_limit_max_attempts_one_immediate_exhaust():
    policy = _policy(rate_limit_max_attempts=1)
    pos, _, sleep = _drive([AdapterResult(status="rate_limited")], policy=policy)
    assert pos.terminal_state == "rate-limited-exhausted"
    assert len(pos.attempts) == 1
    assert pos.attempts[0]["backoff_ms"] is None
    assert sleep.waits == []


# ---------------------------------------------------------------------------
# Mixed sequences: independent counters
# ---------------------------------------------------------------------------


def test_mixed_unsent_and_rate_limited_then_model_failed():
    policy = _policy(rate_limit_max_attempts=4, backoff_base_ms=200)
    script = [
        AdapterResult(status="unsent", reason="dns-failure"),
        AdapterResult(status="rate_limited", reason="http-429", http_status=429),
        AdapterResult(status="unsent", reason="connection-refused"),
        AdapterResult(status="model_error", reason="refusal"),
    ]
    pos, _, sleep = _drive(script, policy=policy)
    assert pos.terminal_state == "model-failed"
    assert pos.model_reaching_attempt_index == 3
    assert [a["outcome"] for a in pos.attempts] == [
        "unreachable",
        "rate-limited",
        "unreachable",
        "model-failed",
    ]
    # only the rate-limited attempt (followed by a retry) has a backoff
    assert pos.attempts[1]["backoff_ms"] == 200
    assert sleep.waits == [0.2]


# ---------------------------------------------------------------------------
# Illegal-reason guard
# ---------------------------------------------------------------------------


def test_illegal_reason_for_outcome_raises():
    with pytest.raises(ValueError):
        _drive([AdapterResult(status="model_error", reason="http-429")])


# ---------------------------------------------------------------------------
# Send-log assembly + schema validation
# ---------------------------------------------------------------------------


def _build_full_send_log():
    policy = _policy(rate_limit_max_attempts=4, rate_limit_max_total_ms=60_000, backoff_base_ms=500)
    N = 3
    positions = []
    # P-min: valid / model-failed / unreachable
    scripts_min = [
        (0, 0, [AdapterResult(status="ok", html="<html></html>")]),
        (1, 1, [AdapterResult(status="model_error", reason="no-valid-html")]),
        (2, 2, [AdapterResult(status="unsent", reason="connection-refused") for _ in range(5)]),
    ]
    # P-q: rate-limited-exhausted / acceptance-unknown / infra-failed
    scripts_q = [
        (
            0,
            0,
            [AdapterResult(status="rate_limited", reason="http-429", http_status=429) for _ in range(4)],
        ),
        (1, 1, [AdapterResult(status="ambiguous")]),
        (2, 2, [AdapterResult(status="infra_error", request_id="rq")]),
    ]
    for variant, scripts in (("P-min", scripts_min), ("P-q", scripts_q)):
        for slot_index, block_index, script in scripts:
            adapter = FakeAdapter(script)
            positions.append(
                run_position(
                    adapter,
                    "P",
                    {"config_id": "gpt-5.6-sol--api--raw--dev"},
                    variant=variant,
                    slot_index=slot_index,
                    block_index=block_index,
                    policy=policy,
                    sleep=SleepRecorder(),
                    clock=FakeClock(),
                )
            )
    send_log = build_send_log(
        batch_id="batch-dev-001",
        config_id="gpt-5.6-sol--api--raw--dev",
        N=N,
        policy=policy,
        positions=positions,
    )
    return send_log, positions


def test_send_log_validates_against_schema():
    send_log, _ = _build_full_send_log()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema).validate(send_log)


def test_send_log_shape_and_projection():
    send_log, positions = _build_full_send_log()
    assert send_log["N"] == 3
    assert send_log["retry_policy"]["max_unreachable_retries"] == 5
    assert send_log["retry_policy"]["rate_limit_backoff"]["max_attempts"] == 4
    assert len(send_log["positions"]) == 6

    terminals = {(p["variant"], p["slot_index"]): p["terminal_state"] for p in send_log["positions"]}
    assert terminals[("P-min", 0)] == "valid"
    assert terminals[("P-min", 1)] == "model-failed"
    assert terminals[("P-min", 2)] == "unreachable"
    assert terminals[("P-q", 0)] == "rate-limited-exhausted"
    assert terminals[("P-q", 1)] == "acceptance-unknown"
    assert terminals[("P-q", 2)] == "infra-failed"

    # model_reaching_attempt_index null iff unfilled terminal
    for p in send_log["positions"]:
        unfilled = p["terminal_state"] in ("unreachable", "rate-limited-exhausted")
        assert (p["model_reaching_attempt_index"] is None) == unfilled

    # meta.json projection + m = count(valid)
    proj = slot_terminal_states(positions)
    assert len(proj) == 6
    assert count_valid(positions) == 1
    assert count_valid(positions, variant="P-min") == 1
    assert count_valid(positions, variant="P-q") == 0


def test_duplicate_position_key_rejected():
    policy = _policy()
    p1 = run_position(
        FakeAdapter([AdapterResult(status="ok")]), "P", {}, variant="P-min", slot_index=0,
        block_index=0, policy=policy, sleep=SleepRecorder(), clock=FakeClock(),
    )
    p2 = run_position(
        FakeAdapter([AdapterResult(status="ok")]), "P", {}, variant="P-min", slot_index=0,
        block_index=1, policy=policy, sleep=SleepRecorder(), clock=FakeClock(),
    )
    with pytest.raises(ValueError):
        build_send_log(batch_id="b", config_id="c", N=3, policy=policy, positions=[p1, p2])
