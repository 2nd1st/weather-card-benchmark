"""The frozen merge domain must stay self-consistent with the channel registry.

`merged` is the number the matrix shows and the READMEs quote. It is derived from
this lock, so a lock that has silently drifted from the channel set (a channel
added, renamed, or demoted to diagnostic) would move every published figure with
nothing failing. These tests are cheap and run on whatever locks exist on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner.similarity.runner import CHANNEL_NAMES
from runner.tools.build_merge_domain_lock import (
    DIAGNOSTIC,
    HI_P,
    LO_P,
    MIN_SPAN,
    POPULATION,
    SCHEMA_ID,
    VARIANTS,
)

REPO = Path(__file__).resolve().parents[2]
LOCKS = sorted(REPO.glob("data/batches*/*/similarity/merge-domain.lock.json"))

FORMAL = [c for c in CHANNEL_NAMES if c not in DIAGNOSTIC]


def test_recipe_constants_are_the_documented_ones():
    """The amendment (COMPARISON-SCHEME-v14-AMENDMENTS §v14.2) pins these. If a
    future edit changes them, every published figure moves — so it must be a
    deliberate act that updates the doc, not a quiet constant tweak."""
    assert (LO_P, HI_P) == (0.01, 0.99)
    assert POPULATION == "cross"
    assert MIN_SPAN == 0.02


@pytest.mark.skipif(not LOCKS, reason="no merge-domain lock on disk")
@pytest.mark.parametrize("lock_path", LOCKS, ids=lambda p: p.parts[-3])
def test_lock_is_consistent_with_the_channel_registry(lock_path: Path):
    doc = json.loads(lock_path.read_text())
    assert doc["schema"] == SCHEMA_ID
    assert doc["recipe"]["percentiles"] == [LO_P, HI_P]
    assert doc["recipe"]["population"] == POPULATION

    assert doc["variants"], "a lock with no variants would silently disable merged"
    for variant, v in doc["variants"].items():
        assert variant in VARIANTS
        chans = v["channels"]
        assert chans, f"{variant}: no channels locked"
        for ch, d in chans.items():
            assert ch in FORMAL, f"{variant}: {ch} is not a formal channel"
            assert ch not in DIAGNOSTIC, f"{variant}: {ch} is diagnostic, never statted"
            assert d["hi"] - d["lo"] >= MIN_SPAN, f"{variant}/{ch}: degenerate span"
            assert 0.0 <= d["lo"] < d["hi"] <= 1.0, f"{variant}/{ch}: out of [0,1]"


@pytest.mark.skipif(not LOCKS, reason="no merge-domain lock on disk")
@pytest.mark.parametrize("lock_path", LOCKS, ids=lambda p: p.parts[-3])
def test_lock_covers_enough_channels_for_the_merge_gate(lock_path: Path):
    """A merged cell needs >=6 contributing channels (site MERGE_MIN_CH). A lock
    that dropped below that would make every merged cell read 'insufficient'."""
    doc = json.loads(lock_path.read_text())
    for variant, v in doc["variants"].items():
        assert len(v["channels"]) >= 6, f"{variant}: only {len(v['channels'])} channels locked"
