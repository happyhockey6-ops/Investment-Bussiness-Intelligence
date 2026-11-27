"""Start the read-only web application after checking the installed schema."""

import uvicorn
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

from ibi.api.app import create_app
from ibi.config import get_settings
from ibi.db.base import build_engine


def check_schema(engine: Engine) -> None:
    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    with engine.connect() as connection:
        installed = MigrationContext.configure(connection).get_current_heads()
    if not expected or set(installed) != set(expected):
        raise RuntimeError("Database schema requires: docker compose run --rm migrate")


def main() -> None:
    settings = get_settings()
    engine = build_engine(settings)
    try:
        check_schema(engine)
    finally:
        engine.dispose()
    uvicorn.run(create_app(), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
