"""Alembic environment: database URL from the service settings, metadata from nimi_service.models."""
from alembic import context
from sqlalchemy import create_engine, pool

from nimi_service.config import load_settings
from nimi_service.models import Base
from nimi_service.views import VIEWS

config = context.config
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # Views are managed explicitly in migrations (nimi_service/views.py), not by autogenerate.
    return not (type_ == "table" and name in VIEWS)


def _url():
    return config.attributes.get("url") or load_settings().db_url


def run_migrations_offline() -> None:
    url = _url()
    context.configure(url=url if isinstance(url, str) else url.render_as_string(hide_password=False),
                      target_metadata=target_metadata, literal_binds=True,
                      include_object=include_object, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # transaction_per_migration: a failing migration rolls back on its own; earlier ones stay applied.
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object,
                          compare_type=True, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
