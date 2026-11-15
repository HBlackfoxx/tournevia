"""Real Compose pg_dump/restore and role grants; only fresh databases/roles are removed."""
import importlib.util
import os
import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import errors, sql
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import LoginSession
from app.auth import COOKIE, CreateUser, create_user, digest
from app.config import ROOT
from app.database_roles import DatabaseAdminSettings, connect, prepare_roles
from app.db import make_engine
from app.edits import EditRequest, submit_edit
from app.jobs import Job, Plan, save_result, submit
from app.main import create_app
from app.setup_database import setup
from app.worker import Worker
from test_jobs import seed, empty_result

pytestmark = [pytest.mark.postgres, pytest.mark.database_ops,
              pytest.mark.skipif(os.environ.get('RUN_DATABASE_OPS_TESTS') != '1', reason='Requires explicit isolated role/Compose backup tests.')]
PASSWORD = 'synthetic recovery passphrase 2026'


@pytest.fixture(scope='session')
def backup_module():
    # Host-only helper is intentionally absent from the API/worker image. Load it
    # only when these explicitly enabled Compose tests actually run.
    spec = importlib.util.spec_from_file_location('database_backup', ROOT / 'scripts/database_backup.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def restricted(database):
    engine, config, original = database
    request = seed(database)
    suffix = uuid4().hex
    settings = DatabaseAdminSettings(_env_file=None, db_host=original.db_host, db_port=original.db_port,
        db_name=original.db_name, postgres_user=original.db_user, postgres_password=original.db_password,
        runtime_db_user='vrp_test_app_'+suffix, runtime_db_password=secrets.token_urlsafe(32),
        migration_db_user='vrp_test_migration_'+suffix, migration_db_password=secrets.token_urlsafe(32),
        app_mode='local_demo', public_origin='http://localhost:8000')
    restored = []
    runtime_engine = None
    try:
        setup(settings)  # Migrate through a non-superuser connection; existing seed survives.
        runtime = settings.runtime_settings()
        runtime_engine = make_engine(runtime)
        yield settings, runtime_engine, request, restored
    finally:
        if runtime_engine is not None: runtime_engine.dispose()
        engine.dispose()
        with connect(settings.admin_url.set(database='postgres'), autocommit=True) as connection:
            for name in [*restored, original.db_name]:
                assert (name.startswith('vrp_restore_') and len(name)==44) or name==original.db_name and name.startswith('vrp_test_')
                connection.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name)))
            for name in [settings.runtime_db_user, settings.migration_db_user]:
                assert name.endswith(suffix) and name.startswith('vrp_test_')
                connection.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(name)))


def test_runtime_grants_allow_workflow_but_deny_ddl_and_escalation(restricted):
    settings, engine, request, _ = restricted
    runtime = settings.runtime_settings()
    with connect(runtime.database_url) as connection:
        assert connection.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone() == (False,)*5
        assert connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='0017_safety_margin'
    forbidden = ['CREATE TABLE public.forbidden (id int)', 'CREATE TEMP TABLE forbidden (id int)',
                 'CREATE SCHEMA forbidden', 'ALTER TABLE plans ADD COLUMN forbidden int', 'DROP TABLE login_throttles',
                 'TRUNCATE orders', 'DELETE FROM orders', "UPDATE alembic_version SET version_num='unsafe'",
                 # Of the company row, only its settings are the runtime role's to change.
                 'UPDATE organizations SET is_demo = false', "UPDATE organizations SET name = 'unsafe'",
                 'CREATE ROLE forbidden_' + uuid4().hex, 'SET ROLE '+settings.migration_db_user]
    for statement in forbidden:
        with pytest.raises(errors.InsufficientPrivilege), connect(runtime.database_url) as connection:
            connection.execute(statement)
    with pytest.raises(errors.InsufficientPrivilege), connect(runtime.database_url,autocommit=True) as connection:
        connection.execute('CREATE DATABASE forbidden_'+uuid4().hex)
    with connect(settings.migration_settings().database_url) as connection:
        connection.execute('CREATE TABLE public.migration_probe (id int)')
        connection.execute('DROP TABLE public.migration_probe')
    with Session(engine) as session,session.begin():
        user=create_user(session,runtime.demo_organization_id,CreateUser(username='recovery-admin',password=PASSWORD,display_name='Recovery admin',role='admin'))
        job=submit(session,runtime.demo_organization_id,request,'restricted',actor=str(user.id))
    worker=Worker(runtime)
    try:
        worker.acquire();job_id,snapshot=worker.claim();assert job_id==job.id
        with worker.transaction() as session:plan_id=save_result(session,job_id,empty_result(snapshot))
    finally:worker.close()
    with TestClient(create_app(runtime.model_copy(update={'app_mode':'shared','public_origin':'https://planner.test'})),base_url='https://planner.test') as client:
        write={'Origin':'https://planner.test','X-VRP-Request':'1'}
        response=client.post('/api/v1/session',json={'username':'recovery-admin','password':PASSWORD},headers=write)
        assert response.status_code==200
        assert client.get('/api/v1/ready').status_code==200
        assert client.get(f'/api/v1/plans/{plan_id}').status_code==200
        depot=client.get('/api/v1/depot').json()
        assert client.put('/api/v1/depot',json=depot['data'],headers={**write,'X-CSRF-Token':response.json()['csrf_token'],'If-Match':f'"{depot["version"]}"'}).status_code==200
        # The runtime role may change the company's settings: its address lookup choice ...
        assert client.put('/api/v1/workspace/address-lookup',json={'public_service':True},headers={**write,'X-CSRF-Token':response.json()['csrf_token']}).status_code==200
        assert client.get('/api/v1/configuration').json()['address_lookup_source']=='public'
        # ... and its safety margin for calculations.
        assert client.put('/api/v1/workspace/safety-margin',json={'minutes':25},headers={**write,'X-CSRF-Token':response.json()['csrf_token']}).status_code==200
        assert client.get('/api/v1/configuration').json()['safety_margin_minutes']==25
        assert client.delete('/api/v1/session',headers={**write,'X-CSRF-Token':response.json()['csrf_token']}).status_code==200


