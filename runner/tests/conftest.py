"""Shared test guards.

Some tests are anchored to corpora the repo deliberately does NOT publish — the
42-card devset, the trial API cache, the full unified batch. In this repo they
are present and the tests are real. In the public mirror they are absent, and
without a guard they turn into a screenful of FileNotFoundError — for a
contributor whose only crime was following the README's `pytest -q` setup check.

An absent corpus is a SKIP. A wrong value is still a failure: nothing here
weakens an assertion, it only decides whether the test can run at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

DEVSET_CARDS = REPO / "data" / "batches-dev" / "devset-42" / "cards"
TRIAL_API_CACHE = REPO / "trial-20260715" / "api-cache"
UNIFIED_SNAPSHOT = (
    REPO / "data" / "batches-dev" / "2026-07-19--unified" / "weather-snapshot.json"
)


def requires(*paths: Path):
    """Skip marker for tests that need unpublished corpora present on disk."""
    missing = [p for p in paths if not p.exists()]
    return pytest.mark.skipif(
        bool(missing),
        reason="corpus not present (not shipped publicly): "
        + ", ".join(str(p.relative_to(REPO)) for p in missing),
    )


requires_devset = requires(DEVSET_CARDS)
requires_trial_cache = requires(TRIAL_API_CACHE)
requires_unified_snapshot = requires(UNIFIED_SNAPSHOT)
