from datetime import timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import User, LoginSession, LoginThrottle
from app.auth import COOKIE, CreateUser, HASHER, Identity, UpdateUser, create_user, digest, stamp, update_user
from app.jobs import Job, save_result
from app.main import create_app
from app.records import DataError
from app.worker import Worker
from test_jobs import seed, empty_result

pytestmark = pytest.mark.postgres
PASSWORD = 'synthetic test passphrase 729!'
ORIGIN = 'https://planner.test'
WRITE = {'Origin': ORIGIN, 'X-VRP-Request': '1'}


@pytest.fixture
def accounts(database):
    engine, config, settings = database
    request = seed(database)
    second = settings.model_copy(update={'demo_organization_id': uuid4()})
    other_request = seed((engine, config, second))
    with Session(engine) as session, session.begin():
        users = [create_user(session, owner, CreateUser(username=name, display_name=name, password=PASSWORD, role=role))
                 for owner, name, role in [(settings.demo_organization_id, 'admin', 'admin'),
                     (settings.demo_organization_id, 'planner', 'planner'), (second.demo_organization_id, 'other', 'admin')]]
    # As behind the bundled nginx, which overwrites X-VRP-Client with the connecting address.
    shared = settings.model_copy(update={'app_mode': 'shared', 'public_origin': ORIGIN, 'trust_proxy_client_header': True})
    with TestClient(create_app(shared), base_url=ORIGIN) as client:
        yield client, users, request, other_request


def sign_in(client, name='admin'):
    response = client.post('/api/v1/session', json={'username': name, 'password': PASSWORD}, headers=WRITE)
    assert response.status_code == 200, response.text
    return {**WRITE, 'X-CSRF-Token': response.json()['csrf_token']}, response


def test_language_is_personal_persistent_and_csrf_protected(accounts):
    client, users, *_ = accounts
    headers, response = sign_in(client, 'planner')
    assert response.json()['identity']['language'] == 'en'
    assert client.put('/api/v1/preferences', json={'language': 'fr'}, headers=WRITE).status_code == 403
    assert client.put('/api/v1/preferences', json={'language': 'de'}, headers=headers).status_code == 422
    assert client.put('/api/v1/preferences', json={'language': 'fr', 'user_id': str(users[0].id)}, headers=headers).status_code == 422
    changed = client.put('/api/v1/preferences', json={'language': 'fr'}, headers=headers)
    assert changed.status_code == 200
    assert changed.json()['language'] == 'fr'
    assert changed.json()['role'] == 'planner'
    assert client.get('/api/v1/session').json()['identity']['language'] == 'fr'
    client.cookies.clear()
    _, second_login = sign_in(client, 'planner')
    assert second_login.json()['identity']['language'] == 'fr'
    _, admin_login = sign_in(client)
    assert admin_login.json()['identity']['language'] == 'en'


def test_shared_endpoints_require_session_and_login_origin(accounts):
    client, users, request, other = accounts
    for path in ['depot', 'configuration', 'vehicles', 'orders?service_date=2026-09-23',
                 'jobs?service_date=2026-09-23', 'plans?service_date=2026-09-23', 'users', f'plans/{uuid4()}/export.csv']:
        assert client.get('/api/v1/' + path).status_code == 401
    assert client.get('/api/v1/health').status_code == 200
    assert client.get('/api/v1/ready').status_code == 200
    for headers in [{}, {'Origin': 'https://evil.test', 'X-VRP-Request':'1'}, {'Origin': ORIGIN}]:
        assert client.post('/api/v1/session', json={'username':'admin','password':PASSWORD}, headers=headers).status_code == 403
    assert client.get('/api/v1/health', headers={'Host':'evil.test'}).status_code == 400


