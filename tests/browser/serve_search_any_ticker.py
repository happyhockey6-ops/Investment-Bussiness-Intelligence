"""Local fixture-only app for the issue-16 Playwright flow; no production IO."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ibi.api.app import create_app
from ibi.data_engine.sec_edgar import listings
from ibi.data_engine.sec_edgar.client import SecHttpClient
from ibi.data_engine.sec_edgar.ingest import ingest_entity
from ibi.data_engine.sec_edgar.source_bytes import sha256_hex
from ibi.db.base import Base
from tests.integration.test_sec_edgar_ingestion import AAPL, FakeConnector, _load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="trusty-mobile-test-") as temporary:
        root = Path(temporary)
        engine = create_engine(f"sqlite:///{root / 'browser-test.db'}")
        Base.metadata.create_all(engine)
        fixture = (
            Path(__file__).resolve().parents[1] / "fixtures/sec_edgar/company_tickers_exchange.json"
        )
        raw = fixture.read_bytes()
        with Session(engine) as session, session.begin():
            listings.refresh_listings(
                session,
                root,
                listings.FetchedListings(
                    raw, sha256_hex(raw), datetime(2026, 11, 15, 12, tzinfo=UTC)
                ),
            )
            ingest_entity(
                session,
                FakeConnector(_load("aapl_submissions.json"), _load("aapl_companyfacts.json")),
                AAPL,
                root,
            )

        def forbidden(*args, **kwargs):
            raise AssertionError("Browser routes must not construct an SEC client")

        SecHttpClient.__init__ = forbidden
        try:
            uvicorn.run(create_app(sessionmaker(bind=engine)), host="127.0.0.1", port=args.port)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
