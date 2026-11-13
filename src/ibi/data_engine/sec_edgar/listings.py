"""SEC-wide security listings: exact bytes, pure parsing and snapshot reads.

    python -m ibi.data_engine.sec_edgar.listings refresh

Listing snapshots identify securities; they do not ingest company facts.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ibi.config import Settings, get_settings
from ibi.core.errors import IBIError, ProviderError, ValidationError
from ibi.core.types import ConflictingEvidence, Unknown
from ibi.data_engine.sec_edgar.client import SecHttpClient, SecHttpClientConfig
from ibi.data_engine.sec_edgar.ingest import RawSnapshotWriteError, _archive_raw_snapshot
from ibi.data_engine.sec_edgar.source_bytes import SourceDecodeError, decode_sec_json, sha256_hex
from ibi.db.base import session_scope
from ibi.db.locks import acquire_entity_write_lock
from ibi.db.models import SecurityListingRecord, SourceDocumentRecord

LISTINGS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FIELDS = ["cik", "name", "ticker", "exchange"]


class MalformedListingsError(ValidationError):
    """An archived listing document cannot be normalized safely."""

    def __init__(self, message: str, *, snapshot_ref: str | None = None):
        self.snapshot_ref = snapshot_ref
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FetchedListings:
    raw_bytes: bytes
    sha256: str
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class Listing:
    cik: int
    name: str
    ticker: str
    raw_ticker: str
    exchange: str | None
    source_document_id: int | None = None

    @property
    def entity_id(self) -> str:
        return f"CIK{self.cik:010d}"


def fetch_listings(
    settings: Settings | None = None, *, client: SecHttpClient | None = None
) -> FetchedListings:
    """Fetch untouched response bytes using the normal SEC contact rules."""
    settings = settings or get_settings()
    contact = settings.require_sec_user_agent()
    client = client or SecHttpClient(SecHttpClientConfig(user_agent=contact))
    raw = client.get_bytes(LISTINGS_URL)
    if isinstance(raw, Unknown):
        raise ProviderError(f"SEC listings unavailable: {raw.reason}")
    return FetchedListings(raw, sha256_hex(raw), datetime.now(UTC))


def parse_listings(raw: bytes) -> tuple[Listing, ...]:
    """Pure, strict parsing. The caller must archive bytes before calling."""
    try:
        body = decode_sec_json(raw)
    except SourceDecodeError as error:
        raise MalformedListingsError(str(error)) from error
    if not isinstance(body, dict) or body.get("fields") != FIELDS:
        raise MalformedListingsError(f"fields must be exactly {FIELDS}")
    rows = body.get("data")
    if not isinstance(rows, list):
        raise MalformedListingsError("data must be an array")
    listings = []
    identities = set()
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(FIELDS):
            raise MalformedListingsError(f"row {index}: expected four fields")
        cik, name, ticker, exchange = row
        if type(cik) is not int or not 0 < cik <= 9_999_999_999:
            raise MalformedListingsError(f"row {index}: CIK must be a positive ten-digit integer")
        if not isinstance(ticker, str) or not ticker.strip():
            raise MalformedListingsError(f"row {index}: ticker must be a non-empty string")
        if not isinstance(name, str):
            raise MalformedListingsError(f"row {index}: name must be a string")
        if exchange is not None and not isinstance(exchange, str):
            raise MalformedListingsError(f"row {index}: exchange must be a string or null")
        normalized = ticker.strip().upper()
        if (normalized, cik) in identities:
            raise MalformedListingsError(f"row {index}: repeated ticker/CIK identity")
        identities.add((normalized, cik))
        listings.append(Listing(cik, name, normalized, ticker, exchange))
    return tuple(listings)


@dataclass(frozen=True, slots=True)
class RefreshResult:
    status: Literal["created", "unchanged"]
    source_document_id: int
    listings_written: int
    snapshot_ref: str


def _latest_snapshot():
    # A delayed older fetch must not replace a newer observation. IDs break
    # retrieval-time ties. Failed imports never publish a source-document row.
    return (
        select(SourceDocumentRecord)
        .where(
            SourceDocumentRecord.source == "SEC EDGAR",
            SourceDocumentRecord.source_url == LISTINGS_URL,
        )
        .order_by(SourceDocumentRecord.retrieval_date.desc(), SourceDocumentRecord.id.desc())
        .limit(1)
    )


def refresh_listings(session: Session, data_dir: Path, snapshot: FetchedListings) -> RefreshResult:
    """Stage one complete snapshot atomically; caller owns the outer commit.

    Use a session.begin()/session_scope() context and roll back on errors.
    Failed decoding/insertion leaves exact archived bytes, never partial rows.
    Repeated bytes write nothing, including no new source-document row.
    """
    acquire_entity_write_lock(session, "sec_edgar:_reference:security_listings")
    if snapshot.sha256 != sha256_hex(snapshot.raw_bytes):
        raise MalformedListingsError("fetched digest does not match listings bytes")
    latest = session.scalars(_latest_snapshot()).first()
    if latest is not None and latest.content_sha256 == snapshot.sha256:
        try:
            stored = Path(latest.content_ref).read_bytes()
        except InterruptedError:
            raise
        except OSError as error:
            raise RawSnapshotWriteError(
                f"Cannot verify existing listings archive: {error}"
            ) from error
        if len(stored) != latest.content_length or sha256_hex(stored) != latest.content_sha256:
            raise RawSnapshotWriteError(
                "Existing listings archive failed digest/length verification"
            )
        return RefreshResult("unchanged", latest.id, 0, latest.content_ref)
    archived = _archive_raw_snapshot(
        data_dir,
        "_reference",
        "company_tickers_exchange",
        snapshot.retrieved_at,
        snapshot.raw_bytes,
        expected_sha256=snapshot.sha256,
    )
    try:
        rows = parse_listings(snapshot.raw_bytes)
    except MalformedListingsError as error:
        raise MalformedListingsError(
            f"{error}; snapshot: {archived.content_ref}", snapshot_ref=archived.content_ref
        ) from error
    document = SourceDocumentRecord(
        source="SEC EDGAR",
        source_tier="primary_regulatory",
        source_url=LISTINGS_URL,
        retrieval_date=snapshot.retrieved_at,
        content_ref=archived.content_ref,
        content_sha256=archived.sha256,
        content_length=archived.byte_length,
    )
    session.add(document)
    session.flush()
    session.add_all(
        [
            SecurityListingRecord(
                source_document_id=document.id,
                cik=row.cik,
                entity_id=row.entity_id,
                ticker=row.ticker,
                raw_ticker=row.raw_ticker,
                name=row.name,
                exchange=row.exchange,
            )
            for row in rows
        ]
    )
    session.flush()
    return RefreshResult("created", document.id, len(rows), archived.content_ref)


def _listing(row: SecurityListingRecord) -> Listing:
    return Listing(
        row.cik, row.name, row.ticker, row.raw_ticker, row.exchange, row.source_document_id
    )


def _current_rows():
    latest_id = _latest_snapshot().with_only_columns(SourceDocumentRecord.id).scalar_subquery()
    return select(SecurityListingRecord).where(
        SecurityListingRecord.source_document_id == latest_id
    )


def _ticker_key(query: str) -> str:
    return query.strip().upper().replace(".", "-")


def resolve_ticker(session: Session, query: str | int) -> Listing | Unknown | ConflictingEvidence:
    """Resolve only an exact ticker or CIK in the newest snapshot.

    Tickers ignore case and treat dots/hyphens as equivalent. CIKs accept
    integers or one to ten ASCII digits, optionally prefixed with CIK.
    Names and partial matches belong exclusively to search_listings;
    no exact match returns Unknown. Multiple CIKs for one exact ticker
    return ConflictingEvidence, never an arbitrary company.
    """
    if type(query) is int:
        query = str(query)
    elif not isinstance(query, str):
        return Unknown("Listing query must be a ticker string or an integer CIK")
    query = query.strip()
    if not query:
        return Unknown("Empty listing query")
    cik = re.fullmatch(r"(?:CIK)?([0-9]{1,10})", query, re.IGNORECASE)
    match = (
        SecurityListingRecord.cik == int(cik.group(1))
        if cik
        else func.replace(SecurityListingRecord.ticker, ".", "-") == _ticker_key(query)
    )
    with session.no_autoflush:
        candidates = session.scalars(
            _current_rows()
            .where(match)
            .order_by(SecurityListingRecord.ticker, SecurityListingRecord.cik)
        ).all()
    if not candidates:
        return Unknown(f"No listing matches {query!r} in the newest SEC snapshot")
    if len({row.cik for row in candidates}) > 1:
        return ConflictingEvidence(
            reason=f"Listing query {query!r} maps to multiple CIKs in one SEC snapshot",
            candidate_values=tuple(_listing(row) for row in candidates),
        )
    return _listing(candidates[0])


def search_listings(session: Session, query: str, limit: int = 20) -> list[Listing]:
    """Case-insensitive literal substring search, restricted to one snapshot."""
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    query = query.strip()
    if not query:
        return []
    ticker = func.replace(SecurityListingRecord.ticker, ".", "-")
    stmt = (
        _current_rows()
        .where(
            or_(
                ticker.contains(_ticker_key(query), autoescape=True),
                func.lower(SecurityListingRecord.name).contains(query.lower(), autoescape=True),
            )
        )
        .order_by(
            case((ticker == _ticker_key(query), 0), else_=1),
            SecurityListingRecord.ticker,
            SecurityListingRecord.cik,
        )
        .limit(limit)
    )
    with session.no_autoflush:
        return [_listing(row) for row in session.scalars(stmt)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["refresh"])
    parser.parse_args(argv)
    try:
        settings = get_settings()
        snapshot = fetch_listings(settings)
        with session_scope(settings) as session:
            outcome = refresh_listings(session, Path(settings.raw_data_dir), snapshot)
    except (IBIError, RuntimeError, SQLAlchemyError) as error:
        print(f"Listings refresh failed ({type(error).__name__}): {error}", file=sys.stderr)
        return 1
    print(
        f"{outcome.status}: source_document_id={outcome.source_document_id}, "
        f"listings_written={outcome.listings_written}, snapshot={outcome.snapshot_ref}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