def test_session_hashing_rotation_csrf_logout_and_secret_errors(accounts, database):
    client, users, request, other = accounts; engine, _, _ = database
    headers, response = sign_in(client)
    cookie = response.headers['set-cookie']
    assert all(part in cookie for part in ['HttpOnly', 'Secure', 'SameSite=strict', 'Path=/']) and 'Domain=' not in cookie
    token = client.cookies.get(COOKIE)
    with Session(engine) as session:
        saved = session.get(LoginSession, digest(token)); assert saved and token not in saved.token_hash
        user = session.get(User, users[0].id); assert user.password_hash.startswith('$argon2id$') and HASHER.verify(user.password_hash,PASSWORD)
    assert token not in response.text and PASSWORD not in response.text
    assert client.get('/api/v1/session').json()['csrf_token'] == headers['X-CSRF-Token']
    for bad in [WRITE, {**headers, 'X-CSRF-Token':'wrong'}]:
        assert client.delete('/api/v1/session', headers=bad).status_code == 403
    rotated, _ = sign_in(client)
    with Session(engine) as session: assert session.get(LoginSession,digest(token)) is None
    assert client.post('/api/v1/optimization-jobs', json=request.model_dump(mode='json'), headers={**headers,'Idempotency-Key':'a'}).status_code == 403
    assert client.delete('/api/v1/session', headers=rotated).status_code == 200
    assert client.get('/api/v1/session').status_code == 401
    invalid = client.post('/api/v1/session',json={'username':'admin','password':'VERY-SECRET-'*30},headers=WRITE)
    assert invalid.status_code == 422 and 'VERY-SECRET' not in invalid.text and 'input' not in invalid.json()['detail'][0]


@pytest.mark.parametrize('expiry',['idle','absolute'])
def test_server_enforces_expiry(accounts,database,expiry):
    client, *_ = accounts; engine, _, _ = database
    sign_in(client); token=client.cookies.get(COOKIE)
    with Session(engine) as session, session.begin():
        row=session.get(LoginSession,digest(token))
        if expiry=='idle':row.last_seen_at=stamp()-timedelta(minutes=31)
        else:row.expires_at=stamp()-timedelta(seconds=1)
    assert client.get('/api/v1/depot').status_code==401


def test_login_throttles_survive_application_restart_and_are_generic(accounts,database):
    client, *_ = accounts; engine, _, settings=database
    responses=[]
    for name in ['admin','missing']:
        for _ in range(5):
            response=client.post('/api/v1/session',json={'username':name,'password':'incorrect'},headers=WRITE)
            assert response.status_code==401;responses.append(response.json())
        assert client.post('/api/v1/session',json={'username':name,'password':PASSWORD},headers=WRITE).status_code==429
    assert all(r==responses[0] for r in responses)
    with TestClient(create_app(settings.model_copy(update={'app_mode':'shared','public_origin':ORIGIN})),base_url=ORIGIN) as restarted:
        assert restarted.post('/api/v1/session',json={'username':'admin','password':PASSWORD},headers=WRITE).status_code==429
    with Session(engine) as session,session.begin():
        for row in session.scalars(select(LoginThrottle)):row.expires_at=stamp()-timedelta(seconds=1)
    sign_in(client)


def test_one_client_cannot_lock_a_username_for_everyone(accounts,database):
    client, *_ = accounts; engine, _, _=database
    def attempt(address,password='incorrect',name='admin'):
        return client.post('/api/v1/session',json={'username':name,'password':password},headers={**WRITE,'X-VRP-Client':address})
    for _ in range(4):assert attempt('203.0.113.9').status_code==401
    assert attempt('203.0.113.9',PASSWORD).status_code==200  # Success clears only this client's failures.
    for _ in range(5):assert attempt('203.0.113.9').status_code==401
    assert attempt('203.0.113.9',PASSWORD).status_code==429
    assert attempt('198.51.100.4',PASSWORD).status_code==200  # The real user elsewhere is unaffected.
    # Distributed guessing is still bounded per username, for every client.
    for index in range(41):assert attempt(f'192.0.2.{index}').status_code==401
    assert attempt('198.51.100.4',PASSWORD).status_code==429
    with Session(engine) as session:
        assert all(len(row.key)==64 for row in session.scalars(select(LoginThrottle)))


