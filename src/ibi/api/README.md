# Stored SEC directory and research pages

Run `python -m ibi.api` from the repository root after applying migrations.
The default bind address is `127.0.0.1`. For private container access see
[the deployment guide](../../../docs/deploy.md). Routes read stored data.

- `/` and `/api/companies?q=` search the newest SEC listing snapshot through
  `listings.search_listings`, with at most 20 results. Results include ticker,
  name, exchange and `ingested` (an `entities` row exists for that CIK).
- Exact ticker/CIK searches open the company page directly. Links use CIKs
  so an ambiguous symbol never silently selects a company.
- `/companies/{ticker}` and `/api/companies/{ticker}/fundamentals` use exact
  ticker/CIK resolution only. Name and substring matching are search-only.
- When the newest directory is empty or missing, fixed default coverage is
  used explicitly. With a populated directory, an absent ticker is not
  resurrected from older snapshots or the fixed universe.

| Company state | HTML | JSON |
|---|---|---|
| Entity exists | Existing statement-v2 page; tables/rules unchanged | Existing financial fields plus `ingestion_status: ingested` and directory provenance |
| No entity | Not yet ingested; name, CIK, exchange, provenance and operator command | `ingestion_status: not_ingested`; no financial fields |
| Unknown | 404 with search link | 404, `error_type: Unknown` |
| Conflicting CIKs | 409; every candidate linked by CIK, no figures | 409, `error_type: ConflictingEvidence`, candidates, no figures |

Every company response identifies `listings_snapshot`: source-document ID,
UTC retrieval date, source URL and recorded digest. The fallback is labeled
`resolution_source: fixed_universe` and a missing/empty reason; unknown
provenance stays null. Search keeps its existing JSON-list contract and
includes provenance per result. `X-Listings-Source-Document-ID`,
`X-Listings-Retrieval-Date` and `X-Resolution-Source` also identify the search
snapshot when zero matches are returned.

Requests use one consistent read transaction: PostgreSQL repeatable-read,
read-only; explicit SQLite read transaction. Autoflush is disabled; each
session rolls back and closes. A concurrent directory refresh cannot mix
resolution, ingestion status and provenance from different snapshots.

The not-ingested state displays a shell-quoted command; it never executes
it. A CIK-selected candidate whose symbol is ambiguous keeps the CIK in
that command. Default-coverage pages explain that a directory refresh is
required before ticker-based ingestion. No missing financial data is
converted to zero or a calculated value.

## Verification

`tests/integration/test_search_any_ticker.py` exercises all states on SQLite
and PostgreSQL using committed listings bytes and fixture ingestion. It
checks statement payload preservation, escaping, command quoting, fallback,
snapshot provenance, and every route/state with commit, DML and SEC access
forbidden. Run the full suite with `pytest --require-postgres -ra` against
a disposable PostgreSQL test database.

For the local mobile check, start the fixture-only server and run the
Playwright script (requires an installed Playwright module and Chromium):

```bash
python -m tests.browser.serve_search_any_ticker --port 8765
node tests/browser/search_any_ticker.cjs http://127.0.0.1:8765 tests/screenshots/issue16
```

`PLAYWRIGHT_MODULE_PATH` may point to the existing Playwright installation;
`CHROMIUM_EXECUTABLE_PATH` may select an installed Chromium. The script
checks search, an exact redirect, the not-ingested state, local-only network
requests, and absence of horizontal scrolling at 390×844. Two screenshots
are committed with the PR. It does not assert/change the fundamentals-table
layout, which is tracked separately in #12. The Python CI suite remains
offline; the browser check is run separately without adding dependencies.
