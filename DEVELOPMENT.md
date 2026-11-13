# Development

## Requirements

- Python >= 3.11 (developed and tested against 3.13).
- PostgreSQL, for running migrations/integration tests against a real
  database. Not required to run the unit test suite.

On this project's primary development machine (Windows), the `python`/`pip`
commands are not on `PATH`; use the `py` launcher instead (`py -m pip`,
`py -m venv`). Adjust the commands below if your environment differs.

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

```bash
# macOS/Linux
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in any values you need locally.
Everything works with the defaults (`IBI_AI_PROVIDER=none`,
`IBI_MARKET_DATA_PROVIDER=null`) — no API keys or database are required to
import the codebase or run the unit tests.

## Running tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

This runs the full suite, including a few integration tests that use an
in-memory/temporary SQLite database (no external service needed) plus PostgreSQL cases that use only `IBI_TEST_POSTGRES_URL`. Without a
dedicated test URL, PostgreSQL cases skip locally; SQLite and unit tests
still run. The connection check also uses this test URL, never
`IBI_DATABASE_URL`.

Run only unit tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit
```

Run only tests that don't require any external service:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration"
```

## Linting

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests alembic
```

## Database & migrations

1. Start a local PostgreSQL instance and set `IBI_DATABASE_URL` in `.env`
   accordingly (see `.env.example`).
