"""Schema smoke test: every model in `ibi.db.models` must be creatable and
round-trip a row. Runs against an in-memory SQLite engine rather than a
live PostgreSQL instance so it needs no external service — it is checking
that the SQLAlchemy model definitions themselves are consistent (valid
foreign keys, no naming collisions), not PostgreSQL-specific behavior. See
test_postgres_connection.py for the real-database check.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ibi.db.base import Base
from ibi.db.models import EntityRecord, EventRecord, SourceDocumentRecord


def test_all_models_create_and_roundtrip():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        entity = EntityRecord(entity_id="acme", kind="company", canonical_name="ACME Corp")
        source = SourceDocumentRecord(
            source="SEC EDGAR",
            source_tier="primary_regulatory",
            retrieval_date=datetime.now(UTC),
        )
        session.add_all([entity, source])
        session.flush()

        event = EventRecord(
            entity_id=entity.entity_id,
            kind="earnings_release",
            occurred_at=datetime.now(UTC),
            description="Q1 earnings released",
            source_document_id=source.id,
        )
        session.add(event)
        session.commit()

        fetched = session.get(EntityRecord, "acme")
        assert fetched is not None
        assert fetched.canonical_name == "ACME Corp"
