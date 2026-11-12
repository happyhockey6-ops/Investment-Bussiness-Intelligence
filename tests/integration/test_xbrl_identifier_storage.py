"""Full XBRL names survive real migrations and keep their full identity.

Names are from SEC filing instances; values/contexts below are synthetic
storage test data. No SEC calls are made. Real examples and source URLs:

OutstandingNumber / ExercisableNumber:
https://www.sec.gov/Archives/edgar/data/1066764/000149315224032507/form10-q_htm.xml
GrantsInPeriod:
https://www.sec.gov/Archives/edgar/data/1836295/000149315224018132/formposam_htm.xml
ForfeituresInPeriod:
https://www.sec.gov/Archives/edgar/data/1836295/000149315224033012/form10-q_htm.xml

Both SQLite and PostgreSQL use Alembic-created schemas, never create_all.
PostgreSQL runs in a temporary schema inside the dedicated test database.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Text, create_engine, delete, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from ibi.config import get_settings
from ibi.db.models import EntityRecord, FinancialDataPointRecord

PREVIOUS = "e7f3a2c91b40"
REVISION = "b91c7e2a4d60"
ENTITY = "CIK0001066764"
REPO_ROOT = Path(__file__).resolve().parents[2]
PREFIX = "us-gaap:ShareBasedCompensationArrangementByShareBasedPaymentAwardOptions"
INSTANT_TAGS = (PREFIX + "OutstandingNumber", PREFIX + "ExercisableNumber")
DURATION_TAGS = (PREFIX + "GrantsInPeriod", PREFIX + "ForfeituresInPeriod")
SHORT = "us-gaap:Revenues"


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.integration)])
def migrated_database(request, tmp_path, monkeypatch):
    admin = engine = None
    schema = "xbrl_test_" + uuid4().hex
    try:
        if request.param == "postgresql":
            url = make_url(request.getfixturevalue("postgres_test_url"))
            admin = create_engine(url, connect_args={"connect_timeout": 3})
            with admin.begin() as connection:
                connection.execute(CreateSchema(schema))
            # libpq applies this to both Alembic's connections and this test's
            # engine, without percent-encoded URL options in Alembic's INI parser.
            monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema}")
        else:
            url = make_url(f"sqlite:///{tmp_path / 'identifiers.db'}")
        monkeypatch.setenv("IBI_DATABASE_URL", url.render_as_string(hide_password=False))
        get_settings.cache_clear()
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        command.upgrade(config, PREVIOUS)
        engine = create_engine(url)
        with Session(engine) as session:
            session.add(EntityRecord(entity_id=ENTITY, kind="company", canonical_name="Test Co"))
            session.commit()
        yield engine, config
    finally:
        if engine is not None:
            engine.dispose()
        if admin is not None:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin.dispose()
        get_settings.cache_clear()


def fact(metric_id, *, start=None, unit="shares", accession="0001066764-24-032507"):
    return FinancialDataPointRecord(
        entity_id=ENTITY,
        metric_id=metric_id,
        unit=unit,
        start_date=start,
        period_end_date=date(2023, 12, 31),
        accession_number=accession,
        value=Decimal("123"),
        epistemic_label="fact",
        known_available_at=datetime(2024, 2, 1, tzinfo=UTC),
        availability_precision="acceptance_timestamp",
        source_form_type="10-K",
    )


def metric_column(engine):
    columns = inspect(engine).get_columns("financial_data")
    return next(c for c in columns if c["name"] == "metric_id")


def index_signatures(engine):
    return sorted(
        (
            i["name"], tuple(i["column_names"]), i["unique"],
            str(i["dialect_options"].get(f"{engine.dialect.name}_where")),
        )
        for i in inspect(engine).get_indexes("financial_data")
    )


def stored_rows(engine):
    with engine.connect() as connection:
        return connection.execute(
            select(FinancialDataPointRecord.__table__).order_by(FinancialDataPointRecord.id)
        ).all()


def test_upgrade_preserves_short_rows_and_indexes(migrated_database):
    engine, config = migrated_database
    assert metric_column(engine)["type"].length == 64
    with Session(engine) as session:
        session.add_all([fact(SHORT, unit="USD"), fact("x" * 64)])
        session.commit()
    before = stored_rows(engine)
    indexes = index_signatures(engine)

    command.upgrade(config, "head")

    column = metric_column(engine)
    assert isinstance(column["type"], Text)
    assert column["nullable"] is False
    assert stored_rows(engine) == before
    assert index_signatures(engine) == indexes
    command.check(config)


@pytest.mark.parametrize(
    ("start", "tags"), [(None, INSTANT_TAGS), (date(2023, 1, 1), DURATION_TAGS)],
    ids=["instant", "duration"],
)
def test_long_names_roundtrip_and_full_key_uniqueness(migrated_database, start, tags):
    engine, config = migrated_database
    assert all(len(tag) > 64 for tag in tags)
    assert tags[0][:64] == tags[1][:64] and tags[0] != tags[1]
    if engine.dialect.name == "postgresql":
        # Demonstrate that this regression really fails on the old schema.
        with pytest.raises(DataError) as error, Session(engine) as session:
            session.add(fact(tags[0], start=start))
            session.commit()
        assert error.value.orig.sqlstate == "22001"

    command.upgrade(config, "head")
    with Session(engine) as session:
        session.add_all([fact(tag, start=start) for tag in tags] + [fact(SHORT, unit="USD")])
        session.commit()

    # Reopen the session to ensure this reads persisted strings, not the ORM cache.
    with Session(engine) as session:
        rows = session.execute(select(FinancialDataPointRecord)).scalars().all()
        assert {r.metric_id: r.value for r in rows} == {
            tag: Decimal("123") for tag in (*tags, SHORT)
        }
        assert session.execute(
            select(FinancialDataPointRecord).where(FinancialDataPointRecord.metric_id == tags[0])
        ).scalar_one().metric_id == tags[0]
        for tag in tags:
            with pytest.raises(IntegrityError) as error, session.begin_nested():
                session.add(fact(tag, start=start))
                session.flush()
            if engine.dialect.name == "postgresql":
                shape = "instant" if start is None else "duration"
                assert error.value.orig.diag.constraint_name == f"uq_financial_data_{shape}_fact"
        # Other components of the original composite identity still distinguish facts.
        session.add_all([
            fact(tags[0], start=start, unit="USD"),
            fact(tags[0], start=start, accession="0001066764-24-032508"),
            fact(tags[0], start=date(2023, 2, 1)),
        ])
        session.commit()
    assert len(stored_rows(engine)) == 6


def test_downgrade_refuses_long_names_and_roundtrips_short_rows(migrated_database):
    engine, config = migrated_database
    with Session(engine) as session:
        session.add_all([fact(SHORT, unit="USD"), fact("x" * 64)])
        session.commit()
    short_rows = stored_rows(engine)
    indexes = index_signatures(engine)
    command.upgrade(config, "head")
    with Session(engine) as session:
        session.add(fact(INSTANT_TAGS[0]))
        session.commit()
    before = stored_rows(engine)

    with pytest.raises(RuntimeError, match="refusing to truncate stored identifiers"):
        command.downgrade(config, PREVIOUS)
    assert stored_rows(engine) == before
    assert isinstance(metric_column(engine)["type"], Text)
    with engine.connect() as connection:
        current = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        assert current == REVISION
    command.check(config)

    # Remove only this test's synthetic long row to exercise a safe narrowing.
    with engine.begin() as connection:
        connection.execute(delete(FinancialDataPointRecord).where(
            FinancialDataPointRecord.metric_id == INSTANT_TAGS[0]
        ))
    command.downgrade(config, PREVIOUS)
    assert metric_column(engine)["type"].length == 64
    assert stored_rows(engine) == short_rows
    assert index_signatures(engine) == indexes
    command.upgrade(config, "head")
    assert stored_rows(engine) == short_rows
    command.check(config)