2. Apply migrations:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic upgrade head
   ```

3. After changing `src/ibi/db/models/*.py`, generate a new migration:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe the change"
   ```

   Always review the generated migration before committing it —
   autogenerate does not reliably detect every kind of change (column type
   changes, some constraint changes) and has not been run against a live
   database for the Phase 0 baseline migration; see DECISIONS.md.

### PostgreSQL CI and local reproduction

`.github/workflows/postgresql.yml` runs on pull requests and pushes to
`main`, using PostgreSQL 16 and Python 3.13. It installs the existing
`.[dev]` dependencies, runs Ruff, upgrades all migrations,
checks schema drift with `alembic check`, and verifies a full
`downgrade base` / `upgrade head` round trip. These checks run **before**
the result-store tests, which intentionally create and drop model tables.
The final step runs all offline tests, including both PostgreSQL and SQLite
result-store cases, with `--require-postgres`.

Strict mode rejects a missing/malformed/non-PostgreSQL test URL, a database
name without `test`, selection of no PostgreSQL result-store cases, and any
skipped integration test. Database connection and SQL errors fail the job.
Live SEC network tests remain excluded by the existing pytest defaults.
The job uses only disposable service credentials; it needs no repository
secrets. Its check name is `PostgreSQL 16 / Python 3.13`; making it a required
branch-protection check is a separate repository-settings action.

To reproduce on macOS/Linux, activate the project virtual environment and
run the following. **Both URLs must point only to a new disposable database.**
The downgrade and result-store tests destroy its tables. Never substitute
an application or production database URL.

```bash
docker run --name ibi-pg-check \
  -e POSTGRES_USER=ibi -e POSTGRES_PASSWORD=ibi_ci_only \
  -e POSTGRES_DB=ibi_ci_test -p 127.0.0.1:55432:5432 \
  --health-cmd='pg_isready -U ibi -d ibi_ci_test' \
  --health-interval=2s --health-timeout=5s --health-retries=15 \
  -d postgres:16

# Wait until this command succeeds before running the checks:
docker exec ibi-pg-check pg_isready -U ibi -d ibi_ci_test

export IBI_DATABASE_URL='postgresql+psycopg://ibi:ibi_ci_only@127.0.0.1:55432/ibi_ci_test'
export IBI_TEST_POSTGRES_URL="$IBI_DATABASE_URL"

# Run each command successfully before continuing (CI fails on any error):
python -m pip install -e ".[dev]"
python -m ruff check src tests alembic
python -m alembic upgrade head
python -m alembic check
python -m alembic downgrade base
python -m alembic upgrade head
python -m alembic check
python -m pytest --require-postgres -ra

# Remove only the disposable container created above:
docker rm -f ibi-pg-check
unset IBI_DATABASE_URL IBI_TEST_POSTGRES_URL
```

On PowerShell, set the two variables with `$env:IBI_DATABASE_URL = "..."`
and `$env:IBI_TEST_POSTGRES_URL = $env:IBI_DATABASE_URL`; invoke the same
Python commands via `.\.venv\Scripts\python.exe -m ...`. Use
`Remove-Item Env:IBI_DATABASE_URL, Env:IBI_TEST_POSTGRES_URL` afterward.

For just the PostgreSQL integration cases, use
`python -m pytest --require-postgres -m integration -ra`. For the ordinary
SQLite/unit development loop, leave `IBI_TEST_POSTGRES_URL` unset and run
`python -m pytest`; strict mode is opt-in outside CI. Test database names
must contain `test` as a mistake guard, not as proof a database is disposable.

## Research web app (local)

```bash
python -m ibi.api            # http://127.0.0.1:8000  (IBI_API_PORT to change)
```

Search a covered ticker (AAPL, MSFT) and open its fundamentals page; the
JSON behind it is at `/api/companies/<ticker>/fundamentals` and the API
docs at `/api/docs`. The app is read-only and has **no authentication**:
it binds to 127.0.0.1 only. Do not expose it on a network until access
control is in place. It shows whatever `IBI_DATABASE_URL` contains, so run
SEC ingestion first. See docs/sec_validation.md for checking it against
real filings.

## Running SEC EDGAR ingestion (Phase 1)

```powershell
$env:IBI_SEC_USER_AGENT = "Your Organization your-contact@example.com"
.\.venv\Scripts\python.exe -c "from ibi.data_engine.sec_edgar.ingest import run_ingestion; print(run_ingestion())"
```

Ingests the fixed universe (`ibi.data_engine.sec_edgar.fixed_universe`) —
currently Apple and Microsoft only — into whatever `IBI_DATABASE_URL`
points at. Safe to re-run: idempotent (see DECISIONS.md). Requires a real
`IBI_SEC_USER_AGENT` (SEC's fair-access policy; not a secret, but must be a
genuine contact — see SECURITY.md).

`run_ingestion()` returns one `IngestResult` per company, in universe order:
`status` is `success` or `failed`; failures include `error_type` and `error`.
Each company uses a separate session/transaction. An outcome becomes successful
only after commit; a company exception (including a database/commit error)
rolls back its entity, filing, fact and source-document writes before the batch
continues. Failed outcomes report zero committed-write counters. Global
configuration failures occur before the batch; cancellation, process interrupts,
`InterruptedError` and `MemoryError` propagate rather than becoming outcomes.

An existing fact identity with the same exact Decimal value is skipped. A
different value raises `DuplicateFactConflict`, fails that company, and leaves
the original fact intact. Different accession numbers, including amendments,
remain distinct facts. A failed result's `conflict` carries the identity,
values, existing ids and incoming snapshot reference. Existing ids are durable
only if the original row was already committed, not if it was staged in the
same failed attempt.

`raw_snapshot_refs` lists successfully written files even after a DB rollback;
retain these with the returned summary for investigation. File writes are not
transactional with PostgreSQL: completed snapshots deliberately remain as
evidence, and an interrupted/failed write may leave a partial file at the path
named in the error. No committed DB reference is left for a failed attempt.
This does not change the existing JSON archival format or add durable run-history
storage. Direct `ingest_entity()` callers still own their transaction and must
roll back on error.

To run the opt-in live-network test suite for this connector (skipped by
default; makes real calls to `data.sec.gov`):

```powershell
$env:IBI_SEC_USER_AGENT = "Your Organization your-contact@example.com"
.\.venv\Scripts\python.exe -m pytest -m live_network tests/integration/test_sec_edgar_live.py
```

## Phase 2B: calculation results

No CLI is provided yet. Build a metric's generation from Python, inside
`session_scope()` (which commits on success and rolls back on error):

```python
from ibi.db.base import session_scope
from ibi.financial_engine.results_store import VersionPair, build_generation

with session_scope() as session:
    print(build_generation(session, "CIK0000320193", "ibi:gross_margin", VersionPair("1", "1")))
```

`build_generation` raises before writing anything on any integrity failure
(`RetroactiveDriftError`, `ResultDriftError`, `InputMutationError`,
`PolicyManifestMismatchError`, `InputIntegrityError`).

Run the result-store tests against PostgreSQL. They create and drop
tables, so point them only at a **dedicated, disposable** database whose
name contains `test`. They never use `IBI_DATABASE_URL`:

```powershell
$env:IBI_TEST_POSTGRES_URL = "postgresql+psycopg://<role>@localhost:5432/ibi_phase2b_test"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_metric_results_store.py
```

Run the approved full-data validation (live SEC, in memory, writes nothing):

```powershell
$env:IBI_SEC_USER_AGENT = "Your Organization your-contact@example.com"
.\.venv\Scripts\python.exe -m pytest -m live_network -s tests/integration/test_phase2b_full_data_live.py
```

### Read-only check of what the live database contains

Run this in your own terminal. psql prompts for the password without
echoing it; don't use `PGPASSWORD`. `PGOPTIONS` forces every session
read-only, and each `SHOW transaction_read_only` must print `on`:

```powershell
$env:PGOPTIONS = "-c default_transaction_read_only=on"
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
& $psql -X -v ON_ERROR_STOP=1 -h localhost -U <role> -d <database> `
  -c "SHOW transaction_read_only;" `
  -c "SELECT version_num FROM alembic_version;" `
  -c "SELECT 'entities' t, count(*) FROM entities UNION ALL SELECT 'filings', count(*) FROM filings UNION ALL SELECT 'financial_data', count(*) FROM financial_data;" `
  -c "SELECT entity_id, epistemic_label, count(*), min(created_at), max(created_at) FROM financial_data GROUP BY 1,2 ORDER BY 1,2;" `
  -c "SELECT regexp_replace(content_ref, '[^\\/]+$', '') AS archive_dir, count(*) FROM source_documents GROUP BY 1;"
Remove-Item Env:PGOPTIONS
```

The output contains counts and an archive directory path only. Share it,
redacting names if you want, before deciding on any backfill.

## Project layout

See [ARCHITECTURE.md](ARCHITECTURE.md) for the domain package layout and
the principles behind it.

## Git

This repository was cloned with a remote already configured
(`origin` → `https://github.com/happyhockey6-ops/Investment-Bussiness-Intelligence.git`)
but had no commits at the time Phase 0 began.

`git` is **not** on this machine's `PATH` (`Get-Command git` fails), but a
working `git.exe` (2.53.0) exists, bundled with GitHub Desktop, at:

```
C:\Users\<you>\AppData\Local\GitHubDesktop\app-<version>\resources\app\git\cmd\git.exe
```

Invoke it by full path when the CLI isn't on `PATH`, e.g.:

```powershell
& "$env:LOCALAPPDATA\GitHubDesktop\app-3.6.5\resources\app\git\cmd\git.exe" status
```

(the `app-<version>` folder name changes on GitHub Desktop updates — glob
for it if scripting this: `Get-ChildItem "$env:LOCALAPPDATA\GitHubDesktop" -Filter git.exe -Recurse`).
No system-wide `PATH`/config changes were made to enable this — it was used
strictly by full path, for read-only inspection.
