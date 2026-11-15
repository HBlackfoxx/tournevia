from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.accounts import Organization
from app.fleet import read_depot
from app.jobs import Job, Plan, OptimizationRequest, submit, save_result, fail_job
from app.main import create_app
from app.planning import Draft, PlanningInput, Route, Unassigned, VehicleSource
from app.provision import provision_demo
from app.records import DataError, Order, Vehicle, write_record
from app.worker import Worker, GuardLost
from test_routing_core import inputs

pytestmark=pytest.mark.postgres
WRITE={'Origin':'http://localhost:8000','X-VRP-Request':'1'}


def seed(database, fixture=None):
    engine,_,settings=database
    fixture=fixture or inputs()
    with Session(engine) as session,session.begin():
        provision_demo(session,settings,fixture.depot.data)
        # Saved vehicles work their fixture hours every day; a calculation dates them again.
        vehicles=[write_record(session,settings.demo_organization_id,VehicleSource.weekly(v.data)) for v in fixture.vehicles]
        orders=[]
        for o in fixture.orders:
            created=write_record(session,settings.demo_organization_id,o.data)
            orders.append(write_record(session,settings.demo_organization_id,o.data,created.id,created.version))
        depot=read_depot(session,settings.demo_organization_id)
    return OptimizationRequest(service_date=fixture.service_date,vehicle_ids=[v.id for v in vehicles],
        order_ids=[o.id for o in orders],routing_policy=fixture.routing_policy,objective=fixture.objective,
        solver_time_limit_seconds=1,source_versions={r.id:r.version for r in [depot,*vehicles,*orders]})


def enqueue(database,request,key='test'):
    engine,_,settings=database
    with Session(engine) as session,session.begin():
        return submit(session,settings.demo_organization_id,request,key)


def empty_result(snapshot):
    return Draft(organization_id=snapshot.organization_id,objective=snapshot.objective,routing_policy=snapshot.routing_policy,
        dataset='Test-only draft',routes=[],unassigned=[Unassigned(order_id=o.id,reason='SOLVER_UNASSIGNED') for o in snapshot.orders],
        validation='PASS',blocking_issues=[])


def test_submission_replay_race_and_immutable_snapshot(database):
    engine,_,settings=database;request=seed(database)
    with ThreadPoolExecutor(2) as pool:
        jobs=list(pool.map(lambda _:enqueue(database,request),range(2)))
    assert jobs[0].id==jobs[1].id
    reordered=request.model_dump();reordered['order_ids'].reverse()
    assert enqueue(database,OptimizationRequest.model_validate(reordered)).id==jobs[0].id
    with Session(engine) as session,session.begin():
        row=session.get(Order,request.order_ids[0]);value=deepcopy(row.data);value['active']=False;row.data=value;row.version+=1
    # Replay does not reread changed sources or silently replace the original snapshot.
    assert enqueue(database,request).id==jobs[0].id
    with Session(engine) as session:
        snapshot=PlanningInput.model_validate(session.get(Job,jobs[0].id).input_data)
        assert all(o.data.active for o in snapshot.orders)
        assert session.scalar(select(func.count()).select_from(Job))==1
    with pytest.raises(DataError) as failure:
        enqueue(database,request,'new')
    assert failure.value.status==412
    changed=request.model_copy(update={'objective':'MODELED_TRAVEL_COST'})
    with pytest.raises(DataError) as failure:enqueue(database,changed)
    assert failure.value.status==409


