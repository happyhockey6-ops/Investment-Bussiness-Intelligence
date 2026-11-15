"""Idempotent ingestion orchestration: connector -> mapping -> database.

One entity's failure must not abort the batch (see ARCHITECTURE.md /
DECISIONS.md "Error handling") — `run_ingestion` isolates each entity in
its own try/except and its own DB transaction. Re-running against
unchanged source data writes no new facts or filings (snapshots are still
archived per attempt). Snapshots are the exact response bytes, named and
recorded with their SHA-256 digest, and are decoded only after archival,
with Decimal-safe number parsing (`source_bytes.decode_sec_json`). Fact idempotency is enforced
here by one identity/value load per company and exact in-memory comparisons, backed at
the database level by the two partial unique indexes on `financial_data`
and the `UNIQUE` constraint on `filings.accession_number`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from ibi.config import Settings, get_settings
from ibi.core.errors import ProviderError, ValidationError
from ibi.core.types import ConflictingEvidence, Unknown
from ibi.data_engine.interfaces import RawRecord
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
from ibi.db.base import get_session_factory, session_scope
from ibi.db.locks import acquire_entity_write_lock
from ibi.db.models import EntityRecord, FilingRecord, FinancialDataPointRecord, SourceDocumentRecord
from ibi.logging import get_logger, log_context

logger = get_logger(__name__)

if TYPE_CHECKING:
    from ibi.data_engine.sec_edgar.listings import Listing
    from ibi.financial_engine.results_store import BuildOutcome

BATCH_SIZE = 500
_FACT_IDENTITY = (
    "entity_id",
    "metric_id",
    "unit",
    "start_date",
    "period_end_date",
    "accession_number",
)


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
    accession_number: str | None
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
    entity_id: str | None
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
    ticker: str | None = None
    listing_source_document_id: int | None = None
    resolution_evidence: tuple[Listing, ...] = ()
    failure_stage: Literal["resolution", "fetch", "ingestion", "metrics"] | None = None
    ingestion_committed: bool = False
    metrics_status: Literal["not_requested", "not_attempted", "success", "failed"] = "not_requested"
    metric_builds: dict[str, BuildOutcome] = field(default_factory=dict)


@dataclass(slots=True)
class IngestSummary:
    results: list[IngestResult] = field(default_factory=list)

    @property
    def had_any_error(self) -> bool:
        return any(r.status != "success" or r.error is not None for r in self.results)


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
    data_dir: Path,
    directory: str,
    label: str,
    retrieved_at: datetime,
    raw: bytes,
    expected_sha256: str | None = None,
) -> ArchivedSnapshot:
    """Archive the exact response bytes and prove they were stored intact.

    `directory` is a company identifier or `_reference` for SEC-wide data.
    The digest is computed from the bytes we received, compared with the
    connector's digest when it supplied one, and checked again by reading
    the file back. The file name carries a digest prefix, so two different
    responses can never share a name.
    """
    digest = sha256_hex(raw)
    if expected_sha256 is not None and expected_sha256 != digest:
        raise MalformedSecPayloadError(
            f"{directory} {label}: connector digest {expected_sha256} does not match "
            f"the received bytes ({digest})"
        )
    entity_dir = data_dir / "sec_edgar" / directory
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


def _fetch_records(connector: SecEdgarConnector, entity_id: str) -> list[RawRecord]:
    """Fully fetch both responses before opening the company's write session."""
    try:
        records = list(connector.fetch(entity_id))
    except (json.JSONDecodeError, UnicodeDecodeError, SourceDecodeError) as error:
        raise MalformedSecPayloadError(f"{entity_id}: invalid SEC JSON: {error}") from error
    if not records:
        raise ProviderError(f"No SEC snapshots available for {entity_id}")
    return records


