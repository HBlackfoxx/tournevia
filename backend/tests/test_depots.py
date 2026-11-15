"""Several depots and places: where each vehicle starts and ends its day, and which depot a delivery ships from."""
import csv
import io
import json
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.config import ROOT
from app.edits import DEPOT_CHANGED_MESSAGE, source_change
from app.jobs import Job, OptimizationRequest, farthest_apart, submit
from app.optimization import prepare
from app.planning import Draft, PlanningInput
from app.records import DataError
from app.routing import Deadline, EngineError, Matrix, coordinates, matrix_tiles
from app.spike import build_routes, points_and_profiles, profile_matrices
from app.validation import validate
from test_pin_checks import fake_engine
from test_sources import WRITE, client  # noqa: F401  (fixture)

FIXTURE = ROOT / 'backend/tests/fixtures/paris-ten.json'
NORD = {'latitude': 48.9236, 'longitude': 2.3600}
HOME = {'latitude': 48.8150, 'longitude': 2.3200}


def place(key: int, code: str, point: dict, kind: str = 'DEPOT', reviewed: bool = True) -> dict:
    owner = json.loads(FIXTURE.read_text())['organization_id']
    return {'id': str(UUID(int=900 + key)), 'organization_id': owner, 'version': 1,
            'main': False, 'data': {'code': code, 'name': code.title(), 'kind': kind, 'address': '',
                                    'location': {**point, 'reviewed': reviewed}, 'timezone': 'Europe/Paris'}}


def with_places(**vehicle_places) -> dict:
    """The fixture with a second depot NORD and a driver's home HOME; vehicles start and end as given."""
    data = json.loads(FIXTURE.read_text())
    data['depot']['main'] = True
    data['depots'] = [place(1, 'NORD', NORD), place(2, 'HOME', HOME, 'PLACE')]
    for index, (start, end) in vehicle_places.items():
        data['vehicles'][int(index[1:])]['data'].update(start=start, end=end)
    return data


def test_route_ends_come_first_and_each_vehicle_starts_and_ends_at_its_own_place():
    model = PlanningInput.model_validate(with_places(v0=('NORD', None), v1=('HOME', 'PARIS-DEMO')))
    points, profiles = points_and_profiles(model)
    main, nord, home = (model.depot.data.location, model.place('NORD').data.location, model.place('HOME').data.location)
    assert points[:3] == [main, nord, home]
    assert sorted(points[3:], key=coordinates) == points[3:]  # Deliveries after the route ends, grouped.
    matrices = [Matrix([[0] * len(points) for _ in points], [[0] * len(points) for _ in points]) for _ in profiles]
    vehicles = prepare(model, points, profiles, matrices).payload['vehicles']
    # NORD with no end returns to NORD; HOME ends its day at the main depot.
    assert [(v['start_index'], v['end_index']) for v in vehicles] == [(1, 1), (2, 0)]
    unchanged = PlanningInput.model_validate(json.loads(FIXTURE.read_text()))
    assert points_and_profiles(unchanged)[0][0] == unchanged.depot.data.location
    assert len(unchanged.route_ends()) == 1


def test_a_delivery_shipping_from_a_depot_needs_a_vehicle_starting_there():
    data = with_places(v0=('NORD', None))
    data['orders'][0]['data']['ships_from'] = 'NORD'
    data['orders'][1]['data']['ships_from'] = 'PARIS-DEMO'
    model = PlanningInput.model_validate(data)
    points, profiles = points_and_profiles(model)
    matrices = [Matrix([[0] * len(points) for _ in points], [[0] * len(points) for _ in points]) for _ in profiles]
    payload = prepare(model, points, profiles, matrices).payload
    equipment = {s for v in model.vehicles for s in v.data.skills} | {s for o in model.orders for s in o.data.required_skills}
    nord, main = len(equipment) + 1, len(equipment) + 2  # One solver skill per depot deliveries ship from, by code.
    assert nord in payload['jobs'][0]['skills'] and main in payload['jobs'][1]['skills']
    assert nord in payload['vehicles'][0]['skills'] and main not in payload['vehicles'][0]['skills']
    assert main in payload['vehicles'][1]['skills'] and nord not in payload['vehicles'][1]['skills']
    assert model.carries(model.vehicles[0].data, model.orders[0].data)
    assert not model.carries(model.vehicles[1].data, model.orders[0].data)


