from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.edits import EditRequest, FinalizeRequest, fail_work, finalize, export_csv, safe_cell, submit_edit
from app.jobs import Job, Plan, save_result
from app.main import create_app
from app.records import DataError, Order
from app.worker import Worker
from test_jobs import WRITE, empty_result, seed, enqueue

pytestmark=pytest.mark.postgres


def saved_plan(database):
    engine,_,settings=database;enqueue(database,seed(database));worker=Worker(settings)
    try:
        worker.acquire();job_id,snapshot=worker.claim()
        with worker.transaction() as session:plan_id=save_result(session,job_id,empty_result(snapshot))
    finally:worker.close()
    edit=EditRequest(routes=[],unassigned_order_ids=[o.id for o in snapshot.orders])
    return plan_id,snapshot,edit


def edit_job(database,plan_id,edit,version=1,key='edit'):
    engine,_,settings=database
    with Session(engine) as session,session.begin():return submit_edit(session,settings.demo_organization_id,plan_id,edit,version,key)


def test_edit_reserves_same_plan_replays_before_stale_version_and_saves_atomically(database):
    engine,_,settings=database;plan_id,snapshot,edit=saved_plan(database)
    with ThreadPoolExecutor(2) as pool:jobs=list(pool.map(lambda _:edit_job(database,plan_id,edit),range(2)))
    job=jobs[0];assert job.id==jobs[1].id and job.target_plan_id==plan_id and job.reserved_version==2
    with Session(engine) as session:
        plan=session.get(Plan,plan_id);assert plan.version==2 and plan.validation=='PENDING' and plan.result_stale
        assert session.get(Job,job.id).input_data is None
    with pytest.raises(DataError) as e:edit_job(database,plan_id,edit,2,'second')
    assert e.value.code=='PLAN_BUSY'
    worker=Worker(settings)
    try:
        worker.acquire();claimed,work=worker.claim();assert claimed==job.id and work==snapshot
        with pytest.raises(RuntimeError),worker.transaction() as session:
            save_result(session,job.id,empty_result(work));raise RuntimeError('Rollback both updates')
        with Session(engine) as session:assert session.get(Plan,plan_id).version==2
        with worker.transaction() as session:assert save_result(session,job.id,empty_result(work))==plan_id
    finally:worker.close()
    with Session(engine) as session:
        plan=session.get(Plan,plan_id);assert plan.version==3 and plan.active_job_id is None and not plan.result_stale
        assert session.scalar(select(func.count()).select_from(Plan))==1
    assert edit_job(database,plan_id,edit).id==job.id
    with pytest.raises(DataError) as e:edit_job(database,plan_id,edit,1,'new')
    assert e.value.status==412
    with pytest.raises(DataError) as e:edit_job(database,plan_id,edit,3,'edit')
    assert e.value.status==409


@pytest.mark.parametrize('failure',['engine','interrupted','stale'])
def test_failed_edit_does_not_look_applied_and_releases_reservation(database,failure):
    engine,_,settings=database;plan_id,snapshot,edit=saved_plan(database);job=edit_job(database,plan_id,edit)
    worker=Worker(settings)
    try:
        worker.acquire();worker.claim()
        if failure=='engine':
            with worker.transaction() as session:fail_work(session,session.get(Job,job.id),'ENGINE_FAILED','Edit was not applied.')
        elif failure=='stale':
            with Session(engine) as session,session.begin():session.get(Plan,plan_id).version+=1
            with pytest.raises(DataError),worker.transaction() as session:save_result(session,job.id,empty_result(snapshot))
            with worker.transaction() as session:fail_work(session,session.get(Job,job.id),'PLAN_CHANGED','Late edit was not applied.')
        else:
            worker.close();worker=Worker(settings);worker.acquire()
        with Session(engine) as session:
            plan=session.get(Plan,plan_id)
            assert plan.result['dataset']=='Test-only draft' and plan.result_stale
            assert plan.validation=='FAIL' and plan.active_job_id is None and plan.version==(4 if failure=='stale' else 3)
        if failure!='stale':assert edit_job(database,plan_id,edit,3,'retry').status=='QUEUED'
    finally:worker.close()


def test_sources_checked_for_edit_finalize_export_and_foreign_ids(database):
    engine,_,settings=database;plan_id,snapshot,edit=saved_plan(database)
    with Session(engine) as session,session.begin():session.get(Order,snapshot.orders[0].id).version+=1
    for operation in [lambda s:submit_edit(s,settings.demo_organization_id,plan_id,edit,1,'new'),
                      lambda s:finalize(s,settings.demo_organization_id,plan_id,1,FinalizeRequest()),
                      lambda s:export_csv(s,settings.demo_organization_id,plan_id,1)]:
        with pytest.raises(DataError) as e,Session(engine) as session,session.begin():operation(session)
        assert e.value.code=='SOURCE_CHANGED'
    with TestClient(create_app(settings),base_url='http://localhost:8000') as client:
        for suffix in ('edits','finalize'):
            body=edit.model_dump(mode='json') if suffix=='edits' else {}
            response=client.post(f'/api/v1/plans/{uuid4()}/{suffix}',json=body,headers={**WRITE,'If-Match':'"1"','Idempotency-Key':'test'})
            assert response.status_code==404
        assert client.get(f'/api/v1/plans/{uuid4()}/export.csv',headers={'If-Match':'"1"'}).status_code==404


def test_invalid_coverage_pending_synthetic_and_http_headers(database):
    engine,_,settings=database;plan_id,snapshot,edit=saved_plan(database)
    invalid=edit.model_copy(update={'unassigned_order_ids':edit.unassigned_order_ids[:-1]})
    with pytest.raises(DataError) as e:edit_job(database,plan_id,invalid)
    assert e.value.code=='INVALID_ASSIGNMENT'
    with TestClient(create_app(settings),base_url='http://localhost:8000') as client:
        path=f'/api/v1/plans/{plan_id}'
        for suffix in ('edits','finalize'):
            response=client.post(path+'/'+suffix,json=edit.model_dump(mode='json') if suffix=='edits' else {},headers=WRITE)
            assert response.status_code==428
        assert client.get(path+'/export.csv').status_code==428
        for method,suffix in [('post','finalize'),('get','export.csv')]:
            kwargs={'json':{}} if method=='post' else {}
            response=getattr(client,method)(path+'/'+suffix,headers={**WRITE,'If-Match':'"1"'},**kwargs)
            assert response.status_code==422 and response.json()['code']=='SYNTHETIC_NOT_OPERATIONAL'
        edit_job(database,plan_id,edit)
        response=client.post(path+'/finalize',json={},headers={**WRITE,'If-Match':'"2"'})
        assert response.status_code==409 and response.json()['code']=='PLAN_BUSY'


def test_csv_formula_cells_are_neutralized():
    for value in ['=SUM(1,2)','+cmd','-1+2','@SUM(A1:A2)','  =cmd','\t=cmd','\r@cmd','\x01=cmd','\ufeff=cmd','\u00a0+cmd']:
        assert safe_cell(value).startswith("'")
    assert safe_cell('Normal, "quoted" address')=='Normal, "quoted" address'
