"""Ready/export safeguards on routed plans: conflicting Ready plans, re-validation, source drift, CSV layout and route sheets."""
import csv
import io
from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edits import EXPORT_COLUMNS, PLANNING_NOTICE, FinalizeRequest, export_csv, finalize, route_sheets
from app.fleet import Depot, depot_record
from app.jobs import Job, Plan, now
from app.main import create_app
from app.planning import DIMENSIONS, Draft, Leg, PlanningInput, Route, Stop, Unassigned
from app.records import DataError, Order
from app.routing import coordinates, meters
from app.spike import points_and_profiles
from app.validation import validate
from test_jobs import WRITE, enqueue, seed

pytestmark = pytest.mark.postgres
LEG_SECONDS = 600


def snapshot(database) -> PlanningInput:
    engine, _, _ = database
    job = enqueue(database, seed(database))
    with Session(engine) as session:
        data = session.get(Job, job.id).input_data
    return PlanningInput.model_validate({**data, 'data_kind': 'ROUTED'})


def route(inputs: PlanningInput, vehicle_index: int, order_ids: list) -> Route:
    """A schedule the independent validator accepts: straight legs, exact times, loads and costs."""
    vehicle = inputs.vehicles[vehicle_index]
    orders = {o.id: o.data for o in inputs.orders}
    start, end = inputs.start_of(vehicle.data).data.location, inputs.end_of(vehicle.data).data.location
    places = [start, *[orders[key].location for key in order_ids], end]
    chain = [None, *order_ids, None]
    departure = inputs.seconds(vehicle.data.available_window.start)
    clock, legs, stops, waiting, service, distance = departure, [], [], 0, 0, 0
    load = [sum(orders[key].demand.vector()[d] for key in order_ids) for d in range(5)]
    for index, (a, b) in enumerate(zip(places, places[1:])):
        length = round(meters(coordinates(a), coordinates(b)))
        legs.append(Leg(from_order_id=chain[index], to_order_id=chain[index + 1], duration_seconds=LEG_SECONDS,
                        distance_m=length, geometry=[coordinates(a), coordinates(b)]))
        clock += LEG_SECONDS
        distance += length
        if index < len(order_ids):
            order = orders[order_ids[index]]
            start = max(clock, inputs.seconds(order.time_window.start))
            load = [amount - demand for amount, demand in zip(load, order.demand.vector())]
            stops.append(Stop(order_id=order_ids[index], arrival=clock, service_start=start,
                              departure=start + order.service_seconds, load_after=load))
            waiting += start - clock
            service += order.service_seconds
            clock = start + order.service_seconds
    cost = vehicle.data.cost
    distance_minor = (2 * distance * cost.per_km_minor + 1000) // 2000
    time_minor = (2 * (clock - departure) * cost.per_hour_minor + 3600) // 7200
    return Route(vehicle_id=vehicle.id, profile=vehicle.data.routing, departure=departure, end=clock, stops=stops, legs=legs,
                 distance_m=distance, driving_seconds=LEG_SECONDS * len(legs), waiting_seconds=waiting, service_seconds=service,
                 fixed_minor=cost.fixed_minor, distance_minor=distance_minor, time_minor=time_minor,
                 known_cost_minor=cost.fixed_minor + distance_minor + time_minor)


def compatible(inputs: PlanningInput, vehicle_index: int, limit: int) -> list:
    vehicle = inputs.vehicles[vehicle_index].data
    return [o.id for o in inputs.orders if set(o.data.required_skills) <= set(vehicle.skills)][:limit]


def routed_plan(database, inputs: PlanningInput, assignment: dict[int, list]):
    engine, _, _ = database
    routes = [route(inputs, index, keys) for index, keys in assignment.items()]
    served = {key for keys in assignment.values() for key in keys}
    result = Draft(status='DRAFT', data_kind='ROUTED', organization_id=inputs.organization_id, objective=inputs.objective,
                   routing_policy=inputs.routing_policy, dataset='Test-only routed draft', routes=routes,
                   unassigned=[Unassigned(order_id=o.id, reason='MANUALLY_UNASSIGNED') for o in inputs.orders if o.id not in served],
                   validation='PASS', blocking_issues=[])
    assert validate(inputs, result, *points_and_profiles(inputs), None) == []
    stamp = now()
    plan = Plan(id=uuid4(), organization_id=inputs.organization_id, version=1, status='DRAFT', service_date=inputs.service_date,
                created_at=stamp, updated_at=stamp, input_data=inputs.model_dump(mode='json'), result=result.model_dump(mode='json'),
                validation='PASS', result_stale=False, active_job_id=None, finalized_at=None, edit_error=None)
    with Session(engine) as session, session.begin():
        session.add(plan)
        return plan.id


