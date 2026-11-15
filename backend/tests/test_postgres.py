"""Real PostgreSQL only. The shared database fixture lives in conftest.py."""
from uuid import uuid4

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts import Organization
from app.config import ROOT
from app.fleet import Depot, DepotInput, read_depot
from app.main import create_app
from app.provision import provision_demo

pytestmark = pytest.mark.postgres



def example() -> DepotInput:
    return DepotInput.model_validate_json((ROOT / 'examples/depot.json').read_text())


def test_migration_round_trip_and_metadata(database):
    engine, config, settings = database
    assert set(inspect(engine).get_table_names()) == {'organizations', 'depots', 'vehicles', 'orders', 'customers', 'planning_jobs', 'plans', 'users', 'login_sessions', 'login_throttles', 'alembic_version'}
    command.check(config)
    command.upgrade(config, 'head')
    command.downgrade(config, 'base')
    assert set(inspect(engine).get_table_names()) == {'alembic_version'}
    command.upgrade(config, 'head')
    command.check(config)


def test_provision_is_idempotent_and_reads_are_scoped(database):
    engine, config, settings = database
    with Session(engine) as session, session.begin():
        provision_demo(session, settings, example())
    with Session(engine) as session, session.begin():
        existing = session.scalar(select(Depot))
        original_id = existing.id
        existing.name = 'Edited synthetic depot'
    with Session(engine) as session, session.begin():
        provision_demo(session, settings, example())
        other_id = uuid4()
        session.add(Organization(id=other_id, name='Second synthetic company'))
        session.flush()
        session.add(Depot(organization_id=other_id, code='OTHER', name='Other company depot',
                          latitude=48.8, longitude=2.3, reviewed=False, timezone='Europe/Paris', main=True))
    with Session(engine) as session:
        depot = read_depot(session, settings.demo_organization_id)
        assert depot.id == original_id
        assert depot.data.name == 'Edited synthetic depot'
        assert read_depot(session, other_id).data.code == 'OTHER'
        assert read_depot(session, uuid4()) is None
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        assert client.get('/api/v1/ready').json() == {'status': 'ready'}
        response = client.get(f'/api/v1/depot?organization_id={other_id}', headers={'X-Organization-ID': str(other_id)})
        assert response.json()['organization_id'] == str(settings.demo_organization_id)
        assert response.json()['data']['code'] == 'PARIS-DEMO'
        assert response.headers['etag'] == '"1"'


def test_ownership_one_main_depot_and_unique_codes(database):
    engine, config, settings = database
    with Session(engine) as session, session.begin():
        provision_demo(session, settings, example())
    place = dict(name='Synthetic', latitude=48.8, longitude=2.3, reviewed=False, timezone='Europe/Paris')
    with Session(engine) as session, session.begin():
        session.add(Depot(organization_id=settings.demo_organization_id, code='SECOND', **place))
    for owner, changes in [(settings.demo_organization_id, {'code': 'SECOND'}),             # same code
                           (settings.demo_organization_id, {'code': 'THIRD', 'main': True}),  # second main depot
                           (settings.demo_organization_id, {'code': 'HOME', 'kind': 'HOUSE'}),
                           (uuid4(), {'code': 'ELSEWHERE', 'main': True})]:                    # no such company
        with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
            session.add(Depot(organization_id=owner, **{**place, **changes}))
            session.flush()
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        row = session.scalar(select(Depot).where(Depot.main))
        row.kind = 'PLACE'  # The main depot is a depot.
        session.flush()


def test_readiness_needs_current_migration_and_demo(database):
    engine, config, settings = database
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        assert client.get('/api/v1/ready').status_code == 503
        with Session(engine) as session, session.begin():
            provision_demo(session, settings, example())
        assert client.get('/api/v1/ready').status_code == 200
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = 'stale'"))
        assert client.get('/api/v1/ready').status_code == 503
