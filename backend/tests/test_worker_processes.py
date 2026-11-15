"""Linux acceptance checks with isolated PostgreSQL, actual processes and local engines."""
import os
import csv
import io
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.jobs import Job, Plan
from app.main import create_app
from app.records import Order
from app.worker import Worker
from app.edits import EditRequest, FinalizeRequest, export_csv, finalize, submit_edit
from app.records import DataError
from test_jobs import seed, enqueue, WRITE
from test_edits import saved_plan, edit_job

pytestmark=[pytest.mark.worker,pytest.mark.skipif(os.environ.get('RUN_WORKER_TESTS')!='1' or sys.platform!='linux',reason='Requires Linux RUN_WORKER_TESTS=1 and local engines.')]


def wait_for(check,seconds=15):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if check():return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for worker/process state')


def dead(pid):
    path=Path(f'/proc/{pid}/stat')
    return not path.exists() or path.read_text().split()[2]=='Z'


@pytest.mark.parametrize('objective',['TRAVEL_TIME','MODELED_TRAVEL_COST'])
@pytest.mark.parametrize('workload',['paris-250',*[pytest.param(name,marks=pytest.mark.skipif(
    os.environ.get('MATRIX_CONCURRENCY')!='4',reason='Requires provisioned four-worker regional routing and MATRIX_CONCURRENCY=4.'))
    for name in ['regional-250','regional-equipped-250']]])
def test_public_250_delivery_job_survives_worker_and_api_restart(database,objective,workload):
    from benchmarks.scaling import scaled_input
    from benchmarks.regional import regional_input,equipped_regional_input
    from test_routing_core import inputs
    engine,_,settings=database
    factory={'paris-250':scaled_input,'regional-250':regional_input,'regional-equipped-250':equipped_regional_input}[workload]
    request=seed(database,factory(inputs()))
    request.objective=objective;request.solver_time_limit_seconds=5
    headers={**WRITE,'Idempotency-Key':f'large-{objective}'}
    with TestClient(create_app(settings),base_url='http://localhost:8000') as client:
        # Same paginated sources used by the browser; no hidden domain submission.
        pages=[client.get(f'/api/v1/orders?service_date={request.service_date}&limit=50&offset={offset}').json() for offset in range(0,300,50)]
        assert [len(p) for p in pages]==[50,50,50,50,50,0]
        assert {o['id'] for p in pages for o in p}=={str(key) for key in request.order_ids}
        from uuid import uuid4
        for field in ['order_ids','vehicle_ids']:
            oversized=request.model_dump(mode='json');oversized[field].append(str(uuid4()))
            assert client.post('/api/v1/optimization-jobs',headers=headers,json=oversized).status_code==422
        stale=request.model_dump(mode='json');stale['source_versions'][str(request.order_ids[-1])]+=1
        assert client.post('/api/v1/optimization-jobs',headers=headers,json=stale).status_code==412
        response=client.post('/api/v1/optimization-jobs',headers=headers,json=request.model_dump(mode='json'))
        assert response.status_code==202,response.text
        job_id=response.json()['id'];assert response.json()['status']=='QUEUED'
        assert client.post('/api/v1/optimization-jobs',headers=headers,json=request.model_dump(mode='json')).json()['id']==job_id
    worker=Worker(settings.model_copy(update={'valhalla_url':os.environ.get('REGIONAL_URL','http://host.docker.internal:8002')}))
    try:
        worker.acquire();assert worker.process_one()
    finally:worker.close()
    with TestClient(create_app(settings),base_url='http://localhost:8000') as client:
        job=client.get(f'/api/v1/jobs/{job_id}').json()
        assert job['status']=='SUCCEEDED',job.get('error')
        response=client.get(f"/api/v1/plans/{job['plan_id']}");assert response.status_code==200
        plan=response.json();result=plan['result']
        assert plan['version']==1 and plan['active_job_id'] is None and not plan['result_stale']
        assert result['validation']=='PASS',result['blocking_issues']
        served=sum(len(r['stops']) for r in result['routes'])
        assert served+len(result['unassigned'])==250
        if workload=='paris-250':
            assert not result['unassigned'] and len(result['routes'])==25
        elif workload=='regional-equipped-250':
            assert not result['unassigned'] and served==250
        else:
            assert served>0 and result['unassigned']
            first=next(o['id'] for o in plan['input_data']['orders'] if o['data']['external_id']=='REGION-ORDER-001')
            assert any(u['order_id']==first and u['reason']=='NO_ROUTE_IN_DATASET' for u in result['unassigned'])
        assert len(plan['input_data']['orders'])==250 and len(plan['input_data']['vehicles'])==25
        assert result['data_kind']=='SYNTHETIC' and not result['operational_ready']
        assert all(len(r['legs'])==len(r['stops'])+1 for r in result['routes'])
        assert client.post('/api/v1/optimization-jobs',headers=headers,json=request.model_dump(mode='json')).json()['id']==job_id
    from uuid import UUID
    with Session(engine) as session:
        saved=session.get(Job,UUID(job_id));assert saved.input_data is None
        assert (saved.finished_at-saved.started_at).total_seconds()<settings.job_timeout_seconds


