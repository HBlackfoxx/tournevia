"""Roadless pins, named snap failures, bounded leg routing and edit departures (no engines)."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.edits import EditRequest, reroute
from app.planning import Leg, PlanningInput
from app.routing import Deadline, EngineError, Matrix, SnapError, Valhalla, coordinates, meters, whole
from app.spike import build_routes, points_and_profiles, profile_matrices
from app.worker import GENERIC_ENGINE_FAILURE, child_environment, engine_message
from test_routing_core import small_input


def two_profiles():
    model = PlanningInput.model_validate(small_input())
    tall = model.vehicles[0].model_copy(deep=True)
    tall.id = UUID(int=77); tall.data.fleet_number = 'TALL-01'; tall.data.routing.height_mm += 500
    model.vehicles.append(tall)
    second = model.orders[0].model_copy(deep=True)
    second.id = UUID(int=78); second.data.external_id = 'FOREST-GATE'; second.data.location.longitude += .01
    model.orders.append(second)
    return model


def fake_engine(roadless=(), *, legs=None):
    """roadless: set of (profile height, coordinates) without a usable road."""
    def unsnappable(points, truck, policy, deadline):
        return [i for i, p in enumerate(points) if (truck.height_mm, coordinates(p)) in roadless]
    def matrix(selected, truck, policy, deadline, *, time_bounds=None):
        size = len(selected)
        return Matrix([[0 if i == j else 60 for j in range(size)] for i in range(size)],
                      [[0 if i == j else 600 for j in range(size)] for i in range(size)])
    def straight(requests, truck, policy, deadline):
        return [Leg(from_order_id=f, to_order_id=t, duration_seconds=60, distance_m=whole(meters(coordinates(a), coordinates(b))),
                    geometry=[coordinates(a), coordinates(b)]) for a, b, f, t in requests]
    return SimpleNamespace(unsnappable=unsnappable, matrix=matrix, legs=legs or straight, dataset='unit', snap_limit_m=50)


def test_locate_response_marks_points_without_edges(monkeypatch):
    engine = Valhalla('http://unused', 'unit')
    model = two_profiles()
    points = [model.depot.data.location, *[o.data.location for o in model.orders]]
    sent = []
    def post(action, payload, deadline):
        sent.append((action, payload))
        return [{'edges': [{'way_id': 1}]}, {'edges': []}, {'edges': None}]
    monkeypatch.setattr(engine, 'post', post)
    assert engine.unsnappable(points, model.vehicles[0].data.routing, 'TRUCK_STANDARD', Deadline.after(5)) == [1, 2]
    action, payload = sent[0]
    assert action == 'locate' and payload['costing'] == 'truck'
    assert payload['locations'] == [engine.point(p) for p in points]
    monkeypatch.setattr(engine, 'post', lambda *a: [{'edges': []}])
    with pytest.raises(EngineError, match='locate response'):
        engine.unsnappable(points, model.vehicles[0].data.routing, 'TRUCK_STANDARD', Deadline.after(5))


def test_roadless_delivery_is_excluded_for_one_profile_only_and_reported():
    model = two_profiles()
    tall = model.vehicles[1].data.routing
    forest = model.orders[1].data.location
    engine = fake_engine({(tall.height_mm, coordinates(forest))})
    points, profiles = points_and_profiles(model)
    matrices = profile_matrices(model, engine, points, profiles, Deadline.after(5))
    index = next(i for i, p in enumerate(points) if coordinates(p) == coordinates(forest))
    low, high = matrices[profiles.index(model.vehicles[0].data.routing)], matrices[profiles.index(tall)]
    assert index not in low.no_road and low.durations[0][index] == 60
    assert high.no_road == {index}
    assert all(high.durations[index][j] is None and high.durations[j][index] is None for j in range(len(points)) if j != index)
    # The low vehicle can still serve it; when nobody can, the reason is explicit.
    result = build_routes(model, engine, [], [o.id for o in model.orders], points, profiles, matrices,
                          Deadline.after(5), lambda _: None)
    assert {u.order_id: u.reason for u in result.unassigned}[model.orders[1].id] == 'SOLVER_UNASSIGNED'
    engine = fake_engine({(v.data.routing.height_mm, coordinates(forest)) for v in model.vehicles})
    matrices = profile_matrices(model, engine, points, profiles, Deadline.after(5))
    result = build_routes(model, engine, [], [o.id for o in model.orders], points, profiles, matrices,
                          Deadline.after(5), lambda _: None)
    assert {u.order_id: u.reason for u in result.unassigned} == {
        model.orders[0].id: 'SOLVER_UNASSIGNED', model.orders[1].id: 'NO_ROAD_NEAR_PIN'}


def test_roadless_depot_stops_the_calculation_naming_vehicles():
    model = two_profiles()
    tall = model.vehicles[1].data.routing
    engine = fake_engine({(tall.height_mm, coordinates(model.depot.data.location))})
    points, profiles = points_and_profiles(model)
    with pytest.raises(EngineError, match='vehicle TALL-01 .*depot pin'):
        profile_matrices(model, engine, points, profiles, Deadline.after(5))


@pytest.mark.parametrize('endpoint,request_index,name', [('end', 0, 'delivery DEMO-ORDER-001'), ('start', 1, 'delivery DEMO-ORDER-001'),
                                                           ('end', 1, 'the depot')])
def test_final_leg_snap_failure_names_the_pin(endpoint, request_index, name):
    model = PlanningInput.model_validate(small_input())
    def legs(requests, truck, policy, deadline):
        error = SnapError(endpoint, 50); error.request_index = request_index
        raise error
    points, profiles = points_and_profiles(model)
    with pytest.raises(EngineError, match=f'road path for {name} ends more than 50 m'):
        build_routes(model, fake_engine(legs=legs), [(model.vehicles[0].id, 0, [model.orders[0].id])], [],
                     points, profiles, None, Deadline.after(5), lambda _: None, manual=True)


@pytest.mark.parametrize('concurrency', [1, 3])
def test_bounded_legs_keep_order_and_reject_on_any_failure(monkeypatch, concurrency):
    engine = Valhalla('http://unused', 'unit', matrix_concurrency=concurrency)
    model = PlanningInput.model_validate(small_input())
    point = model.depot.data.location
    def leg(a, b, truck, policy, deadline, from_id=None, to_id=None):
        if to_id == 'bad':
            raise EngineError('HTTPError')
        return Leg(from_order_id=None, to_order_id=None, duration_seconds=to_id, distance_m=0, geometry=[(0, 0), (0, 0)])
    monkeypatch.setattr(engine, 'leg', leg)
    truck = model.vehicles[0].data.routing
    assert [l.duration_seconds for l in engine.legs([(point, point, None, n) for n in range(7)], truck, 'TRUCK_STANDARD',
                                                     Deadline.after(5))] == list(range(7))
    with pytest.raises(EngineError) as failure:
        engine.legs([(point, point, None, n) for n in (1, 2, 'bad', 4)], truck, 'TRUCK_STANDARD', Deadline.after(5))
    assert failure.value.request_index == 2


def test_manual_edit_arrives_as_the_first_stop_opens():
    model = PlanningInput.model_validate(small_input())
    vehicle, order = model.vehicles[0].data, model.orders[0].data
    start = vehicle.available_window.start
    order.time_window.start, order.time_window.end = start + timedelta(hours=3), start + timedelta(hours=4)
    edit = EditRequest(routes=[{'vehicle_id': model.vehicles[0].id, 'order_ids': [model.orders[0].id]}], unassigned_order_ids=[])
    result = reroute(model, edit, fake_engine(), deadline=Deadline.after(5))
    route = result.routes[0]
    assert result.validation == 'PASS', result.blocking_issues
    # Window opening minus the 60 s final leg: no waiting before the stop and the window's slack is kept.
    assert route.departure == model.seconds(order.time_window.start) - 60
    assert route.waiting_seconds == 0 and route.stops[0].service_start == model.seconds(order.time_window.start)
    # When the first window opened before the vehicle is available, it leaves at availability.
    order.time_window.start = start - timedelta(hours=1)
    route = reroute(model, edit, fake_engine(), deadline=Deadline.after(5)).routes[0]
    assert route.departure == model.seconds(start)


def test_manual_edit_with_roadless_pin_names_vehicle_and_delivery():
    model = PlanningInput.model_validate(small_input())
    truck = model.vehicles[0].data.routing
    engine = fake_engine({(truck.height_mm, coordinates(model.orders[0].data.location))})
    edit = EditRequest(routes=[{'vehicle_id': model.vehicles[0].id, 'order_ids': [model.orders[0].id]}], unassigned_order_ids=[])
    with pytest.raises(EngineError, match='vehicle TRUCK-DEMO-01 .*delivery DEMO-ORDER-001'):
        reroute(model, edit, engine, deadline=Deadline.after(5))


def test_worker_shows_only_application_engine_messages():
    assert engine_message({'error': 'EngineError', 'message': 'No road usable by vehicle A lies within 50 m of delivery X.'}) \
        == 'No road usable by vehicle A lies within 50 m of delivery X. No draft was saved.'
    assert engine_message({'error': 'OperationalError', 'message': 'password=secret'}) == GENERIC_ENGINE_FAILURE
    assert engine_message({'error': 'EngineError', 'message': '  '}) == GENERIC_ENGINE_FAILURE
    assert len(engine_message({'error': 'EngineError', 'message': 'x' * 5000})) < 700


def test_calculation_child_receives_no_database_or_setup_secrets(monkeypatch):
    from app.config import Settings
    for key in ('DB_PASSWORD', 'POSTGRES_PASSWORD', 'SETUP_TOKEN', 'RUNTIME_DB_PASSWORD', 'MIGRATION_DB_PASSWORD'):
        monkeypatch.setenv(key, 'not-for-the-child-' + 'x' * 32)
    monkeypatch.setenv('PATH', '/usr/bin')
    environment = child_environment(Settings(_env_file=None, valhalla_url='http://router:8002', matrix_concurrency=2))
    assert not any(value.startswith('not-for-the-child') for value in environment.values())
    assert environment['VALHALLA_URL'] == 'http://router:8002' and environment['MATRIX_CONCURRENCY'] == '2'
    assert environment['PATH'] == '/usr/bin'


class FakeConnection:
    """Scripted HTTP connection: each step is a status/body pair or an exception."""
    def __init__(self, steps, opened):
        self.steps, self.sock, self.timeout, self.closed = steps, None, None, False
        opened.append(self)
    def request(self, method, path, body, headers):
        self.sent = (method, path)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            self.sock = None
            raise step
        self.response = step
    def getresponse(self):
        import io
        status, body = self.response
        self.sock = SimpleNamespace(settimeout=lambda seconds: None)  # The server kept the connection open.
        response = io.BufferedReader(io.BytesIO(body)); response.status = status; response.will_close = False
        return response
    def close(self):
        self.sock, self.closed = None, True


def scripted(monkeypatch, steps):
    import http.client
    opened = []
    monkeypatch.setattr(http.client, 'HTTPConnection', lambda host, port, timeout: FakeConnection(steps, opened))
    return Valhalla('http://router:8002/base', 'unit'), opened


def test_requests_reuse_one_kept_alive_connection_per_thread(monkeypatch):
    engine, opened = scripted(monkeypatch, [(200, b'{"a": 1}'), (200, b'{"a": 2}'), (200, b'{"a": 3}')])
    assert [engine.post('status', {}, Deadline.after(5))['a'] for _ in range(3)] == [1, 2, 3]
    assert len(opened) == 1 and opened[0].sent == ('POST', '/base/status')


def test_only_an_idle_closed_reused_connection_is_retried_once(monkeypatch):
    import http.client
    engine, opened = scripted(monkeypatch, [(200, b'{"a": 1}'), http.client.RemoteDisconnected('idle close'), (200, b'{"a": 2}')])
    assert engine.post('status', {}, Deadline.after(5)) == {'a': 1}
    assert engine.post('status', {}, Deadline.after(5)) == {'a': 2}  # Resent once after reconnecting.
    assert len(opened) == 1 and opened[0].closed
    # A fresh connection that fails is not retried, and the message names classes only.
    engine, opened = scripted(monkeypatch, [ConnectionRefusedError(111, 'secret-request-text')])
    with pytest.raises(EngineError) as failure:
        engine.post('route', {}, Deadline.after(5))
    assert str(failure.value) == 'Valhalla route failed (ConnectionRefusedError errno 111); no route result accepted.'
    assert len(opened) == 1


def test_a_stalled_request_is_retried_once_when_the_deadline_allows(monkeypatch):
    steps = [TimeoutError('stalled'), (200, b'{"a": 1}')]
    engine, opened = scripted(monkeypatch, steps)
    assert engine.post('sources_to_targets', {}, Deadline.after(60)) == {'a': 1}
    assert opened[0].closed and not steps  # The stalled socket was closed and the request resent.
    steps = [TimeoutError('stalled'), TimeoutError('stalled again')]
    engine, opened = scripted(monkeypatch, steps)
    with pytest.raises(EngineError, match='TimeoutError'):
        engine.post('sources_to_targets', {}, Deadline.after(60))
    assert not steps  # One retry only.
    # Too little deadline left for a full second attempt: fail at once.
    steps = [TimeoutError('stalled'), (200, b'{"a": 1}')]
    engine, opened = scripted(monkeypatch, steps)
    with pytest.raises(EngineError, match='TimeoutError'):
        engine.post('sources_to_targets', {}, Deadline.after(12))
    assert len(steps) == 1


def test_engine_rejections_keep_status_and_code_but_not_text(monkeypatch):
    engine, _ = scripted(monkeypatch, [(400, b'{"error_code": 171, "error": "near secret-location"}')])
    with pytest.raises(EngineError) as failure:
        engine.post('route', {}, Deadline.after(5))
    assert str(failure.value) == 'Valhalla route failed (HTTP 400, engine error 171); no route result accepted.'
    assert failure.value.engine_status == 400 and failure.value.engine_detail['error_code'] == 171
