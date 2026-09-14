"""The Alembic migration must actually be runnable and must produce every
table `ibi.db.models` declares.

Runs against a temporary SQLite file rather than PostgreSQL for the same
reason as test_db_models_smoke.py: this checks the migration script itself
(does it run start-to-finish and create the right tables), not
PostgreSQL-specific SQL behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from ibi.config import get_settings
from ibi.db.base import Base
from ibi.db.models import *  # noqa: F401,F403  (populates Base.metadata)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def temp_sqlite_url(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_path = tmp_path / "alembic_test.sqlite3"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("IBI_DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def test_upgrade_head_creates_every_model_table(temp_sqlite_url: str):
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))

    command.upgrade(config, "head")

    engine = create_engine(temp_sqlite_url)
    actual_tables = set(inspect(engine).get_table_names())
    expected_tables = set(Base.metadata.tables.keys()) - {"alembic_version"}

    missing = expected_tables - actual_tables
    assert not missing, f"Migration did not create: {missing}"
