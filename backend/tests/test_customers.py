"""Saved customers: one reviewed entrance reused by every later delivery, by hand or by import."""
import csv
import io

import pytest

from app.config import ROOT
from test_geocoding import WRITE, client_for
from test_numbering import DAY, delivery, upload

pytestmark = pytest.mark.postgres
ENTRANCE = {'latitude': 48.8156, 'longitude': 2.3889}


def customer(code='CLI-1', **changes):
    return {'code': code, 'name': 'Boulangerie Fictive', 'address': '5 Rue Fictive, 94200 Ivry-sur-Seine',
            'location': {**ENTRANCE, 'reviewed': False}, 'usual_hours': {'start': '06:00', 'end': '09:00'},
            'service_seconds': 600, 'required_skills': ['TAIL_LIFT'], 'instructions': 'Back door.', **changes}


def reviewed_customer(client, code='CLI-1', **changes):
    created = client.post('/api/v1/customers', json=customer(code, **changes), headers=WRITE)
    assert created.status_code == 201, created.text
    # As for any point: saved first, then confirmed in a version-checked edit.
    assert created.json()['data']['location']['reviewed'] is False
    confirmed = {**created.json()['data'], 'location': {**ENTRANCE, 'reviewed': True}}
    response = client.put(f"/api/v1/customers/{created.json()['id']}", json=confirmed, headers={**WRITE, 'If-Match': '"1"'})
    assert response.status_code == 200 and response.json()['data']['location']['reviewed'] is True
    return response.json()


def seed_orders(client):
    assert upload(client, (ROOT / 'examples/orders.csv').read_bytes(), dry=False).json()['committed']


def test_customers_are_listed_searched_and_counted(database):
    with client_for(database) as client:
        reviewed_customer(client)
        client.post('/api/v1/customers', json=customer('CLI-2', name='Garage Exemple', location=None), headers=WRITE)
        assert client.post('/api/v1/customers', json=customer('CLI-2'), headers=WRITE).json()['code'] == 'IDENTIFIER_CONFLICT'
        assert [c['data']['code'] for c in client.get('/api/v1/customers').json()] == ['CLI-1', 'CLI-2']
        assert [c['data']['code'] for c in client.get('/api/v1/customers', params={'q': 'garage'}).json()] == ['CLI-2']
        assert [c['data']['code'] for c in client.get('/api/v1/customers', params={'status': 'missing'}).json()] == ['CLI-2']
        assert client.get('/api/v1/customers/summary').json() == {'total': 2, 'active': 2, 'inactive': 0, 'review': 0, 'missing': 1}
        bad = client.post('/api/v1/customers', json=customer('CLI-3', usual_hours={'start': '10:00', 'end': '09:00'}), headers=WRITE)
        assert bad.status_code == 422


def test_a_delivery_for_a_customer_reuses_its_reviewed_entrance_and_only_that_point(database):
    with client_for(database) as client:
        seed_orders(client)
        reviewed_customer(client)
        linked = client.post('/api/v1/orders', json={**delivery(client, 'D-1'), 'customer': 'CLI-1', 'location': None}, headers=WRITE)
        assert linked.status_code == 201, linked.text
        assert linked.json()['data']['location'] == {**ENTRANCE, 'reviewed': True}
        # The very same point is the customer's reviewed entrance; another point is the delivery's own.
        same = client.post('/api/v1/orders', json={**delivery(client, 'D-2'), 'customer': 'CLI-1',
                                                   'location': {**ENTRANCE, 'reviewed': False}}, headers=WRITE)
        assert same.json()['data']['location']['reviewed'] is True
        other = client.post('/api/v1/orders', json={**delivery(client, 'D-3'), 'customer': 'CLI-1',
                                                    'location': {'latitude': 48.82, 'longitude': 2.39, 'reviewed': True}}, headers=WRITE)
        assert other.json()['data']['location']['reviewed'] is False
        # A review cannot be claimed without a customer.
        alone = client.post('/api/v1/orders', json={**delivery(client, 'D-4'), 'location': {**ENTRANCE, 'reviewed': True}}, headers=WRITE)
        assert alone.json()['data']['location']['reviewed'] is False
        unknown = client.post('/api/v1/orders', json={**delivery(client, 'D-5'), 'customer': 'NOPE'}, headers=WRITE)
        assert unknown.status_code == 422 and unknown.json()['code'] == 'CUSTOMER_UNKNOWN'


