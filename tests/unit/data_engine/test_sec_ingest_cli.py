"""CLI configuration/selection guards without a database or network."""

import pytest

from ibi.config import Settings
from ibi.data_engine.sec_edgar import ingest


@pytest.mark.parametrize("contact", [None, "", "Tests <CONTACT_EMAIL>", "No email"])
def test_cli_rejects_bad_contact_before_connector_or_database(monkeypatch, capsys, contact):
    settings = Settings(sec_user_agent=contact, _env_file=None)
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)

    def forbidden(*args):
        pytest.fail("bad SEC contact must fail before opening a connector or database")

    monkeypatch.setattr(ingest, "SecEdgarConnector", forbidden)
    monkeypatch.setattr(ingest, "session_scope", forbidden)
    monkeypatch.setattr(ingest, "get_session_factory", forbidden)
    assert ingest.main(["--ticker", "MSFT"]) == 1
    assert "IBI_SEC_USER_AGENT" in capsys.readouterr().err


def test_explicit_companies_and_tickers_are_mutually_exclusive():
    with pytest.raises(ValueError, match="companies or tickers"):
        ingest.run_ingestion(companies=[], tickers=["MSFT"])
