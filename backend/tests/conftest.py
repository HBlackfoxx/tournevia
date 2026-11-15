"""Shared pytest fixtures. Each PostgreSQL test uses a fresh, disposable database, never existing data."""
import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import ROOT, Settings


@pytest.fixture
def database(monkeypatch):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set TEST_DATABASE_URL to a PostgreSQL role with CREATEDB for isolated integration tests.')
    admin_url = make_url(url)
    assert admin_url.get_backend_name() == 'postgresql'
    db_name = 'vrp_test_' + uuid4().hex
    admin = create_engine(admin_url, isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{db_name}"'))
    engine = create_engine(admin_url.set(database=db_name))
    for key, value in {'DB_HOST': admin_url.host or '127.0.0.1', 'DB_PORT': str(admin_url.port or 5432),
                       'DB_NAME': db_name, 'DB_USER': admin_url.username or '', 'DB_PASSWORD': admin_url.password or '',
                       'APP_MODE': 'local_demo', 'PUBLIC_ORIGIN': 'http://localhost:8000'}.items():
        monkeypatch.setenv(key, value)
    config = Config(str(ROOT / 'backend/alembic.ini'))
    try:
        command.upgrade(config, 'head')
        yield engine, config, Settings(_env_file=None)
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin.dispose()
