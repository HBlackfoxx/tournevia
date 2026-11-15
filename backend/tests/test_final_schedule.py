"""Final roads can require an earlier departure than matrix estimates suggest."""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.planning import Leg, PlanningInput
from app.routing import Deadline, Matrix, coordinates, meters, whole
from app.spike import build_routes, points_and_profiles
from test_routing_core import small_input


def rebuild(model, departure, durations, *, manual=False):
    points, profiles = points_and_profiles(model)
    times = iter(durations)
    def leg(a, b, truck, policy, deadline, from_id, to_id):
        return Leg(from_order_id=from_id, to_order_id=to_id,
                   duration_seconds=next(times), distance_m=whole(meters(coordinates(a), coordinates(b))),
                   geometry=[coordinates(a), coordinates(b)])
    engine = SimpleNamespace(legs=lambda requests, truck, policy, deadline: [leg(a, b, truck, policy, deadline, f, t)
                                                                           for a, b, f, t in requests],
                             dataset='unit-test geometry', snap_limit_m=50)
    matrix = Matrix([[0 if i == j else 10 for j in range(len(points))] for i in range(len(points))],
                    [[0 if i == j else 100 for j in range(len(points))] for i in range(len(points))])
    return build_routes(model, engine, [(model.vehicles[0].id, departure, [o.id for o in model.orders])],
                        [], points, profiles, None if manual else [matrix], Deadline.after(5), lambda _: None,
                        manual=manual)


@pytest.mark.parametrize('case,proposed,expected,issue', [
    ('window', 90, 81, None), ('return', 90, 81, None), ('unchanged', 40, 40, None),
    ('impossible_window', 90, 0, 'WINDOW:'), ('impossible_return', 90, 0, 'RETURN:'),
])
def test_real_leg_schedule_keeps_inclusive_bounds_and_rejects_infeasibility(case, proposed, expected, issue):
    model = PlanningInput.model_validate(small_input())
    vehicle, order = model.vehicles[0].data, model.orders[0].data
    start = vehicle.available_window.start
    origin = model.seconds(start)
    order.service_seconds = 30
    order.time_window.start = start
    order.time_window.end = start + timedelta(seconds=100 if case in {'window', 'unchanged'} else 1000)
    vehicle.available_window.end = start + timedelta(seconds=150 if case == 'return' else 1000)
    if case == 'impossible_window': order.time_window.end = start + timedelta(seconds=10)
    if case == 'impossible_return': vehicle.available_window.end = start + timedelta(seconds=50)
    result = rebuild(model, origin + proposed, [19, 20])
    route = result.routes[0]
    assert route.departure == origin + expected
    assert route.stops[0].service_start == origin + expected + 19
    assert route.end == origin + expected + 69
    assert route.driving_seconds == 39 and route.service_seconds == 30 and route.waiting_seconds == 0
    assert result.validation == ('FAIL' if issue else 'PASS'), result.blocking_issues
    if issue:
        assert len(result.blocking_issues) == 1 and result.blocking_issues[0].startswith(issue)
    else:
        assert not result.blocking_issues


def test_manual_earliest_departure_preserves_waiting_service_load_and_costs():
    model = PlanningInput.model_validate(small_input())
    extra = model.orders[0].model_copy(deep=True)
    extra.id = UUID(int=9999); extra.data.external_id = 'SECOND'
    extra.data.location.longitude += .001
    model.orders.append(extra)
    start = model.vehicles[0].data.available_window.start
    origin = model.seconds(start)
    for order, opening, closing in zip(model.orders, [100, 200], [150, 300]):
        order.data.time_window.start = start + timedelta(seconds=opening)
        order.data.time_window.end = start + timedelta(seconds=closing)
        order.data.service_seconds = 30
    result = rebuild(model, origin, [19, 21, 20], manual=True)
    route = result.routes[0]
    assert result.validation == 'PASS', result.blocking_issues
    assert route.departure == origin and route.end == origin + 250
    assert [s.service_start-origin for s in route.stops] == [100, 200]
    assert [s.load_after for s in route.stops] == [[1]*5, [0]*5]
    assert route.driving_seconds == 60 and route.service_seconds == 60 and route.waiting_seconds == 130


