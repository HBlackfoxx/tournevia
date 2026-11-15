"""The independent validator catches injected violations without running engines."""
from copy import deepcopy
from datetime import timedelta
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.optimization import run_vroom
from app.planning import DIMENSIONS, Leg, PlanningInput, Unassigned
from app.routing import Deadline, EngineError, Matrix, coordinates, meters, whole
from app.spike import build_routes, points_and_profiles
from app.validation import validate
from test_routing_core import small_input


def straight(requests, truck, policy, deadline):
    return [Leg(from_order_id=f, to_order_id=t, duration_seconds=120, distance_m=whole(meters(coordinates(a), coordinates(b))),
                geometry=[coordinates(a), coordinates(b)]) for a, b, f, t in requests]


@pytest.fixture
def planned():
    model = PlanningInput.model_validate(small_input())
    second = model.orders[0].model_copy(deep=True)
    second.id = UUID(int=31); second.data.external_id = 'SECOND'; second.data.location.longitude += .005
    model.orders.append(second)
    start = model.vehicles[0].data.available_window.start
    for order in model.orders:
        order.data.time_window.start, order.data.time_window.end = start, start + timedelta(hours=2)
    model.vehicles[0].data.cost.per_km_minor, model.vehicles[0].data.cost.per_hour_minor = 70, 3000
    points, profiles = points_and_profiles(model)
    matrices = [Matrix([[0 if i == j else 120 for j in points] for i in points], [[0 if i == j else 500 for j in points] for i in points])]
    engine = SimpleNamespace(legs=straight, dataset='unit', snap_limit_m=50)
    result = build_routes(model, engine, [(model.vehicles[0].id, model.seconds(start), [o.id for o in model.orders])], [],
                          points, profiles, matrices, Deadline.after(5), lambda _: None)
    assert result.validation == 'PASS', result.blocking_issues
    return model, result, points, profiles, matrices


@pytest.mark.parametrize('kind,issue', [
    ('coverage', 'COVERAGE'), ('duplicate_vehicle', 'VEHICLE: repeated'), ('unknown_vehicle', 'VEHICLE: unknown'),
    ('profile', 'PROFILE'), ('arrival', 'SCHEDULE: reported stop'), ('early_departure', 'SCHEDULE: departure before'),
    ('load', 'LOAD'), ('distance', 'TOTALS'), ('waiting', 'TOTALS'), ('cost', 'COST'), ('geometry', 'GEOMETRY'),
    ('snap', 'SNAP'), ('identity', 'IDENTITY'), ('leg_count', 'LEGS'), ('ownership', 'OWNERSHIP'),
    ('provenance', 'PROVENANCE'), ('context', 'CONTEXT'), ('return', 'RETURN'), ('window', 'WINDOW'),
])
def test_injected_result_violation_is_reported(planned, kind, issue):
    model, result, points, profiles, matrices = planned
    bad = result.model_copy(deep=True)
    route = bad.routes[0]
    if kind == 'coverage': bad.unassigned.append(Unassigned(order_id=route.stops[0].order_id, reason='SOLVER_UNASSIGNED'))
    if kind == 'duplicate_vehicle': bad.routes.append(route.model_copy(deep=True))
    if kind == 'unknown_vehicle': route.vehicle_id = uuid4()
    if kind == 'profile': route.profile.height_mm += 1
    if kind == 'arrival': route.stops[0].arrival += 1
    if kind == 'early_departure': route.departure -= 1
    if kind == 'load': route.stops[0].load_after[0] += 1
    if kind == 'distance': route.distance_m += 1
    if kind == 'waiting': route.waiting_seconds += 1
    if kind == 'cost': route.known_cost_minor += 1
    if kind == 'geometry': route.legs[0].geometry = [route.legs[0].geometry[0], (0.0, 0.0), route.legs[0].geometry[-1]]
    if kind == 'snap': route.legs[0].geometry = [(2.0, 48.0), (2.0, 48.0)]
    if kind == 'identity': route.legs[0].to_order_id = route.stops[1].order_id
    if kind == 'leg_count': route.legs.pop()
    if kind == 'ownership': bad.organization_id = uuid4()
    if kind == 'provenance': bad.data_kind = 'ROUTED'
    if kind == 'context': bad.objective = 'MODELED_TRAVEL_COST'
    if kind == 'return': route.legs[-1].duration_seconds += 90000
    if kind == 'window': route.legs[0].duration_seconds += 3*3600
    issues = validate(model, bad, points, profiles, matrices)
    assert any(found.startswith(issue) for found in issues), (kind, issues)


