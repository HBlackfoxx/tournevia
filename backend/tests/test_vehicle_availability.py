"""Weekly working hours and days off: a calculation dates them; vehicles off that day are named."""
from copy import deepcopy
from datetime import date
import json

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.jobs import OptimizationRequest
from app.main import create_app
from app.records import DataError, Order, Vehicle
from test_jobs import WRITE, enqueue, seed

pytestmark = pytest.mark.postgres
WINTER_DAY = date(2026, 11, 4)  # A Wednesday in winter time; the fixture's own day, 23 September, is summer time.


def move_orders_to(database, request: OptimizationRequest, day: date) -> OptimizationRequest:
    engine, _, _ = database
    with Session(engine) as session, session.begin():
        for key in request.order_ids:
            row = session.get(Order, key)
            data = deepcopy(row.data)
            data['service_date'] = day.isoformat()
            data['time_window'] = {k: data['time_window'][k].replace('2026-09-23', day.isoformat()).replace('+02:00', '+01:00')
                                   for k in ('start', 'end')}
            row.data, row.service_date = data, day
    return request.model_copy(update={'service_date': day, 'source_versions': None})


def edit(client, key, change):
    current = client.get(f'/api/v1/vehicles/{key}')
    response = client.put(f'/api/v1/vehicles/{key}', json={**current.json()['data'], **change},
                          headers={**WRITE, 'If-Match': current.headers['etag']})
    assert response.status_code == 200, response.text
    return response.json()


def test_weekly_hours_date_the_calculation_and_days_off_name_the_vehicles(database):
    engine, _, settings = database
    request = move_orders_to(database, seed(database), WINTER_DAY)
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        first, second = (str(key) for key in request.vehicle_ids)
        numbers = [client.get(f'/api/v1/vehicles/{key}').json()['data']['fleet_number'] for key in (first, second)]
        # Saved hours are local: 07:30 in summer and in winter, whatever the offset that day.
        edit(client, first, {'hours': {'wednesday': {'start': '07:30', 'end': '17:00'}}})
        edit(client, second, {'exceptions': [{'date': str(WINTER_DAY), 'hours': None}]})
        summary = client.get('/api/v1/vehicles/summary', params={'service_date': str(WINTER_DAY)}).json()
        assert summary['off_day'] == 1
        off = client.get('/api/v1/vehicles', params={'status': 'off_day', 'service_date': str(WINTER_DAY)}).json()
        assert [v['data']['fleet_number'] for v in off] == [numbers[1]]
        refused = client.post('/api/v1/optimization-jobs', json=request.model_dump(mode='json'),
                              headers={**WRITE, 'Idempotency-Key': 'day-off'})
        assert refused.status_code == 422 and refused.json()['code'] == 'VEHICLE_OFF_DAY'
        assert refused.json()['identifiers'] == [numbers[1]]
        # Another day's exception does not matter; working that day with other hours does.
        edit(client, second, {'exceptions': [{'date': str(WINTER_DAY), 'hours': {'start': '09:00', 'end': '12:30'}},
                                             {'date': '2026-11-05', 'hours': None}]})
        assert client.get('/api/v1/vehicles/summary', params={'service_date': str(WINTER_DAY)}).json()['off_day'] == 0
    job = enqueue(database, request)
    with Session(engine) as session:
        windows = {row['data']['fleet_number']: row['data']['available_window'] for row in
                   session.execute(text('SELECT input_data FROM planning_jobs WHERE id = :id'), {'id': job.id}).scalar()['vehicles']}
    assert windows == {numbers[0]: {'start': '2026-11-04T07:30:00+01:00', 'end': '2026-11-04T17:00:00+01:00'},
                       numbers[1]: {'start': '2026-11-04T09:00:00+01:00', 'end': '2026-11-04T12:30:00+01:00'}}


def test_a_vehicle_without_hours_that_weekday_is_off_and_the_source_rules_hold(database):
    engine, _, settings = database
    request = seed(database)
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        key = str(request.vehicle_ids[0])
        edit(client, key, {'hours': {'monday': {'start': '07:30', 'end': '17:00'}}})  # 23 September is a Wednesday.
        with pytest.raises(DataError) as failure:
            enqueue(database, request.model_copy(update={'source_versions': None}))
        assert failure.value.code == 'VEHICLE_OFF_DAY'
        data = client.get(f'/api/v1/vehicles/{key}').json()['data']
        for bad in [{'hours': {'monday': {'start': '17:00', 'end': '07:30'}}},     # overnight
                    {'hours': {'monday': {'start': '7:30', 'end': '17:00'}}},      # not HH:MM
                    {'hours': {'funday': {'start': '07:30', 'end': '17:00'}}},     # no such day
                    {'exceptions': [{'date': '2026-12-25', 'hours': None}] * 2}]:  # one exception per date
            response = client.put(f'/api/v1/vehicles/{key}', json={**data, **bad}, headers={**WRITE, 'If-Match': '"2"'})
            assert response.status_code == 422, bad
        # Exceptions come back in date order.
        saved = edit(client, key, {'exceptions': [{'date': '2026-12-31', 'hours': None}, {'date': '2026-12-24', 'hours': None}]})
        assert [e['date'] for e in saved['data']['exceptions']] == ['2026-12-24', '2026-12-31']
    with Session(engine) as session:
        assert session.get(Vehicle, request.vehicle_ids[0]).data['hours']['wednesday'] is None


def test_the_upgrade_keeps_a_company_vehicle_to_its_date_and_lets_a_sample_work_every_day(database):
    engine, config, settings = database
    request = seed(database)
    company, sample = request.vehicle_ids
    command.downgrade(config, '0011_depot_address')
    with engine.begin() as connection:
        rows = dict(connection.execute(text('SELECT id, data FROM vehicles')).all())
        assert 'available_window' in rows[company] and 'hours' not in rows[company]
        # As an installation before the upgrade stored them: one dated window each.
        for key, data in rows.items():
            data['available_window'] = {'start': '2026-09-23T07:30:00+02:00', 'end': '2026-09-23T17:00:00+02:00'}
            connection.execute(text('UPDATE vehicles SET data = CAST(:data AS jsonb) WHERE id = :id'), {'id': key, 'data': json.dumps(data)})
        # The second vehicle belongs to a sample company; the first to a real one.
        other = connection.execute(text("INSERT INTO organizations (id, name, is_demo) VALUES (gen_random_uuid(), 'Sample', true) RETURNING id")).scalar()
        connection.execute(text('UPDATE organizations SET is_demo = false WHERE id = :owner'), {'owner': settings.demo_organization_id})
        connection.execute(text('UPDATE vehicles SET organization_id = :other WHERE id = :id'), {'other': other, 'id': sample})
    command.upgrade(config, 'head')
    with engine.connect() as connection:
        rows = dict(connection.execute(text('SELECT id, data FROM vehicles')).all())
    window = {'start': '07:30', 'end': '17:00'}
    assert rows[company]['hours'] == dict.fromkeys(('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'))
    assert rows[company]['exceptions'] == [{'date': '2026-09-23', 'hours': window}]
    assert list(rows[sample]['hours'].values()) == [window] * 7
    assert rows[sample]['exceptions'] == [] and 'available_window' not in rows[sample]
