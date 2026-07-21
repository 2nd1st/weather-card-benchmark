"""The pre-registered hypothesis family for the §5 randomization test (frozen into
the lock BEFORE any p is emitted — scheme §5/§11; CONTRACT-NOTES §9).

The family = the **15 formal channels** (scheme §4; c-ncd / d-tagpath permanently
excluded from all statistics and hypothesis families) + a **locked L1 scalar list**.

FLAG (task S — propose + lock): the scheme fixes the family as "15 formal channels
∪ L1 scalar set" but does NOT enumerate which L1 scalars. This module PROPOSES the
five §3 visual scalars named verbatim in the stats schema's ``l1_scalar`` example
— colorfulness / brightness / contrast / whitespace_ratio / frame_change_median —
as the locked L1 list. It is FLAGGED for the scheme owner to freeze into the lock
(scheme §11); nothing here is authoritative until locked.
"""
from __future__ import annotations

# The 15 FORMAL channels, in the stats.schema.json formalChannel enum order
# (scheme §4). The 2 diagnostics (c-ncd, d-tagpath) are NOT here — permanently
# excluded from statistics and hypothesis families.
FORMAL_CHANNELS: list[str] = [
    "v-phash", "v-dhash", "v-color", "v-palette", "v-layout", "v-edge", "v-ssim",
    "c-shingle", "c-winnow", "c-feature", "c-ast-js", "c-css-prop",
    "d-geom", "d-text", "d-pqgram",
]

# PROPOSED locked L1 scalar list (FLAGGED for lock). Each id maps to a slot
# meta.json ``l1`` path (see load.py:scalar_of). Names match the stats schema
# l1_scalar description verbatim.
L1_SCALARS: list[str] = [
    "colorfulness",
    "brightness",
    "contrast",
    "whitespace_ratio",
    "frame_change_median",
]


def channel_target(channel: str) -> dict:
    """hypothesisTarget object (schema $defs) for a formal-channel hypothesis."""
    return {"kind": "channel", "channel": channel, "l1_scalar": None}


def l1_target(scalar: str) -> dict:
    """hypothesisTarget object for an L1-scalar hypothesis."""
    return {"kind": "l1-scalar", "channel": None, "l1_scalar": scalar}


def pooled_hypothesis_id(target: dict) -> str:
    """Stable pooled hypothesis_id (level=pool × h). MUST appear in holm_family.

    FLAGGED for lock: the id STRING scheme is this contract's design (scheme fixes
    that ids exist + are locked, not their spelling). seed_h derives from NFC(id),
    so the spelling is load-bearing for reproducibility once locked.
    """
    if target["kind"] == "channel":
        return f"H-pooled-channel-{target['channel']}"
    return f"H-pooled-l1-{target['l1_scalar']}"


def exploratory_hypothesis_id(config_id: str, target: dict) -> str:
    """Stable per-config exploratory hypothesis_id (level=config × config × h).
    NOT part of holm_family (exploratory only; no MC, exact enumeration)."""
    if target["kind"] == "channel":
        return f"H-exploratory-{config_id}-channel-{target['channel']}"
    return f"H-exploratory-{config_id}-l1-{target['l1_scalar']}"


def pooled_family() -> list[dict]:
    """The ordered pooled hypothesis family: 15 channels then the L1 list. Each
    entry is ``{"target": ..., "hypothesis_id": ...}``. Order is deterministic and
    fixed; holm_family = [e["hypothesis_id"] for e in this]."""
    fam: list[dict] = []
    for ch in FORMAL_CHANNELS:
        t = channel_target(ch)
        fam.append({"target": t, "hypothesis_id": pooled_hypothesis_id(t)})
    for sc in L1_SCALARS:
        t = l1_target(sc)
        fam.append({"target": t, "hypothesis_id": pooled_hypothesis_id(t)})
    return fam