def test_a_reviewed_delivery_is_saved_as_a_customer_and_linked_to_it(database):
    with client_for(database) as client:
        seed_orders(client)
        order = client.get('/api/v1/orders', params={'service_date': '2026-09-23'}).json()[0]
        reviewed = {**order['data'], 'location': {**order['data']['location'], 'reviewed': True}}
        order = client.put(f"/api/v1/orders/{order['id']}", json=reviewed, headers={**WRITE, 'If-Match': f'"{order["version"]}"'}).json()
        path = f"/api/v1/orders/{order['id']}/customer"
        assert client.post(path, json={'code': 'CLI-9', 'name': 'Client'}, headers={**WRITE, 'If-Match': '"1"'}).status_code == 412
        saved = client.post(path, json={'code': 'CLI-9', 'name': 'Client'}, headers={**WRITE, 'If-Match': f'"{order["version"]}"'})
        assert saved.status_code == 201, saved.text
        made, linked = saved.json()['customer']['data'], saved.json()['order']['data']
        assert made['location'] == order['data']['location'] and made['location']['reviewed'] is True
        assert made['address'] == order['data']['address'] and made['usual_hours'] == {'start': '08:00', 'end': '11:30'}
        assert linked['customer'] == 'CLI-9' and linked['location']['reviewed'] is True
        again = client.post(path, json={'code': 'CLI-9', 'name': 'Client'},
                            headers={**WRITE, 'If-Match': f'"{saved.json()["order"]["version"]}"'})
        assert again.json()['code'] == 'IDENTIFIER_CONFLICT'


def file(*rows, columns=('priority', 'customer')):
    """Example delivery rows with (external_id, address, latitude, longitude, customer)."""
    header, template = list(csv.reader(io.StringIO((ROOT / 'examples/orders.csv').read_text(encoding='utf-8'))))[:2]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow([*header, *columns])
    for external_id, address, latitude, longitude, code in rows:
        row = dict(zip(header, template), external_id=external_id, address=address, latitude=latitude, longitude=longitude,
                   service_date=DAY, time_window_start=f'{DAY}T08:00:00+01:00', time_window_end=f'{DAY}T17:00:00+01:00')
        writer.writerow([*(row[name] for name in header), *({'priority': '0', 'customer': code}[c] for c in columns)])
    return out.getvalue().encode()


