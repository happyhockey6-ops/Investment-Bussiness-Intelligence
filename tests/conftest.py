"""Shared fixtures.

`clear_settings_cache` is the piece worth calling out: `ibi.config.get_settings`
is an `lru_cache`d singleton by design (see config.py's docstring), so any
test that monkeypatches environment variables affecting `Settings` must
clear that cache before and after, or it will silently see another test's
cached settings.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from ibi.config import get_settings


def pytest_addoption(parser):
    parser.addoption(
        "--require-postgres",
        action="store_true",
        help="Require a disposable PostgreSQL URL, PostgreSQL cases and no integration skips",
    )


def _postgres_test_url(config):
    url = os.environ.get("IBI_TEST_POSTGRES_URL")
    problem = None
    if not url:
        problem = "IBI_TEST_POSTGRES_URL is required (dedicated disposable test database)"
    else:
        try:
            parsed = make_url(url)
            if parsed.get_backend_name() != "postgresql":
                problem = "IBI_TEST_POSTGRES_URL must use PostgreSQL"
            elif "test" not in (parsed.database or ""):
                problem = "IBI_TEST_POSTGRES_URL database name must contain 'test'"
        except ArgumentError:
            problem = "IBI_TEST_POSTGRES_URL must be a valid SQLAlchemy URL"
    if problem:
        if config.getoption("--require-postgres"):
            raise pytest.UsageError(problem)
        pytest.skip(problem)
    return url


def pytest_configure(config):
    if config.getoption("--require-postgres"):
        _postgres_test_url(config)


def pytest_collection_finish(session):
    if session.config.getoption("--require-postgres") and not any(
        getattr(item, "callspec", None)
        and item.callspec.params.get("session") == "postgresql"
        for item in session.items
    ):
        raise pytest.UsageError("--require-postgres selected no PostgreSQL result-store cases")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if (
        item.config.getoption("--require-postgres")
        and item.get_closest_marker("integration")
        and report.skipped
    ):
        report.outcome = "failed"
        report.longrepr = f"{item.nodeid}: integration tests cannot skip with --require-postgres"


@pytest.fixture(scope="session")
def postgres_test_url(pytestconfig):
    return _postgres_test_url(pytestconfig)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