@pytest.mark.parametrize('mutation,code',[('unreviewed','LOCATION_NOT_REVIEWED'),('inactive','INVALID_SELECTION'),('off_day','VEHICLE_OFF_DAY'),('foreign','NOT_FOUND'),('versions','SOURCE_CHANGED')])
def test_submission_rejects_invalid_selection(database,mutation,code):
    engine,_,settings=database;request=seed(database)
    request=request.model_copy(update={'source_versions':None})
    if mutation in ('unreviewed','inactive'):
        with Session(engine) as session,session.begin():
            row=session.get(Order,request.order_ids[0]);data=deepcopy(row.data)
            if mutation=='unreviewed':data['location']['reviewed']=False
            else:data['active']=False
            row.data=data
    if mutation=='foreign':request=request.model_copy(update={'vehicle_ids':[uuid4()]})
    if mutation=='off_day':
        with Session(engine) as session,session.begin():
            row=session.get(Vehicle,request.vehicle_ids[0]);data=deepcopy(row.data)
            data['exceptions']=[{'date':request.service_date.isoformat(),'hours':None}]  # A day off on the planning day.
            row.data=data
    if mutation=='versions':request=request.model_copy(update={'source_versions':{uuid4():1}})
    with pytest.raises(DataError) as failure:enqueue(database,request)
    assert failure.value.code==code
    with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Job))==0


def test_atomic_save_single_snapshot_and_late_result_rejection(database):
    engine,_,settings=database;job=enqueue(database,seed(database));worker=Worker(settings)
    try:
        worker.acquire();job_id,snapshot=worker.claim();result=empty_result(snapshot)
        with pytest.raises(RuntimeError),Session(engine) as session,session.begin():
            save_result(session,job_id,result)
            raise RuntimeError('Simulate transaction failure after both writes')
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Plan))==0
            row=session.get(Job,job.id);assert row.status=='RUNNING' and row.input_data is not None
        with Session(engine) as session,session.begin():plan_id=save_result(session,job_id,result)
        with Session(engine) as session:
            row=session.get(Job,job.id);plan=session.get(Plan,plan_id)
            assert row.status=='SUCCEEDED' and row.input_data is None
            assert plan.input_data==snapshot.model_dump(mode='json')
            assert not plan.result['operational_ready'] and plan.status=='DRAFT' and not plan.result['routes']
        with pytest.raises(DataError),Session(engine) as session,session.begin():save_result(session,job_id,result)
    finally:worker.close()


@pytest.mark.parametrize('mutation',['owner','coverage','policy','validation','profile','provenance'])
def test_save_rejects_inconsistent_result(database,mutation):
    engine,_,settings=database;enqueue(database,seed(database));worker=Worker(settings)
    try:
        worker.acquire();job_id,snapshot=worker.claim();result=empty_result(snapshot)
        if mutation=='owner':result.organization_id=uuid4()
        if mutation=='provenance':result.data_kind='ROUTED'
        if mutation=='coverage':result.unassigned=[]
        if mutation=='policy':result.routing_policy='TRUCK_PREFER_NO_TOLLS' if result.routing_policy!='TRUCK_PREFER_NO_TOLLS' else 'TRUCK_STANDARD'
        if mutation=='validation':result.blocking_issues=['Injected violation']
        if mutation=='profile':
            wrong=snapshot.vehicles[0].data.routing.model_copy(update={'height_mm':9999})
            result.routes=[Route(vehicle_id=snapshot.vehicles[0].id,profile=wrong,departure=0,end=0,stops=[],legs=[],
                distance_m=0,driving_seconds=0,waiting_seconds=0,service_seconds=0,fixed_minor=0,distance_minor=0,time_minor=0,known_cost_minor=0)]
        with pytest.raises(DataError),Session(engine) as session,session.begin():save_result(session,job_id,result)
        with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Plan))==0
    finally:worker.close()


def test_guard_exclusion_connection_loss_and_recovery(database):
    engine,_,settings=database;job=enqueue(database,seed(database));first=Worker(settings);second=Worker(settings)
    try:
        first.acquire();first.claim()
        with pytest.raises(GuardLost):second.acquire()
        second.close()
        with first.transaction() as session:pid=session.scalar(text('SELECT pg_backend_pid()'))
        with engine.begin() as connection:assert connection.scalar(text('SELECT pg_terminate_backend(:pid)'),{'pid':pid})
        with pytest.raises(GuardLost):first.tick(job.id,'SAVE')
        with pytest.raises(GuardLost):first.tick()
        assert first.broken
        second=Worker(settings);second.acquire()
        with Session(engine) as session:
            row=session.get(Job,job.id);assert row.status=='FAILED' and row.error['code']=='WORKER_INTERRUPTED'
            assert session.scalar(select(func.count()).select_from(Plan))==0
        assert second.claim() is None
    finally:first.close();second.close()


