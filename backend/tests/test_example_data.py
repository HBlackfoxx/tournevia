"""Example records instead of a sample mode: added for a day, planned only with other examples,
stamped on every dispatch output, removed together, and migrated from first-use sample workspaces."""
import csv
from datetime import date, datetime
import io
import json
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import ROOT, Settings
from app.edits import EXAMPLE_NOTICE
from app.jobs import Job, OptimizationRequest, Plan, submit
from app.main import create_app
from app.planning import PARIS, PlanningInput, VehicleSource
from app.records import DataError, Order, Vehicle
from test_dispatch import compatible, export, ready, routed_plan, sheets
from test_onboarding import TOKEN, payload
from test_sources import WRITE, client  # noqa: F401  (fixture)

pytestmark = pytest.mark.postgres
DAY = date(2026, 11, 3)
FIXTURE = PlanningInput.model_validate_json((ROOT / 'backend/tests/fixtures/paris-ten.json').read_text())


def add(client, day=DAY):  # noqa: F811
    return client.post('/api/v1/examples', json={'service_date': day.isoformat()}, headers=WRITE)


def request(vehicles, orders, day=DAY):
    return OptimizationRequest(service_date=day, vehicle_ids=[v['id'] for v in vehicles], order_ids=[o['id'] for o in orders],
                               routing_policy='TRUCK_STANDARD', solver_time_limit_seconds=1)


def own_vehicle(client, **changes):  # noqa: F811
    data = {**VehicleSource.weekly(FIXTURE.vehicles[1].data).model_dump(mode='json'), 'fleet_number': 'VUL-01', **changes}
    return client.post('/api/v1/vehicles', json=data, headers=WRITE)


def test_examples_are_added_for_a_day_once_with_their_depot_and_vehicles(client):  # noqa: F811
    added = add(client)
    assert added.status_code == 201, added.text
    body = added.json()
    assert (body['depot'], body['vehicles']) == ('EXAMPLE', ['EX-TRUCK-01', 'EX-VAN-01'])
    assert body['deliveries'] == [f'EX-20261103-{n:02d}' for n in range(1, 11)]
    places = client.get('/api/v1/depots').json()
    example = next(p for p in places if p['example'])
    assert (example['main'], example['data']['kind'], example['data']['location']['reviewed']) == (False, 'DEPOT', True)
    assert not next(p for p in places if p['main'])['example']
    vehicles = client.get('/api/v1/vehicles').json()
    assert all(v['example'] and v['data']['start'] == 'EXAMPLE' for v in vehicles)
    # The examples work every day.
    assert all(v['data']['hours'][day] for v in vehicles for day in ('monday', 'saturday', 'sunday'))
    orders = client.get('/api/v1/orders', params={'service_date': DAY.isoformat()}).json()
    assert len(orders) == 10 and all(o['example'] and o['data']['location']['reviewed'] for o in orders)
    first = next(o['data'] for o in orders if o['data']['external_id'] == 'EX-20261103-01')
    start = datetime.fromisoformat(first['time_window']['start'])
    assert first['address'] == 'Example stop 1, Paris' and start.astimezone(PARIS).date() == DAY
    assert start.astimezone(PARIS).strftime('%H:%M') == FIXTURE.orders[0].data.time_window.start.strftime('%H:%M')
    # Once per day; another day adds only its deliveries.
    again = add(client)
    assert (again.status_code, again.json()['code'], again.json()['identifiers']) == (409, 'EXAMPLES_EXIST', ['2026-11-03'])
    other = add(client, date(2026, 11, 4)).json()
    assert other['vehicles'] == [] and other['deliveries'][0] == 'EX-20261104-01'
    assert len(client.get('/api/v1/vehicles').json()) == 2


def test_own_records_never_use_the_example_depot_and_it_is_never_main(client):  # noqa: F811
    assert add(client).status_code == 201
    refused = own_vehicle(client, start='EXAMPLE')
    assert (refused.status_code, refused.json()['code'], refused.json()['identifiers']) == (422, 'EXAMPLE_PLACE', ['EXAMPLE'])
    order = {**FIXTURE.orders[3].data.model_dump(mode='json'), 'external_id': 'LIV-1', 'ships_from': 'EXAMPLE'}
    assert client.post('/api/v1/orders', json=order, headers=WRITE).json()['code'] == 'EXAMPLE_PLACE'
    # An example vehicle may change and keep its example depot.
    truck = next(v for v in client.get('/api/v1/vehicles').json() if v['data']['fleet_number'] == 'EX-TRUCK-01')
    changed = client.put(f"/api/v1/vehicles/{truck['id']}", json={**truck['data'], 'active': False},
                         headers={**WRITE, 'If-Match': f'"{truck["version"]}"'})
    assert changed.status_code == 200 and changed.json()['example'] is True
    example = next(p for p in client.get('/api/v1/depots').json() if p['example'])
    main = client.post(f"/api/v1/depots/{example['id']}/main", headers={**WRITE, 'If-Match': f'"{example["version"]}"'})
    assert (main.status_code, main.json()['code']) == (422, 'EXAMPLE_NOT_MAIN')
    # A file of the company's own vehicles cannot name it either.
    header = (ROOT / 'examples/vehicles.csv').read_text().splitlines()[0]
    row = (ROOT / 'examples/vehicles.csv').read_text().splitlines()[2].replace('VAN-DEMO-01', 'VUL-09')
    content = f'{header},max_stops,max_distance_m,max_driving_seconds,start,end\n{row},,,,EXAMPLE,\n'.encode()
    preview = client.post('/api/v1/imports/vehicles?dry_run=true', content=content, headers={**WRITE, 'Content-Type': 'text/csv'})
    assert [e['message'] for e in preview.json()['errors']] == ['Only example vehicles and deliveries use an example depot.']


