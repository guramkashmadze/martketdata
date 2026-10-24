"""Alembic environment. Migrations are plain SQL, so there is no metadata to compare against.

The URL is DATABASE_URL, unless a caller (the integration tests) passes `database_url` in
`Config.attributes`.
"""

from alembic import context
from sqlalchemy import create_engine, pool

from marketdata.config import load_settings
from marketdata.log import configure_logging


def _sqlalchemy_url(database_url: str) -> str:
    """Use the psycopg 3 driver; DATABASE_URL itself stays a plain postgresql:// URL."""
    scheme, _, rest = database_url.partition("://")
    if scheme in ("postgres", "postgresql"):
        return f"postgresql+psycopg://{rest}"
    return database_url


def _database_url() -> str:
    url: str | None = context.config.attributes.get("database_url")
    if url is None:
        settings = load_settings()
        configure_logging(settings.env, settings.log_level)
        url = str(settings.database_url)
    return _sqlalchemy_url(url)


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=None, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