@pytest.mark.parametrize('change,problem', [
    (lambda d: d['vehicles'][0]['data'].update(start='NOWHERE'), 'start and end needs its depot'),
    (lambda d: d['orders'][0]['data'].update(ships_from='HOME'), 'ships from one of the selected depots'),
    (lambda d: d['depots'][0]['data']['location'].update(reviewed=False), 'explicit review'),
    (lambda d: d['depots'][0]['data'].update(code='PARIS-DEMO'), 'codes must be unique'),
])
def test_a_calculation_names_only_known_reviewed_places_and_ships_from_depots(change, problem):
    data = with_places(v0=('NORD', None))
    change(data)
    with pytest.raises(ValueError, match=problem):
        PlanningInput.model_validate(data)


LYON = {'latitude': 45.7576, 'longitude': 4.8317}  # About 390 km from the Paris fixture.


def test_a_main_depot_no_selected_vehicle_uses_needs_no_review_and_sets_no_distance():
    data = with_places(**{f'v{i}': ('NORD', None) for i in range(len(json.loads(FIXTURE.read_text())['vehicles']))})
    data['depot']['data']['location'] = {**LYON, 'reviewed': False}
    model = PlanningInput.model_validate(data)
    assert not model.uses_main_depot and farthest_apart(model, 200) == []
    points, profiles = points_and_profiles(model)
    matrices = profile_matrices(model, fake_engine(), points, profiles, Deadline.after(5))
    assert all(m.durations[0][j] is None and m.durations[j][0] is None for m in matrices for j in range(len(points)))
    # As soon as one selected vehicle ends its day at the main depot, its point is reviewed and in range.
    data['vehicles'][0]['data']['end'] = data['depot']['data']['code']
    with pytest.raises(ValueError, match='explicit review'):
        PlanningInput.model_validate(data)
    data['depot']['data']['location']['reviewed'] = True
    model = PlanningInput.model_validate(data)
    assert model.uses_main_depot and farthest_apart(model, 200)[0] == 'depot'


def routed(model, assignments):
    points, profiles = points_and_profiles(model)
    return build_routes(model, fake_engine(), [(model.vehicles[v].id, None, [model.orders[o].id for o in stops])
                                               for v, stops in assignments],
                        [o.id for i, o in enumerate(model.orders) if all(i not in s for _, s in assignments)],
                        points, profiles, None, Deadline.after(5), lambda _: None, manual=True), points, profiles


def test_routes_run_from_their_own_start_to_their_own_end_and_the_validator_checks_it():
    data = with_places(v0=('NORD', None), v1=('HOME', 'PARIS-DEMO'))
    data['orders'][0]['data']['ships_from'] = 'NORD'
    for order in data['orders']:
        order['data']['required_skills'] = []  # Either vehicle may carry any delivery but for its depot.
    model = PlanningInput.model_validate(data)
    draft, points, profiles = routed(model, [(0, [0]), (1, [1])])
    first, second = draft.routes
    assert first.legs[0].geometry[0] == coordinates(model.place('NORD').data.location)
    assert first.legs[-1].geometry[-1] == coordinates(model.place('NORD').data.location)
    assert second.legs[0].geometry[0] == coordinates(model.place('HOME').data.location)
    assert second.legs[-1].geometry[-1] == coordinates(model.depot.data.location)
    assert validate(model, draft, points, profiles, None) == []
    # The same stops from the main depot do not reach the vehicle's own start; a delivery
    # shipping from NORD on a vehicle starting at HOME breaks the rule.
    wrong = Draft.model_validate(draft.model_dump())
    wrong.routes[0].legs[0].geometry[0] = coordinates(model.depot.data.location)
    assert any(issue.startswith('SNAP') for issue in validate(model, wrong, points, profiles, None))
    swapped, *_ = routed(model, [(1, [0]), (0, [1])])
    assert 'SHIPS_FROM: vehicle does not start at the depot the delivery ships from (VAN-DEMO-01, DEMO-ORDER-001)' in \
        validate(model, swapped, points, profiles, None)


