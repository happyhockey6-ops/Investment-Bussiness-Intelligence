"""Idempotent ingestion orchestration: connector -> mapping -> database.

One entity's failure must not abort the batch (see ARCHITECTURE.md /
DECISIONS.md "Error handling") — `run_ingestion` isolates each entity in
its own try/except and its own DB transaction. Re-running against
unchanged source data must write zero new rows (idempotency) — enforced
here at the application level via a pre-insert existence check, backed at
the database level by the two partial unique indexes on `financial_data`
and the `UNIQUE` constraint on `filings.accession_number`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ibi.config import Settings, get_settings
from ibi.core.errors import ProviderError
from ibi.data_engine.sec_edgar.client import SecHttpClient, SecHttpClientConfig
from ibi.data_engine.sec_edgar.connector import SecEdgarConnector
from ibi.data_engine.sec_edgar.fixed_universe import FIXED_UNIVERSE, FixedUniverseEntity
from ibi.data_engine.sec_edgar.mapping import (
    FormTypeMismatchError,
    parse_companyfacts,
    parse_submissions,
)
from ibi.db.base import session_scope
from ibi.db.locks import acquire_entity_write_lock
from ibi.db.models import EntityRecord, FilingRecord, FinancialDataPointRecord, SourceDocumentRecord
from ibi.logging import get_logger, log_context

logger = get_logger(__name__)


@dataclass(slots=True)
class IngestResult:
    entity_id: str
    source_documents_written: int = 0
    filings_written: int = 0
    filings_skipped_existing: int = 0
    facts_written: int = 0
    facts_skipped_duplicate: int = 0
    error: str | None = None


@dataclass(slots=True)
class IngestSummary:
    results: list[IngestResult] = field(default_factory=list)

    @property
    def had_any_error(self) -> bool:
        return any(r.error is not None for r in self.results)


def _archive_raw_snapshot(
    data_dir: Path, entity_id: str, label: str, retrieved_at: datetime, body: dict
) -> str:
    entity_dir = data_dir / "sec_edgar" / entity_id
    entity_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{label}_{retrieved_at.strftime('%Y%m%dT%H%M%S%f')}Z.json"
    path = entity_dir / filename
    path.write_text(json.dumps(body), encoding="utf-8")
    return str(path)


def _ensure_entity(session: Session, entity: FixedUniverseEntity) -> None:
    existing = session.get(EntityRecord, entity.entity_id)
    if existing is None:
        session.add(
            EntityRecord(
                entity_id=entity.entity_id, kind="company", canonical_name=entity.canonical_name
            )
        )
        session.flush()


def ingest_entity(
    session: Session, connector: SecEdgarConnector, entity: FixedUniverseEntity, data_dir: Path
) -> IngestResult:
    result = IngestResult(entity_id=entity.entity_id)
    acquire_entity_write_lock(session, entity.entity_id)
    _ensure_entity(session, entity)

    raw_records = connector.fetch(entity.entity_id)

    bodies: dict[str, dict] = {}
    for raw in raw_records:
        label = raw.payload["kind"]
        body = raw.payload["body"]
        content_ref = _archive_raw_snapshot(
            data_dir, entity.entity_id, label, raw.provenance.retrieval_date, body
        )
        doc = SourceDocumentRecord(
            source=raw.provenance.source,
            source_tier=raw.provenance.source_tier.value,
            source_url=raw.provenance.source_url,
            retrieval_date=raw.provenance.retrieval_date,
            content_ref=content_ref,
        )
        session.add(doc)
        session.flush()
        result.source_documents_written += 1
        bodies[label] = {"body": body, "source_document_id": doc.id}

    filing_lookup = {}
    if "submissions" in bodies:
        filing_rows = parse_submissions(entity.entity_id, bodies["submissions"]["body"])
        filing_lookup = {f.accession_number: f for f in filing_rows}
        for frow in filing_rows:
            existing = session.execute(
                select(FilingRecord).where(FilingRecord.accession_number == frow.accession_number)
            ).scalar_one_or_none()
            if existing is not None:
                result.filings_skipped_existing += 1
                continue
            session.add(
                FilingRecord(
                    entity_id=frow.entity_id,
                    source=frow.source,
                    accession_number=frow.accession_number,
                    form_type=frow.form_type,
                    filing_date=frow.filing_date,
                    period_of_report=frow.period_of_report,
                    primary_document=frow.primary_document,
                    known_available_at=frow.availability.known_available_at,
                    availability_precision=frow.availability.availability_precision,
                    source_document_id=bodies["submissions"]["source_document_id"],
                    items=frow.items,
                )
            )
            result.filings_written += 1
        session.flush()

    if "companyfacts" in bodies:
        fact_rows = parse_companyfacts(
            entity.entity_id, bodies["companyfacts"]["body"], filing_lookup=filing_lookup
        )
        for frow in fact_rows:
            duplicate = session.execute(
                select(FinancialDataPointRecord).where(
                    FinancialDataPointRecord.entity_id == frow.entity_id,
                    FinancialDataPointRecord.metric_id == frow.metric_id,
                    FinancialDataPointRecord.unit == frow.unit,
                    FinancialDataPointRecord.start_date == frow.start_date,
                    FinancialDataPointRecord.period_end_date == frow.end_date,
                    FinancialDataPointRecord.accession_number == frow.accession_number,
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                result.facts_skipped_duplicate += 1
                continue
            session.add(
                FinancialDataPointRecord(
                    entity_id=frow.entity_id,
                    metric_id=frow.metric_id,
                    unit=frow.unit,
                    start_date=frow.start_date,
                    period_end_date=frow.end_date,
                    value=frow.value,
                    epistemic_label=frow.epistemic_label.value,
                    calculation_version=None,
                    accession_number=frow.accession_number,
                    known_available_at=frow.availability.known_available_at,
                    availability_precision=frow.availability.availability_precision,
                    source_document_id=bodies["companyfacts"]["source_document_id"],
                    source_form_type=frow.form_type,
                )
            )
            result.facts_written += 1

    return result


def run_ingestion(settings: Settings | None = None) -> IngestSummary:
    """Ingest the fixed universe. Each entity is isolated: one entity's
    failure is logged and recorded on its `IngestResult.error`, never
    raised out of this function, so the rest of the batch still runs."""
    settings = settings or get_settings()
    client = SecHttpClient(SecHttpClientConfig(user_agent=settings.require_sec_user_agent()))
    connector = SecEdgarConnector(client)
    data_dir = Path(settings.raw_data_dir)

    summary = IngestSummary()
    for entity in FIXED_UNIVERSE:
        try:
            with session_scope(settings) as session:
                result = ingest_entity(session, connector, entity, data_dir)
        except (ProviderError, FormTypeMismatchError) as e:
            logger.warning(
                f"sec_edgar ingestion failed for entity: {e}",
                extra=log_context(
                    operation="data_engine.sec_edgar.ingest",
                    provider="sec_edgar",
                    entity_id=entity.entity_id,
                ),
            )
            result = IngestResult(entity_id=entity.entity_id, error=str(e))
        summary.results.append(result)
    return summary
