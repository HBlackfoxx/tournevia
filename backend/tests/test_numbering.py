"""Delivery numbers and what a delivery import tells the dispatcher to check."""
import csv
from datetime import date
import io

import pytest
from pydantic import ValidationError

from app.config import ROOT
from app.numbering import NumberingSettings, split
from test_geocoding import WRITE, FakeAddok, client_for, geocoder  # noqa: F401  (geocoder is a fixture)

DAY = '2026-11-03'


def test_a_format_has_one_number_and_plain_text_around_its_parts():
    assert NumberingSettings(format='LIV-{YYYY}-{N:4}').format == 'LIV-{YYYY}-{N:4}'
    assert NumberingSettings(format='').format is None
    for bad in ['LIV-', 'A{N}B{N:3}', 'LIV-{N:0}', 'LIV{X}-{N}', 'LIV}-{N}', 'L;IV-{N}']:
        with pytest.raises(ValidationError):
            NumberingSettings(format=bad)
    assert split('LIV-{YY}{MM}{DD}-{N:3}/A', date(2026, 11, 3)) == ('LIV-261103-', '/A', 3)
    assert split('{N}', date(2026, 11, 3)) == ('', '', 1)


def rows(*cells):
    """Delivery rows from the example file, each with the given (external_id, address, latitude, longitude)."""
    header, template = list(csv.reader(io.StringIO((ROOT / 'examples/orders.csv').read_text(encoding='utf-8'))))[:2]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(header)
    for external_id, address, latitude, longitude in cells:
        row = dict(zip(header, template), external_id=external_id, address=address, latitude=latitude,
                   longitude=longitude, service_date=DAY, time_window_start=f'{DAY}T08:00:00+01:00',
                   time_window_end=f'{DAY}T17:00:00+01:00')
        writer.writerow([row[name] for name in header])
    return out.getvalue().encode()


def upload(client, content, dry=True):
    return client.post(f'/api/v1/imports/orders?dry_run={str(dry).lower()}', content=content,
                       headers={**WRITE, 'Content-Type': 'text/csv'})


def delivery(client, external_id='TYPED-1'):
    order = client.get('/api/v1/orders', params={'service_date': '2026-09-23'}).json()[0]['data']
    return {**order, 'external_id': external_id, 'service_date': DAY,
            'time_window': {'start': f'{DAY}T08:00:00+01:00', 'end': f'{DAY}T17:00:00+01:00'}}


@pytest.mark.postgres
def test_new_deliveries_receive_the_next_number_in_the_company_format(database, monkeypatch):
    monkeypatch.setattr('app.data_api.paris_today', lambda: date(2026, 11, 1))
    with client_for(database) as client:
        assert upload(client, (ROOT / 'examples/orders.csv').read_bytes(), dry=False).json()['committed']
        numbering = client.get('/api/v1/workspace/numbering').json()
        # Off until chosen, with a format that would continue the example's identifiers.
        assert numbering['format'] is None and numbering['next'] is None
        assert numbering['suggestion'] == 'DEMO-ORDER-{N:3}'
        assert client.get('/api/v1/orders/next-identifier', params={'service_date': DAY}).json() == {'identifier': None}
        off = client.post('/api/v1/orders?assign_identifier=true', json=delivery(client), headers=WRITE)
        assert off.status_code == 409 and off.json()['code'] == 'NUMBERING_OFF'

        saved = client.put('/api/v1/workspace/numbering', json={'format': 'LIV-{YYYY}-{N:4}', 'start': 1}, headers=WRITE)
        assert saved.json()['next'] == 'LIV-2026-0001'
        assert client.get('/api/v1/orders/next-identifier', params={'service_date': DAY}).json() == {'identifier': 'LIV-2026-0001'}
        # Two people opened the form with the same proposal; each save receives its own number.
        first = client.post('/api/v1/orders?assign_identifier=true', json=delivery(client, 'LIV-2026-0001'), headers=WRITE)
        second = client.post('/api/v1/orders?assign_identifier=true', json=delivery(client, 'LIV-2026-0001'), headers=WRITE)
        assert [first.json()['data']['external_id'], second.json()['data']['external_id']] == ['LIV-2026-0001', 'LIV-2026-0002']
        # A hand-typed identifier in the same format moves the numbering on.
        assert client.post('/api/v1/orders', json=delivery(client, 'LIV-2026-0040'), headers=WRITE).status_code == 201
        assert client.get('/api/v1/orders/next-identifier', params={'service_date': DAY}).json()['identifier'] == 'LIV-2026-0041'
        # The year part starts again each year; the starting number continues an earlier system.
        assert client.get('/api/v1/orders/next-identifier', params={'service_date': '2027-01-04'}).json()['identifier'] == 'LIV-2027-0001'
        client.put('/api/v1/workspace/numbering', json={'format': 'LIV-{YYYY}-{N:4}', 'start': 1500}, headers=WRITE)
        assert client.get('/api/v1/orders/next-identifier', params={'service_date': DAY}).json()['identifier'] == 'LIV-2026-1500'
        bad = client.put('/api/v1/workspace/numbering', json={'format': 'LIV', 'start': 1}, headers=WRITE)
        assert bad.status_code == 422
        # Before saving another format, the page can show the number it would give.
        preview = client.get('/api/v1/workspace/numbering/preview', params={'format': 'DEMO-ORDER-{N:3}'})
        assert preview.json() == {'identifier': 'DEMO-ORDER-003'}
        assert client.get('/api/v1/workspace/numbering/preview', params={'format': 'X{N}{N}'}).status_code == 422


