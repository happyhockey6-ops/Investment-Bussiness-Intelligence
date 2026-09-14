"""Point-in-time simulation boundary.

Hard requirement (see ARCHITECTURE.md): for any simulated historical
decision, only information that would actually have been available at that
simulated timestamp may be used. This package's job is to make violating
that requirement structurally hard, not merely documented — see
`PointInTimeDataset` in `interfaces.py`, whose only read method takes an
``as_of`` timestamp and is contractually forbidden from returning anything
with a later `retrieval_date` (see `ibi.core.epistemics.Provenance`).

No backtest runner is implemented in Phase 0.
"""
