from alembic import context

from app.accounts import Organization  # noqa: F401
from app.config import Settings
from app.db import Base, make_engine
from app.fleet import Depot  # noqa: F401
from app.records import Vehicle, Order  # noqa: F401
from app.jobs import Job, Plan  # noqa: F401

settings = Settings()

if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get('connection') is not None:
    context.configure(connection=context.config.attributes['connection'], target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(settings)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