def test_role_setup_is_repeatable_preserves_data_and_rejects_unmanaged_roles(restricted):
    settings, engine, request, _=restricted
    setup(settings)
    with Session(engine) as session:
        from app.records import Order
        assert session.get(Order,request.order_ids[0]) is not None
    unsafe=settings.model_copy(update={'runtime_db_user':settings.postgres_user})
    with pytest.raises(ValueError,match='distinct'):prepare_roles(unsafe)
    # On this cluster the actual unmanaged bootstrap owner is checked with a third name.
    with connect(settings.admin_url) as connection:
        connection.execute(sql.SQL('COMMENT ON ROLE {} IS NULL').format(sql.Identifier(settings.runtime_db_user)))
    with pytest.raises(ValueError,match='unmanaged'):prepare_roles(settings)


def test_setup_migrates_empty_schema_as_non_superuser(restricted):
    settings, engine, _, _ = restricted
    # This fixture owns a fresh random test database, never the configured demo.
    assert settings.db_name.startswith('vrp_test_')
    with connect(settings.migration_settings().database_url) as connection:
        connection.execute('DROP SCHEMA public CASCADE')
        connection.execute('CREATE SCHEMA public')
    setup(settings)
    with connect(settings.runtime_settings().database_url) as connection:
        assert connection.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '0017_safety_margin'
        assert connection.execute('SELECT count(*) FROM depots').fetchone()[0] == 1
        owners = connection.execute("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname='public'").fetchall()
        assert owners == [(settings.migration_db_user,)]


def test_first_company_setup_runs_with_restricted_runtime_credentials(restricted):
    from test_onboarding import payload, TOKEN
    settings, engine, _, _ = restricted
    assert settings.db_name.startswith('vrp_test_')
    with connect(settings.migration_settings().database_url) as connection:
        connection.execute('DROP SCHEMA public CASCADE')
        connection.execute('CREATE SCHEMA public')
    settings = DatabaseAdminSettings.model_validate({**settings.model_dump(), 'app_mode':'local', 'setup_token':TOKEN})
    setup(settings)
    runtime = settings.runtime_settings()
    with TestClient(create_app(runtime),base_url='http://localhost:8000') as client:
        assert client.get('/api/v1/setup').json()['required']
        response = client.post('/api/v1/setup',json=payload(),headers={'Origin':'http://localhost:8000','X-VRP-Request':'1'})
        assert response.status_code == 201, response.text
        assert client.get('/api/v1/configuration').json()['data_kind'] == 'UNVERIFIED'
        assert client.get('/api/v1/vehicles').json() == []
    setup(settings)
    with connect(runtime.database_url) as connection:
        assert connection.execute('SELECT name,is_demo FROM organizations').fetchall() == [('Example company',False)]
        assert connection.execute('SELECT count(*) FROM users').fetchone()[0] == 1


