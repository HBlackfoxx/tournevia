from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.planning import VehicleSource
from app.accounts import Organization, User
from app.auth import LOCAL_COOKIE
from app.config import ROOT, Settings
from app.main import create_app
from app.onboarding import SetupRequest, initialize
from app.records import DataError, Order, Vehicle
from test_geocoding import geocoder  # noqa: F401  (a fixture)

pytestmark = pytest.mark.postgres
TOKEN = 'synthetic-setup-token-32-characters-long'
PASSWORD = 'my synthetic onboarding password'


def payload(**changes):
    import json
    return {'setup_token': TOKEN, 'username': 'owner', 'password': PASSWORD,
            'display_name': 'Workspace owner', 'company_name': 'Example company',
            'depot': json.loads((ROOT/'examples/depot.json').read_text()), **changes}


@pytest.mark.parametrize('mode,origin,cookie', [('local','http://localhost:8000',LOCAL_COOKIE),
                                             ('shared','https://planner.test','__Host-vrp_session')])
def test_empty_installation_creates_named_company_once_and_requires_login(database,mode,origin,cookie):
    engine,_,base=database
    settings=base.model_copy(update={'app_mode':mode,'public_origin':origin,'setup_token':TOKEN,'map_style_url':'https://maps.example/own-style.json'})
    # model_copy does not validate secrets; use ordinary validated settings.
    settings=Settings.model_validate(settings.model_dump())
    write={'Origin':origin,'X-VRP-Request':'1'}
    with TestClient(create_app(settings),base_url=origin) as client:
        assert client.get('/api/v1/ready').status_code==200
        assert client.get('/api/v1/setup').json()=={'required':True,'enabled':True,'map_style_url':'https://maps.example/own-style.json'}
        assert client.get('/api/v1/depot').status_code==401
        assert client.post('/api/v1/setup',json=payload()).status_code==403
        assert client.post('/api/v1/setup',json=payload(setup_token='wrong-token-that-is-at-least-32-characters'),headers=write).status_code==403
        response=client.post('/api/v1/setup',json=payload(),headers=write)
        assert response.status_code==201, response.text
        assert response.json()['identity']['role']=='admin' and response.json()['identity']['user_id']
        assert response.json()['identity']['language']=='en'
        assert TOKEN not in response.text and PASSWORD not in response.text
        assert client.cookies.get(cookie)
        assert ('Secure' in response.headers['set-cookie'])==(mode=='shared')
        csrf=response.json()['csrf_token']
        assert client.get('/api/v1/setup').json()=={'required':False,'enabled':False}
        config=client.get('/api/v1/configuration').json()
        assert config['company_name']=='Example company' and config['data_kind']=='UNVERIFIED'
        assert client.get('/api/v1/vehicles').json()==[]
        assert client.post('/api/v1/setup',json=payload(username='intruder'),headers=write).status_code==409
        assert client.delete('/api/v1/session',headers=write).status_code==403
        assert client.delete('/api/v1/session',headers={**write,'X-CSRF-Token':csrf}).status_code==200
        assert client.get('/api/v1/depot').status_code==401
        assert client.post('/api/v1/session',json={'username':'owner','password':PASSWORD},headers=write).status_code==200
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Organization))==1
        assert session.scalar(select(func.count()).select_from(User))==1
        assert session.scalar(select(func.count()).select_from(Order))==0


def test_setup_race_rolls_back_loser_and_creates_an_empty_company(database):
    engine,_,base=database
    settings=Settings.model_validate({**base.model_dump(),'app_mode':'local','setup_token':TOKEN})
    def claim(index):
        try:
            with Session(engine) as session,session.begin():
                initialize(session,settings,SetupRequest.model_validate(payload(username=f'owner{index}')))
            return 201
        except DataError as error:
            return error.status
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(claim,range(2)))==[201,409]
    with Session(engine) as session:
        assert not session.scalar(select(Organization)).is_demo
        assert session.scalar(select(func.count()).select_from(User))==1
        assert session.scalar(select(func.count()).select_from(Vehicle))==0
        assert session.scalar(select(func.count()).select_from(Order))==0


def test_invalid_or_disabled_setup_leaves_empty_database(database):
    engine,_,base=database
    settings=Settings.model_validate({**base.model_dump(),'app_mode':'local','setup_token':TOKEN})
    write={'Origin':settings.public_origin,'X-VRP-Request':'1'}
    with TestClient(create_app(settings),base_url=settings.public_origin) as client:
        for change in [{'depot':None},{'workspace_kind':'DEMO'},{'password':'short'},{'company_name':'  '},
                       {'organization_id':str(uuid4())},{'role':'planner'}]:
            assert client.post('/api/v1/setup',json=payload(**change),headers=write).status_code==422
    locked=Settings.model_validate({**settings.model_dump(),'setup_token':''})
    with TestClient(create_app(locked),base_url=locked.public_origin) as client:
        assert client.get('/api/v1/setup').json()=={'required':True,'enabled':False}
        assert client.post('/api/v1/setup',json=payload(),headers=write).status_code==403
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Organization))==0


def test_failed_setup_is_atomic(database,monkeypatch):
    engine,_,base=database
    settings=Settings.model_validate({**base.model_dump(),'app_mode':'local','setup_token':TOKEN})
    def fail(*args,**kwargs): raise RuntimeError('simulated account write failure')
    monkeypatch.setattr('app.onboarding.create_user',fail)
    with pytest.raises(RuntimeError),Session(engine) as session,session.begin():
        initialize(session,settings,SetupRequest.model_validate(payload()))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Organization))==0