def test_a_calculation_takes_only_examples_or_only_the_company_s_own_records(client, database):  # noqa: F811
    engine, _, settings = database
    owner = settings.demo_organization_id
    assert add(client).status_code == 201
    examples = client.get('/api/v1/vehicles').json()
    own = own_vehicle(client).json()
    orders = client.get('/api/v1/orders', params={'service_date': DAY.isoformat()}).json()
    with Session(engine) as session, session.begin(), pytest.raises(DataError) as error:
        submit(session, owner, request([*examples, own], orders), 'mixed')
    assert error.value.code == 'EXAMPLES_MIXED'
    with Session(engine) as session, session.begin():
        job = submit(session, owner, request(examples, orders), 'examples')
    with Session(engine) as session:
        inputs = PlanningInput.model_validate(session.get(Job, job.id).input_data)
    assert inputs.example and [row.data.code for row in inputs.depots] == ['EXAMPLE']
    assert all(row.example for row in [*inputs.vehicles, *inputs.orders])
    with pytest.raises(ValueError, match='planned only with other examples'):
        PlanningInput.model_validate({**inputs.model_dump(mode='json'),
                                      'vehicles': [{**inputs.vehicles[0].model_dump(mode='json'), 'example': False},
                                                   inputs.vehicles[1].model_dump(mode='json')]})


def test_a_ready_example_plan_says_so_on_its_sheets_and_csv_rows(client, database):  # noqa: F811
    engine, _, settings = database
    assert add(client).status_code == 201
    vehicles = client.get('/api/v1/vehicles').json()
    orders = client.get('/api/v1/orders', params={'service_date': DAY.isoformat()}).json()
    with Session(engine) as session, session.begin():
        job = submit(session, settings.demo_organization_id, request(vehicles, orders), 'ready')
    with Session(engine) as session:
        data = session.get(Job, job.id).input_data
    # Calculated on roads as a company's plans are (this test workspace itself is the legacy sample).
    inputs = PlanningInput.model_validate({**data, 'data_kind': 'ROUTED'})
    plan = routed_plan(database, inputs, {0: compatible(inputs, 0, 2)})
    version = ready(database, plan, 1).version
    assert sheets(database, plan, version).example is True
    rows = list(csv.DictReader(io.StringIO(export(database, plan, version)[0])))
    assert rows and all(row['planning_notice'].startswith(EXAMPLE_NOTICE['en']) for row in rows)
    french = list(csv.DictReader(io.StringIO(export(database, plan, version, spreadsheet=True, language='fr')[0][1:]), delimiter=';'))
    assert french[0]['planning_notice'].startswith(EXAMPLE_NOTICE['fr'])


def test_removing_the_examples_keeps_the_company_s_own_records(client, database):  # noqa: F811
    engine, _, settings = database
    owner = settings.demo_organization_id
    assert add(client).status_code == 201 and add(client, date(2026, 11, 4)).status_code == 201
    own = own_vehicle(client).json()
    vehicles = [v for v in client.get('/api/v1/vehicles').json() if v['example']]
    orders = client.get('/api/v1/orders', params={'service_date': DAY.isoformat()}).json()
    with Session(engine) as session, session.begin():
        job = submit(session, owner, request(vehicles, orders), 'busy')
    busy = client.post('/api/v1/examples/remove', headers=WRITE)
    assert (busy.status_code, busy.json()['code']) == (409, 'JOB_ACTIVE')
    with engine.begin() as connection:
        connection.execute(text("UPDATE planning_jobs SET status = 'FAILED', stage = 'FAILED', finished_at = now(), "
                                "error = '{\"code\": \"TEST\", \"message\": \"test\"}' WHERE id = :id"), {'id': job.id})
    inputs = PlanningInput.model_validate({**session_input(engine, job.id), 'data_kind': 'ROUTED'})
    routed_plan(database, inputs, {0: compatible(inputs, 0, 2)})
    removed = client.post('/api/v1/examples/remove', headers=WRITE)
    assert removed.status_code == 200 and removed.json()['removed'] == 1 + 2 + 20, removed.text
    assert [v['id'] for v in client.get('/api/v1/vehicles').json()] == [own['id']]
    assert [p['main'] for p in client.get('/api/v1/depots').json()] == [True]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Plan)) == 0
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(Order)) == 0
        assert session.scalar(select(func.count()).select_from(Vehicle)) == 1
    assert client.post('/api/v1/examples/remove', headers=WRITE).json()['removed'] == 0