def test_restricted_runtime_removes_only_the_examples(restricted):
    from test_onboarding import payload, TOKEN
    settings, engine, _, _ = restricted
    assert settings.db_name.startswith('vrp_test_')
    with connect(settings.migration_settings().database_url) as connection:
        connection.execute('DROP SCHEMA public CASCADE')
        connection.execute('CREATE SCHEMA public')
    settings = DatabaseAdminSettings.model_validate({**settings.model_dump(), 'app_mode':'local', 'setup_token':TOKEN})
    setup(settings)
    runtime = settings.runtime_settings()
    with connect(runtime.database_url) as connection:
        # Owned by the migration role, runs with its rights, callable by nobody else; the sample's function is gone.
        assert connection.execute("SELECT proowner::regrole::text, prosecdef FROM pg_proc WHERE proname='remove_examples'"
                                  ).fetchone() == (settings.migration_db_user, True)
        assert connection.execute("SELECT count(*) FROM pg_proc WHERE proname='start_company_from_sample'").fetchone() == (0,)
    write = {'Origin':'http://localhost:8000','X-VRP-Request':'1'}
    with TestClient(create_app(runtime),base_url='http://localhost:8000') as client:
        response = client.post('/api/v1/setup',json=payload(),headers=write)
        assert response.status_code == 201, response.text
        headers = {**write,'X-CSRF-Token':response.json()['csrf_token']}
        assert client.get('/api/v1/vehicles').json() == []
        added = client.post('/api/v1/examples',json={'service_date':'2026-11-03'},headers=headers)
        assert added.status_code == 201, added.text
        for statement in ('DELETE FROM vehicles', 'DELETE FROM orders', 'DELETE FROM depots'):
            with pytest.raises(errors.InsufficientPrivilege), connect(runtime.database_url) as connection:
                connection.execute(statement)
        removed = client.post('/api/v1/examples/remove',headers=headers)
        assert removed.status_code == 200 and removed.json()['removed'] == 1 + 2 + 10, removed.text
        assert client.get('/api/v1/configuration').json()['examples'] is False
    with connect(runtime.database_url) as connection:
        assert connection.execute('SELECT (SELECT count(*) FROM vehicles),(SELECT count(*) FROM orders),(SELECT count(*) FROM depots)'
                                  ).fetchone() == (0, 0, 1)
    setup(settings)  # Rerunning setup keeps the grant and the company.
    with connect(runtime.database_url) as connection:
        assert connection.execute('SELECT name,is_demo FROM organizations').fetchall() == [('Example company',False)]
        assert connection.execute('SELECT public.remove_examples(id) FROM organizations').fetchone() == (0,)


def test_backup_restore_preserves_users_jobs_plans_and_recovers_interrupted_edit(restricted,tmp_path,backup_module):
    settings, engine, request, restored=restricted;runtime=settings.runtime_settings()
    with Session(engine) as session,session.begin():
        user=create_user(session,runtime.demo_organization_id,CreateUser(username='restore-admin',password=PASSWORD,display_name='Restore admin',role='admin'))
        job=submit(session,runtime.demo_organization_id,request,'original',actor=str(user.id))
    worker=Worker(runtime)
    try:
        worker.acquire();job_id,snapshot=worker.claim()
        with worker.transaction() as session:plan_id=save_result(session,job_id,empty_result(snapshot))
        with Session(engine) as session,session.begin():
            edit=submit_edit(session,runtime.demo_organization_id,plan_id,EditRequest(routes=[],unassigned_order_ids=request.order_ids),1,'edit',actor=str(user.id))
        worker.claim()  # Snapshot an interrupted RUNNING edit and its reservation.
    finally:worker.close()
    shared=runtime.model_copy(update={'app_mode':'shared','public_origin':'https://planner.test'})
    with TestClient(create_app(shared),base_url='https://planner.test') as client:
        response=client.post('/api/v1/session',json={'username':'restore-admin','password':PASSWORD},headers={'Origin':'https://planner.test','X-VRP-Request':'1'})
        assert response.status_code==200;old_token=client.cookies.get(COOKIE)
    service=backup_module.ComposeDatabase(settings)
    archive=service.backup(tmp_path/'recovery.dump')
    assert archive.read_bytes()[:5]==b'PGDMP'
    with pytest.raises(backup_module.BackupError,match='exists'):service.backup(archive)
    target='vrp_restore_'+uuid4().hex;restored.append(target)
    assert service.restore(archive,target)==target
    with pytest.raises(backup_module.BackupError,match='already exists'):service.restore(archive,target)
    with pytest.raises(backup_module.BackupError,match='never the source'):service.restore(archive,settings.db_name)
    copied=runtime.model_copy(update={'db_name':target});copy_engine=make_engine(copied)
    try:
        with Session(copy_engine) as session:
            assert session.get(LoginSession,digest(old_token)) is None
            assert session.get(Job,job.id).plan_id==plan_id
            assert session.get(Plan,plan_id).version==2
        recovered=Worker(copied)
        try:
            recovered.acquire()
            with Session(copy_engine) as session:
                assert session.get(Job,edit.id).error['code']=='WORKER_INTERRUPTED'
                assert session.get(Plan,plan_id).version==3
                assert session.get(Plan,plan_id).active_job_id is None
            with Session(copy_engine) as session,session.begin():new_job=submit(session,copied.demo_organization_id,request,'after-restore',actor=str(user.id))
            job_id,snapshot=recovered.claim();assert job_id==new_job.id
            with recovered.transaction() as session:save_result(session,job_id,empty_result(snapshot))
        finally:recovered.close()
        with TestClient(create_app(copied.model_copy(update={'app_mode':'shared','public_origin':'https://planner.test'})),base_url='https://planner.test') as client:
            assert client.get('/api/v1/session',headers={'Cookie':f'{COOKIE}={old_token}'}).status_code==401
            response=client.post('/api/v1/session',json={'username':'restore-admin','password':PASSWORD},headers={'Origin':'https://planner.test','X-VRP-Request':'1'})
            assert response.status_code==200
            assert client.get(f'/api/v1/plans/{plan_id}').json()['version']==3
            assert client.get(f'/api/v1/jobs/{new_job.id}').json()['status']=='SUCCEEDED'
        with Session(engine) as session:
            assert session.get(Plan,plan_id).version==2 and session.get(Job,edit.id).status=='RUNNING'
            assert session.get(LoginSession,digest(old_token)) is not None
        assert service.summary(target)['plans']==2
    finally:copy_engine.dispose()