def test_safe_failure_and_explicit_rerun(database,monkeypatch):
    engine,_,settings=database;request=seed(database);job=enqueue(database,request);worker=Worker(settings)
    def fail(*args):raise ValueError('Sensitive input must not leak')
    monkeypatch.setattr(worker,'compute',fail)
    try:
        worker.acquire();assert worker.process_one()
        with Session(engine) as session:
            row=session.get(Job,job.id);assert row.status=='FAILED' and 'Sensitive' not in str(row.error)
        assert enqueue(database,request).id==job.id
        retry=enqueue(database,request,'explicit-rerun');assert retry.id!=job.id
        with Session(engine) as session,session.begin():
            row=session.get(Job,retry.id);row.input_data={'invalid':True}
        assert not worker.process_one()
        with Session(engine) as session:assert session.get(Job,retry.id).error['code']=='INPUT_INVALID'
    finally:worker.close()


def test_http_ownership_headers_and_saved_plan(database):
    engine,_,settings=database;request=seed(database)
    with TestClient(create_app(settings),base_url='http://localhost:8000') as client:
        payload=request.model_dump(mode='json');path='/api/v1/optimization-jobs'
        assert client.post(path,json=payload,headers=WRITE).status_code==428
        response=client.post(path,json=payload,headers={**WRITE,'Idempotency-Key':'http'})
        assert response.status_code==202 and response.headers['location'].endswith(response.json()['id'])
        assert client.get('/api/v1/jobs').status_code==422
        jobs=client.get(f'/api/v1/jobs?service_date={request.service_date}').json();assert len(jobs)==1
        worker=Worker(settings)
        try:
            worker.acquire();job_id,snapshot=worker.claim()
            with worker.transaction() as session:plan_id=save_result(session,job_id,empty_result(snapshot))
        finally:worker.close()
        plan=client.get(f'/api/v1/plans/{plan_id}');assert plan.status_code==200 and plan.headers['etag']=='"1"'
        assert not plan.json()['result']['operational_ready']
        assert 'result' not in client.get(f'/api/v1/plans?service_date={request.service_date}').json()[0]
        other=uuid4()
        with Session(engine) as session,session.begin():
            session.add(Organization(id=other,name='Other company'));session.flush()
            session.get(Job,job_id).plan_id=None
            fail_job(session.get(Job,job_id),'TEST','test');session.get(Job,job_id).input_data=snapshot.model_dump(mode='json')
            session.flush()
            session.get(Job,job_id).organization_id=other
            session.get(Plan,plan_id).organization_id=other
        assert client.get(f'/api/v1/jobs/{job_id}').status_code==404
        assert client.get(f'/api/v1/plans/{plan_id}').status_code==404
        assert client.get(f'/api/v1/jobs?service_date={request.service_date}').json()==[]
        assert client.post(path,json={**payload,'organization_id':str(other)},headers={**WRITE,'Idempotency-Key':'fake'}).status_code==422
        assert client.post(path,json={**payload,'data_kind':'ROUTED'},headers={**WRITE,'Idempotency-Key':'forged-origin'}).status_code==422


def test_points_farther_apart_than_the_router_allows_are_refused_before_queueing(database):
    engine,_,_=database
    request=seed(database)
    with Session(engine) as session,session.begin():
        far=session.get(Order,request.order_ids[0])
        far.data={**far.data,'location':{'latitude':45.7576,'longitude':4.8317,'reviewed':True}}  # Lyon, ~390 km away.
        name=far.data['external_id']
    with pytest.raises(DataError) as refused:
        submit_with(database,request.model_copy(update={'source_versions':None}),max_km=200)
    assert refused.value.code=='AREA_TOO_WIDE' and name in refused.value.identifiers
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Job))==0
    assert submit_with(database,request.model_copy(update={'source_versions':None}),max_km=500).status=='QUEUED'


def submit_with(database,request,max_km):
    engine,_,settings=database
    with Session(engine) as session,session.begin():
        return submit(session,settings.demo_organization_id,request,f'area-{max_km}',max_km=max_km)
