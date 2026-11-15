"""Optional end-to-end recovery check inside test_https's owned disposable stack."""
import json
import os
from pathlib import Path
import re
import time

import httpx

from database_backup import BackupError, ComposeDatabase, DatabaseAdminSettings, cutover
from test_https import ROOT, run


def checked(response, status=200):
    # Never echo bodies containing source records or login tokens on failure.
    assert response.status_code == status, f'Expected HTTP {status}, received {response.status_code}.'
    return response.json()


def completed(client, job_id):
    end = time.monotonic() + 140
    while time.monotonic() < end:
        job = checked(client.get(f'/api/v1/jobs/{job_id}'))
        if job['status'] in ('SUCCEEDED', 'FAILED'):
            assert job['status'] == 'SUCCEEDED', f'Job failed: {(job.get("error") or {}).get("code")}.'
            plan = checked(client.get(f'/api/v1/plans/{job["plan_id"]}'))
            result = plan['result']
            assert result['validation'] == 'PASS' and not result['blocking_issues']
            assert not result['unassigned'] and sum(len(r['stops']) for r in result['routes']) == 10
            assert all(leg['geometry'] for route in result['routes'] for leg in route['legs'])
            return plan
        time.sleep(.25)
    raise AssertionError('Queued calculation did not finish within the acceptance deadline.')


