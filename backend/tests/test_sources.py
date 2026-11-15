from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import Organization
from app.config import ROOT
from app.imports import import_csv
from app.main import create_app
from app.planning import OrderInput, VehicleSource
from app.provision import provision_demo
from app.records import DataError, Order, write_record
from app.routing import EngineError, Matrix
from test_postgres import example

pytestmark = pytest.mark.postgres
WRITE = {'Origin':'http://localhost:8000', 'X-VRP-Request':'1'}


def sample(name):
    return json.loads((ROOT / f'examples/{name}.json').read_text())


@pytest.fixture
def client(database):
    engine, _, settings = database
    with Session(engine) as session, session.begin():
        provision_demo(session, settings, example())
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        yield client


def upload(client, kind, content=None, dry=False):
    return client.post(f'/api/v1/imports/{kind}?dry_run={str(dry).lower()}',
                       content=content if content is not None else (ROOT/f'examples/{kind}.csv').read_bytes(),
                       headers={**WRITE, 'Content-Type':'text/csv'})


def test_crud_ownership_versions_review_reset_and_dates(client, database):
    engine, _, settings = database
    data = sample('order')
    response = client.post('/api/v1/orders', json=data, headers=WRITE)
    assert response.status_code == 201
    row = response.json(); path = '/api/v1/orders/'+row['id']
    assert not row['data']['location']['reviewed'] and response.headers['etag']=='"1"'
    assert row['organization_id'] == str(settings.demo_organization_id)
    assert client.post('/api/v1/orders', json={**data,'organization_id':str(uuid4())},headers=WRITE).status_code==422
    assert client.post('/api/v1/orders', json=data,headers=WRITE).status_code==409
    assert client.put(path,json=data,headers=WRITE).status_code==428
    assert client.put(path,json=data,headers={**WRITE,'If-Match':'*'}).status_code==400
    assert client.put(path,json=data,headers={**WRITE,'If-Match':'"9"'}).status_code==412
    confirmed=client.put(path,json=data,headers={**WRITE,'If-Match':'"1"'})
    assert confirmed.json()['data']['location']['reviewed']
    data['location']['latitude']+=.001
    moved=client.put(path,json=data,headers={**WRITE,'If-Match':'"2"'}).json()
    assert moved['version']==3 and not moved['data']['location']['reviewed']
    assert client.put(path,json=data,headers={**WRITE,'If-Match':'"2"'}).status_code==412
    assert client.get('/api/v1/orders').status_code==422
    assert len(client.get('/api/v1/orders?service_date=2026-09-23').json())==1
    assert client.get('/api/v1/orders?service_date=2026-09-24').json()==[]
    other=uuid4()
    with Session(engine) as session, session.begin():
        session.add(Organization(id=other,name='Second synthetic organization'));session.flush()
        foreign=write_record(session,other,OrderInput.model_validate(data))
        v=write_record(session,other,VehicleSource.model_validate(sample('vehicle')))
    assert client.get(f'/api/v1/orders/{foreign.id}').status_code==404
    assert client.put(f'/api/v1/orders/{foreign.id}',json=data,headers={**WRITE,'If-Match':'"1"'}).status_code==404
    assert client.get(f'{path}/access-check?vehicle_id={v.id}').status_code==404
    assert client.get(f'/api/v1/vehicles/{v.id}').status_code==404


def test_depot_and_vehicle_edits_preserve_constraints(client):
    depot=client.get('/api/v1/depot').json(); data=depot['data']; data['location']['longitude']+=.001
    changed=client.put('/api/v1/depot',json=data,headers={**WRITE,'If-Match':'"1"'})
    assert changed.status_code==200 and not changed.json()['data']['location']['reviewed']
    assert client.put('/api/v1/depot',json=data,headers={**WRITE,'If-Match':'"1"'}).status_code==412
    v=sample('vehicle');v['limits']={'max_stops':4,'max_distance_m':5000,'max_driving_seconds':3600}
    r=client.post('/api/v1/vehicles',json=v,headers=WRITE);assert r.status_code==201
    path='/api/v1/vehicles/'+r.json()['id'];v['active']=False
    assert client.put(path,json=v,headers={**WRITE,'If-Match':'"1"'}).json()['data']['active'] is False
    for changes in [{'limits':{'max_stops':0}},{'pickup':True},{'hours':{'monday':{'start':'18:00','end':'02:00'}}},
                    {'exceptions':[{'date':'2026-12-24','hours':None},{'date':'2026-12-24','hours':None}]}]:
        assert client.put(path,json={**v,**changes},headers={**WRITE,'If-Match':'"2"'}).status_code==422