def test_bad_archive_does_not_touch_source_or_publish_backup(restricted,tmp_path,monkeypatch,backup_module):
    settings,engine,request,restored=restricted
    service=backup_module.ComposeDatabase(settings)
    bad=tmp_path/'bad.dump';bad.write_bytes(b'not a database archive')
    target='vrp_restore_'+uuid4().hex
    with pytest.raises(backup_module.BackupError):service.restore(bad,target)
    with connect(settings.admin_url) as connection:
        assert connection.execute('SELECT 1 FROM pg_database WHERE datname=%s',(target,)).fetchone() is None
    from app.records import Order
    with Session(engine) as session:assert session.get(Order,request.order_ids[0]) is not None
    original_run=service.run
    def failed_dump(tool,args,**kwargs):
        if tool=='pg_dump':
            kwargs['output_file'].write(b'PGDMP incomplete')
            raise backup_module.BackupError('Simulated dump failure')
        return original_run(tool,args,**kwargs)
    monkeypatch.setattr(service,'run',failed_dump)
    destination=tmp_path/'failed.dump'
    with pytest.raises(backup_module.BackupError,match='Simulated'):service.backup(destination)
    assert not destination.exists() and not list(tmp_path.glob('*.partial'))


def test_restricted_runtime_deletes_deliveries_only_through_the_checked_function(restricted):
    settings, engine, request, _ = restricted
    from app.records import Order, Versioned, delete_orders
    with connect(settings.runtime_settings().database_url) as connection:
        # Runs with its owner's rights (the role that migrated), callable by the runtime role only.
        assert connection.execute("SELECT prosecdef FROM pg_proc WHERE proname='delete_orders'").fetchone() == (True,)
        assert connection.execute("SELECT has_function_privilege('public', 'public.delete_orders(uuid, uuid[])', 'EXECUTE')"
                                  ).fetchone() == (False,)
    with Session(engine) as session, session.begin():
        owner = session.get(Order, request.order_ids[0]).organization_id
        rows = [session.get(Order, key) for key in request.order_ids[:2]]
        assert delete_orders(session, owner, [Versioned(id=r.id, version=r.version) for r in rows]) == 2
    with Session(engine) as session:
        assert all(session.get(Order, key) is None for key in request.order_ids[:2])
        assert session.get(Order, request.order_ids[2]) is not None


def test_restricted_runtime_deletes_a_depot_only_through_the_checked_function(restricted):
    settings, engine, request, _ = restricted
    from app.fleet import Depot, DepotInput, Location
    from app.records import create_depot, remove_depot
    with Session(engine) as session, session.begin():
        owner = session.scalar(select(Depot.organization_id).where(Depot.main))
        place = create_depot(session, owner, DepotInput(code='HOME', name='Home', kind='PLACE', timezone='Europe/Paris',
                                                        location=Location(latitude=48.8, longitude=2.3, reviewed=False)))
    with pytest.raises(errors.InsufficientPrivilege), connect(settings.runtime_settings().database_url) as connection:
        connection.execute('DELETE FROM depots')
    with connect(settings.runtime_settings().database_url) as connection:
        assert connection.execute("SELECT prosecdef FROM pg_proc WHERE proname='delete_depot'").fetchone() == (True,)
        main = connection.execute('SELECT id FROM depots WHERE main').fetchone()[0]
    with pytest.raises(errors.ObjectNotInPrerequisiteState), connect(settings.runtime_settings().database_url) as connection:
        connection.execute('SELECT public.delete_depot(%s, %s)', (owner, main))  # Never the main depot.
    with Session(engine) as session, session.begin():
        remove_depot(session, owner, place.id, place.version)
    with Session(engine) as session:
        assert session.get(Depot, place.id) is None and session.get(Depot, main) is not None
