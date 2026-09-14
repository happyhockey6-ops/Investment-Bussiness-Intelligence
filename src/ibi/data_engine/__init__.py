"""Data ingestion boundary.

Owns bringing external source data (filings, prices, news, macro releases)
into the platform as immutable, versioned, provenance-tagged records. No
ingestion pipeline is implemented in Phase 0 — see `interfaces.py` for the
connector contract every future source-specific ingester will implement.
"""
