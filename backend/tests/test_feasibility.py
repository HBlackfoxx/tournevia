from types import SimpleNamespace

import pytest

from app.routing import Deadline, EngineError, Matrix
from benchmarks.feasibility import diagnose
from benchmarks.cases import tiny_case


@pytest.mark.parametrize('blocker', ['none', 'equipment', 'capacity', 'outward', 'return',
                                    'window', 'return_window', 'distance', 'driving'])
def test_diagnostic_keeps_eligibility_access_and_single_job_limits_distinct(blocker):
    from datetime import timedelta
    model, _ = tiny_case('directed', 'TRAVEL_TIME')
    model.vehicles = model.vehicles[:1]; model.orders = model.orders[:1]
    v, o = model.vehicles[0].data, model.orders[0].data
    o.time_window.start = v.available_window.start
    o.time_window.end = v.available_window.end
    if blocker == 'equipment': o.required_skills = ['REFRIGERATED']
    if blocker == 'capacity': o.demand.weight_kg = v.capacity.weight_kg + 1
    if blocker == 'window': o.time_window.end = v.available_window.start + timedelta(seconds=1)
    if blocker == 'return_window': v.available_window.end = v.available_window.start + timedelta(seconds=1)
    if blocker == 'distance': v.limits.max_distance_m = 1
    if blocker == 'driving': v.limits.max_driving_seconds = 1
    matrix = Matrix([[0, None if blocker == 'outward' else 10],
                     [None if blocker == 'return' else 20, 0]], [[0, 100], [200, 0]])
    engine = SimpleNamespace(matrix=lambda *a: matrix,
                             leg=lambda *a: SimpleNamespace(duration_seconds=10, distance_m=100))
    draft = SimpleNamespace(unassigned=[SimpleNamespace(order_id=model.orders[0].id, reason='SOLVER_UNASSIGNED')])
    report = diagnose(model, draft, engine, Deadline.after(5))[0]
    expected = {'none': [], 'equipment': ['EQUIPMENT:REFRIGERATED'], 'capacity': ['CAPACITY:weight_kg'],
                'outward': ['MATRIX_ROUTE_DISAGREEMENT'], 'return': ['MATRIX_ROUTE_DISAGREEMENT'],
                'window': ['WINDOW'], 'return_window': ['RETURN_WINDOW'],
                'distance': ['DISTANCE_LIMIT'], 'driving': ['DRIVING_LIMIT']}
    assert report['profiles'][0]['vehicles'][0]['blockers'] == expected[blocker]
    assert report['single_delivery_candidates'] == ([v.fleet_number] if blocker == 'none' else [])
    assert report['solver_reason'] == 'SOLVER_UNASSIGNED'


def test_diagnostic_propagates_engine_failure_instead_of_inventing_no_road():
    model, _ = tiny_case('directed', 'TRAVEL_TIME')
    def fail(*args): raise EngineError('offline')
    draft = SimpleNamespace(unassigned=[SimpleNamespace(order_id=model.orders[0].id, reason='SOLVER_UNASSIGNED')])
    with pytest.raises(EngineError, match='offline'):
        diagnose(model, draft, SimpleNamespace(matrix=fail), Deadline.after(5))


@pytest.mark.parametrize('code', [442, 171])
def test_only_explicit_no_path_response_is_classified_as_missing_route(code):
    import json
    from benchmarks.feasibility import route_probe
    from app.routing import engine_rejection
    def fail(*args):
        raise engine_rejection('route', 400, json.dumps({'error_code': code}).encode())
    if code == 442:
        assert route_probe(SimpleNamespace(leg=fail), None, None, None, None, Deadline.after(1)) == {'found': False, 'error_code': 442}
    else:
        with pytest.raises(EngineError):
            route_probe(SimpleNamespace(leg=fail), None, None, None, None, Deadline.after(1))