def check_recovery(compose, env_file: Path, directory: Path, origin, context, login):
    assert directory.name.startswith('vrp-tls-test-')
    assert os.environ.get('COMPOSE_PROJECT_NAME') == directory.name
    settings = DatabaseAdminSettings(_env_file=env_file, db_name='vrp')
    database = ComposeDatabase(settings, env_file)
    # test_https explicitly supplies COMPOSE_PROJECT_NAME in its environment.
    # The ordinary backup helper therefore targets this same random project.
    assert database.query('SELECT count(*) FROM public.orders;') == '10'
    write = {'Origin': origin, 'X-VRP-Request': '1'}
    secrets = []
    with httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=15) as client:
        page = client.get('/')
        assert page.status_code == 200
        assets = re.findall(r'(?:src|href)="(/assets/[^"<>]+)"', page.text)
        assert any(path.endswith('.js') for path in assets) and any(path.endswith('.css') for path in assets)
        for path in assets:
            response = client.get(path)
            assert response.status_code == 200 and 'text/html' not in response.headers['content-type']
        session = checked(client.post('/api/v1/session', json=login, headers=write))
        token = client.cookies.get('__Host-vrp_session')
        secrets.extend([token, session['csrf_token']])
        headers = {**write, 'X-CSRF-Token': session['csrf_token']}
        fixture = json.loads((ROOT / 'backend/tests/fixtures/paris-ten.json').read_text())
        # The acceptance run added the fictional examples after setup. Exercise the
        # source APIs on those records before backing up the real worker result.
        day = database.query('SELECT DISTINCT service_date FROM public.orders;')
        vehicles = checked(client.get('/api/v1/vehicles'))
        orders = checked(client.get('/api/v1/orders', params={'service_date': day}))
        assert len(vehicles) == 2 and len(orders) == 10
        for i, row in enumerate(orders):
            orders[i] = checked(client.put(f'/api/v1/orders/{row["id"]}', json=row['data'],
                headers={**headers, 'If-Match': f'"{row["version"]}"'}))
        depot = checked(client.get('/api/v1/depot'))
        request = {'service_date': day, 'vehicle_ids': [v['id'] for v in vehicles],
                   'order_ids': [o['id'] for o in orders], 'routing_policy': fixture['routing_policy'],
                   'objective': fixture['objective'], 'solver_time_limit_seconds': 1,
                   'source_versions': {r['id']: r['version'] for r in [depot, *vehicles, *orders]}}
        print('Recovery acceptance: calculating ten example deliveries through the HTTPS API and real worker.', flush=True)
        job = checked(client.post('/api/v1/optimization-jobs', json=request,
            headers={**headers, 'Idempotency-Key': 'before-backup'}), 202)
        plan = completed(client, job['id'])
        assert plan['version'] == 1
        # Leave a real claimed edit unfinished in this owned test database.
        run([*compose, 'stop', 'worker'])
        edit = {'routes': [{'vehicle_id': r['vehicle_id'], 'order_ids': [s['order_id'] for s in r['stops']]}
                           for r in plan['result']['routes']], 'unassigned_order_ids': []}
        pending = checked(client.post(f'/api/v1/plans/{plan["id"]}/edits', json=edit,
            headers={**headers, 'If-Match': '"1"', 'Idempotency-Key': 'interrupted-edit'}), 202)
        claim = """
from app.config import Settings
from app.worker import Worker
w = Worker(Settings())
try:
    w.acquire()
    claimed = w.claim()
    assert claimed is not None
finally:
    w.close()
"""
        run([*compose, 'run', '--rm', '--no-deps', '-T', 'worker', 'python', '-c', claim])
        assert checked(client.get(f'/api/v1/jobs/{pending["id"]}'))['status'] == 'RUNNING'
        reserved = checked(client.get(f'/api/v1/plans/{plan["id"]}'))
        assert reserved['version'] == 2 and reserved['active_job_id'] == pending['id']
        before = database.summary('vrp')
        assert before['vehicles'] == 2 and before['orders'] == 10 and before['plans'] == 1
        run([*compose, 'run', '--rm', '--no-deps', '-T', 'setup'])
        assert database.summary('vrp') == before
        assert checked(client.get(f'/api/v1/plans/{plan["id"]}')) == reserved
        archive = database.backup(directory / 'recovery.dump')
        try:
            database.backup(archive)
        except BackupError:
            pass
        else:
            raise AssertionError('Backup overwrote an existing archive.')
        target = database.restore(archive)
        assert database.summary(target) == {**before, 'login_sessions': 0}
        try:
            database.restore(archive, target)
        except BackupError:
            pass
        else:
            raise AssertionError('Restore overwrote an existing database.')
        print('Recovery acceptance: backup restored; checking session invalidation and worker recovery in the isolated copy.', flush=True)
        # The operator cutover: stop API and worker, point the installation at the copy, restart, wait.
        assert cutover(database, compose, env_file, target) == 'vrp'
        try:
            cutover(database, compose, env_file, target)
        except BackupError:
            pass
        else:
            raise AssertionError('Cutover restarted onto the database already in use.')
        for attempt in range(60):
            try:
                if client.get('/api/v1/ready').status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(.25)
        else:
            raise AssertionError('Restored application never became ready.')
        assert client.get('/api/v1/session').status_code == 401
        session = checked(client.post('/api/v1/session', json=login, headers=write))
        secrets.extend([client.cookies.get('__Host-vrp_session'), session['csrf_token']])
        headers = {**write, 'X-CSRF-Token': session['csrf_token']}
        for attempt in range(60):
            recovered = checked(client.get(f'/api/v1/jobs/{pending["id"]}'))
            if recovered['status'] == 'FAILED':
                break
            time.sleep(.25)
        assert recovered['status'] == 'FAILED' and recovered['error']['code'] == 'WORKER_INTERRUPTED'
        saved = checked(client.get(f'/api/v1/plans/{plan["id"]}'))
        assert saved['version'] == 3 and saved['active_job_id'] is None
        assert saved['result'] == reserved['result'] and saved['input_data'] == reserved['input_data']
        for collection, rows in [('vehicles', vehicles), ('orders', orders)]:
            for row in rows:
                assert checked(client.get(f'/api/v1/{collection}/{row["id"]}')) == row
        job = checked(client.post('/api/v1/optimization-jobs', json=request,
            headers={**headers, 'Idempotency-Key': 'after-restore'}), 202)
        restored_plan = completed(client, job['id'])
        export = client.get(f'/api/v1/plans/{restored_plan["id"]}/export.csv', headers={'If-Match': '"1"'})
        # A draft is never dispatched, examples or not.
        assert checked(export, 409)['code'] == 'PLAN_NOT_READY'
        assert database.summary('vrp') == before
        assert database.query("SELECT status FROM planning_jobs WHERE kind='EDIT';") == 'RUNNING'
        assert database.query('SELECT version FROM plans;') == '2'
        assert database.summary(target)['plans'] == 2
        checked(client.delete('/api/v1/session', headers=headers))
        assert client.get('/api/v1/depot').status_code == 401
    print('PASS: fresh installation, repeatable setup, ten-delivery real-road plan, backup/restore, old-session rejection, preserved sources/results, interrupted-edit release and new worker calculation; source database unchanged.', flush=True)
    return secrets
