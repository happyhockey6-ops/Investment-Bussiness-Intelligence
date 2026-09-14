"""Decision state boundary.

Principle this package encodes: the platform improves investment decision
quality, not a BUY/SELL signal generator. No automatic trade execution is,
or will be, part of this project. `interfaces.py` defines the decision
states; the reasoning that arrives at one (combining `scoring`, `valuation`,
`thesis_engine`, and `red_team` output) is Phase 1+ work.
"""