def _insert_batches(session: Session, model, rows: list[dict]) -> None:
    # One multi-VALUES INSERT per batch, including nullable instant/duration
    # fields. No per-row SELECT, INSERT/RETURNING, or conflict suppression.
    for offset in range(0, len(rows), BATCH_SIZE):
        session.execute(insert(model.__table__).values(rows[offset : offset + BATCH_SIZE]))


def _fact_key(values) -> tuple:
    return tuple(values[column] for column in _FACT_IDENTITY)


def _write_facts(session: Session, rows, source_id: int, snapshot_ref: str, result: IngestResult):
    model = FinancialDataPointRecord
    existing: dict[tuple, list] = {}
    for stored in session.execute(
        select(
            *(getattr(model, column) for column in _FACT_IDENTITY),
            model.id,
            model.value,
            model.source_document_id,
        )
        .where(model.entity_id == result.entity_id)
        .order_by(model.id)
    ).mappings():
        # Nullable legacy accessions can evade a SQL unique index. Do not
        # discard any value when building the in-memory identity lookup.
        existing.setdefault(_fact_key(stored), []).append(stored)
    pending: dict[tuple, dict] = {}
    for row in rows:
        values = dict(
            entity_id=row.entity_id,
            metric_id=row.metric_id,
            unit=row.unit,
            start_date=row.start_date,
            period_end_date=row.end_date,
            value=row.value,
            epistemic_label=row.epistemic_label.value,
            calculation_version=None,
            accession_number=row.accession_number,
            known_available_at=row.availability.known_available_at,
            availability_precision=row.availability.availability_precision,
            source_document_id=source_id,
            source_form_type=row.form_type,
        )
        key = _fact_key(values)
        duplicates = existing.get(key, [])
        if not duplicates and key in pending:
            duplicates = [pending[key]]
        if not duplicates:
            pending[key] = values
            continue
        # Every stored value must agree, not just whichever row a dict kept.
        duplicate = next(
            (
                old
                for old in duplicates
                if old["value"] is None or Decimal(old["value"]) != row.value
            ),
            duplicates[0],
        )
        old_value = Decimal(duplicate["value"]) if duplicate["value"] is not None else None
        if old_value is None or old_value != row.value:
            if "id" not in duplicate:
                # Only the exceptional within-payload conflict needs a staged
                # fact ID. Keep the same evidence contract without per-row reads
                # or inventing an ID for a row that has not reached the DB.
                _insert_batches(session, model, list(pending.values()))
                duplicate = (
                    session.execute(
                        select(model.id, model.source_document_id).where(
                            *(getattr(model, column) == values[column] for column in _FACT_IDENTITY)
                        )
                    )
                    .mappings()
                    .one()
                )
            raise DuplicateFactConflict(
                FactConflict(
                    entity_id=row.entity_id,
                    metric_id=row.metric_id,
                    unit=row.unit,
                    start_date=row.start_date,
                    period_end_date=row.end_date,
                    accession_number=row.accession_number,
                    existing_fact_id=duplicate["id"],
                    existing_value=old_value,
                    incoming_value=row.value,
                    existing_source_document_id=duplicate["source_document_id"],
                    incoming_snapshot_ref=snapshot_ref,
                )
            )
        result.facts_skipped_duplicate += 1
    _insert_batches(session, model, list(pending.values()))
    result.facts_written = len(pending)


