"""Valuation approach boundary.

Principle this package encodes: there is no single universal valuation
formula appropriate for every asset or sector. A DCF is inappropriate for a
pre-revenue biotech; EV/EBITDA is unreliable for a bank; asset-based
valuation dominates for a REIT or holding company. `interfaces.py` defines
one contract that every approach implements so `scoring` and
`decision_engine` can consume valuation results uniformly, without needing
to know which approach(es) produced them.
"""