def session_input(engine, key):
    with Session(engine) as session:
        return session.get(Job, key).input_data


def test_a_new_company_adds_and_removes_examples_and_plans_them_on_roads(database):
    engine, _, base = database
    settings = Settings.model_validate({**base.model_dump(), 'app_mode': 'local', 'setup_token': TOKEN})
    with TestClient(create_app(settings), base_url=settings.public_origin) as company:
        setup = company.post('/api/v1/setup', json=payload(), headers=WRITE)
        assert setup.status_code == 201, setup.text
        headers = {**WRITE, 'X-CSRF-Token': setup.json()['csrf_token'], 'Idempotency-Key': 'examples'}
        assert company.get('/api/v1/configuration').json()['examples'] is False
        assert company.post('/api/v1/examples', json={'service_date': DAY.isoformat()}, headers=headers).status_code == 201
        assert company.get('/api/v1/configuration').json()['examples'] is True
        vehicles = company.get('/api/v1/vehicles').json()
        orders = company.get('/api/v1/orders', params={'service_date': DAY.isoformat()}).json()
        queued = company.post('/api/v1/optimization-jobs', json=request(vehicles, orders).model_dump(mode='json'), headers=headers)
        assert queued.status_code == 202, queued.text
        # A company's example plan is calculated on roads, so it can be made Ready, its outputs stamped.
        assert session_input(engine, UUID(queued.json()['id']))['data_kind'] == 'ROUTED'
        with engine.begin() as connection:
            connection.execute(text("UPDATE planning_jobs SET status = 'FAILED', stage = 'FAILED', finished_at = now(), "
                                    "error = '{\"code\": \"TEST\", \"message\": \"test\"}'"))
        assert company.post('/api/v1/examples/remove', headers=headers).json()['removed'] == 13
        assert company.get('/api/v1/configuration').json()['examples'] is False


def test_upgrading_turns_a_setup_sample_into_a_company_with_examples(database, client):  # noqa: F811
    from alembic import command
    from sqlalchemy.exc import DBAPIError
    engine, config, settings = database
    owner = settings.demo_organization_id
    # A sample created by first-use setup (it has a user), with the sample's vehicles.
    command.downgrade(config, '0015_depots')
    with engine.begin() as connection:
        connection.execute(text("UPDATE organizations SET is_demo = true"))
        connection.execute(text("INSERT INTO users (id, organization_id, username, display_name, password_hash, role, active, language) "
                                "VALUES (gen_random_uuid(), :owner, 'sample-admin', 'Sample admin', 'x', 'admin', true, 'en')"),
                           {'owner': owner})
        connection.execute(text("INSERT INTO vehicles (id, organization_id, version, identifier, data) "
                                "VALUES (gen_random_uuid(), :owner, 1, 'TRUCK-DEMO-01', CAST(:data AS jsonb))"),
                           {'owner': owner, 'data': json.dumps(VehicleSource.weekly(FIXTURE.vehicles[0].data).model_dump(mode='json'))})
    command.upgrade(config, 'head')
    with engine.connect() as connection:
        assert connection.execute(text('SELECT is_demo FROM organizations')).scalar() is False
        assert connection.execute(text('SELECT example FROM vehicles')).scalar() is True
        assert connection.execute(text('SELECT example FROM depots WHERE main')).scalar() is False
    with pytest.raises(DBAPIError, match='example records exist'):
        command.downgrade(config, '0015_depots')
    with engine.begin() as connection:
        connection.execute(text('SELECT public.remove_examples(:owner)'), {'owner': owner})
    command.downgrade(config, '0015_depots')
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pg_proc WHERE proname = 'start_company_from_sample'")).scalar() == 1
    command.upgrade(config, 'head')


def test_upgrading_keeps_the_legacy_local_demonstration_a_sample(database, client):  # noqa: F811
    from alembic import command
    engine, config, _ = database
    assert add(client).status_code == 201
    with engine.begin() as connection:
        connection.execute(text('DELETE FROM orders'))
        connection.execute(text('DELETE FROM vehicles'))
        connection.execute(text('DELETE FROM depots WHERE example'))
    command.downgrade(config, '0015_depots')
    command.upgrade(config, 'head')
    with engine.connect() as connection:
        # No user account: the local demonstration, which stays a sample.
        assert connection.execute(text('SELECT is_demo FROM organizations')).scalar() is True
