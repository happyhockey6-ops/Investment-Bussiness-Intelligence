"""SEC EDGAR ingestion — Phase 1's first real `data_engine` source.

Every module here is either pure (no I/O: `holidays.py`, `availability.py`,
`mapping.py`) or a thin, swappable boundary (`client.py`, `connector.py`,
`ingest.py`). No financial calculation, no AI interpretation, no market
data, and no vendor beyond SEC lives in this package — see ARCHITECTURE.md.
"""
