# Real SEC validation

Checks that Trusty's stored facts and research page match what companies
actually reported, using live SEC data. Run it whenever ingestion, the
selection policy or the fundamentals rules change.

## Requirements

1. **SEC contact.** SEC requires every request to identify the requester.
   Set `IBI_SEC_USER_AGENT` to the organization name and a monitored email,
   e.g. `Trusty Investment Research ops@your-domain.com`. `.env.example`
   ships with `<CONTACT_EMAIL>`; ingestion refuses to start while it is
   still there, or when no email is present (`Settings.require_sec_user_agent`).
2. **Network access to `data.sec.gov`.** In a Claude Code cloud session,
   the environment's network policy must allow `data.sec.gov` (and
   `www.sec.gov` for filing links). The `Investment-Bussiness-Intelligence`
   cloud environment allows both; the `Default` environment does not
   (the proxy answers `403` to the `CONNECT`). Locally, no change is needed.
3. **A dedicated database** whose name contains `validation` or `test`.
   The procedure migrates it to head and writes to it. Never use a
   production database.

## Run

```bash
export IBI_SEC_USER_AGENT="Trusty Investment Research you@your-domain.com"
createdb ibi_sec_validation
python -m ibi.validation.sec_reconciliation \
  --database-url postgresql+psycopg://<role>@localhost:5432/ibi_sec_validation
```

It ingests the covered companies (`fixed_universe.py`) at SEC's 2 requests
per second, builds `ibi:gross_margin` and `ibi:free_cash_flow`, and prints:

- per company, how many page cells are values, restated, conflicting or
  missing, and the status mix of each calculated metric;
- for the reference fiscal year in
  `src/ibi/validation/expected_fundamentals.json`, every line item as
  `ok` or `FAIL` with the XBRL tag and accession used.

It exits `0` only when every reference value matches and every company
ingested successfully.

## First run

The reference values are marked `"verified": false`: they were transcribed
for this check and have not been confirmed against the filings yet. On the
first run, open each filing from its link in the research page and confirm
every reference value, then set `"verified": true` in a pull request. Any
`FAIL` is a finding to investigate (wrong tag mapping, a restatement, or a
wrong reference value), never something to paper over by editing the
expected value without checking the filing.
