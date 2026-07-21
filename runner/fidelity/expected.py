"""Frozen expected-value snapshot for the devset (scheme §1.2 期望值表随 extractor 版本落盘).

The devset-42 corpus was rendered at ONE canonical fixture — Berlin
(52.52, 13.405) @ 2026-07-16 (== UTC "today"), against the frozen trial
``api-cache`` (scheme §8 batch directory freezes a snapshot hash). The expected
values below are the ground truth every card is judged against; they are DERIVED
from that api-cache, not from any card:

  * daily:  temperature_2m_max = 28.5 °C, temperature_2m_min = 18.6 °C,
            weather_code = 3  → WMO "Overcast".
  * hourly: temperature_2m for 2026-07-16 00:00..23:00 UTC (24 values).

These are the majority (36-file) values of the frozen trial cache for
lat=52.52,lon=13.4 (verified R5). A small number of cache-overlay entries
(fetched fresh on a devset-build cache miss) returned different live data
(max=30.1, or a shifted hourly series); cards that happened to render against
those overlay responses will legitimately read out as ``mismatch`` here — that
is a real property of the dev fixture, not an extractor error, and is surfaced
in the informational SUMMARY.

The expected table is versioned WITH the extractor: bump ``EXTRACTOR_VERSION``
whenever any expected value, pattern, or vocabulary changes (scheme §11 lock).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

# Base extractor version — the algorithm/pattern/vocabulary revision. A derived,
# batch-specific ``extractor_version`` STAMPS the frozen expected-value table it
# judges against onto this base (scheme §1.2 期望值表随 extractor 版本落盘 / §11 lock).
EXTRACTOR_BASE = "fidelity-extractor-v1"

# extractor_version — encodes both the algorithm revision AND the frozen
# expected-value fixture it judges against (scheme §11: frozen into lock). Kept
# as the devset-42 default; batch runs stamp their own via ``extractor_version_for``.
EXTRACTOR_VERSION = "fidelity-extractor-v1+berlin-2026-07-16"


@dataclass(frozen=True)
class ExpectedSnapshot:
    """One card's expected ground truth (scheme §3 fields)."""

    name: str
    day: date  # expected UTC calendar day
    weather_code: int  # WMO code → condition text via the vocab table
    temp_max: Decimal
    temp_min: Decimal
    hourly: tuple[Decimal, ...] = field(default=())  # UTC hour 0..23

    def __post_init__(self) -> None:
        if len(self.hourly) not in (0, 24):
            raise ValueError("hourly expected must be empty or exactly 24 values")


# Canonical devset expected snapshot.
DEVSET_EXPECTED = ExpectedSnapshot(
    name="Berlin",
    day=date(2026, 7, 16),
    weather_code=3,  # Overcast
    temp_max=Decimal("28.5"),
    temp_min=Decimal("18.6"),
    hourly=tuple(
        Decimal(v)
        for v in (
            "20.8", "20.2", "20.0", "19.4", "19.0", "18.9", "18.6", "18.7",
            "19.1", "19.9", "21.1", "22.5", "23.2", "24.7", "25.8", "26.7",
            "27.7", "28.1", "28.5", "28.5", "28.3", "27.6", "26.5", "25.2",
        )
    ),
)


# --------------------------------------------------------------------------- #
# Generalized derivation from a batch weather-snapshot.json (scheme §1.2)
# --------------------------------------------------------------------------- #
def expected_from_snapshot(snapshot: dict[str, Any], location_index: int = 0) -> ExpectedSnapshot:
    """Derive one location's expected ground truth from a frozen
    ``weather-snapshot.json`` (scheme §1.2: 期望值表 derived from the snapshot,
    not hardcoded). Reads the pinned canary block — the exact contract-field
    values (max/min/weather_code + 24 hourly) that were frozen for the batch.

    The canary carries display-precision decimal STRINGS already (run_batch's
    canonical-decimal encoding), so ``Decimal`` is exact — no float ingress."""
    loc = snapshot["locations"][location_index]
    canary = loc["canary"]
    daily = canary["daily"]
    hourly = canary.get("hourly_temperature_2m", []) or []
    return ExpectedSnapshot(
        name=str(loc["city"]),
        day=date.fromisoformat(str(loc["date"])),
        weather_code=int(daily["weather_code"]),
        temp_max=Decimal(str(daily["temperature_2m_max"])),
        temp_min=Decimal(str(daily["temperature_2m_min"])),
        hourly=tuple(Decimal(str(v)) for v in hourly),
    )


def extractor_version_for(snapshot: dict[str, Any], location_index: int = 0) -> str:
    """Batch-specific extractor_version stamp: ``<base>+<city>-<date>`` (scheme
    §11 lock). Identifies WHICH frozen expected table a verdict was judged against;
    frozen into the batch directory alongside the verdicts."""
    loc = snapshot["locations"][location_index]
    return f"{EXTRACTOR_BASE}+{loc['city']}-{loc['date']}"