@pytest.mark.postgres
def test_an_import_numbers_rows_without_identifier_after_those_the_file_names(database):
    with client_for(database) as client:
        empty = rows(('', 'Synthetic entrance A', '48.84', '2.38'))
        refused = upload(client, empty).json()
        assert refused['errors'][0]['field'] == 'external_id'  # No format: an identifier is required.
        client.put('/api/v1/workspace/numbering', json={'format': 'D{N:3}', 'start': 1}, headers=WRITE)
        content = rows(('', 'Synthetic entrance A', '48.84', '2.38'), ('D001', 'Synthetic entrance B', '48.85', '2.37'),
                       (' ', 'Synthetic entrance C', '48.86', '2.36'))
        preview = upload(client, content).json()
        assert preview['errors'] == []
        assert [(r['data']['external_id'], r['numbered']) for r in preview['rows']] == [
            ('D002', True), ('D001', False), ('D003', True)]
        upload(client, content, dry=False)
        assert client.get('/api/v1/orders/next-identifier', params={'service_date': DAY}).json()['identifier'] == 'D004'


@pytest.mark.postgres
def test_a_delivery_import_says_where_each_point_comes_from_and_what_to_check(database, geocoder, monkeypatch):  # noqa: F811
    monkeypatch.setattr('app.data_api.paris_today', lambda: date(2026, 11, 5))
    content = rows(('F-1', 'Synthetic entrance A', '48.84', '2.38'),       # from the file
                   ('F-2', '12 rue de rivoli paris', '', ''),              # found from its address
                   ('F-3', 'somewhere vague', '', ''),                     # a whole town only
                   ('F-8', 'weak match', '', ''),                          # an uncertain address
                   ('F-4', 'unknown place', '', ''),                       # not found
                   ('F-5', 'Synthetic entrance A', '48.90', '2.30'),       # same address, another point
                   ('F-6', 'Swapped entrance', '2.35', '48.85'),           # latitude and longitude swapped
                   ('F-7', 'Lyon entrance', '45.76', '4.83'))              # far from the depot
    with client_for(database, geocoder_url=geocoder) as client:
        preview = upload(client, content).json()
    found = {r['data']['external_id']: (r['location_source'], [w['code'] for w in r['warnings']]) for r in preview['rows']}
    past = ['PAST_DATE']  # 3 November is before the patched 5 November.
    assert found == {
        'F-1': ('FILE', past), 'F-2': ('ADDRESS', past), 'F-3': ('NONE', past + ['ADDRESS_VAGUE']),
        'F-4': ('NONE', past + ['ADDRESS_NOT_FOUND']), 'F-5': ('FILE', past + ['SAME_ADDRESS_ELSEWHERE']),
        'F-6': ('FILE', past + ['COORDINATES_SWAPPED']), 'F-7': ('FILE', past + ['FAR_FROM_DEPOT']),
        'F-8': ('NONE', past + ['ADDRESS_UNCERTAIN'])}
    told = {w['code']: w['values'] for r in preview['rows'] for w in r['warnings']}
    assert told['ADDRESS_VAGUE'] == ['Paris'] and told['ADDRESS_UNCERTAIN'] == ['1 Rue Inconnue 75012 Paris', '41']
    assert preview['errors'] == []  # Warnings never stop an import.
    # Without address lookup, a row without coordinates is simply not located.
    with client_for(database) as client:
        alone = upload(client, rows(('G-1', '12 rue de rivoli paris', '', ''))).json()['rows'][0]
    assert alone['location_source'] == 'NONE' and [w['code'] for w in alone['warnings']] == ['PAST_DATE', 'NO_LOCATION']
    assert FakeAddok.received  # The fake geocoder answered the first import.


@pytest.mark.postgres
def test_numbering_skips_overlong_numbers_and_examples_and_reads_saved_numbers_once_per_import(database):
    from sqlalchemy import event
    with client_for(database) as client:
        assert client.post('/api/v1/examples', json={'service_date': DAY}, headers=WRITE).status_code == 201
        # Only examples so far: their identifiers suggest no format for the company's own deliveries.
        assert client.get('/api/v1/workspace/numbering').json()['suggestion'] is None
        assert upload(client, (ROOT / 'examples/orders.csv').read_bytes(), dry=False).json()['committed']
        client.put('/api/v1/workspace/numbering', json={'format': 'D{N:3}', 'start': 1}, headers=WRITE)
        # One mistyped identifier with a very long number does not make every later number as long.
        assert client.post('/api/v1/orders', json=delivery(client, 'D' + '9' * 60), headers=WRITE).status_code == 201
        assert client.post('/api/v1/orders', json=delivery(client, 'D041'), headers=WRITE).status_code == 201
        assert client.get('/api/v1/orders/next-identifier', params={'service_date': DAY}).json()['identifier'] == 'D042'
        searches = []
        listen = lambda conn, cursor, statement, *rest: searches.append(statement) if 'FROM orders' in statement and 'LIKE' in statement else None
        event.listen(client.app.state.engine, 'before_cursor_execute', listen)
        try:
            preview = upload(client, rows(*(('', f'Synthetic entrance {n}', '48.84', '2.38') for n in range(5)))).json()
        finally:
            event.remove(client.app.state.engine, 'before_cursor_execute', listen)
        assert [r['data']['external_id'] for r in preview['rows']] == ['D042', 'D043', 'D044', 'D045', 'D046']
        assert len(searches) == 1  # Not once per row.