@pytest.mark.parametrize('recover_self_arcs',[False,True])
def test_real_worker_calculates_ten_delivery_saved_draft(database,recover_self_arcs):
    engine,_,settings=database;request=seed(database)
    if recover_self_arcs:
        with Session(engine) as session,session.begin():
            for key,(lat,lon) in zip(request.order_ids,[(48.843178,2.383811),(48.84281,2.384589),(48.84246,2.38533)]):
                row=session.get(Order,key)
                row.data={**row.data,'location':{'latitude':lat,'longitude':lon,'reviewed':True}}
    job=enqueue(database,request)
    worker=Worker(settings.model_copy(update={'valhalla_url':os.environ.get('REGIONAL_URL','http://host.docker.internal:8002')}))
    try:
        worker.acquire();assert worker.process_one()
        with Session(engine) as session:
            saved=session.get(Job,job.id);assert saved.status=='SUCCEEDED',saved.error
            plan=session.get(Plan,saved.plan_id);assert saved.input_data is None
            assert plan.result['validation']=='PASS' and not plan.result['unassigned']
            assert sum(len(r['stops']) for r in plan.result['routes'])==10
            assert len(plan.result['routes'])==2 and not plan.result['operational_ready']
    finally:worker.close()


def test_missing_engine_fails_without_plan(database):
    engine,_,settings=database;job=enqueue(database,seed(database))
    worker=Worker(settings.model_copy(update={'valhalla_url':'http://127.0.0.1:1'}))
    try:
        worker.acquire();assert worker.process_one()
        with Session(engine) as session:
            row=session.get(Job,job.id);assert row.status=='FAILED' and row.error['code']=='ENGINE_FAILED'
            assert session.scalar(select(func.count()).select_from(Plan))==0
    finally:worker.close()


def test_real_all_unassigned_stays_non_operational(database):
    engine,_,settings=database;request=seed(database)
    with Session(engine) as session,session.begin():
        for row in session.scalars(select(Order)):
            row.data={**row.data,'required_skills':['UNAVAILABLE_TEST_EQUIPMENT']}
    job=enqueue(database,request)
    worker=Worker(settings.model_copy(update={'valhalla_url':os.environ.get('REGIONAL_URL','http://host.docker.internal:8002')}))
    try:
        worker.acquire();assert worker.process_one()
        with Session(engine) as session:
            saved=session.get(Job,job.id);assert saved.status=='SUCCEEDED',saved.error
            plan=session.get(Plan,saved.plan_id)
            assert not plan.result['routes'] and len(plan.result['unassigned'])==10
            assert plan.status=='DRAFT' and plan.result['validation']=='PASS' and not plan.result['operational_ready']
    finally:worker.close()


