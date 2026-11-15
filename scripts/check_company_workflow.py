"""Company-path acceptance with fictional inputs in test_https's disposable stack.

No database provenance edits or injected READY results: all writes use named APIs,
and the actual worker computes road geometry and independently validates routes.
This is software acceptance, not evidence about an actual customer's fleet.
"""
import csv
from datetime import datetime
import io
import json
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from test_https import ROOT

PARIS = ZoneInfo('Europe/Paris')
DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


def checked(response, status=200):
    assert response.status_code == status, f'Expected HTTP {status}, received {response.status_code}.'
    return response.json()


def finished(client, job):
    end = time.monotonic() + 140
    while time.monotonic() < end:
        record = checked(client.get('/api/v1/jobs/' + job['id']))
        if record['status'] in ('SUCCEEDED', 'FAILED'):
            assert record['status'] == 'SUCCEEDED', (record.get('error') or {}).get('code')
            return checked(client.get('/api/v1/plans/' + record['plan_id']))
        time.sleep(.25)
    raise AssertionError('Company calculation exceeded acceptance deadline.')


def saved_vehicle(data):
    """A fixture vehicle as the fleet saves it: its dated window's local hours, every day of the week."""
    start, end = (datetime.fromisoformat(data['available_window'][edge]).astimezone(PARIS).strftime('%H:%M')
                  for edge in ('start', 'end'))
    return {**{key: value for key, value in data.items() if key != 'available_window'},
            'hours': {day: {'start': start, 'end': end} for day in DAYS}}