def test_a_disabled_admin_cannot_finish_user_management_with_an_earlier_session(accounts,database):
    engine, _, settings = database
    with Session(engine) as session, session.begin():
        extra = [create_user(session, settings.demo_organization_id, CreateUser(username=name, display_name=name, password=PASSWORD, role='admin'))
                 for name in ('admin-b', 'admin-c')]
    first = Identity(organization_id=settings.demo_organization_id, user_id=accounts[1][0].id, username='admin', display_name='admin', role='admin')
    stale = Identity(organization_id=settings.demo_organization_id, user_id=extra[0].id, username='admin-b', display_name='admin-b', role='admin')
    with Session(engine) as session, session.begin():
        update_user(session, first, extra[0].id, UpdateUser(display_name='admin-b', role='admin', active=False))
    # The disabled admin's request had already passed authentication before the change.
    with pytest.raises(DataError) as error, Session(engine) as session, session.begin():
        update_user(session, stale, extra[0].id, UpdateUser(display_name='admin-b', role='admin', active=True))
    assert error.value.status == 403 and error.value.code == 'ADMIN_REQUIRED'
    with pytest.raises(DataError) as error, Session(engine) as session, session.begin():
        update_user(session, stale, extra[1].id, UpdateUser(display_name='admin-c', role='planner', active=True))
    assert error.value.code == 'ADMIN_REQUIRED'


def test_spraying_many_names_from_many_addresses_never_locks_out_everyone(accounts,database):
    client, *_ = accounts
    def attempt(address,name,password='incorrect'):
        return client.post('/api/v1/session',json={'username':name,'password':password},headers={**WRITE,'X-VRP-Client':address})
    # More failures than the removed installation-wide bucket allowed (120 per minute).
    for index in range(130):assert attempt(f'192.0.2.{index % 60}',f'guess-{index}').status_code==401
    assert attempt('198.51.100.4','admin',PASSWORD).status_code==200


def test_client_header_is_ignored_unless_a_trusted_proxy_is_declared(database):
    engine, _, settings = database
    seed(database)
    with Session(engine) as session, session.begin():
        create_user(session, settings.demo_organization_id, CreateUser(username='admin', display_name='admin', password=PASSWORD, role='admin'))
    direct = settings.model_copy(update={'app_mode': 'shared', 'public_origin': ORIGIN})
    with TestClient(create_app(direct), base_url=ORIGIN) as client:
        for index in range(5):  # Rotating the header cannot escape this client's own bucket.
            response = client.post('/api/v1/session',json={'username':'admin','password':'incorrect'},
                                   headers={**WRITE,'X-VRP-Client':f'203.0.113.{index}'})
            assert response.status_code==401
        assert client.post('/api/v1/session',json={'username':'admin','password':PASSWORD},
                           headers={**WRITE,'X-VRP-Client':'198.51.100.7'}).status_code==429


def test_background_polling_does_not_extend_an_idle_session(accounts,database):
    client, *_ = accounts; engine, _, _=database
    sign_in(client); token=client.cookies.get(COOKIE)
    def last_seen(value=None):
        with Session(engine) as session, session.begin():
            row=session.get(LoginSession,digest(token))
            if value is not None:row.last_seen_at=value
            return row.last_seen_at
    earlier=last_seen(stamp()-timedelta(minutes=20))
    assert client.get('/api/v1/jobs?service_date=2026-09-23',headers={'X-VRP-Background':'1'}).status_code==200
    assert last_seen()==earlier
    assert client.get('/api/v1/jobs?service_date=2026-09-23').status_code==200
    assert last_seen()>earlier
    last_seen(stamp()-timedelta(minutes=31))
    assert client.get('/api/v1/jobs?service_date=2026-09-23',headers={'X-VRP-Background':'1'}).status_code==401