def test_an_import_takes_the_entrance_of_the_customer_it_names_or_whose_address_it_repeats(database):
    with client_for(database) as client:
        reviewed_customer(client)
        content = file(('I-1', '', '', '', 'CLI-1'),                                   # by code, address from the customer
                       ('I-2', '5 rue fictive,  94200 IVRY-SUR-SEINE', '', '', ''),     # by the same address
                       ('I-3', 'Somewhere else', '48.90', '2.30', 'CLI-1'),            # its own point, far from the entrance
                       ('I-4', 'Elsewhere', '', '', ''))
        preview = upload(client, content).json()
        assert preview['errors'] == [], preview['errors']
        rows = {r['data']['external_id']: r for r in preview['rows']}
        assert (rows['I-1']['location_source'], rows['I-1']['customer_match']) == ('CUSTOMER', 'CODE')
        assert rows['I-1']['data']['address'] == '5 Rue Fictive, 94200 Ivry-sur-Seine'
        assert rows['I-1']['data']['location'] == {**ENTRANCE, 'reviewed': True}
        assert (rows['I-2']['location_source'], rows['I-2']['customer_match'], rows['I-2']['data']['customer']) == ('CUSTOMER', 'ADDRESS', 'CLI-1')
        assert rows['I-3']['location_source'] == 'FILE'
        assert 'CUSTOMER_POINT_DIFFERS' in [w['code'] for w in rows['I-3']['warnings']]
        assert rows['I-4']['location_source'] == 'NONE' and rows['I-4']['customer_match'] is None
        assert upload(client, content, dry=False).json()['committed']
        saved = {o['data']['external_id']: o['data'] for o in client.get('/api/v1/orders', params={'service_date': DAY}).json()}
        assert saved['I-1']['location']['reviewed'] is True and saved['I-2']['location']['reviewed'] is True
        assert saved['I-3']['location']['reviewed'] is False
        # Importing the same file again changes nothing, reviews included.
        again = upload(client, content).json()
        assert again['errors'] == [] and {r['action'] for r in again['rows']} == {'UNCHANGED'}
        unknown = upload(client, file(('I-9', 'X', '', '', 'NOPE'))).json()
        assert [(e['field'], e['message']) for e in unknown['errors']] == [('customer', 'No saved customer has this code.')]
        # Files that stop after priority, or before it, are still read.
        assert upload(client, file(('I-10', 'X', '', '', ''), columns=('priority',))).json()['errors'] == []
        assert upload(client, file(('I-11', 'X', '', '', ''), columns=())).json()['errors'] == []
        reordered = upload(client, file(('I-12', 'X', '', '', ''), columns=('customer', 'priority'))).json()
        assert reordered['errors'][0]['field'] == 'headers'


def test_a_customer_code_deliveries_name_cannot_change_and_an_old_rename_fails_only_its_row(database):
    from sqlalchemy import text
    engine, _, _ = database
    with client_for(database) as client:
        seed_orders(client)
        saved = reviewed_customer(client)
        linked = client.post('/api/v1/orders', json={**delivery(client, 'D-1'), 'customer': 'CLI-1', 'location': None}, headers=WRITE)
        assert linked.status_code == 201, linked.text
        at = {**WRITE, 'If-Match': f'"{saved["version"]}"'}
        renamed = client.put(f"/api/v1/customers/{saved['id']}", json={**saved['data'], 'code': 'CLI-9'}, headers=at)
        assert renamed.status_code == 409
        assert (renamed.json()['code'], renamed.json()['identifiers']) == ('CUSTOMER_IN_USE', ['D-1'])
        # Its other details still change, and a customer no delivery names may take a new code.
        assert client.put(f"/api/v1/customers/{saved['id']}", json={**saved['data'], 'name': 'Renamed'}, headers=at).status_code == 200
        unused = reviewed_customer(client, 'CLI-2')
        recoded = client.put(f"/api/v1/customers/{unused['id']}", json={**unused['data'], 'code': 'CLI-3'},
                             headers={**WRITE, 'If-Match': f'"{unused["version"]}"'})
        assert recoded.status_code == 200 and recoded.json()['data']['code'] == 'CLI-3'
        # A code changed before renames were refused: re-importing the delivery names its row instead of failing.
        with engine.begin() as connection:
            connection.execute(text("UPDATE customers SET identifier = 'CLI-OLD', data = jsonb_set(data, '{code}', '\"CLI-OLD\"') "
                                    "WHERE identifier = 'CLI-1'"))
        again = client.post('/api/v1/imports/orders?dry_run=true&update_existing=true', headers={**WRITE, 'Content-Type': 'text/csv'},
                            content=file(('D-1', '5 Rue Fictive, 94200 Ivry-sur-Seine', '48.8156', '2.3889', ''), columns=('priority',)))
        assert again.status_code == 422
        assert [(e['row'], e['field'], e['message']) for e in again.json()['errors']] == [(2, 'customer', 'No saved customer has this code.')]