def test_no_vehicle_starting_at_the_depot_a_delivery_ships_from_leaves_it_unassigned_by_name():
    data = with_places(v0=('HOME', None))
    data['orders'][0]['data']['ships_from'] = 'NORD'
    model = PlanningInput.model_validate(data)
    points, profiles = points_and_profiles(model)
    matrices = profile_matrices(model, fake_engine(), points, profiles, Deadline.after(5))
    draft = build_routes(model, fake_engine(), [], [model.orders[0].id] + [o.id for o in model.orders[1:]],
                         points, profiles, matrices, Deadline.after(5), lambda _: None)
    assert draft.unassigned[0].reason == 'NO_COMPATIBLE_VEHICLE'


def test_profile_matrices_take_only_their_vehicles_route_ends_first_and_never_bound_them():
    model = PlanningInput.model_validate(with_places(v0=('NORD', 'HOME')))
    requested = []
    engine = fake_engine()
    plain = engine.matrix
    def matrix(selected, truck, policy, deadline, *, time_bounds=None):
        requested.append((truck.height_mm, [coordinates(p) for p in selected], time_bounds))
        return plain(selected, truck, policy, deadline, time_bounds=time_bounds)
    engine.matrix = matrix
    points, profiles = points_and_profiles(model)
    profile_matrices(model, engine, points, profiles, Deadline.after(5))
    truck, van = requested
    nord, home, main = (coordinates(model.place(code).data.location) for code in ('NORD', 'HOME', 'PARIS-DEMO'))
    assert truck[1][:2] == [nord, home] and main not in truck[1][:2] and truck[2][:2] == [None, None]
    assert all(bound is not None for bound in truck[2][2:])
    assert van[1][0] == main and van[2][0] is None and all(b is not None for b in van[2][1:])


def test_a_place_without_a_road_stops_the_calculation_naming_its_vehicles():
    model = PlanningInput.model_validate(with_places(v0=('HOME', None)))
    truck = model.vehicles[0].data.routing
    engine = fake_engine({(truck.height_mm, coordinates(model.place('HOME').data.location))})
    points, profiles = points_and_profiles(model)
    with pytest.raises(EngineError, match='vehicle TRUCK-DEMO-01 .* the pin of place HOME'):
        profile_matrices(model, engine, points, profiles, Deadline.after(5))


def test_window_pruned_tiles_keep_every_route_end_row_and_column():
    # Three route ends, then four delivery points in two window groups far apart in time.
    bounds = [None, None, None, (0, 10), (0, 10), (500, 600), (500, 600)]
    cells = {(i, j) for s, t, se, te in matrix_tiles(7, 2, bounds, anchors=3) for i in range(s, se) for j in range(t, te)}
    assert all((i, j) in cells for i in range(7) for j in range(3))   # Returns to every route end.
    assert all((i, j) in cells for i in range(3) for j in range(7))   # Departures from every route end.
    assert (5, 3) not in cells and (3, 5) in cells  # A late stop cannot precede an early one.


# ---- Saved depots and places, and the records naming them ----

def depot(code, point=NORD, kind='DEPOT'):
    return {'code': code, 'name': code.title(), 'kind': kind, 'address': '', 'location': {**point, 'reviewed': False},
            'timezone': 'Europe/Paris'}


def add(client, data):  # noqa: F811
    response = client.post('/api/v1/depots', json=data, headers=WRITE)
    assert response.status_code == 201, response.text
    return response.json()


def confirm(client, row):  # noqa: F811
    data = {**row['data'], 'location': {**row['data']['location'], 'reviewed': True}}
    response = client.put(f"/api/v1/depots/{row['id']}", json=data, headers={**WRITE, 'If-Match': f'"{row["version"]}"'})
    assert response.status_code == 200, response.text
    return response.json()