def test_system_status_reports_worker_queue_and_routing(accounts,database,monkeypatch):
    import app.jobs_api
    client, users, request, other = accounts; engine, _, settings=database
    headers,_=sign_in(client)
    monkeypatch.setattr(app.jobs_api,'routing_available',lambda url:False)
    status=client.get('/api/v1/system-status').json()
    assert status=={'worker':'NOT_RUNNING','routing':'UNAVAILABLE','dataset':settings.routing_dataset,
                    'queued_jobs':0,'running_jobs':0,'oldest_queued_seconds':None}
    assert client.post('/api/v1/optimization-jobs',json=request.model_dump(mode='json'),headers={**headers,'Idempotency-Key':'status'}).status_code==202
    monkeypatch.setattr(app.jobs_api,'routing_available',lambda url:True)
    worker=Worker(settings)
    try:
        worker.acquire()
        status=client.get('/api/v1/system-status').json()
        assert status['worker']=='RUNNING' and status['routing']=='AVAILABLE'
        assert status['queued_jobs']==1 and status['oldest_queued_seconds']>=0
    finally:worker.close()
    assert client.get('/api/v1/system-status').json()['worker']=='NOT_RUNNING'
    client.cookies.clear();sign_in(client,'other')
    assert client.get('/api/v1/system-status').json()['queued_jobs']==0  # Counts are company-scoped.


def test_planner_permissions_admin_user_management_and_revocation(accounts,database):
    client, users, request, other=accounts;engine,_,_=database
    planner_headers,_=sign_in(client,'planner');planner_token=client.cookies.get(COOKIE)
    depot=client.get('/api/v1/depot').json()
    assert client.put('/api/v1/depot',json=depot['data'],headers={**planner_headers,'If-Match':'"1"'}).status_code==403
    # Planners see the depots and places their vehicles use; administrators manage them.
    assert client.get('/api/v1/depots').status_code==200
    assert client.post('/api/v1/depots',json={**depot['data'],'code':'OTHER'},headers=planner_headers).status_code==403
    assert client.post(f"/api/v1/depots/{depot['id']}/main",headers={**planner_headers,'If-Match':'"1"'}).status_code==403
    assert client.delete(f"/api/v1/depots/{depot['id']}",headers={**planner_headers,'If-Match':'"1"'}).status_code==403
    # Adding and removing the examples is an administrator's choice too.
    assert client.post('/api/v1/examples',json={'service_date':'2026-11-03'},headers=planner_headers).status_code==403
    assert client.post('/api/v1/examples/remove',headers=planner_headers).status_code==403
    assert client.get('/api/v1/users').status_code==403
    # Sending addresses to an outside service is the administrator's decision.
    assert client.put('/api/v1/workspace/address-lookup',json={'public_service':True},headers=planner_headers).status_code==403
    assert client.post('/api/v1/users',json={'username':'new','display_name':'New','password':PASSWORD},headers=planner_headers).status_code==403
    assert client.post('/api/v1/optimization-jobs',json=request.model_dump(mode='json'),headers={**planner_headers,'Idempotency-Key':'same'}).status_code==202
    client.cookies.clear()  # Keep the planner's server session alive for revocation.
    admin_headers,_=sign_in(client)
    assert len(client.get('/api/v1/users').json())==2
    created=client.post('/api/v1/users',json={'username':'NEW','display_name':'New','password':PASSWORD},headers=admin_headers)
    assert created.status_code==201 and created.json()['username']=='new' and 'password' not in created.text
    assert client.put('/api/v1/depot',json=depot['data'],headers={**admin_headers,'If-Match':'"1"'}).status_code==200
    assert client.put(f'/api/v1/users/{users[0].id}',json={'display_name':'Admin','role':'planner','active':True},headers=admin_headers).status_code==409
    assert client.put(f'/api/v1/users/{users[2].id}',json={'display_name':'Other','role':'planner','active':False},headers=admin_headers).status_code==404
    with Session(engine) as session:assert session.get(LoginSession,digest(planner_token)) is not None
    result=client.put(f'/api/v1/users/{users[1].id}',json={'display_name':'Planner','role':'planner','active':False},headers=admin_headers)
    assert result.status_code==200
    with Session(engine) as session:assert session.get(LoginSession,digest(planner_token)) is None
    assert client.post('/api/v1/session',json={'username':'planner','password':PASSWORD},headers=WRITE).status_code==401


