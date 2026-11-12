"""Real-database integration check.

Uses only `IBI_TEST_POSTGRES_URL`, never the application database URL.
Local runs may skip when no test database is available; --require-postgres
makes that a failure in CI.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.integration

# A refused connection resolves quickly; an unreachable/firewalled host does
# not, and the OS-level TCP timeout can be minutes. A short explicit timeout
# keeps "no database available" from stalling the whole suite.
_CONNECT_TIMEOUT_SECONDS = 3


def test_can_connect_and_select_1(postgres_test_url, pytestconfig):
    engine = create_engine(
        postgres_test_url,
        future=True,
        connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
    )
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    except OperationalError:
        if pytestconfig.getoption("--require-postgres"):
            raise
        pytest.skip("No reachable PostgreSQL instance at IBI_TEST_POSTGRES_URL")
    finally:
        engine.dispose()