def test_depots_and_places_are_listed_added_changed_made_main_and_deleted(client):  # noqa: F811
    main = client.get('/api/v1/depot').json()
    assert main['main'] is True and client.get('/api/v1/depots').json() == [main]
    nord = add(client, depot('NORD'))
    assert nord['main'] is False and nord['data']['location']['reviewed'] is False  # A new point is unreviewed.
    home = add(client, depot('HOME', HOME, 'PLACE'))
    assert client.post('/api/v1/depots', json=depot('NORD'), headers=WRITE).json()['code'] == 'IDENTIFIER_CONFLICT'
    assert [d['data']['code'] for d in client.get('/api/v1/depots').json()] == ['PARIS-DEMO', 'NORD', 'HOME']
    nord = confirm(client, nord)
    assert nord['data']['location']['reviewed'] is True and nord['version'] == 2
    stale = client.post(f"/api/v1/depots/{nord['id']}/main", headers={**WRITE, 'If-Match': '"1"'})
    assert stale.status_code == 412
    place_main = client.post(f"/api/v1/depots/{home['id']}/main", headers={**WRITE, 'If-Match': '"1"'})
    assert place_main.status_code == 422 and place_main.json()['code'] == 'MAIN_DEPOT_KIND'
    made = client.post(f"/api/v1/depots/{nord['id']}/main", headers={**WRITE, 'If-Match': '"2"'}).json()
    assert made['main'] is True and client.get('/api/v1/depot').json()['data']['code'] == 'NORD'
    old = next(d for d in client.get('/api/v1/depots').json() if d['data']['code'] == 'PARIS-DEMO')
    assert old['main'] is False and old['version'] == main['version'] + 1
    refused = client.delete(f"/api/v1/depots/{made['id']}", headers={**WRITE, 'If-Match': f'"{made["version"]}"'})
    assert refused.status_code == 409 and refused.json()['code'] == 'MAIN_DEPOT'
    gone = client.delete(f"/api/v1/depots/{home['id']}", headers={**WRITE, 'If-Match': '"1"'})
    assert gone.status_code == 204
    assert [d['data']['code'] for d in client.get('/api/v1/depots').json()] == ['NORD', 'PARIS-DEMO']


def vehicle(client, **places):  # noqa: F811
    row = client.get('/api/v1/vehicles').json()[0]
    return client.put(f"/api/v1/vehicles/{row['id']}", json={**row['data'], **places},
                      headers={**WRITE, 'If-Match': f'"{row["version"]}"'})


def test_vehicles_and_deliveries_name_existing_places_and_those_cannot_disappear(client):  # noqa: F811
    assert client.post('/api/v1/imports/vehicles?dry_run=false', content=(ROOT / 'examples/vehicles.csv').read_bytes(),
                       headers={**WRITE, 'Content-Type': 'text/csv'}).json()['committed']
    assert client.post('/api/v1/imports/orders?dry_run=false', content=(ROOT / 'examples/orders.csv').read_bytes(),
                       headers={**WRITE, 'Content-Type': 'text/csv'}).json()['committed']
    nord, home = add(client, depot('NORD')), add(client, depot('HOME', HOME, 'PLACE'))
    unknown = vehicle(client, start='NOWHERE')
    assert unknown.status_code == 422 and unknown.json()['code'] == 'PLACE_UNKNOWN'
    placed = vehicle(client, start='HOME', end='NORD')
    assert placed.status_code == 200 and (placed.json()['data']['start'], placed.json()['data']['end']) == ('HOME', 'NORD')
    order = client.get('/api/v1/orders', params={'service_date': '2026-09-23'}).json()[0]
    path, headers = f"/api/v1/orders/{order['id']}", {**WRITE, 'If-Match': f'"{order["version"]}"'}
    from_home = client.put(path, json={**order['data'], 'ships_from': 'HOME'}, headers=headers)
    assert from_home.status_code == 422 and from_home.json()['code'] == 'NOT_A_DEPOT'
    assert client.put(path, json={**order['data'], 'ships_from': 'NORD'}, headers=headers).status_code == 200
    for row, changes in [(nord, {'code': 'NORD-2'}), (home, {'code': 'HOME-2'})]:
        renamed = client.put(f"/api/v1/depots/{row['id']}", json={**row['data'], **changes}, headers={**WRITE, 'If-Match': '"1"'})
        assert renamed.status_code == 409 and renamed.json()['code'] == 'DEPOT_IN_USE'
    to_place = client.put(f"/api/v1/depots/{nord['id']}", json={**nord['data'], 'kind': 'PLACE'}, headers={**WRITE, 'If-Match': '"1"'})
    assert to_place.status_code == 409 and to_place.json()['identifiers'] == [order['data']['external_id']]
    in_use = client.delete(f"/api/v1/depots/{home['id']}", headers={**WRITE, 'If-Match': '"1"'})
    assert in_use.status_code == 409 and in_use.json()['identifiers'] == ['TRUCK-DEMO-01']


