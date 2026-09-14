"""Real-database integration check.

Skipped automatically when no PostgreSQL instance is reachable at
`IBI_DATABASE_URL` — Phase 0 does not require a live database to be present
for the test suite to pass, per the acceptance criteria (see
docs/testing_strategy.md), but this test exists so the moment a database is
available (locally or in CI), it is exercised for real.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from ibi.config import get_settings

pytestmark = pytest.mark.integration

# A refused connection resolves quickly; an unreachable/firewalled host does
# not, and the OS-level TCP timeout can be minutes. A short explicit timeout
# keeps "no database available" from stalling the whole suite.
_CONNECT_TIMEOUT_SECONDS = 3


def test_can_connect_and_select_1():
    engine = create_engine(
        get_settings().database_url,
        future=True,
        connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
    )
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    except OperationalError:
        pytest.skip("No reachable PostgreSQL instance at IBI_DATABASE_URL")