def test_named_submission_uses_workspace_origin_and_rejects_client_override(database):
    from app.jobs import Job
    from app.planning import PlanningInput
    engine,_,base=database
    settings=Settings.model_validate({**base.model_dump(),'app_mode':'local','setup_token':TOKEN})
    fixture=PlanningInput.model_validate_json((ROOT/'backend/tests/fixtures/paris-ten.json').read_text())
    write={'Origin':settings.public_origin,'X-VRP-Request':'1'}
    with TestClient(create_app(settings),base_url=settings.public_origin) as client:
        setup=client.post('/api/v1/setup',json=payload(),headers=write)
        assert setup.status_code==201
        headers={**write,'X-CSRF-Token':setup.json()['csrf_token'],'Idempotency-Key':'company-job'}
        vehicle=client.post('/api/v1/vehicles',json={**VehicleSource.weekly(fixture.vehicles[0].data).model_dump(mode='json'),
                                                    'fleet_number':'CUSTOM-1'},headers=headers).json()
        order=client.post('/api/v1/orders',json={**fixture.orders[0].data.model_dump(mode='json'),'external_id':'CUSTOM-1'},headers=headers).json()
        request={'service_date':str(fixture.service_date),'vehicle_ids':[vehicle['id']],'order_ids':[order['id']],
                 'routing_policy':'TRUCK_STANDARD','objective':'TRAVEL_TIME','solver_time_limit_seconds':1}
        assert client.post('/api/v1/optimization-jobs',json={**request,'data_kind':'ROUTED'},headers=headers).status_code==422
        assert client.post('/api/v1/optimization-jobs',json=request,headers=headers).json()['code']=='LOCATION_NOT_REVIEWED'
        data={**order['data'],'location':{**order['data']['location'],'reviewed':True}}
        assert client.put('/api/v1/orders/'+order['id'],json=data,headers={**headers,'If-Match':f'"{order["version"]}"'}).status_code==200
        # The company's safety margin: 15 minutes until an administrator changes it, copied into each calculation.
        settings_headers={**write,'X-CSRF-Token':setup.json()['csrf_token']}
        assert client.get('/api/v1/configuration').json()['safety_margin_minutes']==15
        assert client.put('/api/v1/workspace/safety-margin',json={'minutes':121},headers=settings_headers).status_code==422
        assert client.put('/api/v1/workspace/safety-margin',json={'minutes':20},headers=settings_headers).json()=={'minutes':20}
        assert client.get('/api/v1/configuration').json()['safety_margin_minutes']==20
        queued=client.post('/api/v1/optimization-jobs',json=request,headers=headers)
        assert queued.status_code==202,queued.text
        assert client.post('/api/v1/optimization-jobs',json=request,headers=headers).json()['id']==queued.json()['id']
    from uuid import UUID
    with Session(engine) as session:
        row=session.get(Job,UUID(queued.json()['id']))
        assert row.input_data['data_kind']=='ROUTED' and row.input_data['safety_margin_seconds']==1200
        assert row.submitted_by==setup.json()['identity']['user_id']


def test_setup_finds_the_depot_address_only_with_the_setup_token(database, geocoder, monkeypatch):  # noqa: F811
    # IGN's service, played by the fake geocoder: nothing leaves the test.
    monkeypatch.setattr('app.geocoding.PUBLIC_SERVICE_URL', geocoder)
    engine, _, base = database
    settings = Settings.model_validate({**base.model_dump(), 'app_mode': 'local', 'setup_token': TOKEN,
                                        'public_origin': 'http://localhost:8000'})
    write = {'Origin': 'http://localhost:8000', 'X-VRP-Request': '1'}
    find = lambda client, token=TOKEN, public=True: client.get(
        '/api/v1/setup/geocode', params={'q': '12 rue de rivoli paris', 'public': public}, headers={'X-Setup-Token': token})
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        # Without the installation's own geocoder, setup offers IGN's service as a choice.
        assert 'address_lookup' not in client.get('/api/v1/setup').json()
        assert find(client, token='wrong-token-that-is-at-least-32-characters').status_code == 403
        assert find(client, public=False).json()['code'] == 'GEOCODER_DISABLED'
        found = find(client)
        assert found.status_code == 200 and found.json()[0]['label'] == '12 Rue de Rivoli 75004 Paris'
        depot = {**payload()['depot'], 'address': found.json()[0]['label']}
        created = client.post('/api/v1/setup', json=payload(depot=depot, public_address_lookup=True), headers=write)
        assert created.status_code == 201, created.text
        # The choice carries over to the company, and the depot keeps its address.
        assert client.get('/api/v1/configuration').json()['address_lookup_source'] == 'public'
        assert client.get('/api/v1/depot').json()['data']['address'] == '12 Rue de Rivoli 75004 Paris'
        # Once set up, the setup lookup is closed.
        assert find(client).json()['code'] == 'SETUP_COMPLETE'
    with TestClient(create_app(settings.model_copy(update={'geocoder_url': geocoder})), base_url='http://localhost:8000') as client:
        assert find(client).status_code == 409


def test_the_first_administrator_keeps_the_language_setup_was_completed_in(database):
    engine,_,base=database
    settings=Settings.model_validate({**base.model_dump(),'app_mode':'local','setup_token':TOKEN})
    write={'Origin':settings.public_origin,'X-VRP-Request':'1'}
    with TestClient(create_app(settings),base_url=settings.public_origin) as client:
        response=client.post('/api/v1/setup',json=payload(language='fr'),headers=write)
        assert response.status_code==201, response.text
        assert response.json()['identity']['language']=='fr'
        assert client.get('/api/v1/session').json()['identity']['language']=='fr'
        assert client.post('/api/v1/setup',json=payload(language='de'),headers=write).status_code in (409,422)

