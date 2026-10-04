"""Declarative base and engine/session factories.

Session-per-unit-of-work: callers use `session_scope()` to get a session
that commits on success and rolls back on any exception, rather than
managing commit/rollback ad hoc at each call site.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeEngine

from ibi.config import Settings, get_settings


class Base(DeclarativeBase):
    """Shared declarative base for every model in `ibi.db.models`."""


def portable_json() -> TypeEngine:
    """JSON column type that renders as JSONB on PostgreSQL — binary storage,
    indexable, supports containment queries — and falls back to generic JSON
    on every other dialect (SQLite in tests). Use this instead of importing
    `sqlalchemy.JSON` directly in any model with a JSON-typed column."""
    return JSON().with_variant(JSONB(), "postgresql")


class TimestampMixin:
    """`created_at` is set once, at insert, and never updated — consistent
    with the platform's immutable-raw-data principle. A correction is a new
    row, not an update to this one.

    It is set by the application clock (UTC), not by a database default.
    Every table uses this same clock, which is what lets system-time
    comparisons across tables (e.g. Phase 2B's "what did the system answer
    at time S" reads) stay consistent; it is not a commit timestamp."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


def build_engine(settings: Settings | None = None):
    settings = settings or get_settings()
    return create_engine(settings.database_url, future=True)


_SessionFactory: sessionmaker | None = None


def get_session_factory(settings: Settings | None = None) -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=build_engine(settings), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
