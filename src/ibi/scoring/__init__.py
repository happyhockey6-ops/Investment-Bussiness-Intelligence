"""Dynamic scoring boundary.

Principle this package encodes: there is no single universal static score.
Weights depend on asset class, sector, industry, business model, investment
horizon, market regime, and data availability. `interfaces.py` defines the
shape of a weighting scheme and a scorer; Phase 0 does not implement any
concrete weighting policy or the full scoring pipeline.
"""
