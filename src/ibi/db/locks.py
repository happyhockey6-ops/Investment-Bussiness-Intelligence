"""Per-entity write serialization.

Ingestion (`data_engine.sec_edgar.ingest`) and metric-generation builds
(`financial_engine.results_store`) both take the same transaction-scoped
PostgreSQL advisory lock for an entity, so fact ids for one entity are
committed in order and a generation's id watermark identifies exactly the
facts it saw. On SQLite (tests) this is a no-op: SQLite already serializes
writers database-wide.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.orm import Session


def entity_lock_key(entity_id: str) -> int:
    """Deterministic signed 64-bit key (PostgreSQL advisory locks take bigint)."""
    digest = hashlib.sha256(f"ibi:entity:{entity_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def acquire_entity_write_lock(session: Session, entity_id: str) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": entity_lock_key(entity_id)}
    )
