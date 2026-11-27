"""FastAPI application: company search and the fundamentals research page.

Read-only: no route writes to the database or calls an external service.
The local default is loopback. See docs/deploy.md for single-user tailnet access.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ibi.api import render
from ibi.api.directory import Company, Directory
from ibi.core.types import ConflictingEvidence, Unknown
from ibi.data_engine.sec_edgar.fixed_universe import FixedUniverseEntity
from ibi.db.base import get_session_factory
from ibi.financial_engine.resolver import canonical_decimal
from ibi.research.fundamentals import LINE_ITEMS, Fundamentals, annual_fundamentals

SessionFactory = Callable[[], Session]


def _jsonable(f: Fundamentals, entity: FixedUniverseEntity) -> dict:
    def exact(value):
        if is_dataclass(value):
            return exact(asdict(value))
        if isinstance(value, Decimal):
            return canonical_decimal(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: exact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [exact(v) for v in value]
        return value

    return {
        "company": {
            "ticker": entity.ticker,
            "name": entity.canonical_name,
            "cik": entity.cik,
            "entity_id": entity.entity_id,
        },
        "as_of": f.as_of.isoformat(),
        "statement_version": f.statement_version,
        "non_reliance_coverage": f.non_reliance_coverage,
        "basis": "As reported in annual 10-K/10-K/A filings; facts only, no estimates.",
        "fiscal_years": [
            {"label": y.label, "start": y.start.isoformat(), "end": y.end.isoformat()}
            for y in f.years
        ],
        "line_items": [
            {
                "key": item.key,
                "label": item.label,
                "statement": item.statement.value,
                "unit": item.unit,
                "values": {y.end.isoformat(): exact(f.rows[item.key][y.end]) for y in f.years},
            }
            for item in LINE_ITEMS
        ],
        "derived": {
            metric: {end.isoformat(): exact(c) for end, c in cells.items()}
            for metric, cells in f.derived.items()
        },
    }


def _company_json(company: Company) -> dict:
    return {
        "ticker": company.entity.ticker,
        "name": company.entity.canonical_name,
        "cik": company.entity.cik,
        "entity_id": company.entity.entity_id,
        "exchange": company.exchange,
        "ingested": company.ingested,
        "ingestion_status": company.ingestion_status,
        "url": company.url,
        "listings_snapshot": asdict(company.snapshot),
    }


def create_app(session_factory: SessionFactory | None = None) -> FastAPI:
    app = FastAPI(title="Trusty", version="0.1.0", docs_url="/api/docs", redoc_url=None)

    @contextmanager
    def session() -> Iterator[Session]:
        s = (session_factory or get_session_factory())()
        try:
            # Resolution, fallback decisions, status and provenance must see
            # one snapshot even if another process refreshes the directory.
            if s.get_bind().dialect.name == "postgresql":
                s.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                s.execute(text("SET TRANSACTION READ ONLY"))
            elif s.get_bind().dialect.name == "sqlite":
                s.connection().exec_driver_sql("BEGIN")
            with s.no_autoflush:
                yield s
        finally:
            s.rollback()  # read-only: never commit anything a route may have staged
            s.close()

    def validate_as_of(as_of: datetime | None):
        if as_of is not None and as_of.tzinfo is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/api/companies")
    def api_search(response: Response, q: str = "") -> list[dict]:
        with session() as s:
            directory = Directory(s)
            # Keep the existing list contract. Even zero-result responses
            # identify their searched snapshot through response headers.
            response.headers["X-Listings-Source-Document-ID"] = str(
                directory.snapshot.source_document_id or "none"
            )
            response.headers["X-Listings-Retrieval-Date"] = (
                directory.snapshot.retrieval_date or "unknown"
            )
            response.headers["X-Resolution-Source"] = directory.snapshot.resolution_source
            return [_company_json(company) for company in directory.search(q)]

    @app.get("/api/companies/{ticker}/fundamentals")
    def api_fundamentals(ticker: str, as_of: datetime | None = None) -> JSONResponse:
        validate_as_of(as_of)
        with session() as s:
            directory = Directory(s)
            answer = directory.resolve(ticker)
            snapshot = {"listings_snapshot": asdict(directory.snapshot)}
            if isinstance(answer, Unknown):
                return JSONResponse(
                    {
                        "status": "unknown",
                        "error_type": "Unknown",
                        "detail": answer.reason,
                        **snapshot,
                    },
                    status_code=404,
                )
            if isinstance(answer, ConflictingEvidence):
                return JSONResponse(
                    {
                        "status": "conflicting",
                        "error_type": "ConflictingEvidence",
                        "detail": answer.reason,
                        "candidates": [_company_json(c) for c in answer.candidate_values],
                        **snapshot,
                    },
                    status_code=409,
                )
            if not answer.ingested:
                return JSONResponse(
                    {
                        "company": _company_json(answer),
                        "ingestion_status": "not_ingested",
                        "ingest_command": answer.ingest_command,
                        **snapshot,
                    }
                )
            entity = answer.entity
            body = _jsonable(annual_fundamentals(s, entity.entity_id, entity.cik, as_of), entity)
            body["company"]["exchange"] = answer.exchange
            body.update(ingestion_status="ingested", **snapshot)
            return JSONResponse(body)

    @app.get("/", response_class=HTMLResponse)
    def home(q: str = "") -> HTMLResponse:
        q = q.strip()
        with session() as s:
            directory = Directory(s)
            if q:
                answer = directory.resolve(q)
                if isinstance(answer, ConflictingEvidence):
                    return HTMLResponse(
                        render.conflict_page(q, answer, directory.snapshot), status_code=409
                    )
                if isinstance(answer, Company):
                    location = answer.url
                    if answer.snapshot.resolution_source == "fixed_universe":
                        location = f"/companies/{answer.entity.ticker}"
                    return HTMLResponse(render.redirect_page(location))
            return HTMLResponse(render.home_page(q, directory.search(q), directory.snapshot))

    @app.get("/companies/{ticker}", response_class=HTMLResponse)
    def company_page(ticker: str, as_of: datetime | None = None) -> HTMLResponse:
        validate_as_of(as_of)
        with session() as s:
            directory = Directory(s)
            answer = directory.resolve(ticker)
            if isinstance(answer, Unknown):
                return HTMLResponse(
                    render.unknown_page(ticker, directory.snapshot), status_code=404
                )
            if isinstance(answer, ConflictingEvidence):
                return HTMLResponse(
                    render.conflict_page(ticker, answer, directory.snapshot), status_code=409
                )
            if not answer.ingested:
                return HTMLResponse(render.not_ingested_page(answer))
            entity = answer.entity
            fundamentals = annual_fundamentals(s, entity.entity_id, entity.cik, as_of)
            return HTMLResponse(
                render.company_page(entity, fundamentals, listing_snapshot=answer.snapshot)
            )

    return app