def test_csv_preview_atomic_commit_replay_and_conflict(client):
    preview=upload(client,'orders',dry=True).json()
    assert preview['inserted']==2 and not preview['committed']
    assert client.get('/api/v1/orders?service_date=2026-09-23').json()==[]
    assert upload(client,'orders').json()['committed']
    orders=client.get('/api/v1/orders?service_date=2026-09-23').json()
    row=orders[0]; row['data']['location']['reviewed']=True
    path='/api/v1/orders/'+row['id']
    assert client.put(path,json=row['data'],headers={**WRITE,'If-Match':'"1"'}).status_code==200
    replay=upload(client,'orders').json()
    assert replay['unchanged']==2 and replay['inserted']==0
    assert client.get(path).json()['data']['location']['reviewed']
    assert client.get(path).json()['version']==2
    row['data']['priority']=10
    assert client.put(path,json=row['data'],headers={**WRITE,'If-Match':'"2"'}).status_code==200
    # A file without the priority column says nothing about it: the saved priority stays.
    assert upload(client,'orders').json()['unchanged']==2
    lines=(ROOT/'examples/orders.csv').read_text().splitlines()
    changed='\n'.join([lines[0],lines[1].replace(',1200,',',1500,'),lines[2]])
    conflict=upload(client,'orders',changed);assert conflict.status_code==422
    assert 'version-checked edit' in conflict.text
    assert client.get(path).json()['data']['priority']==10
    # A later conflicting row must roll back all earlier would-be inserts.
    content='\n'.join([lines[0],lines[1].replace('DEMO-ORDER-001','NEW-ATOMIC'),lines[1].replace('1820','1821')])
    assert upload(client,'orders',content).status_code==422
    assert len(client.get('/api/v1/orders?service_date=2026-09-23').json())==2


def test_csv_optional_priority_and_route_limit_columns(client):
    lines=(ROOT/'examples/orders.csv').read_text().splitlines()
    orders='\n'.join([lines[0]+',priority',lines[1]+',70',lines[2]+','])
    assert upload(client,'orders',orders).status_code==200
    saved={o['data']['external_id']:o['data']['priority'] for o in client.get('/api/v1/orders?service_date=2026-09-23').json()}
    assert saved=={'DEMO-ORDER-001':70,'DEMO-ORDER-002':0}
    vehicle=(ROOT/'examples/vehicles.csv').read_text().splitlines()
    limits='\n'.join([vehicle[0]+',max_stops,max_distance_m,max_driving_seconds',vehicle[1]+',12,,28800'])
    assert upload(client,'vehicles',limits).status_code==200
    assert client.get('/api/v1/vehicles').json()[0]['data']['limits']=={'max_stops':12,'max_driving_seconds':28800}
    # Partial or reordered optional columns and out-of-range values are rejected.
    for header,row in [(',max_stops',',12'),(',max_driving_seconds,max_stops,max_distance_m',',1,2,3')]:
        response=upload(client,'vehicles','\n'.join([vehicle[0]+header,vehicle[1].replace('TRUCK-DEMO-01','OTHER')+row]))
        assert response.status_code==422 and response.json()['errors'][0]['field']=='headers'
    bad=upload(client,'orders','\n'.join([lines[0]+',priority',lines[1].replace('DEMO-ORDER-001','NEW')+',101']))
    assert bad.status_code==422 and bad.json()['errors'][0]['field']=='priority'


def test_csv_commit_rechecks_after_preview(client):
    assert upload(client,'vehicles',dry=True).status_code==200
    data=sample('vehicle');data['capacity']={**data['capacity'],'weight_kg':data['capacity']['weight_kg']-1}
    assert client.post('/api/v1/vehicles',json=data,headers=WRITE).status_code==201
    assert upload(client,'vehicles').status_code==422
    assert len(client.get('/api/v1/vehicles').json())==1


@pytest.mark.parametrize('mutation', ['duplicate','extra_column','bad_number','bad_bool','bad_utf8','too_many','too_big','bad_quote'])
def test_bad_csv_is_actionable_and_writes_nothing(client,mutation):
    text=(ROOT/'examples/orders.csv').read_text();lines=text.splitlines()
    if mutation=='duplicate':text+='\n'+lines[1]
    if mutation=='extra_column':text=text.replace('external_id,','unsupported,external_id,',1)
    if mutation=='bad_number':text=text.replace(',1820,',',1.5,')
    if mutation=='bad_bool':text=text.replace(',true,',',yes,')
    if mutation=='bad_utf8':text=b'\xff'
    if mutation=='too_many':text='\n'.join([lines[0]]+[lines[1].replace('DEMO-ORDER-001',f'ID-{i}') for i in range(251)])
    if mutation=='too_big':text=b'x'*1048577
    if mutation=='bad_quote':text=lines[0]+'\n"unterminated'
    response=upload(client,'orders',text)
    assert response.status_code in (413,422)
    if response.status_code==422:assert response.json()['errors'][0]['field']
    assert client.get('/api/v1/orders?service_date=2026-09-23').json()==[]


