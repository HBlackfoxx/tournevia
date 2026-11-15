"""One-shot setup: bootstrap roles, migrate as schema owner, then grant runtime DML."""
import json

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.config import ROOT
from app.database_roles import DatabaseAdminSettings, apply_grants, prepare_roles
from app.db import make_engine
from app.fleet import DepotInput
from app.provision import provision_demo


def setup(settings: DatabaseAdminSettings):
    prepare_roles(settings)
    migration = settings.migration_settings()
    engine = make_engine(migration, statement_timeout_ms=0)
    try:
        config = Config(str(ROOT / 'backend/alembic.ini'))
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
        if settings.app_mode == 'local_demo':
            with Session(engine) as session, session.begin():
                depot = DepotInput.model_validate(json.loads((ROOT / 'examples/depot.json').read_text(encoding='utf-8')))
                provision_demo(session, migration, depot)
    finally:
        engine.dispose()
    apply_grants(settings)


if __name__ == '__main__':
    try:
        setup(DatabaseAdminSettings())
    except Exception as error:
        # Role/driver errors can include password verifiers or connection details.
        raise SystemExit(f'Database setup failed ({type(error).__name__}); check role names, credentials and database schema.') from None
    print('Roles, migrations and runtime grants ready; existing records preserved. Open the application to finish first-use setup.')