def check_company(origin, context, login):
    fixture = json.loads((ROOT/'backend/tests/fixtures/paris-ten.json').read_text())
    write = {'Origin':origin, 'X-VRP-Request':'1'}
    with httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=15) as client:
        identity = checked(client.post('/api/v1/session', json=login, headers=write))
        secrets = [client.cookies.get('__Host-vrp_session'), identity['csrf_token']]
        headers = {**write, 'X-CSRF-Token':identity['csrf_token']}
        assert checked(client.get('/api/v1/vehicles')) == []
        assert checked(client.get('/api/v1/orders', params={'service_date':fixture['service_date']})) == []
        vehicles, orders = [], []
        for collection, saved in [('vehicles',vehicles), ('orders',orders)]:
            for row in fixture[collection]:
                data = row['data'] if collection == 'orders' else saved_vehicle(row['data'])
                if collection == 'orders':
                    data['instructions'] = '=SUM(1,2)'  # Spreadsheet formula regression.
                created = checked(client.post('/api/v1/'+collection, json=data, headers=headers), 201)
                if collection == 'orders':
                    assert created['data']['location']['reviewed'] is False
                    created = checked(client.put('/api/v1/orders/'+created['id'], json=data,
                        headers={**headers,'If-Match':f'"{created["version"]}"'}))
                saved.append(created)
        depot = checked(client.get('/api/v1/depot'))
        request = {key:fixture[key] for key in ('service_date','routing_policy','objective')}
        request.update(vehicle_ids=[v['id'] for v in vehicles],order_ids=[o['id'] for o in orders],
            solver_time_limit_seconds=1,source_versions={r['id']:r['version'] for r in [depot,*vehicles,*orders]})
        submit_headers = {**headers,'Idempotency-Key':str(uuid4())}
        assert client.post('/api/v1/optimization-jobs',json={**request,'data_kind':'ROUTED'},headers=submit_headers).status_code == 422
        job = checked(client.post('/api/v1/optimization-jobs',json=request,headers=submit_headers),202)
        assert checked(client.post('/api/v1/optimization-jobs',json=request,headers=submit_headers),202)['id'] == job['id']
        plan = finished(client,job)
        assert plan['input_data']['data_kind'] == plan['result']['data_kind'] == 'ROUTED'
        assert plan['validation'] == 'PASS' and not plan['result']['unassigned']
        assert sum(len(r['stops']) for r in plan['result']['routes']) == 10
        assert all(len(r['legs']) == len(r['stops'])+1 for r in plan['result']['routes'])
        assert plan['status'] == 'DRAFT' and not plan['result']['operational_ready']
        path = '/api/v1/plans/'+plan['id']

        def versioned(p): return {**headers,'If-Match':f'"{p["version"]}"'}
        def finalize(p, accept=False):
            return client.post(path+'/finalize',json={'accept_unassigned':accept},headers=versioned(p))
        def export(p): return client.get(path+'/export.csv',headers=versioned(p))
        def edit(p, routes, unassigned):
            return checked(client.post(path+'/edits',json={'routes':routes,'unassigned_order_ids':unassigned},
                headers={**versioned(p),'Idempotency-Key':str(uuid4())}),202)

        assert export(plan).status_code == 409
        ready = checked(finalize(plan))
        assert ready['status'] == 'READY' and ready['result']['operational_ready']
        assert ready['version'] == plan['version']+1
        assert export(plan).status_code == 412
        response = export(ready)
        assert response.status_code == 200 and response.headers['content-type'].startswith('text/csv')
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert len(rows) == 10 and {r['plan_version'] for r in rows} == {str(ready['version'])}
        assert all(r['instructions'] == "'=SUM(1,2)" and 'Tolls are not included.' in r['planning_notice'] for r in rows)
        assert checked(finalize(ready))['version'] == ready['version']
        # Removing one delivery preserves the others' explicit assignment/order.
        removed = ready['result']['routes'][0]['stops'][0]['order_id']
        routes = [{'vehicle_id':r['vehicle_id'],'order_ids':[s['order_id'] for s in r['stops'] if s['order_id'] != removed]}
                  for r in ready['result']['routes']]
        routes = [r for r in routes if r['order_ids']]
        edited = edit(ready,routes,[removed])
        pending = checked(client.get(path))
        assert pending['status'] == 'DRAFT' and not pending['result']['operational_ready']
        assert export(pending).status_code != 200
        partial = finished(client,edited)
        assert partial['validation'] == 'PASS' and len(partial['result']['unassigned']) == 1
        assert checked(finalize(partial),422)['code'] == 'UNASSIGNED_NOT_ACCEPTED'
        partial_ready = checked(finalize(partial,True))
        assert len(list(csv.DictReader(io.StringIO(export(partial_ready).text)))) == 9
        # Force known capacity/equipment violations; recalculation must not authorize dispatch.
        invalid = finished(client,edit(partial_ready,[{'vehicle_id':vehicles[1]['id'],'order_ids':[o['id'] for o in orders]}],[]))
        assert invalid['validation'] == 'FAIL' and invalid['result']['blocking_issues']
        assert checked(finalize(invalid),422)['code'] == 'VALIDATION_FAILED'
        assert export(invalid).status_code == 422
        empty = finished(client,edit(invalid,[],[o['id'] for o in orders]))
        assert empty['validation'] == 'PASS' and not empty['result']['routes']
        assert checked(finalize(empty,True),422)['code'] == 'NO_SERVED_WORK'
        assert export(empty).status_code == 422
        # Restore the previously feasible assignment, then stale a selected source.
        restored = finished(client,edit(empty,routes,[removed]))
        restored_ready = checked(finalize(restored,True))
        row = orders[0]
        checked(client.put('/api/v1/orders/'+row['id'],json=row['data'],headers={**headers,'If-Match':f'"{row["version"]}"'}))
        assert checked(export(restored_ready),412)['code'] == 'SOURCE_CHANGED'
        assert checked(finalize(restored_ready,True),412)['code'] == 'SOURCE_CHANGED'
        print('PASS: named company setup, source review, real worker, READY and CSV; partial acceptance, invalid/empty edits, source/version guards and formula-safe export.',flush=True)
        return secrets
