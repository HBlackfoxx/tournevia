"""Delivery and fleet lists: search, filters and counts cover the whole day or fleet, not one page."""
from datetime import date
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.config import ROOT
from app.main import create_app
from app.planning import DAYS, OrderInput, VehicleSource
from app.provision import provision_demo
from app.records import Order, Vehicle, write_record
from test_postgres import example

pytestmark = pytest.mark.postgres
DAY = '2026-09-23'


def sample(name):
    return json.loads((ROOT / f'examples/{name}.json').read_text())


def order(external_id, *, address='Synthetic entrance, Paris', active=True, reviewed=True, located=True, day=DAY):
    data = sample('order')
    data.update(external_id=external_id, address=address, active=active, service_date=day)
    data['time_window'] = {k: v.replace(DAY, day) for k, v in data['time_window'].items()}
    data['location'] = {**data['location'], 'reviewed': reviewed} if located else None
    return OrderInput.model_validate(data)


def vehicle(fleet_number, *, active=True, day=DAY, skills=('TAIL_LIFT',), payload=9500):
    """A vehicle that works only on the weekday of `day`."""
    data = sample('vehicle')
    data.update(fleet_number=fleet_number, active=active, skills=list(skills))
    data['capacity']['weight_kg'] = payload
    data['hours'] = {DAYS[date.fromisoformat(day).weekday()]: {'start': '07:30', 'end': '17:00'}}
    return VehicleSource.model_validate(data)


@pytest.fixture
def client(database):
    engine, _, settings = database
    owner = settings.demo_organization_id
    with Session(engine) as session, session.begin():
        provision_demo(session, settings, example())
        for data in [order('PAR-003', address='12 Rue de Rivoli, Paris'), order('PAR-001', reviewed=False),
                     order('PAR-002', active=False), order('PAR-004', located=False), order('PAR_10%'),
                     order('PAR-005', day='2026-09-24'),
                     vehicle('TRK-02', skills=('REFRIGERATED', 'TAIL_LIFT')), vehicle('TRK-01', day='2026-09-24'),
                     vehicle('TRK-03', active=False, skills=('CRANE',), payload=4000)]:
            row = write_record(session, owner, data)
            # A new location is unreviewed on creation; store the state each case needs.
            if isinstance(data, OrderInput) and data.location is not None:
                session.get(Order, row.id).data = {**session.get(Order, row.id).data, 'location': data.location.model_dump(mode='json')}
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        yield client


def names(response, key='external_id'):
    assert response.status_code == 200, response.text
    return [row['data'][key] for row in response.json()]


def test_deliveries_are_filtered_and_searched_across_the_whole_day(client):
    get = lambda query='': client.get(f'/api/v1/orders?service_date={DAY}{query}')
    assert names(get()) == ['PAR-001', 'PAR-002', 'PAR-003', 'PAR-004', 'PAR_10%']  # Identifier order.
    assert names(get('&status=review')) == ['PAR-001']
    assert names(get('&status=missing')) == ['PAR-004']
    assert names(get('&status=inactive')) == ['PAR-002']
    assert names(get('&status=ready')) == ['PAR-003', 'PAR_10%']
    assert names(get('&q=rivoli')) == ['PAR-003']  # Address, case-insensitive.
    assert names(get('&q=par-00&status=active&limit=2&offset=1')) == ['PAR-003', 'PAR-004']
    assert names(get('&q=_1')) == ['PAR_10%'] and names(get('&q=%25')) == ['PAR_10%']  # Wildcards are literal.
    assert get('&status=off_day').status_code == 422 and get('&q=').status_code == 422
    summary = client.get(f'/api/v1/orders/summary?service_date={DAY}').json()
    assert summary == {'total': 5, 'active': 4, 'inactive': 1, 'review': 1, 'missing': 1, 'ready': 2}
    searched = client.get(f'/api/v1/orders/summary?service_date={DAY}&q=PAR-00').json()
    assert searched['total'] == 4 and searched['ready'] == 1
    assert client.get('/api/v1/orders/summary?service_date=2026-09-25').json()['total'] == 0


def test_fleet_counts_and_off_day_vehicles_cover_the_whole_fleet(client):
    get = lambda query='': client.get(f'/api/v1/vehicles?limit=50{query}')
    assert names(get(), 'fleet_number') == ['TRK-01', 'TRK-02', 'TRK-03']
    assert names(get('&status=inactive'), 'fleet_number') == ['TRK-03']
    assert names(get(f'&status=off_day&service_date={DAY}'), 'fleet_number') == ['TRK-01']
    assert names(get('&status=off_day&service_date=2026-09-24'), 'fleet_number') == ['TRK-02']
    assert get('&status=off_day').json()['code'] == 'SERVICE_DATE_REQUIRED'
    assert names(get('&q=trk-02'), 'fleet_number') == ['TRK-02']
    summary = client.get(f'/api/v1/vehicles/summary?service_date={DAY}').json()
    assert summary == {'total': 3, 'active': 2, 'inactive': 1, 'off_day': 1, 'active_payload_kg': 19000,
                       'equipment': ['CRANE', 'REFRIGERATED', 'TAIL_LIFT']}
    assert client.get(f'/api/v1/vehicles/summary?service_date={DAY}&q=TRK-03').json()['active_payload_kg'] == 0


def test_lists_and_counts_stay_within_the_company(client, database):
    engine, _, settings = database
    other = uuid4()
    with Session(engine) as session, session.begin():
        provision_demo(session, settings.model_copy(update={'demo_organization_id': other}), example())
        write_record(session, other, order('PAR-900'))
        write_record(session, other, vehicle('TRK-900'))
    assert 'PAR-900' not in names(client.get(f'/api/v1/orders?service_date={DAY}&q=900'))
    assert client.get(f'/api/v1/orders/summary?service_date={DAY}').json()['total'] == 5
    assert client.get(f'/api/v1/vehicles/summary?service_date={DAY}').json()['total'] == 3
    with Session(engine) as session:
        assert session.query(Vehicle).filter(Vehicle.organization_id == other).count() == 1