def ready(database, plan_id, version, **options):
    engine, _, settings = database
    with Session(engine) as session, session.begin():
        return finalize(session, settings.demo_organization_id, plan_id, version, FinalizeRequest(accept_unassigned=True, **options))


def export(database, plan_id, version, **options):
    engine, _, settings = database
    with Session(engine) as session, session.begin():
        return export_csv(session, settings.demo_organization_id, plan_id, version, **options)


def sheets(database, plan_id, version, **options):
    engine, _, settings = database
    with Session(engine) as session, session.begin():
        return route_sheets(session, settings.demo_organization_id, plan_id, version, **options)


def starting_at_home(database, inputs: PlanningInput, vehicle_index: int) -> PlanningInput:
    """The inputs with a saved, reviewed driver's home where this vehicle starts its day (it still ends at the main depot)."""
    engine, _, settings = database
    with Session(engine) as session, session.begin():
        home = Depot(organization_id=settings.demo_organization_id, code='HOME-1', name='Domicile du chauffeur',
                     address='20 Rue de Paris 93100 Montreuil', latitude=48.8590, longitude=2.4380, reviewed=True,
                     timezone='Europe/Paris', kind='PLACE', main=False)
        session.add(home)
        session.flush()
        record = depot_record(home)
    data = inputs.model_dump(mode='json')
    data['depots'] = [record.model_dump(mode='json')]
    data['vehicles'][vehicle_index]['data'].update(start='HOME-1', end=inputs.depot.data.code)
    return PlanningInput.model_validate(data)


def test_a_second_ready_plan_for_the_same_work_requires_explicit_replacement(database):
    engine, _, _ = database
    inputs = snapshot(database)
    first = routed_plan(database, inputs, {0: compatible(inputs, 0, 2)})
    second = routed_plan(database, inputs, {0: compatible(inputs, 0, 2)})
    assert ready(database, first, 1).status == 'READY'
    with pytest.raises(DataError) as error:
        ready(database, second, 1)
    assert error.value.status == 409 and error.value.code == 'READY_CONFLICT' and str(first) in error.value.message
    replaced = ready(database, second, 1, replace_conflicting_ready=True)
    assert replaced.status == 'READY' and replaced.version == 2
    with Session(engine) as session:
        old = session.get(Plan, first)
        assert (old.status, old.finalized_at, old.version) == ('DRAFT', None, 3)
        assert old.result['status'] == 'DRAFT' and old.result['operational_ready'] is False
    with pytest.raises(DataError) as error:
        export(database, first, 3)
    assert error.value.code == 'PLAN_NOT_READY'
    assert export(database, second, 2)[1] == 2


def test_ready_plans_for_separate_vehicles_and_deliveries_coexist(database):
    inputs = snapshot(database)
    first_orders = compatible(inputs, 0, 2)
    second_orders = [key for key in compatible(inputs, 1, 10) if key not in first_orders][:2]
    assert len(second_orders) == 2
    first = routed_plan(database, inputs, {0: first_orders})
    second = routed_plan(database, inputs, {1: second_orders})
    assert ready(database, first, 1).status == ready(database, second, 1).status == 'READY'


def test_finalize_and_export_revalidate_a_stored_pass(database):
    engine, _, _ = database
    inputs = snapshot(database)
    plan = routed_plan(database, inputs, {0: compatible(inputs, 0, 2)})
    with Session(engine) as session, session.begin():
        row = session.get(Plan, plan)
        tampered = Draft.model_validate(row.result)
        tampered.routes[0].stops[0].service_start += 60  # Saved as PASS, but no longer matches its own legs.
        row.result = tampered.model_dump(mode='json')
    with pytest.raises(DataError) as error:
        ready(database, plan, 1)
    assert error.value.code == 'VALIDATION_FAILED' and 'Independent re-validation failed' in error.value.message


