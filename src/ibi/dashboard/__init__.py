"""Presentation / natural-language interface boundary.

Will eventually expose entities, evidence, theses, decisions, and alerts
through a UI and a natural-language query interface. No UI is implemented
in Phase 0 — see `interfaces.py` for the read-only query contract this
layer will be built against, which is the same contract used internally so
the dashboard never has privileged access to unlabeled data.
"""