@pytest.mark.parametrize('first_closes,departure,waiting', [(1000, 430, 0), (100, 81, 349)])
def test_manual_departure_absorbs_later_waiting_without_closing_any_window(first_closes, departure, waiting):
    # Removing a stop must not lengthen a route: a manual sequence leaves late enough
    # to absorb waiting at any later stop, but never past a stop's closing time.
    model = PlanningInput.model_validate(small_input())
    extra = model.orders[0].model_copy(deep=True)
    extra.id = UUID(int=9999); extra.data.external_id = 'SECOND'
    extra.data.location.longitude += .001
    model.orders.append(extra)
    start = model.vehicles[0].data.available_window.start
    origin = model.seconds(start)
    model.vehicles[0].data.available_window.end = start + timedelta(seconds=5000)
    for order, opening, closing in zip(model.orders, [0, 500], [first_closes, 1000]):
        order.data.time_window.start = start + timedelta(seconds=opening)
        order.data.time_window.end = start + timedelta(seconds=closing)
        order.data.service_seconds = 30
    result = rebuild(model, None, [19, 21, 20], manual=True)
    route = result.routes[0]
    assert result.validation == 'PASS', result.blocking_issues
    assert route.departure == origin + departure and route.end == origin + 550
    assert route.stops[1].service_start == origin + 500 and route.waiting_seconds == waiting
    hourly = Decimal(model.vehicles[0].data.cost.per_hour_minor)
    assert route.time_minor == int((Decimal(550 - departure) * hourly / 3600).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@pytest.mark.parametrize('impossible_wait', [False, True])
def test_departure_accounts_for_later_stops_but_cannot_bypass_their_opening_times(impossible_wait):
    model = PlanningInput.model_validate(small_input())
    extra = model.orders[0].model_copy(deep=True)
    extra.id = UUID(int=9999); extra.data.external_id = 'SECOND'
    extra.data.location.longitude += .001
    model.orders.append(extra)
    start = model.vehicles[0].data.available_window.start
    origin = model.seconds(start)
    for order in model.orders:
        order.data.time_window.start = start
        order.data.time_window.end = start + timedelta(seconds=1000)
        order.data.service_seconds = 30
    model.orders[1].data.time_window.end = start + timedelta(seconds=170)
    if impossible_wait:
        model.orders[0].data.time_window.start = start + timedelta(seconds=200)
    result = rebuild(model, origin+110, [19, 21, 20])
    route = result.routes[0]
    assert route.departure == origin+100
    assert [s.order_id for s in route.stops] == [o.id for o in model.orders]
    if impossible_wait:
        assert result.validation == 'FAIL' and any(i.startswith('WINDOW:') for i in result.blocking_issues)
        assert route.stops[1].service_start == origin+251
    else:
        assert result.validation == 'PASS', result.blocking_issues
        assert [s.service_start-origin for s in route.stops] == [119, 170]
        assert route.end == origin+220


@pytest.mark.parametrize('margin,expected', [(0, 81), (30, 51), (90, 0)])
def test_safety_margin_moves_the_departure_earlier_and_never_fails_a_valid_schedule(margin, expected):
    # Window 0–100 s, 30 s of service, legs of 19 and 20 s; the solver proposes leaving at 90.
    # Without a margin, service starts at the window's end (departure 81). A 30 s margin starts it
    # by 70 (departure 51). A 90 s margin would need service by 10, which no departure allows: the
    # earliest departure is used, and the real window still validates the plan.
    model = PlanningInput.model_validate(small_input())
    model.safety_margin_seconds = margin
    vehicle, order = model.vehicles[0].data, model.orders[0].data
    start = vehicle.available_window.start
    origin = model.seconds(start)
    order.service_seconds = 30
    order.time_window.start = start
    order.time_window.end = start + timedelta(seconds=100)
    vehicle.available_window.end = start + timedelta(seconds=1000)
    result = rebuild(model, origin + 90, [19, 20])
    route = result.routes[0]
    assert route.departure == origin + expected
    assert route.stops[0].service_start == origin + expected + 19
    assert result.validation == 'PASS', result.blocking_issues


def test_manual_sequence_keeps_the_safety_margin_when_it_absorbs_waiting():
    # As in the manual-departure test: the later stop opens at 500, so the route leaves late to
    # avoid waiting, but no later than the first stop's window end less the margin allows.
    model = PlanningInput.model_validate(small_input())
    model.safety_margin_seconds = 60
    extra = model.orders[0].model_copy(deep=True)
    extra.id = UUID(int=9999); extra.data.external_id = 'SECOND'
    extra.data.location.longitude += .001
    model.orders.append(extra)
    start = model.vehicles[0].data.available_window.start
    origin = model.seconds(start)
    model.vehicles[0].data.available_window.end = start + timedelta(seconds=5000)
    for order, opening, closing in zip(model.orders, [0, 500], [100, 1000]):
        order.data.time_window.start = start + timedelta(seconds=opening)
        order.data.time_window.end = start + timedelta(seconds=closing)
        order.data.service_seconds = 30
    result = rebuild(model, None, [19, 21, 20], manual=True)
    route = result.routes[0]
    assert result.validation == 'PASS', result.blocking_issues
    # The first stop starts by 40 (100 less 60), so the route leaves at 21 instead of 81.
    assert route.departure == origin + 21 and route.stops[0].service_start == origin + 40
