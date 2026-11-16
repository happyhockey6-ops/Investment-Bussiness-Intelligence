"""Read-only presentation of the newest SEC directory and ingestion status."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, replace
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from ibi.core.types import ConflictingEvidence, Unknown
from ibi.data_engine.sec_edgar import listings
from ibi.data_engine.sec_edgar.fixed_universe import FIXED_UNIVERSE, FixedUniverseEntity
from ibi.db.models import EntityRecord, SecurityListingRecord, SourceDocumentRecord


@dataclass(frozen=True, slots=True)
class ListingsSnapshot:
    source_document_id: int | None
    retrieval_date: str | None
    source_url: str | None
    content_sha256: str | None
    resolution_source: str
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Company:
    entity: FixedUniverseEntity
    exchange: str | None
    ingested: bool
    snapshot: ListingsSnapshot
    ingest_identifier: str

    @property
    def url(self) -> str:
        # Stable, unambiguous links even for conflicting/changed ticker symbols.
        return f"/companies/{self.entity.entity_id}"

    @property
    def ingestion_status(self) -> str:
        return "ingested" if self.ingested else "not_ingested"

    @property
    def ingest_command(self) -> str:
        value = shlex.quote(self.ingest_identifier)
        option = (
            f"--ticker={value}" if self.ingest_identifier.startswith("-") else f"--ticker {value}"
        )
        return f"python -m ibi.data_engine.sec_edgar.ingest {option}"


def _ticker_key(query: str) -> str:
    return query.strip().upper().replace(".", "-")


class Directory:
    """One request's directory view. The route supplies a consistent read transaction."""

    def __init__(self, session: Session):
        self.session = session
        document = session.scalars(
            select(SourceDocumentRecord)
            .where(
                SourceDocumentRecord.source == "SEC EDGAR",
                SourceDocumentRecord.source_url == listings.LISTINGS_URL,
            )
            .order_by(SourceDocumentRecord.retrieval_date.desc(), SourceDocumentRecord.id.desc())
            .limit(1)
        ).first()
        available = (
            document is not None
            and session.scalar(
                select(SecurityListingRecord.id)
                .where(SecurityListingRecord.source_document_id == document.id)
                .limit(1)
            )
            is not None
        )
        retrieved = document.retrieval_date if document is not None else None
        if retrieved is not None and retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=UTC)
        self.snapshot = ListingsSnapshot(
            source_document_id=document.id if document is not None else None,
            retrieval_date=retrieved.isoformat() if retrieved is not None else None,
            source_url=document.source_url if document is not None else None,
            content_sha256=document.content_sha256 if document is not None else None,
            resolution_source="sec_listings" if available else "fixed_universe",
            fallback_reason=(
                None
                if available
                else "empty_listings_snapshot"
                if document is not None
                else "missing_listings_snapshot"
            ),
        )

    def _companies(self, entities: list[tuple[FixedUniverseEntity, str | None]]) -> list[Company]:
        ids = list({entity.entity_id for entity, _ in entities})
        ingested = set()
        for offset in range(0, len(ids), 500):
            ingested.update(
                self.session.scalars(
                    select(EntityRecord.entity_id).where(
                        EntityRecord.entity_id.in_(ids[offset : offset + 500])
                    )
                )
            )
        return [
            Company(
                entity,
                exchange,
                entity.entity_id in ingested,
                self.snapshot,
                entity.ticker or entity.entity_id,
            )
            for entity, exchange in entities
        ]

    def _from_listings(self, rows: list[listings.Listing]) -> list[Company]:
        return self._companies(
            [
                (FixedUniverseEntity(row.cik, row.entity_id, row.name, row.ticker), row.exchange)
                for row in rows
            ]
        )

    def search(self, query: str) -> list[Company]:
        if self.snapshot.resolution_source == "sec_listings":
            return self._from_listings(listings.search_listings(self.session, query, limit=20))
        needle = query.strip().lower()
        return self._companies(
            [
                (entity, None)
                for entity in FIXED_UNIVERSE
                if not needle
                or needle in (entity.ticker or "").lower()
                or needle in entity.canonical_name.lower()
            ][:20]
        )

    def resolve(self, query: str) -> Company | Unknown | ConflictingEvidence:
        if self.snapshot.resolution_source == "sec_listings":
            answer = listings.resolve_ticker(self.session, query)
            if isinstance(answer, Unknown):
                return answer
            if isinstance(answer, ConflictingEvidence):
                return replace(
                    answer,
                    candidate_values=tuple(self._from_listings(list(answer.candidate_values))),
                )
            company = self._from_listings([answer])[0]
            if not company.ingested and _ticker_key(query) != _ticker_key(answer.ticker):
                # A CIK may resolve via a symbol that itself has multiple CIKs.
                # Its operator command must keep the user's unambiguous CIK.
                symbol = listings.resolve_ticker(self.session, answer.ticker)
                if not isinstance(symbol, listings.Listing) or symbol.cik != answer.cik:
                    company = replace(company, ingest_identifier=answer.entity_id)
            return company
        wanted = query.strip().upper()
        cik_text = wanted[3:] if wanted.startswith("CIK") else wanted
        cik = (
            int(cik_text)
            if cik_text.isascii() and cik_text.isdecimal() and len(cik_text) <= 10
            else None
        )
        for entity in FIXED_UNIVERSE:
            if _ticker_key(entity.ticker or "") == _ticker_key(wanted) or entity.cik == cik:
                return self._companies([(entity, None)])[0]
        return Unknown(f"No exact ticker or CIK matches {query!r} in the fixed default coverage")
