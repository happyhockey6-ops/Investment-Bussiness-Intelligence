"""Exact SEC source bytes, digests and exact fact values, end to end.

Reuses the per-company batch harness (SQLite and an isolated PostgreSQL
schema) from test_sec_edgar_integrity. All data is synthetic.
"""

import json
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from ibi.data_engine.sec_edgar import ingest
from ibi.data_engine.sec_edgar.source_bytes import sha256_hex
from ibi.db.models import FinancialDataPointRecord, SourceDocumentRecord
from tests.integration import test_sec_edgar_integrity as harness
from tests.integration.test_sec_edgar_integrity import (
    COMPANIES,
    MIDDLE,
    assert_isolated,
    documents,
)

batch = harness.batch  # the shared SQLite/PostgreSQL per-company fixture


def serve_bytes(batch, monkeypatch, kind, raw, sha256="auto"):
    """Make the middle company's `kind` response exactly `raw`."""
    fetch = batch.connector.fetch

    def fetch_with_bytes(entity_id):
        records = fetch(entity_id)
        if entity_id == MIDDLE:
            for record in records:
                if record.payload["kind"] == kind:
                    record.payload["raw_bytes"] = raw
                    record.payload["sha256"] = sha256_hex(raw) if sha256 == "auto" else sha256
        return records

    monkeypatch.setattr(batch.connector, "fetch", fetch_with_bytes)


def middle_sources(batch):
    with batch.engine.connect() as connection:
        return connection.execute(
            select(SourceDocumentRecord.__table__)
            .where(SourceDocumentRecord.content_ref.contains(MIDDLE))
            .order_by(SourceDocumentRecord.id)
        ).all()


def test_archive_is_the_exact_response_and_digest_is_recorded(batch, monkeypatch):
    # Unusual but valid formatting: re-serializing would change these bytes.
    body = documents(COMPANIES[1])["companyfacts"]
    raw = b"{ \"facts\" :" + json.dumps(body["facts"], indent=3).encode() + b"\n}\r\n"
    serve_bytes(batch, monkeypatch, "companyfacts", raw)
    summary = ingest.run_ingestion(batch.settings)
    assert not summary.had_any_error
    sources = {Path(r.content_ref).name.split("_")[0]: r for r in middle_sources(batch)}
    assert set(sources) == {"submissions", "companyfacts"}
    for row in sources.values():
        stored = Path(row.content_ref).read_bytes()
        assert row.content_sha256 == sha256_hex(stored)
        assert row.content_length == len(stored)
        assert row.content_ref.endswith(f"_{row.content_sha256[:16]}.json")
        assert oct(os.stat(row.content_ref).st_mode & 0o777) == "0o444"
    assert Path(sources["companyfacts"].content_ref).read_bytes() == raw
    assert not list(Path(batch.settings.raw_data_dir).rglob("*.partial"))


def test_values_beyond_six_decimals_are_exact_and_reingest_is_idempotent(batch, monkeypatch):
    tags = batch.connector.payloads[MIDDLE]["companyfacts"]["facts"]["us-gaap"]
    tags["Assets"]["units"]["USD"][0]["val"] = "@0.12345678@"
    if batch.engine.dialect.name == "postgresql":
        # 21 significant digits: exact only on PostgreSQL. SQLite stores
        # NUMERIC as an 8-byte REAL (~15 digits); it is a test/dev backend only.
        tags["Revenues"]["units"]["USD"][0]["val"] = "@123456789012.123456789@"
    fetch = batch.connector.fetch

    def numeric_literals(entity_id):  # emit the markers as bare JSON numbers
        records = fetch(entity_id)
        for record in records:
            raw = re.sub(rb'"@([0-9.]+)@"', rb"\1", record.payload["raw_bytes"])
            record.payload.update(raw_bytes=raw, sha256=sha256_hex(raw))
        return records

    monkeypatch.setattr(batch.connector, "fetch", numeric_literals)
    first = ingest.run_ingestion(batch.settings)
    assert not first.had_any_error, [r.error for r in first.results]
    second = ingest.run_ingestion(batch.settings)
    assert not second.had_any_error, [r.error for r in second.results]
    assert second.results[1].facts_skipped_duplicate == 2
    with batch.engine.connect() as connection:
        values = dict(connection.execute(
            select(FinancialDataPointRecord.metric_id, FinancialDataPointRecord.value)
            .where(FinancialDataPointRecord.entity_id == MIDDLE)
        ).all())
    assert values["us-gaap:Assets"] == Decimal("0.12345678")
    if batch.engine.dialect.name == "postgresql":
        assert values["us-gaap:Revenues"] == Decimal("123456789012.123456789")


@pytest.mark.parametrize(
    "raw",
    [b"{\"facts\": ", b'{"facts": {"us-gaap": {"Assets": {"units": {"USD": [{"val": NaN}]}}}}}',
     b"\xff\xfe"],
    ids=["truncated", "nan", "not_utf8"],
)
def test_undecodable_response_is_archived_then_fails_the_company(batch, monkeypatch, raw):
    serve_bytes(batch, monkeypatch, "companyfacts", raw)
    failed = assert_isolated(batch, ingest.run_ingestion(batch.settings))
    assert failed.error_type == "MalformedSecPayloadError"
    archived = [Path(ref) for ref in failed.raw_snapshot_refs]
    assert [p.read_bytes() for p in archived if p.name.startswith("companyfacts_")] == [raw]


def test_digest_mismatch_fails_the_company_without_archiving(batch, monkeypatch):
    raw = json.dumps(documents(COMPANIES[1])["companyfacts"]).encode()
    serve_bytes(batch, monkeypatch, "companyfacts", raw, sha256="0" * 64)
    failed = assert_isolated(batch, ingest.run_ingestion(batch.settings))
    assert failed.error_type == "MalformedSecPayloadError"
    assert "does not match" in failed.error
    assert not any(Path(ref).name.startswith("companyfacts_") for ref in failed.raw_snapshot_refs)


def test_non_bytes_payload_is_rejected(batch, monkeypatch):
    serve_bytes(batch, monkeypatch, "submissions", "{}", sha256=None)
    failed = assert_isolated(batch, ingest.run_ingestion(batch.settings))
    assert failed.error_type == "MalformedSecPayloadError"
    assert "exact response bytes" in failed.error


def test_existing_snapshot_is_never_overwritten(tmp_path):
    path = tmp_path / "snapshot.json"
    ingest._write_new_file(path, b"original")
    with pytest.raises(FileExistsError):
        ingest._write_new_file(path, b"replacement")
    assert path.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.partial"))
