"""Idempotent ingestion orchestration: connector -> mapping -> database.

One entity's failure must not abort the batch (see ARCHITECTURE.md /
DECISIONS.md "Error handling") — `run_ingestion` isolates each entity in
its own try/except and its own DB transaction. Re-running against
unchanged source data writes no new facts or filings (snapshots are still
archived per attempt). Snapshots are the exact response bytes, named and
recorded with their SHA-256 digest, and are decoded only after archival,
with Decimal-safe number parsing (`source_bytes.decode_sec_json`). Fact idempotency is enforced
here at the application level via a pre-insert existence check, backed at
the database level by the two partial unique indexes on `financial_data`
and the `UNIQUE` constraint on `filings.accession_number`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ibi.config import Settings, get_settings
from ibi.core.errors import ProviderError, ValidationError
from ibi.data_engine.sec_edgar.client import SecHttpClient, SecHttpClientConfig
from ibi.data_engine.sec_edgar.connector import SecEdgarConnector
from ibi.data_engine.sec_edgar.fixed_universe import FIXED_UNIVERSE, FixedUniverseEntity
from ibi.data_engine.sec_edgar.mapping import (
    FormTypeMismatchError,
    parse_companyfacts,
    parse_submissions,
)
from ibi.data_engine.sec_edgar.source_bytes import (
    SourceDecodeError,
    decode_sec_json,
    sha256_hex,
)
from ibi.db.base import session_scope
from ibi.db.locks import acquire_entity_write_lock
from ibi.db.models import EntityRecord, FilingRecord, FinancialDataPointRecord, SourceDocumentRecord
from ibi.logging import get_logger, log_context

logger = get_logger(__name__)


class MalformedSecPayloadError(ValidationError):
    """A fetched SEC document cannot be safely normalized."""


class RawSnapshotWriteError(ProviderError):
    """A source snapshot could not be written; no DB references may commit."""


@dataclass(frozen=True, slots=True)
class FactConflict:
    """Evidence captured before rollback.

    Existing ids may describe rows staged earlier in this same transaction;
    only already-committed rows keep their ids after rollback. The incoming
    snapshot holds both entries for a conflict within one payload.
    """
    entity_id: str
    metric_id: str
    unit: str
    start_date: date | None
    period_end_date: date
    accession_number: str
    existing_fact_id: int
    existing_value: Decimal | None
    incoming_value: Decimal
    existing_source_document_id: int | None
    incoming_snapshot_ref: str
    """The snapshot survives rollback; a new DB source-document id would not."""


class DuplicateFactConflict(ValidationError):
    """One fact identity reports two values; never pick or overwrite one."""

    def __init__(self, conflict: FactConflict):
        self.conflict = conflict
        super().__init__(
            f"{conflict.entity_id} {conflict.metric_id} {conflict.unit} "
            f"{conflict.start_date}..{conflict.period_end_date} "
            f"accession {conflict.accession_number}: fact {conflict.existing_fact_id} "
            f"has {conflict.existing_value}, incoming value is {conflict.incoming_value}; "
            f"snapshot: {conflict.incoming_snapshot_ref}"
        )


@dataclass(slots=True)
class IngestResult:
    entity_id: str
    source_documents_written: int = 0
    filings_written: int = 0
    filings_skipped_existing: int = 0
    facts_written: int = 0
    facts_skipped_duplicate: int = 0
    error: str | None = None
    status: Literal["success", "failed"] = "success"
    error_type: str | None = None
    raw_snapshot_refs: list[str] = field(default_factory=list)
    conflict: FactConflict | None = None


@dataclass(slots=True)
class IngestSummary:
    results: list[IngestResult] = field(default_factory=list)

    @property
    def had_any_error(self) -> bool:
        return any(r.error is not None for r in self.results)


@dataclass(frozen=True, slots=True)
class ArchivedSnapshot:
    content_ref: str
    sha256: str
    byte_length: int


def _write_new_file(path: Path, data: bytes) -> None:
    """Create `path` with exactly `data`; never overwrite an existing file.

    Written to a sibling `.partial` file, fsynced, then hard-linked into
    place, so a reader never sees a half-written snapshot and an existing
    snapshot can never be replaced (the link fails if the name exists).
    """
    partial = path.with_name(path.name + ".partial")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def _archive_raw_snapshot(
    data_dir: Path, entity_id: str, label: str, retrieved_at: datetime, raw: bytes,
    expected_sha256: str | None = None,
) -> ArchivedSnapshot:
    """Archive the exact response bytes and prove they were stored intact.

    The digest is computed from the bytes we received, compared with the
    connector's digest when it supplied one, and checked again by reading
    the file back. The file name carries a digest prefix, so two different
    responses can never share a name.
    """
    digest = sha256_hex(raw)
    if expected_sha256 is not None and expected_sha256 != digest:
        raise MalformedSecPayloadError(
            f"{entity_id} {label}: connector digest {expected_sha256} does not match "
            f"the received bytes ({digest})"
        )
    entity_dir = data_dir / "sec_edgar" / entity_id
    filename = f"{label}_{retrieved_at.strftime('%Y%m%dT%H%M%S%f')}Z_{digest[:16]}.json"
    path = entity_dir / filename
    try:
        entity_dir.mkdir(parents=True, exist_ok=True)
        _write_new_file(path, raw)
        stored = path.read_bytes()
    except InterruptedError:
        raise
    except OSError as e:
        raise RawSnapshotWriteError(f"Cannot write {label} snapshot {path}: {e}") from e
    if sha256_hex(stored) != digest:
        raise RawSnapshotWriteError(f"{label} snapshot {path} does not read back intact")
    return ArchivedSnapshot(content_ref=str(path), sha256=digest, byte_length=len(raw))


def _decode_body(entity_id: str, label: str, raw: bytes) -> object:
    try:
        return decode_sec_json(raw)
    except SourceDecodeError as e:
        raise MalformedSecPayloadError(f"{entity_id} {label}: {e}") from e


def _parse_rows(entity_id: str, label: str, body: dict, filing_lookup=None):
    """Validate the containers the mapper consumes, then classify parsing errors.

    Missing containers must not turn into an apparently successful empty ingest.
    Archival has already happened, so malformed documents remain inspectable.
    """
    try:
        if not isinstance(body, dict):
            raise ValueError("document must be a JSON object")
        if label == "submissions":
            recent = body["filings"]["recent"]
            if not isinstance(recent, dict) or not isinstance(recent["accessionNumber"], list):
                raise ValueError("filings.recent.accessionNumber must be an array")
            count = len(recent["accessionNumber"])
            required = ("accessionNumber", "filingDate", "form")
            optional = ("acceptanceDateTime", "reportDate", "primaryDocument", "items")
            for key in (*required, *(k for k in optional if k in recent)):
                if not isinstance(recent[key], list) or len(recent[key]) != count:
                    raise ValueError(f"filings.recent.{key} must have {count} entries")
            return parse_submissions(entity_id, body)
        facts = body["facts"]
        if not isinstance(facts, dict):
            raise ValueError("facts must be an object")
        for tags in facts.values():
            for tag_data in tags.values():
                units = tag_data["units"]
                if not isinstance(units, dict) or any(
                    not isinstance(entries, list) for entries in units.values()
                ):
                    raise ValueError("fact units must map units to arrays of entries")
        rows = parse_companyfacts(entity_id, body, filing_lookup=filing_lookup)
        if any(not row.value.is_finite() for row in rows):
            raise ValueError("fact values must be finite numbers")
        return rows
    except FormTypeMismatchError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, DecimalException) as e:
        if isinstance(e, DecimalException):
            detail = "invalid numeric fact value"
        elif isinstance(e, KeyError):
            detail = f"missing required field {e}"
        else:
            detail = str(e)
        raise MalformedSecPayloadError(
            f"{entity_id} {label}: malformed SEC payload: {detail}"
        ) from e


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
    session: Session, connector: SecEdgarConnector, entity: FixedUniverseEntity, data_dir: Path,
    *, snapshot_refs: list[str] | None = None,
) -> IngestResult:
    """Stage one company in the caller's transaction; never commit here.

    `run_ingestion` owns commit/rollback. Its snapshot list retains evidence
    even if this function or the subsequent commit fails.
    """
    result = IngestResult(
        entity_id=entity.entity_id,
        raw_snapshot_refs=snapshot_refs if snapshot_refs is not None else [],
    )
    acquire_entity_write_lock(session, entity.entity_id)
    _ensure_entity(session, entity)

    try:
        raw_records = connector.fetch(entity.entity_id)
    except (json.JSONDecodeError, UnicodeDecodeError, SourceDecodeError) as e:
        raise MalformedSecPayloadError(f"{entity.entity_id}: invalid SEC JSON: {e}") from e
    if not raw_records:
        raise ProviderError(f"No SEC snapshots available for {entity.entity_id}")

    bodies: dict[str, dict] = {}
    for raw in raw_records:
        try:
            label = raw.payload["kind"]
            raw_bytes = raw.payload["raw_bytes"]
            if label not in ("submissions", "companyfacts") or label in bodies:
                raise ValueError(f"unexpected or repeated snapshot kind: {label!r}")
            if not isinstance(raw_bytes, bytes):
                raise TypeError("raw_bytes must be the exact response bytes")
        except (KeyError, TypeError, ValueError) as e:
            raise MalformedSecPayloadError(f"{entity.entity_id}: malformed SEC record: {e}") from e
        # Archive first: a response that later fails to decode is still evidence.
        archived = _archive_raw_snapshot(
            data_dir, entity.entity_id, label, raw.provenance.retrieval_date, raw_bytes,
            expected_sha256=raw.payload.get("sha256"),
        )
        result.raw_snapshot_refs.append(archived.content_ref)
        doc = SourceDocumentRecord(
            source=raw.provenance.source,
            source_tier=raw.provenance.source_tier.value,
            source_url=raw.provenance.source_url,
            retrieval_date=raw.provenance.retrieval_date,
            content_ref=archived.content_ref,
            content_sha256=archived.sha256,
            content_length=archived.byte_length,
        )
        session.add(doc)
        session.flush()
        result.source_documents_written += 1
        bodies[label] = {
            "body": _decode_body(entity.entity_id, label, raw_bytes),
            "source_document_id": doc.id,
            "content_ref": archived.content_ref,
        }

    filing_lookup = {}
    if "submissions" in bodies:
        filing_rows = _parse_rows(entity.entity_id, "submissions", bodies["submissions"]["body"])
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
        fact_rows = _parse_rows(
            entity.entity_id, "companyfacts", bodies["companyfacts"]["body"],
            filing_lookup=filing_lookup,
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
                if duplicate.value is None or Decimal(duplicate.value) != frow.value:
                    raise DuplicateFactConflict(FactConflict(
                        entity_id=frow.entity_id, metric_id=frow.metric_id, unit=frow.unit,
                        start_date=frow.start_date, period_end_date=frow.end_date,
                        accession_number=frow.accession_number, existing_fact_id=duplicate.id,
                        existing_value=(Decimal(duplicate.value) if duplicate.value is not None
                                        else None),
                        incoming_value=frow.value,
                        existing_source_document_id=duplicate.source_document_id,
                        incoming_snapshot_ref=bodies["companyfacts"]["content_ref"],
                    ))
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

    session.flush()
    return result


def run_ingestion(settings: Settings | None = None) -> IngestSummary:
    """Ingest the fixed universe. Each entity is isolated: one entity's
    failure is logged and recorded after rollback, including commit failures.
    Interruptions/cancellation propagate. Global configuration is checked
    before starting the batch; company exceptions never become silent skips.
    """
    settings = settings or get_settings()
    client = SecHttpClient(SecHttpClientConfig(user_agent=settings.require_sec_user_agent()))
    connector = SecEdgarConnector(client)
    data_dir = Path(settings.raw_data_dir)

    summary = IngestSummary()
    for entity in FIXED_UNIVERSE:
        snapshot_refs: list[str] = []
        try:
            with session_scope(settings) as session:
                result = ingest_entity(
                    session, connector, entity, data_dir, snapshot_refs=snapshot_refs
                )
        except (MemoryError, InterruptedError):
            raise
        except Exception as e:
            # This is the batch boundary, outside session_scope: failed writes
            # have rolled back before recording an outcome or starting a new session.
            message = str(e) or type(e).__name__
            logger.warning(
                "sec_edgar ingestion failed (%s): %s", type(e).__name__, message,
                extra=log_context(
                    operation="data_engine.sec_edgar.ingest",
                    provider="sec_edgar",
                    entity_id=entity.entity_id,
                ),
                exc_info=True,
            )
            result = IngestResult(
                entity_id=entity.entity_id, status="failed", error_type=type(e).__name__,
                error=message, raw_snapshot_refs=snapshot_refs,
                conflict=e.conflict if isinstance(e, DuplicateFactConflict) else None,
            )
        summary.results.append(result)
    return summary