@pytest.mark.parametrize('constraint', [*DIMENSIONS, 'skills', 'max_stops', 'max_distance_m', 'max_driving_seconds'])
def test_source_constraints_are_checked_again_against_saved_routes(planned, constraint):
    model, result, points, profiles, matrices = planned
    inputs = model.model_copy(deep=True)
    vehicle = inputs.vehicles[0].data
    if constraint in DIMENSIONS: setattr(vehicle.capacity, constraint, 1)
    elif constraint == 'skills': inputs.orders[0].data.required_skills = ['TAIL_LIFT']
    else: setattr(vehicle.limits, constraint, 1)
    expected = {'skills': 'SKILLS'}.get(constraint, 'CAPACITY' if constraint in DIMENSIONS else 'LIMIT')
    assert any(found.startswith(expected) for found in validate(inputs, result, points, profiles, matrices))


def test_route_over_a_forbidden_connection_is_rejected(planned):
    model, result, points, profiles, matrices = planned
    forbidden = deepcopy(matrices)
    index = {coordinates(p): i for i, p in enumerate(points)}
    first = next(o for o in model.orders if o.id == result.routes[0].stops[0].order_id)
    forbidden[0].durations[0][index[coordinates(first.data.location)]] = None
    assert any(found.startswith('FORBIDDEN_ARC') for found in validate(model, result, points, profiles, forbidden))


@pytest.mark.skipif(os.name != 'posix', reason='Native solver supervision uses Linux process limits.')
def test_solver_search_limit_shrinks_to_the_remaining_deadline(tmp_path):
    script = tmp_path/'echo'
    script.write_text('#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps({"code": 0, "argv": sys.argv[1:]}))\n')
    script.chmod(0o755)
    limit = lambda data: int(data['argv'][data['argv'].index('-l')+1])
    assert limit(run_vroom({}, 60, Deadline.after(10.5), str(script))) == 7
    assert limit(run_vroom({}, 5, Deadline.after(60), str(script))) == 5


@pytest.mark.skipif(os.name != 'posix', reason='Native solver supervision uses Linux process limits.')
def test_native_process_timeout_and_output_budget(tmp_path):
    import time
    script = tmp_path/'slow'; script.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n'); script.chmod(0o755)
    start = time.monotonic()
    with pytest.raises(EngineError, match='Solver process deadline exceeded'):
        run_vroom({}, 1, Deadline.after(4.5), str(script))  # Killed at its 1 s limit plus exit margin.
    assert 3.5 < time.monotonic()-start < 6
    start = time.monotonic()
    with pytest.raises(EngineError, match='Too little of the job deadline remains'):
        run_vroom({}, 1, Deadline.after(.15), str(script))  # Refused before starting the solver.
    assert time.monotonic()-start < 1
    script.write_text('#!/usr/bin/env python3\nimport sys\nsys.stdout.write("x"*5000000)\n')
    with pytest.raises(EngineError, match='output'):
        run_vroom({}, 1, Deadline.after(5), str(script))


def test_issues_name_the_vehicle_and_delivery_to_inspect(planned):
    model, result, points, profiles, matrices = planned
    bad = result.model_copy(deep=True)
    bad.routes[0].stops[0].load_after[0] += 1
    bad.routes[0].distance_m += 1
    fleet, first = model.vehicles[0].data.fleet_number, next(o.data for o in model.orders if o.id == bad.routes[0].stops[0].order_id)
    issues = validate(model, bad, points, profiles, matrices)
    assert f'LOAD: incorrect remaining delivery load ({fleet}, {first.external_id})' in issues
    assert f'TOTALS: route totals disagree with final legs ({fleet})' in issues