def test_two_company_sources_jobs_plans_edits_exports_and_user_scoped_retries(accounts,database):
    client,users,request,other=accounts;engine,_,settings=database
    headers,_=sign_in(client)
    def submit(req):return client.post('/api/v1/optimization-jobs',json=req.model_dump(mode='json'),headers={**headers,'Idempotency-Key':'same'})
    original=submit(request).json();assert submit(request).json()['id']==original['id']
    headers,_=sign_in(client,'planner')
    second=submit(request).json();assert second['id']!=original['id']
    worker=Worker(settings)
    try:
        worker.acquire();job_id,snapshot=worker.claim()
        with worker.transaction() as session: plan_id=save_result(session,job_id,empty_result(snapshot))
    finally:worker.close()
    edit_body={'routes':[],'unassigned_order_ids':[str(k) for k in request.order_ids]}
    edit_headers={**headers,'If-Match':'"1"','Idempotency-Key':'same-edit'}
    edit=client.post(f'/api/v1/plans/{plan_id}/edits',json=edit_body,headers=edit_headers)
    assert edit.status_code==202
    assert client.post(f'/api/v1/plans/{plan_id}/edits',json=edit_body,headers=edit_headers).json()['id']==edit.json()['id']
    headers,_=sign_in(client,'admin')
    assert client.post(f'/api/v1/plans/{plan_id}/edits',json=edit_body,headers={**headers,'If-Match':'"1"','Idempotency-Key':'same-edit'}).status_code==409
    headers,_=sign_in(client,'other')
    own=client.get('/api/v1/depot',headers={'X-Organization-ID':str(users[0].organization_id)}).json()
    assert own['organization_id']==str(users[2].organization_id)
    for path in [f'vehicles/{request.vehicle_ids[0]}',f'orders/{request.order_ids[0]}',f'jobs/{original["id"]}',f'plans/{plan_id}',f'plans/{plan_id}/export.csv',f'orders/{request.order_ids[0]}/access-check?vehicle_id={other.vehicle_ids[0]}']:
        assert client.get('/api/v1/'+path,headers={'If-Match':'"1"'}).status_code==404
    for kind in ['jobs','plans']:
        assert client.get(f'/api/v1/{kind}?service_date={request.service_date}').json()==[]
    assert submit(request).status_code==404
    assert submit(other).status_code==202
    for kind,ids in [('vehicles',other.vehicle_ids),('orders',other.order_ids)]:
        suffix=f'?service_date={request.service_date}' if kind=='orders' else ''
        assert {UUID(r['id']) for r in client.get('/api/v1/'+kind+suffix).json()}==set(ids)
        foreign=request.vehicle_ids[0] if kind=='vehicles' else request.order_ids[0]
        data=client.get(f'/api/v1/{kind}/{ids[0]}').json()['data']
        assert client.put(f'/api/v1/{kind}/{foreign}',json=data,headers={**headers,'If-Match':'"1"'}).status_code==404
        assert client.post('/api/v1/'+kind,json={**data,'organization_id':str(users[0].organization_id)},headers=headers).status_code==422
    for suffix,body in [('finalize',{}),('edits',{'routes':[],'unassigned_order_ids':[str(k) for k in request.order_ids]})]:
        assert client.post(f'/api/v1/plans/{plan_id}/{suffix}',json=body,headers={**headers,'If-Match':'"1"','Idempotency-Key':'edit'}).status_code==404
    # CSV imports must resolve to the signed-in company, too.
    from app.config import ROOT
    content=(ROOT/'examples/vehicles.csv').read_bytes().replace(b'DEMO-',b'IMPORT-')
    response=client.post('/api/v1/imports/vehicles?dry_run=false',content=content,headers={**headers,'Content-Type':'text/csv'})
    assert response.status_code==200, response.text
    assert all(r['organization_id']==str(users[2].organization_id) for r in client.get('/api/v1/vehicles').json())