def test_local_writes_require_origin_and_custom_header(client):
    for headers in [{},{'Origin':'http://localhost:8000'},{'Origin':'https://evil.example','X-VRP-Request':'1'}]:
        assert client.post('/api/v1/vehicles',json=sample('vehicle'),headers=headers).status_code==403
    assert client.post('/api/v1/imports/orders',content='hello',headers={**WRITE,'Content-Type':'text/plain'}).status_code==415


def test_concurrent_edits_and_imports_are_serialized(database,client):
    engine,_,settings=database
    created=client.post('/api/v1/vehicles',json=sample('vehicle'),headers=WRITE).json()
    def edit(label):
        value=sample('vehicle');value['fleet_number']=label
        try:
            with Session(engine) as session,session.begin():
                write_record(session,settings.demo_organization_id,VehicleSource.model_validate(value),UUID(created['id']),1)
            return 'saved'
        except DataError as e:return e.status
    from uuid import UUID
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(edit,['EDIT-A','EDIT-B']),key=str)==[412,'saved']
    def commit(_):
        with Session(engine) as session,session.begin():
            return import_csv(session,settings.demo_organization_id,'orders',(ROOT/'examples/orders.csv').read_bytes(),False)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(commit,range(2)))
    assert sorted(r.inserted for r in results)==[0,2]
    with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Order))==2


def test_access_failures_are_visible_and_never_review_point(client,monkeypatch):
    v=client.post('/api/v1/vehicles',json=sample('vehicle'),headers=WRITE).json()
    o=client.post('/api/v1/orders',json=sample('order'),headers=WRITE).json()
    path=f"/api/v1/orders/{o['id']}/access-check?vehicle_id={v['id']}"
    monkeypatch.setattr('app.data_api.Valhalla.matrix',lambda *args:Matrix([[0,None],[None,0]],[[0,None],[None,0]]))
    response=client.get(path);assert response.status_code==200 and response.json()['status']=='NO_ROUTE_IN_DATASET'
    def fail(*args):raise EngineError('Road point missing.')
    monkeypatch.setattr('app.data_api.Valhalla.matrix',fail)
    response=client.get(path);assert response.status_code==503 and response.json()['code']=='ACCESS_CHECK_FAILED'
    assert not client.get('/api/v1/orders/'+o['id']).json()['data']['location']['reviewed']


def test_csv_errors_name_the_column_with_translatable_wording(client):
    rows=list(csv.reader(io.StringIO((ROOT/'examples/orders.csv').read_text())))
    rows[1][4]='95'  # latitude
    rows[2][7]=rows[2][6].replace('08:00','07:00')  # window end before its start
    out=io.StringIO();csv.writer(out,lineterminator='\n').writerows(rows[:3])
    errors=upload(client,'orders',out.getvalue(),dry=True).json()['errors']
    assert {'row':2,'field':'latitude','message':'Use a value of at most 90.','template':'Use a value of at most {0}.','values':['90']} in errors
    window=next(e for e in errors if e['row']==3)
    assert window['field']=='time_window_start, time_window_end' and window['template']==window['message']=='Window start must precede end.'
    headers=upload(client,'orders','wrong,headers\n1,2',dry=True).json()['errors'][0]
    assert headers['template']=='Expected exact headers: {0}, optionally followed by: {1}' and headers['values'][1]=='priority,customer,ships_from'


def test_a_file_saved_by_a_french_spreadsheet_reads_like_the_standard_one(client):
    # Semicolons, a byte-order mark, decimal commas and the spreadsheet's own words for true.
    rows=list(csv.reader(io.StringIO((ROOT/'examples/orders.csv').read_text(encoding='utf-8'))))
    spreadsheet=[rows[0]]+[[{'true':word}.get(cell,cell) for cell in row] for row,word in zip(rows[1:],['VRAI','TRUE','Vrai']*99)]
    for row in spreadsheet[1:]:
        row[4],row[5]=row[4].replace('.',','),row[5].replace('.',',')
    out=io.StringIO();out.write('\ufeff');csv.writer(out,delimiter=';',lineterminator='\r\n').writerows(spreadsheet)
    assert ',' in spreadsheet[1][4] and ';' in out.getvalue().splitlines()[0]
    french=upload(client,'orders',out.getvalue(),dry=True).json()
    standard=upload(client,'orders',(ROOT/'examples/orders.csv').read_text(encoding='utf-8'),dry=True).json()
    assert french['errors']==[] and [r['data'] for r in french['rows']]==[r['data'] for r in standard['rows']]
    rows[1][2]='oui'
    out=io.StringIO();csv.writer(out,lineterminator='\n').writerows(rows[:2])
    assert upload(client,'orders',out.getvalue(),dry=True).json()['errors'][0]['message']=='Use true or false.'


def test_api_field_errors_carry_translatable_wording(client):
    response=client.post('/api/v1/vehicles',json={'fleet_number':''},headers=WRITE)
    assert response.status_code==422
    missing=[e for e in response.json()['detail'] if e['type']=='missing']
    assert missing and all(e['template']=='This value is required.' and e['values']==[] for e in missing)
