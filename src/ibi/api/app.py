"""FastAPI application: company search and the fundamentals research page.

Read-only: no route writes to the database or calls an external service.
There is no authentication yet, so `__main__` binds to 127.0.0.1 only.
Do not expose this app on a network until access control is decided.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ibi.api import render
from ibi.data_engine.sec_edgar.fixed_universe import FIXED_UNIVERSE, FixedUniverseEntity
from ibi.db.base import get_session_factory
from ibi.financial_engine.resolver import canonical_decimal
from ibi.research.fundamentals import LINE_ITEMS, Fundamentals, annual_fundamentals

SessionFactory = Callable[[], Session]


def find_company(ticker: str) -> FixedUniverseEntity:
    wanted = ticker.strip().upper()
    for entity in FIXED_UNIVERSE:
        if entity.ticker == wanted or entity.entity_id == wanted:
            return entity
    raise HTTPException(
        status_code=404, detail=f"{ticker!r} is not in the current coverage universe"
    )


def search(q: str) -> list[FixedUniverseEntity]:
    needle = q.strip().lower()
    if not needle:
        return list(FIXED_UNIVERSE)
    return [
        e
        for e in FIXED_UNIVERSE
        if needle in (e.ticker or "").lower() or needle in e.canonical_name.lower()
    ]


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


def create_app(session_factory: SessionFactory | None = None) -> FastAPI:
    app = FastAPI(title="Trusty", version="0.1.0", docs_url="/api/docs", redoc_url=None)

    @contextmanager
    def session() -> Iterator[Session]:
        s = (session_factory or get_session_factory())()
        try:
            yield s
        finally:
            s.rollback()  # read-only: never commit anything a route may have staged
            s.close()

    def load(ticker: str, as_of: datetime | None):
        entity = find_company(ticker)
        if as_of is not None and as_of.tzinfo is None:
            raise HTTPException(status_code=422, detail="as_of must include a timezone")
        with session() as s:
            return entity, annual_fundamentals(s, entity.entity_id, entity.cik, as_of)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/api/companies")
    def api_search(q: str = "") -> list[dict]:
        return [{"ticker": e.ticker, "name": e.canonical_name, "cik": e.cik} for e in search(q)]

    @app.get("/api/companies/{ticker}/fundamentals")
    def api_fundamentals(ticker: str, as_of: datetime | None = None) -> dict:
        entity, fundamentals = load(ticker, as_of)
        return _jsonable(fundamentals, entity)

    @app.get("/", response_class=HTMLResponse)
    def home(q: str = "") -> str:
        q = q.strip()
        if q:
            exact = [e for e in FIXED_UNIVERSE if (e.ticker or "") == q.upper()]
            if exact:
                return render.redirect_page(f"/companies/{exact[0].ticker}")
        return render.home_page(q, search(q) if q else list(FIXED_UNIVERSE))

    @app.get("/companies/{ticker}", response_class=HTMLResponse)
    def company_page(ticker: str, as_of: datetime | None = None) -> str:
        entity, fundamentals = load(ticker, as_of)
        return render.company_page(entity, fundamentals)

    return app