def ingest_entity(
    session: Session,
    connector: SecEdgarConnector,
    entity: FixedUniverseEntity,
    data_dir: Path,
    *,
    snapshot_refs: list[str] | None = None,
    raw_records: Sequence[RawRecord] | None = None,
    source_notes: str | None = None,
) -> IngestResult:
    """Stage one company in the caller's transaction; never commit here.

    `run_ingestion` owns commit/rollback. Its snapshot list retains evidence
    even if this function or the subsequent commit fails. Batch callers pass
    already-fetched records. The legacy direct-call form fetches before this
    function takes a lock or performs SQL; its caller owns the session lifetime.
    """
    result = IngestResult(
        entity_id=entity.entity_id,
        raw_snapshot_refs=snapshot_refs if snapshot_refs is not None else [],
    )
    raw_records = (
        _fetch_records(connector, entity.entity_id) if raw_records is None else raw_records
    )
    if not raw_records:
        raise ProviderError(f"No SEC snapshots available for {entity.entity_id}")
    acquire_entity_write_lock(session, entity.entity_id)
    _ensure_entity(session, entity)

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
            data_dir,
            entity.entity_id,
            label,
            raw.provenance.retrieval_date,
            raw_bytes,
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
            notes=source_notes,
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
        existing = set(
            session.scalars(
                select(FilingRecord.accession_number).where(
                    FilingRecord.entity_id == entity.entity_id
                )
            )
        )
        pending_filings = []
        for frow in filing_rows:
            if frow.accession_number in existing:
                result.filings_skipped_existing += 1
                continue
            existing.add(frow.accession_number)
            pending_filings.append(
                dict(
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
        _insert_batches(session, FilingRecord, pending_filings)

    if "companyfacts" in bodies:
        fact_rows = _parse_rows(
            entity.entity_id,
            "companyfacts",
            bodies["companyfacts"]["body"],
            filing_lookup=filing_lookup,
        )
        _write_facts(
            session,
            fact_rows,
            bodies["companyfacts"]["source_document_id"],
            bodies["companyfacts"]["content_ref"],
            result,
        )

    session.flush()
    return result


def _build_metrics(entity_id: str, settings: Settings) -> dict[str, BuildOutcome]:
    from ibi.financial_engine import results_store

    # One separate transaction for the company's two builds. Return outcomes
    # only after COMMIT, so a failed second build cannot advertise the first.
    with session_scope(settings) as session:
        outcomes = {
            metric: results_store.build_generation(
                session, entity_id, metric, results_store.VersionPair("1", "1")
            )
            for metric in ("ibi:gross_margin", "ibi:free_cash_flow")
        }
    return outcomes


def run_ingestion(
    settings: Settings | None = None,
    companies: Sequence[FixedUniverseEntity] | None = None,
    *,
    tickers: Sequence[str] | None = None,
    build_metrics: bool = False,
) -> IngestSummary:
    """Ingest explicit companies/tickers or, by default, FIXED_UNIVERSE.

    The legacy Python call remains ingestion-only; the CLI enables metric
    builds. Resolver failures, fetch failures, writes and commits are isolated
    per request. Both fetches finish before the company's write session/lock.
    Metric builds commit separately; a build failure retains committed facts.
    Interruptions/cancellation propagate. No name-search fallback is allowed.
    """
    if companies is not None and tickers is not None:
        raise ValueError("Supply companies or tickers, not both")
    settings = settings or get_settings()
    client = SecHttpClient(SecHttpClientConfig(user_agent=settings.require_sec_user_agent()))
    connector = SecEdgarConnector(client)
    data_dir = Path(settings.raw_data_dir)

    summary = IngestSummary()
    requests = (
        [(None, query) for query in tickers]
        if tickers is not None
        else [(company, None) for company in (FIXED_UNIVERSE if companies is None else companies)]
    )
    for entity, query in requests:
        snapshot_refs: list[str] = []
        resolution = None
        stage = "resolution" if query is not None else "fetch"
        try:
            if query is not None:
                # listings uses the archive helper in this module. Import here
                # to avoid making that shared archive dependency circular.
                from ibi.data_engine.sec_edgar.listings import resolve_ticker

                with get_session_factory(settings)() as session:
                    resolution = resolve_ticker(session, query)
                if isinstance(resolution, (Unknown, ConflictingEvidence)):
                    summary.results.append(
                        IngestResult(
                            entity_id=None,
                            ticker=query,
                            status="failed",
                            failure_stage="resolution",
                            error_type=type(resolution).__name__,
                            error=resolution.reason,
                            resolution_evidence=(
                                resolution.candidate_values
                                if isinstance(resolution, ConflictingEvidence)
                                else ()
                            ),
                            metrics_status="not_attempted" if build_metrics else "not_requested",
                        )
                    )
                    continue
                entity = FixedUniverseEntity(
                    cik=resolution.cik,
                    entity_id=resolution.entity_id,
                    canonical_name=resolution.name,
                    ticker=resolution.ticker,
                )
            stage = "fetch"
            raw_records = _fetch_records(connector, entity.entity_id)
            stage = "ingestion"
            with session_scope(settings) as session:
                result = ingest_entity(
                    session,
                    connector,
                    entity,
                    data_dir,
                    snapshot_refs=snapshot_refs,
                    raw_records=raw_records,
                    source_notes=(
                        json.dumps(
                            {
                                "ticker_or_cik": query,
                                "listing_source_document_id": resolution.source_document_id,
                                "resolved_cik": resolution.cik,
                                "resolved_ticker": resolution.ticker,
                            },
                            sort_keys=True,
                        )
                        if resolution is not None
                        else None
                    ),
                )
            result.ingestion_committed = True
        except (MemoryError, InterruptedError):
            raise
        except Exception as e:
            # This is the batch boundary, outside session_scope: failed writes
            # have rolled back before recording an outcome or starting a new session.
            message = str(e) or type(e).__name__
            logger.warning(
                "sec_edgar ingestion failed (%s): %s",
                type(e).__name__,
                message,
                extra=log_context(
                    operation="data_engine.sec_edgar.ingest",
                    provider="sec_edgar",
                    entity_id=entity.entity_id if entity is not None else None,
                ),
                exc_info=True,
            )
            result = IngestResult(
                entity_id=entity.entity_id if entity is not None else None,
                status="failed",
                error_type=type(e).__name__,
                failure_stage=stage,
                error=message,
                raw_snapshot_refs=snapshot_refs,
                conflict=e.conflict if isinstance(e, DuplicateFactConflict) else None,
            )
        result.ticker = query if query is not None else entity.ticker
        if resolution is not None:
            result.listing_source_document_id = resolution.source_document_id
            result.resolution_evidence = (resolution,)
        if build_metrics:
            result.metrics_status = "not_attempted"
            if result.ingestion_committed:
                try:
                    result.metric_builds = _build_metrics(entity.entity_id, settings)
                    result.metrics_status = "success"
                except (MemoryError, InterruptedError):
                    raise
                except Exception as error:
                    result.status = "failed"
                    result.failure_stage = "metrics"
                    result.metrics_status = "failed"
                    result.error_type = type(error).__name__
                    result.error = str(error) or type(error).__name__
        summary.results.append(result)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest SEC companies and build gross margin/FCF")
    parser.add_argument(
        "--ticker",
        action="append",
        dest="tickers",
        help="Exact ticker or CIK; repeat for multiple companies",
    )
    args = parser.parse_args(argv)
    try:
        summary = run_ingestion(tickers=args.tickers, build_metrics=True)
    except RuntimeError as error:
        print(f"SEC ingestion configuration failed: {error}", file=sys.stderr)
        return 1
    for result in summary.results:
        print(
            f"{result.ticker or result.entity_id}: {result.status} "
            f"entity_id={result.entity_id} ingestion_committed={result.ingestion_committed} "
            f"listing_source_document_id={result.listing_source_document_id} "
            f"facts_written={result.facts_written} filings_written={result.filings_written} "
            f"metrics={result.metrics_status} stage={result.failure_stage or '-'} "
            f"{result.error_type or ''} {result.error or ''}"
        )
        for metric, outcome in result.metric_builds.items():
            print(
                f"  {metric}: {outcome.status} generation_id={outcome.generation_id} "
                f"versions=1,1 rows_written={outcome.rows_written}"
            )
    return 1 if summary.had_any_error else 0


if __name__ == "__main__":
    sys.exit(main())
