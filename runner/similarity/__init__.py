"""L2 similarity channels (scheme §4, appendix A).

Each channel module exposes:

    compute(card_a_artifacts, card_b_artifacts) -> {"s": float | None, ...diagnostics}

per the methodology law. Zero-vector / empty-set / empty-bag on either side
(double-empty included) yields ``s = None`` (§4).
"""