@pytest.mark.parametrize('interruption',['timeout','sigterm','sigkill','database_loss'])
@pytest.mark.parametrize('kind',['OPTIMIZE','EDIT'])
def test_process_interruption_cleans_native_solver_and_cannot_publish(database,tmp_path,interruption,kind):
    engine,_,settings=database
    if kind=='EDIT':
        plan_id,_,edit=saved_plan(database);job=edit_job(database,plan_id,edit)
    else:job=enqueue(database,seed(database))
    solver=tmp_path/'slow-vroom'
    pid_file=tmp_path/'native-pid'
    solver.write_text(f'#!/usr/local/bin/python\nimport os,time\nfrom pathlib import Path\nPath({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n')
    solver.chmod(0o755)
    harness=tmp_path/'worker_harness.py'
    # Only this test swaps calculate for a native executable that remains running.
    # The production Worker, database guard, child supervisor and run_vroom are real.
    harness.write_text('''import sys
from app import worker
from app.optimization import run_vroom
from app.routing import Deadline
original=worker.subprocess.Popen
def launch(command,**kwargs):
    if '--compute' in command:
        command=[sys.executable,__file__,*command[command.index('--compute'):]]
    return original(command,**kwargs)
worker.subprocess.Popen=launch
def slow(*args,**kwargs):
    kwargs['on_stage']('SOLVE')
    return run_vroom({},60,Deadline.after(60),executable=SOLVER)
worker.calculate=slow
worker.reroute=slow
worker.main()
'''.replace('SOLVER',repr(str(solver))))
    environment={**os.environ,'PYTHONPATH':str(Path.cwd()),'JOB_TIMEOUT_SECONDS':'3' if interruption=='timeout' else '45'}
    with (tmp_path/'worker.log').open('w') as log:
        process=subprocess.Popen([sys.executable,str(harness),'--once'],env=environment,stdout=log,stderr=log)
        try:
            wait_for(pid_file.exists)
            native_pid=int(pid_file.read_text())
            if interruption=='database_loss':
                with engine.begin() as connection:
                    pid=connection.scalar(text("SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND application_name='tournevia-worker'"))
                    assert pid and connection.scalar(text('SELECT pg_terminate_backend(:pid)'),{'pid':pid})
            elif interruption!='timeout':process.send_signal(signal.SIGTERM if interruption=='sigterm' else signal.SIGKILL)
            process.wait(timeout=12)
            wait_for(lambda:dead(native_pid),5)
            recovery=Worker(settings)
            try:recovery.acquire()
            finally:recovery.close()
            with Session(engine) as session:
                row=session.get(Job,job.id);assert row.status=='FAILED'
                assert row.error['code']==('JOB_TIMEOUT' if interruption=='timeout' else 'WORKER_INTERRUPTED')
                assert row.plan_id is None and session.scalar(select(func.count()).select_from(Plan))==(1 if kind=='EDIT' else 0)
                if kind=='EDIT':
                    plan=session.get(Plan,plan_id)
                    assert plan.version==3 and plan.active_job_id is None and plan.validation=='FAIL' and plan.result_stale
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)


def test_trusted_domain_finalization_partial_acceptance_export_and_edit_clears_ready(database):
    engine,_,settings=database;request=seed(database)
    with Session(engine) as session,session.begin():
        row=session.get(Order,request.order_ids[0]);row.data={**row.data,'instructions':'=SUM(1,2)'}
    job=enqueue(database,request)
    # Trusted domain fixture only, in an isolated test database. Public APIs and
    # the CLI cannot select ROUTED provenance; the running demo stays synthetic.
    with Session(engine) as session,session.begin():
        row=session.get(Job,job.id);row.input_data={**row.input_data,'data_kind':'ROUTED'}
    worker=Worker(settings.model_copy(update={'valhalla_url':os.environ.get('REGIONAL_URL','http://host.docker.internal:8002')}))
    try:
        worker.acquire();assert worker.process_one()
        with Session(engine) as session,session.begin():
            plan_id=session.get(Job,job.id).plan_id
            ready=finalize(session,settings.demo_organization_id,plan_id,1,FinalizeRequest())
            assert ready.version==2 and ready.status=='READY' and ready.result.operational_ready
        with Session(engine) as session,session.begin():
            body,version=export_csv(session,settings.demo_organization_id,plan_id,2)
            rows=list(csv.DictReader(io.StringIO(body)))
            assert len(rows)==10 and {r['plan_version'] for r in rows}=={'2'}
            assert any(r['instructions']=="'=SUM(1,2)" for r in rows)
            assert finalize(session,settings.demo_organization_id,plan_id,2,FinalizeRequest()).version==2
        first=ready.result.routes[0].stops[0].order_id
        partial=EditRequest(routes=[{'vehicle_id':r.vehicle_id,'order_ids':[s.order_id for s in r.stops if s.order_id!=first]} for r in ready.result.routes],unassigned_order_ids=[first])
        with Session(engine) as session,session.begin():
            edit=submit_edit(session,settings.demo_organization_id,plan_id,partial,2,'manual')
            plan=session.get(Plan,plan_id);assert plan.status=='DRAFT' and plan.finalized_at is None and plan.validation=='PENDING'
        assert worker.process_one()
        with Session(engine) as session:
            plan=session.get(Plan,plan_id);assert plan.version==4 and plan.validation=='PASS' and plan.active_job_id is None
            assert session.get(Job,edit.id).plan_id==plan_id
        with pytest.raises(DataError) as e,Session(engine) as session,session.begin():finalize(session,settings.demo_organization_id,plan_id,4,FinalizeRequest())
        assert e.value.code=='UNASSIGNED_NOT_ACCEPTED'
        with Session(engine) as session,session.begin():assert finalize(session,settings.demo_organization_id,plan_id,4,FinalizeRequest(accept_unassigned=True)).version==5
        with Session(engine) as session,session.begin():session.get(Order,first).version+=1
        with pytest.raises(DataError) as e,Session(engine) as session,session.begin():export_csv(session,settings.demo_organization_id,plan_id,5)
        assert e.value.code=='SOURCE_CHANGED'
    finally:worker.close()
