"""Re-importing changed records, cancelling and restoring deliveries, and deleting them."""
import csv
import io

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import ROOT
from app.edits import SOURCES_CHANGED_MESSAGE, source_change
from app.jobs import Plan
from app.planning import PlanningInput
from app.records import DataError, Order, Versioned, delete_orders
from test_edits import saved_plan
from test_jobs import enqueue, seed
from test_sources import WRITE, client  # noqa: F401  (fixture)

pytestmark = pytest.mark.postgres
DAY = '2026-09-23'


def upload(client, kind, content, dry=True, update=True):  # noqa: F811
    return client.post(f'/api/v1/imports/{kind}?dry_run={str(dry).lower()}&update_existing={str(update).lower()}',
                       content=content, headers={**WRITE, 'Content-Type': 'text/csv'})


def table(kind):
    return list(csv.DictReader(io.StringIO((ROOT / f'examples/{kind}.csv').read_text(encoding='utf-8'))))


def written(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def review(client, order):  # noqa: F811
    data = {**order['data'], 'location': {**order['data']['location'], 'reviewed': True}}
    response = client.put(f"/api/v1/orders/{order['id']}", json=data, headers={**WRITE, 'If-Match': f'"{order["version"]}"'})
    assert response.status_code == 200, response.text
    return response.json()


def orders(client):  # noqa: F811
    return {o['data']['external_id']: o for o in client.get('/api/v1/orders', params={'service_date': DAY}).json()}


def test_a_reimport_changes_existing_deliveries_only_when_asked_and_keeps_their_reviewed_points(client):  # noqa: F811
    rows = table('orders')
    assert upload(client, 'orders', written(rows), dry=False).json()['committed']
    before = {key: review(client, o) for key, o in orders(client).items()}
    changed = [{**rows[0], 'service_seconds': '1500', 'latitude': '', 'longitude': ''},  # no point: keeps the reviewed one
               {**rows[1], 'address': 'Synthetic delivery entrance B2, Paris'},     # the same point: keeps its review
               {**rows[1], 'external_id': 'NEW-1'}]
    refused = upload(client, 'orders', written(changed), update=False)
    assert refused.status_code == 422 and 'version-checked edit' in refused.text
    preview = upload(client, 'orders', written(changed)).json()
    assert (preview['inserted'], preview['updated'], preview['unchanged']) == (1, 2, 0)
    first, second, new = preview['rows']
    assert (first['action'], first['changes'], first['location_source']) == ('UPDATE', ['service_seconds'], 'SAVED')
    assert first['data']['location'] == before['DEMO-ORDER-001']['data']['location']
    assert (second['action'], second['changes'], second['location_source']) == ('UPDATE', ['address'], 'FILE')
    assert second['data']['location']['reviewed'] is True
    assert (new['action'], new['changes'], new['data']['location']['reviewed']) == ('INSERT', [], False)
    assert orders(client)['DEMO-ORDER-001']['data']['service_seconds'] == 1200  # A preview changes nothing.
    done = upload(client, 'orders', written(changed), dry=False).json()
    assert done['committed'] and done['updated'] == 2
    after = orders(client)
    assert after['DEMO-ORDER-001']['data']['service_seconds'] == 1500
    assert after['DEMO-ORDER-001']['data']['location']['reviewed'] is True
    assert after['DEMO-ORDER-001']['version'] == before['DEMO-ORDER-001']['version'] + 1
    assert after['DEMO-ORDER-002']['data']['address'] == 'Synthetic delivery entrance B2, Paris'
    # Unchanged again: nothing to write. A point moved in the file must be reviewed again.
    assert upload(client, 'orders', written(changed)).json()['unchanged'] == 3
    moved = upload(client, 'orders', written([{**changed[1], 'latitude': '48.851'}]), dry=False).json()['rows'][0]
    assert moved['changes'] == ['location'] and moved['data']['location']['reviewed'] is False
    assert orders(client)['DEMO-ORDER-002']['data']['location']['reviewed'] is False


def test_a_reimport_of_vehicles_changes_their_hours_and_keeps_their_days_off(client):  # noqa: F811
    rows = table('vehicles')
    assert upload(client, 'vehicles', written(rows), dry=False).json()['committed']
    truck = next(v for v in client.get('/api/v1/vehicles').json() if v['data']['fleet_number'] == 'TRUCK-DEMO-01')
    off = [{'date': '2026-11-11', 'hours': None}]
    updated = client.put(f"/api/v1/vehicles/{truck['id']}", json={**truck['data'], 'exceptions': off},
                         headers={**WRITE, 'If-Match': f'"{truck["version"]}"'})
    assert updated.status_code == 200, updated.text
    done = upload(client, 'vehicles', written([{**rows[0], 'saturday': '07:00-12:00'}, rows[1]]), dry=False).json()
    assert (done['updated'], done['unchanged']) == (1, 1) and done['rows'][0]['changes'] == ['hours']
    truck = client.get(f"/api/v1/vehicles/{truck['id']}").json()
    assert truck['data']['hours']['saturday'] == {'start': '07:00', 'end': '12:00'}
    assert truck['data']['exceptions'] == off


def test_deliveries_are_cancelled_restored_and_deleted_at_their_current_version(client):  # noqa: F811
    assert upload(client, 'orders', (ROOT / 'examples/orders.csv').read_bytes(), dry=False).json()['committed']
    first, second = orders(client).values()
    selection = [{'id': o['id'], 'version': o['version']} for o in (first, second)]
    cancelled = client.post('/api/v1/orders/activation', json={'orders': selection, 'active': False}, headers=WRITE)
    assert cancelled.status_code == 200, cancelled.text
    assert [(o['data']['active'], o['version']) for o in cancelled.json()] == [(False, 2), (False, 2)]
    assert client.get('/api/v1/orders/summary', params={'service_date': DAY}).json()['inactive'] == 2
    stale = client.post('/api/v1/orders/activation', json={'orders': selection, 'active': True}, headers=WRITE)
    assert stale.status_code == 412 and stale.json()['code'] == 'SOURCE_CHANGED'
    current = [{'id': o['id'], 'version': o['version']} for o in cancelled.json()]
    restored = client.post('/api/v1/orders/activation', json={'orders': current[:1], 'active': True}, headers=WRITE).json()
    assert (restored[0]['data']['active'], restored[0]['version']) == (True, 3)
    twice = client.post('/api/v1/orders/delete', json={'orders': current[1:] * 2}, headers=WRITE)
    assert twice.status_code == 422 and twice.json()['code'] == 'DUPLICATE_RECORD'
    assert client.post('/api/v1/orders/delete', json={'orders': current[:1]}, headers=WRITE).status_code == 412
    deleted = client.post('/api/v1/orders/delete', json={'orders': current[1:]}, headers=WRITE)
    assert deleted.status_code == 200 and deleted.json() == {'deleted': 1}
    assert list(orders(client)) == [first['data']['external_id']]
    assert client.get(f"/api/v1/orders/{second['id']}").status_code == 404
    # Deleting again finds nothing; the identifier is free for a new delivery.
    assert client.post('/api/v1/orders/delete', json={'orders': current[1:]}, headers=WRITE).status_code == 404
    assert upload(client, 'orders', (ROOT / 'examples/orders.csv').read_bytes(), dry=True).json()['inserted'] == 1


def test_a_delivery_in_a_waiting_calculation_is_not_deleted(database):
    engine, _, settings = database
    owner = settings.demo_organization_id
    request = seed(database)
    enqueue(database, request, 'waiting')
    with Session(engine) as session:
        busy = session.get(Order, request.order_ids[0])
        busy = Versioned(id=busy.id, version=busy.version)
    with Session(engine) as session, session.begin(), pytest.raises(DataError) as error:
        delete_orders(session, owner, [busy])
    assert error.value.code == 'JOB_ACTIVE'
    # The database function refuses on its own as well, whatever called it.
    with Session(engine) as session, session.begin(), pytest.raises(DBAPIError) as refused:
        session.execute(text('SELECT public.delete_orders(:owner, :keys)'), {'owner': owner, 'keys': [busy.id]})
    assert refused.value.orig.sqlstate == '55006'
    with Session(engine) as session:
        assert session.get(Order, busy.id) is not None


def test_a_saved_plan_keeps_its_copy_of_a_deleted_delivery_and_asks_for_a_fresh_calculation(database):
    engine, _, settings = database
    owner = settings.demo_organization_id
    plan_id, snapshot, _ = saved_plan(database)
    planned = snapshot.orders[0]
    with Session(engine) as session, session.begin():
        assert delete_orders(session, owner, [Versioned(id=planned.id, version=planned.version)]) == 1
    with Session(engine) as session:
        assert session.get(Order, planned.id) is None
        plan = session.get(Plan, plan_id)
        inputs = PlanningInput.model_validate(plan.input_data)
        assert planned.id in {o.id for o in inputs.orders}
        assert source_change(session, owner, inputs) == SOURCES_CHANGED_MESSAGE


def test_a_file_without_an_optional_column_keeps_that_value_and_an_empty_cell_resets_it(client):  # noqa: F811
    rows = table('orders')
    assert upload(client, 'orders', written(rows), dry=False).json()['committed']
    first = orders(client)['DEMO-ORDER-001']
    changed = client.put(f"/api/v1/orders/{first['id']}", json={**first['data'], 'priority': 70},
                         headers={**WRITE, 'If-Match': f'"{first["version"]}"'})
    assert changed.status_code == 200
    # Without the priority column the file says nothing about priority, even when changes are refused.
    assert upload(client, 'orders', written(rows), update=False).json()['unchanged'] == 2
    reset = upload(client, 'orders', written([{**rows[0], 'priority': ''}])).json()['rows'][0]
    assert (reset['action'], reset['changes'], reset['data']['priority']) == ('UPDATE', ['priority'], 0)
    vehicles = table('vehicles')
    limited = [{**vehicles[0], 'max_stops': '12', 'max_distance_m': '', 'max_driving_seconds': '28800'}]
    assert upload(client, 'vehicles', written(limited), dry=False).json()['committed']
    assert upload(client, 'vehicles', written(vehicles[:1])).json()['unchanged'] == 1
    cleared = upload(client, 'vehicles', written([{**limited[0], 'max_stops': '', 'max_driving_seconds': ''}])).json()
    assert cleared['rows'][0]['changes'] == ['limits']