def test_plan_detail_reports_whether_its_sources_are_current(database):
    engine, _, settings = database
    inputs = snapshot(database)
    plan = routed_plan(database, inputs, {0: compatible(inputs, 0, 2)})
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        assert client.get(f'/api/v1/plans/{plan}').json()['sources_current'] is True
        with Session(engine) as session, session.begin():
            session.get(Order, inputs.orders[0].id).version += 1
        assert client.get(f'/api/v1/plans/{plan}').json()['sources_current'] is False


def test_export_carries_the_full_schedule_in_paris_time_and_a_spreadsheet_layout(database):
    engine, _, settings = database
    inputs = snapshot(database)
    orders = compatible(inputs, 0, 2)
    plan = routed_plan(database, inputs, {0: orders})
    version = ready(database, plan, 1).version
    text, _ = export(database, plan, version)
    assert not text.startswith('﻿')
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == EXPORT_COLUMNS and [row['order'] for row in rows] == [
        o.data.external_id for key in orders for o in inputs.orders if o.id == key]
    first = rows[0]
    assert first['start_place'] == first['end_place'] == inputs.depot.data.code
    times = [datetime.fromisoformat(first[k]) for k in ('vehicle_departure', 'arrival', 'service_start', 'stop_departure', 'vehicle_return')]
    assert times == sorted(times) and all(t.utcoffset() is not None and t.utcoffset().total_seconds() in (3600, 7200) for t in times)
    assert datetime.fromisoformat(first['exported_at']).utcoffset().total_seconds() in (3600, 7200)
    assert all(row['planning_notice'] == PLANNING_NOTICE['en'] for row in rows)
    # Each stop carries what a driver or a dispatch system needs about the delivery itself.
    delivery = next(o.data for o in inputs.orders if o.data.external_id == first['order'])
    assert (float(first['latitude']), float(first['longitude'])) == (delivery.location.latitude, delivery.location.longitude)
    assert [datetime.fromisoformat(first[k]) for k in ('time_window_start', 'time_window_end')] == [
        delivery.time_window.start, delivery.time_window.end]
    assert first['time_window_start'].endswith(('+01:00', '+02:00'))
    assert int(first['service_seconds']) == delivery.service_seconds
    assert {k: int(first[k]) for k in DIMENSIONS} == delivery.demand.model_dump()
    assert first['required_skills'] == '|'.join(delivery.required_skills)
    spreadsheet, _ = export(database, plan, version, spreadsheet=True, language='fr')
    assert spreadsheet.startswith('﻿')
    rows = list(csv.DictReader(io.StringIO(spreadsheet[1:]), delimiter=';'))
    assert list(rows[0]) == EXPORT_COLUMNS and rows[0]['planning_notice'] == PLANNING_NOTICE['fr']
    # A decimal comma, so spreadsheet programs using one read coordinates as numbers.
    assert float(rows[0]['latitude'].replace(',', '.')) == delivery.location.latitude and '.' not in rows[0]['latitude']
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        path = f'/api/v1/plans/{plan}/export.csv'
        response = client.get(path, params={'layout': 'spreadsheet'}, headers={'If-Match': f'"{version}"'})
        assert response.status_code == 200 and response.content.startswith('﻿'.encode())
        assert client.get(path, params={'layout': 'pdf'}, headers={'If-Match': f'"{version}"'}).status_code == 422
        conflict = client.post(f'/api/v1/plans/{plan}/finalize', json={'replace_conflicting_ready': 'yes'},
                               headers={**WRITE, 'If-Match': f'"{version}"'})
        assert conflict.status_code == 422
    with Session(engine) as session:
        assert session.scalar(select(Plan.status).where(Plan.id == plan)) == 'READY'