def rows(kind):
    return list(csv.DictReader(io.StringIO((ROOT / f'examples/{kind}.csv').read_text(encoding='utf-8'))))


def written(table):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(table[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(table)
    return out.getvalue().encode()


def upload(client, kind, content, dry=True):  # noqa: F811
    return client.post(f'/api/v1/imports/{kind}?dry_run={str(dry).lower()}&update_existing=true', content=content,
                       headers={**WRITE, 'Content-Type': 'text/csv'})


def test_csv_files_name_depots_and_places_and_keep_them_when_the_column_is_absent(client):  # noqa: F811
    add(client, depot('NORD')), add(client, depot('HOME', HOME, 'PLACE'))
    limits = {'max_stops': '', 'max_distance_m': '', 'max_driving_seconds': ''}
    vehicles = [{**rows('vehicles')[0], **limits, 'start': 'HOME', 'end': 'NORD'}, {**rows('vehicles')[1], **limits, 'start': '', 'end': ''}]
    done = upload(client, 'vehicles', written(vehicles), dry=False).json()
    assert done['committed'] and done['rows'][0]['data']['start'] == 'HOME' and done['rows'][1]['data']['start'] is None
    assert upload(client, 'vehicles', written(rows('vehicles'))).json()['unchanged'] == 2  # No start column: kept.
    bad = upload(client, 'vehicles', written([{**vehicles[0], 'start': 'NOWHERE'}])).json()['errors']
    assert bad == [{'row': 2, 'field': 'start', 'message': 'No depot or place has this code.', 'template': None, 'values': []}]
    orders = [{**row, 'priority': '', 'customer': '', 'ships_from': code} for row, code in zip(rows('orders'), ['NORD', ''])]
    done = upload(client, 'orders', written(orders), dry=False).json()
    assert [r['data']['ships_from'] for r in done['rows']] == ['NORD', None]
    assert upload(client, 'orders', written(rows('orders'))).json()['unchanged'] == 2
    home = upload(client, 'orders', written([{**orders[0], 'ships_from': 'HOME'}])).json()['errors'][0]
    assert (home['field'], home['message']) == ('ships_from', 'A delivery ships from a depot, not from another place.')


def test_a_calculation_copies_the_places_its_vehicles_use_and_notices_when_they_change(database, client):  # noqa: F811
    engine, _, settings = database
    owner = settings.demo_organization_id
    assert upload(client, 'vehicles', (ROOT / 'examples/vehicles.csv').read_bytes(), dry=False).json()['committed']
    assert upload(client, 'orders', (ROOT / 'examples/orders.csv').read_bytes(), dry=False).json()['committed']
    nord = add(client, depot('NORD'))
    assert vehicle(client, start='NORD').status_code == 200
    for order in client.get('/api/v1/orders', params={'service_date': '2026-09-23'}).json():
        reviewed = {**order['data'], 'location': {**order['data']['location'], 'reviewed': True}}
        client.put(f"/api/v1/orders/{order['id']}", json=reviewed, headers={**WRITE, 'If-Match': f'"{order["version"]}"'})
    request = OptimizationRequest(service_date='2026-09-23', vehicle_ids=[v['id'] for v in client.get('/api/v1/vehicles').json()],
                                  order_ids=[o['id'] for o in client.get('/api/v1/orders', params={'service_date': '2026-09-23'}).json()],
                                  routing_policy='TRUCK_STANDARD', solver_time_limit_seconds=1)
    with Session(engine) as session, session.begin(), pytest.raises(DataError) as error:
        submit(session, owner, request, 'unreviewed')
    assert (error.value.code, error.value.identifiers) == ('PLACE_NOT_REVIEWED', ['NORD'])
    nord = confirm(client, nord)
    with Session(engine) as session, session.begin():
        job = submit(session, owner, request, 'reviewed')
    with Session(engine) as session:
        inputs = PlanningInput.model_validate(session.get(Job, job.id).input_data)
        truck = next(v.data for v in inputs.vehicles if v.data.fleet_number == 'TRUCK-DEMO-01')
        assert [row.data.code for row in inputs.depots] == ['NORD'] and inputs.start_of(truck).data.code == 'NORD'
        assert source_change(session, owner, inputs) is None
    moved = {**nord['data'], 'name': 'Nord (Saint-Denis)'}
    assert client.put(f"/api/v1/depots/{nord['id']}", json=moved, headers={**WRITE, 'If-Match': '"2"'}).status_code == 200
    with Session(engine) as session:
        assert source_change(session, owner, inputs) == DEPOT_CHANGED_MESSAGE


def test_a_far_unreviewed_main_depot_does_not_block_or_change_plans_of_another_depot(database, client):  # noqa: F811
    engine, _, settings = database
    owner = settings.demo_organization_id
    assert upload(client, 'vehicles', (ROOT / 'examples/vehicles.csv').read_bytes(), dry=False).json()['committed']
    assert upload(client, 'orders', (ROOT / 'examples/orders.csv').read_bytes(), dry=False).json()['committed']
    confirm(client, add(client, depot('NORD')))
    nord_truck = vehicle(client, start='NORD').json()
    for order in client.get('/api/v1/orders', params={'service_date': '2026-09-23'}).json():
        reviewed = {**order['data'], 'location': {**order['data']['location'], 'reviewed': True}}
        client.put(f"/api/v1/orders/{order['id']}", json=reviewed, headers={**WRITE, 'If-Match': f'"{order["version"]}"'})
    main = client.get('/api/v1/depot').json()
    lyon = {**main['data'], 'location': {**LYON, 'reviewed': False}}  # Far away and, once moved, unreviewed.
    main = client.put('/api/v1/depot', json=lyon, headers={**WRITE, 'If-Match': f'"{main["version"]}"'}).json()
    request = OptimizationRequest(service_date='2026-09-23', vehicle_ids=[nord_truck['id']],
                                  order_ids=[o['id'] for o in client.get('/api/v1/orders', params={'service_date': '2026-09-23'}).json()],
                                  routing_policy='TRUCK_STANDARD', solver_time_limit_seconds=1)
    with Session(engine) as session, session.begin():
        job = submit(session, owner, request, 'nord-only', max_km=200)
    with Session(engine) as session:
        inputs = PlanningInput.model_validate(session.get(Job, job.id).input_data)
        assert not inputs.uses_main_depot and source_change(session, owner, inputs) is None
    renamed = {**main['data'], 'name': 'Renamed main depot'}
    assert client.put('/api/v1/depot', json=renamed, headers={**WRITE, 'If-Match': f'"{main["version"]}"'}).status_code == 200
    with Session(engine) as session:
        assert source_change(session, owner, inputs) is None  # The plan never used the main depot.
    # A vehicle that starts at the main depot brings back both its review and its distance.
    both = request.model_copy(update={'vehicle_ids': [UUID(v['id']) for v in client.get('/api/v1/vehicles').json()]})
    with Session(engine) as session, session.begin(), pytest.raises(DataError) as error:
        submit(session, owner, both, 'with-main', max_km=200)
    assert error.value.code == 'LOCATION_NOT_REVIEWED'
    confirm(client, client.get('/api/v1/depot').json())
    with Session(engine) as session, session.begin(), pytest.raises(DataError) as error:
        submit(session, owner, both, 'with-main-reviewed', max_km=200)
    assert error.value.code == 'AREA_TOO_WIDE' and 'depot' in error.value.identifiers


def test_upgrading_makes_each_existing_depot_main_and_downgrading_waits_for_other_places(database, client):  # noqa: F811
    from alembic import command
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    engine, config, settings = database
    command.downgrade(config, '0014_delete_orders')
    command.upgrade(config, 'head')
    assert client.get('/api/v1/depot').json()['main'] is True
    add(client, depot('HOME', HOME, 'PLACE'))
    with pytest.raises(DBAPIError, match='other depots or places are in use'):
        command.downgrade(config, '0014_delete_orders')
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM depots WHERE code = 'HOME'"))
    command.downgrade(config, '0014_delete_orders')
    with engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM depots')).scalar() == 1
    command.upgrade(config, 'head')
