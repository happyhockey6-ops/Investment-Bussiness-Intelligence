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
in-memory/temporary SQLite database (no external service needed) and one
(`tests/integration/test_postgres_connection.py`) that attempts a real
PostgreSQL connection and self-skips within a few seconds if none is
reachable at `IBI_DATABASE_URL`.

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

### Real-PostgreSQL validation procedure (not yet run — no instance available)

The Phase 0 baseline migration (`alembic/versions/50445f5a9e59_initial_schema.py`)
has only been verified against SQLite (see
`tests/integration/test_alembic_migration.py` and `test_db_models_smoke.py`).
No PostgreSQL instance was available in the Phase 0 development environment
(no service, no listening port 5432, no Docker). Run this procedure the
first time a real PostgreSQL instance is available, to close that gap:

```powershell
# 1. Start a local PostgreSQL instance (Docker is the fastest path, if available)
docker run --name ibi-pg-check -e POSTGRES_PASSWORD=ibi -e POSTGRES_USER=ibi `
  -e POSTGRES_DB=ibi_dev -p 5432:5432 -d postgres:16

# 2. Point configuration at it
$env:IBI_DATABASE_URL = "postgresql+psycopg://ibi:ibi@localhost:5432/ibi_dev"

# 3. Run the migration for real
.\.venv\Scripts\python.exe -m alembic upgrade head

# 4. Confirm every model table exists with the expected types
#    (\d+ each table, or use psql / a GUI client) — pay particular
#    attention to: JSON columns actually created as jsonb (\d observations,
#    \d theses, \d scenarios), timestamp columns as timestamptz, and that
#    all foreign keys/nullability match `src/ibi/db/models/*.py` exactly.

# 5. Run the full test suite against the real database
.\.venv\Scripts\python.exe -m pytest

# 6. Confirm autogenerate detects zero drift against the models
#    (an empty diff here is the real confirmation that the hand-written
#    migration and the ORM models agree, on PostgreSQL specifically)
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "drift check"
#    -> inspect the generated file; it should contain only `pass` in both
#       upgrade() and downgrade(). Delete it after inspecting.

# 7. Tear down
docker rm -f ibi-pg-check
```

Until this has been run, treat the migration as **SQLite-verified, PostgreSQL-unverified**.

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