def test_route_sheets_give_each_vehicle_its_places_stops_and_times_once_the_plan_is_ready(database):
    engine, _, settings = database
    inputs = starting_at_home(database, snapshot(database), 0)
    first = compatible(inputs, 0, 3)
    second = [key for key in compatible(inputs, 1, 10) if key not in first][:2]
    plan = routed_plan(database, inputs, {0: first, 1: second})
    with pytest.raises(DataError) as error:
        sheets(database, plan, 1)  # A draft is not dispatched.
    assert error.value.code == 'PLAN_NOT_READY'
    version = ready(database, plan, 1).version
    with pytest.raises(DataError) as error:
        sheets(database, plan, version - 1)
    assert error.value.code == 'PLAN_CHANGED'
    result = sheets(database, plan, version)
    assert (result.plan_id, result.plan_version, result.service_date) == (plan, version, inputs.service_date)
    assert result.company and result.generated_at.utcoffset().total_seconds() in (3600, 7200)
    home, other = result.sheets
    vehicles = {v.id: v.data for v in inputs.vehicles}
    assert (home.vehicle_id, home.vehicle) == (inputs.vehicles[0].id, vehicles[inputs.vehicles[0].id].fleet_number)
    # The vehicle leaves from the driver's home and returns to the main depot; the other one leaves from and returns to it.
    assert (home.start.code, home.start.name, home.start.address) == ('HOME-1', 'Domicile du chauffeur', '20 Rue de Paris 93100 Montreuil')
    assert (home.end.code, other.start.code, other.end.code) == (inputs.depot.data.code,) * 3
    assert (home.end.latitude, home.end.longitude) == (inputs.depot.data.location.latitude, inputs.depot.data.location.longitude)
    orders = {o.id: o.data for o in inputs.orders}
    assert [s.order_id for s in home.stops] == first and [s.sequence for s in home.stops] == [1, 2, 3]
    stop = home.stops[0]
    delivery = orders[stop.order_id]
    assert (stop.delivery, stop.address, stop.customer) == (delivery.external_id, delivery.address, delivery.customer)
    assert (stop.latitude, stop.longitude) == (delivery.location.latitude, delivery.location.longitude)
    assert (stop.time_window.start, stop.time_window.end) == (delivery.time_window.start, delivery.time_window.end)
    assert (stop.service_seconds, stop.demand, stop.required_skills, stop.instructions) == (
        delivery.service_seconds, delivery.demand, delivery.required_skills, delivery.instructions)
    times = [home.departure, *[t for s in home.stops for t in (s.arrival, s.service_start, s.departure)], home.arrival]
    assert times == sorted(times) and all(t.utcoffset().total_seconds() in (3600, 7200) for t in times)
    assert stop.time_window.start.utcoffset() == stop.arrival.utcoffset()
    assert home.load.weight_kg == sum(orders[key].demand.weight_kg for key in first)
    with Session(engine) as session:
        saved = Draft.model_validate(session.get(Plan, plan).result).routes
    route = next(r for r in saved if r.vehicle_id == home.vehicle_id)
    assert (home.distance_m, home.driving_seconds, home.waiting_seconds, home.service_seconds) == (
        route.distance_m, route.driving_seconds, route.waiting_seconds, route.service_seconds)
    # One vehicle's sheet; a vehicle without a route in this plan has none.
    assert [s.vehicle_id for s in sheets(database, plan, version, vehicle=other.vehicle_id).sheets] == [other.vehicle_id]
    with pytest.raises(DataError) as error:
        sheets(database, plan, version, vehicle=uuid4())
    assert (error.value.status, error.value.code) == (404, 'NOT_FOUND')
    # The CSV export names where each route starts and ends.
    rows = list(csv.DictReader(io.StringIO(export(database, plan, version)[0])))
    assert {(row['vehicle'], row['start_place'], row['end_place']) for row in rows} == {
        (home.vehicle, 'HOME-1', inputs.depot.data.code), (other.vehicle, inputs.depot.data.code, inputs.depot.data.code)}
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        path = f'/api/v1/plans/{plan}/sheets'
        response = client.get(path, params={'vehicle': str(home.vehicle_id)}, headers={'If-Match': f'"{version}"'})
        assert response.status_code == 200 and response.headers['ETag'] == f'"{version}"'
        assert response.headers['Cache-Control'] == 'no-store'
        body = response.json()
        assert [s['vehicle'] for s in body['sheets']] == [home.vehicle] and body['sheets'][0]['start']['code'] == 'HOME-1'
        assert body['sheets'][0]['stops'][0]['arrival'].endswith(('+01:00', '+02:00'))
        assert client.get(path).status_code == 428
        assert client.get(path, headers={'If-Match': f'"{version + 1}"'}).json()['code'] == 'PLAN_CHANGED'
        assert client.get(f'/api/v1/plans/{uuid4()}/sheets', headers={'If-Match': '"1"'}).status_code == 404
    # A source changed after Ready: the sheets wait for a fresh calculation, like the CSV file.
    with Session(engine) as session, session.begin():
        session.get(Order, first[0]).version += 1
    with pytest.raises(DataError) as error:
        sheets(database, plan, version)
    assert error.value.code == 'SOURCE_CHANGED'