def test_auth_body_limit_applies_before_json_parsing(accounts):
    client,*_=accounts
    response=client.post('/api/v1/session',content=iter([b'x'*600000,b'y'*600000]),headers={**WRITE,'Content-Type':'application/json'})
    assert response.status_code==413


def test_operator_provision_reset_revokes_sessions_and_migration_preserves_accounts(accounts,database,monkeypatch):
    from alembic import command
    from app import manage_users
    import sys
    client,*_=accounts;engine,config,_=database
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr(manage_users,'getpass',lambda _:PASSWORD)
    monkeypatch.setattr(sys,'argv',['manage_users','create-admin','operator','--name','Operator'])
    manage_users.main()
    sign_in(client,'operator');token=client.cookies.get(COOKIE)
    new_password=PASSWORD+' reset'
    monkeypatch.setattr(manage_users,'getpass',lambda _:new_password)
    monkeypatch.setattr(sys,'argv',['manage_users','reset-password','operator'])
    manage_users.main()
    with Session(engine) as session:assert session.get(LoginSession,digest(token)) is None
    assert client.get('/api/v1/session').status_code==401
    assert client.post('/api/v1/session',json={'username':'operator','password':PASSWORD},headers=WRITE).status_code==401
    assert client.post('/api/v1/session',json={'username':'operator','password':new_password},headers=WRITE).status_code==200
    with pytest.raises(RuntimeError,match='pre-account backup'):command.downgrade(config,'0004_edits')
    with Session(engine) as session:assert session.scalar(select(User).where(User.username=='operator')) is not None


def test_worker_rejects_cross_company_submitter(accounts,database):
    client,users,request,_=accounts;engine,_,settings=database
    headers,_=sign_in(client)
    job=client.post('/api/v1/optimization-jobs',json=request.model_dump(mode='json'),headers={**headers,'Idempotency-Key':'job'}).json()
    with Session(engine) as session,session.begin():session.get(Job,UUID(job['id'])).submitted_by=str(users[2].id)
    worker=Worker(settings)
    try:
        worker.acquire();assert worker.claim() is None
    finally:worker.close()
    with Session(engine) as session:
        row=session.get(Job,UUID(job['id']));assert row.status=='FAILED' and row.error['code']=='INPUT_INVALID'


def test_overlapping_admin_changes_cannot_remove_last_administrator(accounts,database):
    from app.auth import identity, UpdateUser, update_user
    from app.records import DataError
    _,users,*_=accounts;engine,_,settings=database
    with Session(engine) as session,session.begin():
        second=create_user(session,settings.demo_organization_id,CreateUser(username='second-admin',display_name='Second',password=PASSWORD,role='admin'))
        first_identity=identity(session.get(User,users[0].id));second_identity=identity(session.get(User,second.id))
    change=UpdateUser(display_name='Disabled',role='admin',active=False)
    with Session(engine) as session,session.begin():update_user(session,first_identity,second.id,change)
    # Simulate the second request having authenticated before the first committed.
    with pytest.raises(DataError) as error,Session(engine) as session,session.begin():
        update_user(session,second_identity,users[0].id,change)
    # The second request's actor is no longer an admin, so it is refused before the
    # last-administrator check; the company keeps its one active administrator.
    assert error.value.code=='ADMIN_REQUIRED'
    with Session(engine) as session:
        assert session.get(User,users[0].id).active and session.get(User,users[0].id).role=='admin'


