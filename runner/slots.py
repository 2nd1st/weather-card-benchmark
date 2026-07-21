"""R2 — slot engine (COMPARISON-SCHEME-v12 §1.1, v12.1; DECISIONS-M0 §6).

Six terminal states for a slot POSITION (config × variant × slot_index k):

    valid | model-failed | infra-failed | acceptance-unknown
        | unreachable | rate-limited-exhausted

Ruling model (DECISIONS-M0 §6 / CONTRACT-NOTES): every config × variant has N
slot POSITIONS k=0..N-1. A position gets **at most one model-reaching request**.
The four model-reaching terminals (valid / model-failed / infra-failed /
acceptance-unknown) FILL the position, consuming the cost budget (cost bound =
model-reaching requests ≤ N). ``unreachable`` and ``rate-limited-exhausted`` are
the SAME positions left UNFILLED after non-slot-consuming retries exhausted.

Non-slot-consuming retries (scheme §1.1) are permitted on exactly two paths:

  (a) provably-UNSENT — connect-refused / DNS / TLS failure BEFORE the request
      write. Immediate retry, no backoff, cap = ``max_unreachable_retries`` (5).
      Exhausted → terminal ``unreachable``.
  (b) provably-UNBILLED pre-model rejection — HTTP 429 / quota-exceeded /
      503-overloaded with a DETERMINISTIC uncharged signal. Exponential backoff,
      frozen budget (``max_attempts`` + ``max_total_ms``). Exhausted → terminal
      ``rate-limited-exhausted``.

Ambiguous cases (bytes sent, acceptance/billing unprovable) are NEVER retried:
they fall to ``acceptance-unknown`` and consume the position (scheme §1.1
conservative rule — 已发送即可能计费).

Every attempt is recorded in the per-config send-log (send-log.schema.json),
keyed by ``(variant, slot_index)``. ``m = count(valid)``.

Timestamps are OBSERVATIONAL (scheme §0) — excluded from every verify hash.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Adapter interface (R3 implements concrete vendor adapters against this shape)
# ---------------------------------------------------------------------------

# The six adapter statuses map onto the slot engine's per-attempt outcomes.
_ADAPTER_STATUSES = ("ok", "model_error", "infra_error", "ambiguous", "unsent", "rate_limited")


@dataclass
class AdapterResult:
    """One attempt's result from a vendor adapter.

    ``status`` is the load-bearing classification (see ``_ADAPTER_STATUSES``).
    ``reason`` / ``http_status`` / ``request_id`` are optional finer evidence
    forwarded into the send-log; when ``reason`` is omitted a status-default is
    used. ``html`` / ``telemetry`` / ``served_model`` / ``raw_error`` are the
    payload + observational context.
    """

    status: str
    html: Optional[str] = None
    telemetry: dict[str, Any] = field(default_factory=dict)
    served_model: Optional[str] = None
    raw_error: Optional[str] = None
    # Optional finer send-log evidence:
    reason: Optional[str] = None
    http_status: Optional[int] = None
    request_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in _ADAPTER_STATUSES:
            raise ValueError(
                f"AdapterResult.status={self.status!r} not in {_ADAPTER_STATUSES}"
            )


@runtime_checkable
class Adapter(Protocol):
    """A vendor client: one call == one model-reaching *attempt* (or a proof of
    not-sent / uncharged-refusal)."""

    def call(self, prompt: str, config: dict[str, Any]) -> AdapterResult: ...


# ---------------------------------------------------------------------------
# Status → outcome / terminal maps (scheme §1.1 + send-log.schema.json enums)
# ---------------------------------------------------------------------------

# Per-attempt outcome enum (send-log.schema.json: attempts[].outcome).
_STATUS_TO_OUTCOME = {
    "ok": "valid",
    "model_error": "model-failed",
    "infra_error": "infra-failed",
    "ambiguous": "acceptance-unknown",
    "unsent": "unreachable",
    "rate_limited": "rate-limited",
}

# The four model-reaching outcomes FILL the position (consume the cost budget).
_MODEL_REACHING = frozenset({"valid", "model-failed", "infra-failed", "acceptance-unknown"})

# charged: True = provably billed, False = provably uncharged, None = unprovable.
_STATUS_TO_CHARGED = {
    "ok": True,
    "model_error": True,
    "infra_error": True,
    "ambiguous": None,  # acceptance-unknown: may have billed → conservative null
    "unsent": False,
    "rate_limited": False,
}

# Default reason per status when the adapter does not supply a finer one.
_STATUS_TO_DEFAULT_REASON = {
    "ok": "ok",
    "model_error": "no-valid-html",
    "infra_error": "mid-stream-death",
    "ambiguous": "pre-header-timeout",
    "unsent": "connection-refused",
    "rate_limited": "http-429",
}

# Reason enum allowed per per-attempt outcome (send-log.schema.json reason enum).
_ALLOWED_REASONS = {
    "valid": frozenset({"ok"}),
    "model-failed": frozenset({"no-valid-html", "refusal", "self-truncated"}),
    "infra-failed": frozenset({"mid-stream-death"}),
    "acceptance-unknown": frozenset({"pre-header-timeout"}),
    "unreachable": frozenset({"connection-refused", "dns-failure", "tls-failure"}),
    "rate-limited": frozenset({"http-429", "quota-exceeded", "http-503-overloaded"}),
}

_TERMINAL_STATES = frozenset(
    {
        "valid",
        "model-failed",
        "infra-failed",
        "acceptance-unknown",
        "unreachable",
        "rate-limited-exhausted",
    }
)


# ---------------------------------------------------------------------------
# Retry policy (frozen per batch — scheme §1.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryPolicy:
    """Frozen batch-wide retry budgets (scheme §1.1 / appendix A).

    Only ``max_unreachable_retries`` (const 5) and the rate-limit
    ``max_attempts`` / ``max_total_ms`` are serialized into the send-log
    (send-log.schema.json ``retry_policy`` is ``additionalProperties:false``).
    The backoff *shape* parameters are engine-local and not part of the frozen
    send-log record.
    """

    rate_limit_max_attempts: int
    rate_limit_max_total_ms: int
    max_unreachable_retries: int = 5  # scheme §1.1: 未发送重试上限 5 (schema const 5)
    backoff_base_ms: int = 500
    backoff_factor: float = 2.0
    backoff_max_single_ms: Optional[int] = None  # None → no per-step cap

    def __post_init__(self) -> None:
        if self.max_unreachable_retries < 1:
            raise ValueError("max_unreachable_retries must be >= 1")
        if self.rate_limit_max_attempts < 1:
            raise ValueError("rate_limit_max_attempts must be >= 1")
        if self.rate_limit_max_total_ms < 0:
            raise ValueError("rate_limit_max_total_ms must be >= 0")
        if self.backoff_base_ms < 0:
            raise ValueError("backoff_base_ms must be >= 0")
        if self.backoff_factor < 1.0:
            raise ValueError("backoff_factor must be >= 1.0")

    def backoff_ms(self, rate_limited_attempt_n: int) -> int:
        """Backoff (ms) to wait AFTER the ``n``-th rate-limited attempt (1-based),
        i.e. before attempt n+1. Exponential: base * factor**(n-1)."""
        raw = self.backoff_base_ms * (self.backoff_factor ** (rate_limited_attempt_n - 1))
        val = int(raw)
        if self.backoff_max_single_ms is not None:
            val = min(val, self.backoff_max_single_ms)
        return val

    def to_send_log_dict(self) -> dict[str, Any]:
        return {
            "max_unreachable_retries": self.max_unreachable_retries,
            "rate_limit_backoff": {
                "max_attempts": self.rate_limit_max_attempts,
                "max_total_ms": self.rate_limit_max_total_ms,
            },
        }


# ---------------------------------------------------------------------------
# Position result
# ---------------------------------------------------------------------------

_VARIANTS = frozenset({"P-min", "P-q"})


@dataclass
class SlotPosition:
    """Terminal record for one slot POSITION = (variant, slot_index)."""

    variant: str
    slot_index: int
    block_index: int
    terminal_state: str
    model_reaching_attempt_index: Optional[int]
    attempts: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "slot_index": self.slot_index,
            "block_index": self.block_index,
            "terminal_state": self.terminal_state,
            "model_reaching_attempt_index": self.model_reaching_attempt_index,
            "attempts": self.attempts,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Slot engine — drive one position to a terminal state
# ---------------------------------------------------------------------------


def run_position(
    adapter: Adapter,
    prompt: str,
    config: dict[str, Any],
    *,
    variant: str,
    slot_index: int,
    block_index: int,
    policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = _utc_now,
) -> SlotPosition:
    """Drive a single slot position to one of the six terminal states.

    At most ONE model-reaching request is issued. Non-slot-consuming retries are
    limited to provably-unsent (cap ``max_unreachable_retries``) and
    provably-uncharged pre-model refusals (exponential backoff within the frozen
    budget). Ambiguous outcomes terminate immediately as ``acceptance-unknown``.
    """
    if variant not in _VARIANTS:
        raise ValueError(f"variant={variant!r} not in {sorted(_VARIANTS)}")
    if slot_index < 0:
        raise ValueError("slot_index must be >= 0")
    if block_index < 0:
        raise ValueError("block_index must be >= 0")

    attempts: list[dict[str, Any]] = []
    attempt_index = 0
    unreachable_count = 0
    rate_limited_count = 0
    total_backoff_ms = 0
    terminal_state: Optional[str] = None
    model_reaching_attempt_index: Optional[int] = None

    while True:
        started = clock()
        result = adapter.call(prompt, config)
        ended = clock()

        status = result.status  # validated in AdapterResult.__post_init__
        outcome = _STATUS_TO_OUTCOME[status]
        reached_model = outcome in _MODEL_REACHING
        charged = _STATUS_TO_CHARGED[status]
        reason = result.reason if result.reason is not None else _STATUS_TO_DEFAULT_REASON[status]
        if reason not in _ALLOWED_REASONS[outcome]:
            raise ValueError(
                f"reason={reason!r} illegal for outcome={outcome!r}; "
                f"allowed={sorted(_ALLOWED_REASONS[outcome])}"
            )

        attempt: dict[str, Any] = {
            "attempt_index": attempt_index,
            "outcome": outcome,
            "reached_model": reached_model,
            "charged": charged,
            "reason": reason,
            "http_status": result.http_status,
            "backoff_ms": None,  # set below only when a backoff actually follows
            "request_id": result.request_id,
            "started_at": _iso(started),
            "ended_at": _iso(ended),
        }
        attempts.append(attempt)

        # ---- model-reaching → fills the position, terminal immediately ----
        if reached_model:
            terminal_state = outcome
            model_reaching_attempt_index = attempt_index
            break

        # ---- provably-unsent → immediate non-billing retry, cap 5 ----
        if status == "unsent":
            unreachable_count += 1
            if unreachable_count >= policy.max_unreachable_retries:
                terminal_state = "unreachable"
                break
            attempt_index += 1
            continue

        # ---- provably-uncharged rate-limit → exponential backoff budget ----
        # status == "rate_limited"
        rate_limited_count += 1
        if rate_limited_count >= policy.rate_limit_max_attempts:
            # attempt-count budget spent — no further wait, exhausted
            terminal_state = "rate-limited-exhausted"
            break
        next_backoff = policy.backoff_ms(rate_limited_count)
        if total_backoff_ms + next_backoff > policy.rate_limit_max_total_ms:
            # total-time budget would be exceeded — stop before waiting
            terminal_state = "rate-limited-exhausted"
            break
        attempt["backoff_ms"] = next_backoff
        total_backoff_ms += next_backoff
        sleep(next_backoff / 1000.0)
        attempt_index += 1

    assert terminal_state in _TERMINAL_STATES
    return SlotPosition(
        variant=variant,
        slot_index=slot_index,
        block_index=block_index,
        terminal_state=terminal_state,
        model_reaching_attempt_index=model_reaching_attempt_index,
        attempts=attempts,
    )


# ---------------------------------------------------------------------------
# Send-log assembly + meta.json terminal-state projection
# ---------------------------------------------------------------------------


def build_send_log(
    *,
    batch_id: str,
    config_id: str,
    N: int,
    policy: RetryPolicy,
    positions: list[SlotPosition],
    allow_dev_n: bool = False,
) -> dict[str, Any]:
    """Assemble a send-log.json document (send-log.schema.json) from positions.

    Enforces ``(variant, slot_index)`` composite uniqueness (schema is
    runner-enforced on this key). ``allow_dev_n`` widens N to >=2 for DEV batches
    (the schema keeps the [3,4,5] enum, so a dev N=2 send-log fails validation by
    design — documented in the batch CONFORMANCE-REPORT).
    """
    _valid_n = (2, 3, 4, 5) if allow_dev_n else (3, 4, 5)
    if N not in _valid_n:
        raise ValueError(f"N={N} not in {_valid_n}")
    seen: set[tuple[str, int]] = set()
    pos_dicts: list[dict[str, Any]] = []
    for p in positions:
        key = (p.variant, p.slot_index)
        if key in seen:
            raise ValueError(f"duplicate position key {key!r}")
        seen.add(key)
        pos_dicts.append(p.to_dict())
    return {
        "batch_id": batch_id,
        "config_id": config_id,
        "N": N,
        "retry_policy": policy.to_send_log_dict(),
        "positions": pos_dicts,
    }


def slot_terminal_states(positions: list[SlotPosition]) -> list[dict[str, Any]]:
    """Per-slot terminal-state projection for meta.json (one row per position)."""
    return [
        {
            "variant": p.variant,
            "slot_index": p.slot_index,
            "terminal_state": p.terminal_state,
        }
        for p in positions
    ]


def count_valid(positions: list[SlotPosition], *, variant: Optional[str] = None) -> int:
    """m = count(valid) (scheme §1.1). Optionally scoped to one variant."""
    return sum(
        1
        for p in positions
        if p.terminal_state == "valid" and (variant is None or p.variant == variant)
    )
