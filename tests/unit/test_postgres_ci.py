"""Exercise the CI guard in isolated pytest processes, without a database."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture
def guarded_suite(pytester):
    pytester.makeconftest((Path(__file__).resolve().parents[1] / "conftest.py").read_text())
    pytester.makeini("[pytest]\nmarkers = integration: database tests\n")
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture(params=[pytest.param('postgresql', marks=pytest.mark.integration)])
        def session(request):
            return request.param

        def test_postgres(session):
            assert session == 'postgresql'
        """
    )
    return pytester


@pytest.mark.parametrize(
    ("url", "message"),
    [
        (None, "IBI_TEST_POSTGRES_URL is required"),
        ("sqlite:///test.db", "must use PostgreSQL"),
        ("postgresql+psycopg://localhost/ibi", "database name must contain 'test'"),
        ("invalid-url", "must be a valid SQLAlchemy URL"),
    ],
)
def test_required_postgres_rejects_bad_configuration(guarded_suite, monkeypatch, url, message):
    if url is None:
        monkeypatch.delenv("IBI_TEST_POSTGRES_URL", raising=False)
    else:
        monkeypatch.setenv("IBI_TEST_POSTGRES_URL", url)
    result = guarded_suite.runpytest_subprocess("--require-postgres")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert message in result.stderr.str()


def test_required_postgres_rejects_skips(guarded_suite, monkeypatch):
    monkeypatch.setenv("IBI_TEST_POSTGRES_URL", "postgresql+psycopg://localhost/ibi_test")
    guarded_suite.makepyfile(
        """
        import pytest

        @pytest.fixture(params=[pytest.param('postgresql', marks=pytest.mark.integration)])
        def session(request):
            pytest.skip('database unavailable')

        def test_postgres(session):
            pass
        """
    )
    result = guarded_suite.runpytest_subprocess("--require-postgres")
    result.assert_outcomes(errors=1)
    assert "integration tests cannot skip" in result.stdout.str()


def test_required_postgres_rejects_empty_selection(guarded_suite, monkeypatch):
    monkeypatch.setenv("IBI_TEST_POSTGRES_URL", "postgresql+psycopg://localhost/ibi_test")
    result = guarded_suite.runpytest_subprocess("--require-postgres", "-k", "does_not_exist")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "selected no PostgreSQL result-store cases" in result.stderr.str()


def test_default_run_still_allows_skips(guarded_suite, monkeypatch):
    monkeypatch.delenv("IBI_TEST_POSTGRES_URL", raising=False)
    guarded_suite.makepyfile(
        """
        import pytest

        @pytest.mark.integration
        def test_optional_database(postgres_test_url):
            pass
        """
    )
    result = guarded_suite.runpytest_subprocess()
    result.assert_outcomes(skipped=1)
