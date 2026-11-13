"""Migration c4d2e8f1a7b3 on Alembic-created SQLite and isolated PostgreSQL schemas."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Numeric, bindparam, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from ibi.config import get_settings

PREVIOUS = "b91c7e2a4d60"
REVISION = "c4d2e8f1a7b3"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.integration)])
def migrated(request, tmp_path, monkeypatch):
    admin = engine = None
    schema = "srcint_test_" + uuid4().hex
    try:
        if request.param == "postgresql":
            url = make_url(request.getfixturevalue("postgres_test_url"))
            admin = create_engine(url, connect_args={"connect_timeout": 3})
            with admin.begin() as connection:
                connection.execute(CreateSchema(schema))
            monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema}")
        else:
            url = make_url(f"sqlite:///{tmp_path / 'srcint.db'}")
        monkeypatch.setenv("IBI_DATABASE_URL", url.render_as_string(hide_password=False))
        get_settings.cache_clear()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        command.upgrade(config, PREVIOUS)
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO entities (entity_id, kind, canonical_name, created_at) "
                "VALUES ('CIK0000000001', 'company', 'Test', :now)"
            ), {"now": datetime.now(UTC)})
            connection.execute(text(
                "INSERT INTO source_documents (source, source_tier, retrieval_date, created_at) "
                "VALUES ('SEC EDGAR', 'primary_regulatory', :now, :now)"
            ), {"now": datetime.now(UTC)})
        yield engine, config
    finally:
        if engine is not None:
            engine.dispose()
        if admin is not None:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin.dispose()
        get_settings.cache_clear()


def insert_fact(engine, value, accession):
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO financial_data (entity_id, metric_id, unit, period_end_date, value, "
            "epistemic_label, accession_number, created_at) VALUES ('CIK0000000001', "
            "'us-gaap:Assets', 'USD', :end, :value, 'fact', :accn, :now)"
        ).bindparams(bindparam("value", type_=Numeric())),
        {"end": date(2023, 12, 31), "value": value, "accn": accession,
            "now": datetime.now(UTC)})


def rows(engine, sql):
    with engine.connect() as connection:
        return connection.execute(text(sql)).all()


def test_upgrade_preserves_rows_and_legacy_sources_stay_unhashed(migrated):
    engine, config = migrated
    insert_fact(engine, Decimal("394328000000"), "a")
    insert_fact(engine, Decimal("1.250000"), "b")
    before = rows(engine, "SELECT id, value FROM financial_data ORDER BY id")
    command.upgrade(config, "head")
    assert rows(engine, "SELECT id, value FROM financial_data ORDER BY id") == before
    assert rows(engine, "SELECT content_sha256, content_length FROM source_documents") == [
        (None, None)
    ]
    columns = {c["name"]: c for c in inspect(engine).get_columns("source_documents")}
    assert columns["content_sha256"]["nullable"] and columns["content_length"]["nullable"]
    command.check(config)


@pytest.mark.parametrize("blocker", ["digest", "precision"])
def test_downgrade_refuses_to_destroy_evidence(migrated, blocker):
    engine, config = migrated
    # Test c4's protection at c4, independently of later migrations.
    command.upgrade(config, REVISION)
    if blocker == "digest":
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE source_documents SET content_sha256 = :h, content_length = 3"),
                {"h": "a" * 64},
            )
    else:
        insert_fact(engine, Decimal("0.12345678"), "c")
    before = rows(engine, "SELECT id, value FROM financial_data ORDER BY id")
    if blocker == "precision" and engine.dialect.name == "sqlite":
        # SQLite really stores REAL and ignores declared NUMERIC scale.
        # Verify this behavior explicitly instead of skipping the backend.
        assert rows(engine, "SELECT typeof(value) FROM financial_data") == [("real",)]
        command.downgrade(config, PREVIOUS)
        value_column = next(
            c for c in inspect(engine).get_columns("financial_data") if c["name"] == "value"
        )
        assert value_column["type"].scale == 6
        assert rows(engine, "SELECT id, value FROM financial_data ORDER BY id") == before
        assert before[0][1] == 0.12345678
        command.upgrade(config, "head")
        assert rows(engine, "SELECT id, value FROM financial_data ORDER BY id") == before
        command.check(config)
        return
    with pytest.raises(RuntimeError, match="refusing to destroy evidence"):
        command.downgrade(config, PREVIOUS)
    assert rows(engine, "SELECT version_num FROM alembic_version") == [(REVISION,)]
    assert rows(engine, "SELECT id, value FROM financial_data ORDER BY id") == before
    command.upgrade(config, "head")
    command.check(config)


def test_safe_downgrade_and_reupgrade(migrated):
    engine, config = migrated
    insert_fact(engine, Decimal("12.5"), "d")
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS)
    assert "content_sha256" not in {
        c["name"] for c in inspect(engine).get_columns("source_documents")
    }
    command.upgrade(config, "head")
    assert [Decimal(v) for (v,) in rows(engine, "SELECT value FROM financial_data")] == [
        Decimal("12.5")
    ]
    command.check(config)