NEW_PASSWORD = 'another synthetic passphrase 4815'


def test_own_password_change_needs_the_current_password_and_ends_other_sessions(accounts,database):
    client, users, *_ = accounts; engine, _, _ = database
    sign_in(client, 'planner'); elsewhere = client.cookies.get(COOKIE)
    client.cookies.clear()
    headers, _ = sign_in(client, 'planner'); here = client.cookies.get(COOKIE)
    change = lambda current, new, h=headers: client.put('/api/v1/session/password', json={'current_password': current, 'new_password': new}, headers=h)
    assert change(PASSWORD, NEW_PASSWORD, WRITE).status_code == 403  # CSRF still applies.
    short = change(PASSWORD, 'too short')
    assert short.status_code == 422 and 'too short' not in short.text
    assert change(PASSWORD, PASSWORD).json()['code'] == 'PASSWORD_UNCHANGED'
    wrong = change('incorrect', NEW_PASSWORD)
    assert wrong.status_code == 403 and wrong.json()['code'] == 'PASSWORD_INCORRECT'
    assert client.get('/api/v1/session').status_code == 200  # A wrong current password does not sign out.
    assert change(PASSWORD, NEW_PASSWORD).status_code == 200
    with Session(engine) as session:
        assert session.get(LoginSession, digest(here)) is not None and session.get(LoginSession, digest(elsewhere)) is None
        assert HASHER.verify(session.get(User, users[1].id).password_hash, NEW_PASSWORD)
    assert client.get('/api/v1/session').status_code == 200
    client.cookies.clear()
    assert client.post('/api/v1/session', json={'username': 'planner', 'password': PASSWORD}, headers=WRITE).status_code == 401
    assert client.post('/api/v1/session', json={'username': 'planner', 'password': NEW_PASSWORD}, headers=WRITE).status_code == 200


def test_guessing_the_current_password_from_a_session_is_throttled(accounts,database):
    client, *_ = accounts
    headers, _ = sign_in(client, 'planner')
    change = lambda current: client.put('/api/v1/session/password', json={'current_password': current, 'new_password': NEW_PASSWORD}, headers=headers)
    for _ in range(5):
        assert change('incorrect').status_code == 403
    # Failures count in the user's sign-in limits: a stolen session cannot guess freely.
    assert change(PASSWORD).status_code == 429
    client.cookies.clear()
    assert client.post('/api/v1/session', json={'username': 'planner', 'password': PASSWORD}, headers=WRITE).status_code == 429


def test_administrator_resets_another_users_password_in_the_same_company_only(accounts,database):
    client, users, *_ = accounts; engine, _, _ = database
    sign_in(client, 'planner'); planner_token = client.cookies.get(COOKIE)
    client.cookies.clear()
    planner_headers, _ = sign_in(client, 'planner')
    reset = lambda key, h, password=NEW_PASSWORD: client.post(f'/api/v1/users/{key}/password', json={'new_password': password}, headers=h)
    assert reset(users[0].id, planner_headers).status_code == 403
    client.cookies.clear()
    admin_headers, _ = sign_in(client)
    assert reset(users[1].id, admin_headers, 'short').status_code == 422
    assert reset(users[2].id, admin_headers).status_code == 404
    assert reset(users[0].id, admin_headers).json()['code'] == 'OWN_PASSWORD'
    assert reset(users[1].id, admin_headers).status_code == 200
    with Session(engine) as session:
        assert session.get(LoginSession, digest(planner_token)) is None
        assert HASHER.verify(session.get(User, users[1].id).password_hash, NEW_PASSWORD)
    assert client.post('/api/v1/session', json={'username': 'planner', 'password': NEW_PASSWORD}, headers=WRITE).status_code == 200
