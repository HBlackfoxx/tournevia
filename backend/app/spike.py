"""Typed routing calculation shared by the worker and the synthetic CLI."""
import argparse
import json
from pathlib import Path

from app.optimization import decode_solution, prepare, run_vroom
from app.planning import Draft, PlanningInput, Route, Stop, Truck, Unassigned, SyntheticInput, SyntheticDraft
from app.routing import MAX_MATRIX_POINTS, Deadline, EngineError, Matrix, SnapError, Valhalla, coordinates
from app.validation import validate


def place_name(inputs: PlanningInput, place) -> str:
    """How messages name a depot or place: "the depot" while the main depot is the only one."""
    if place.id == inputs.depot.id and not inputs.depots:
        return 'the depot'
    return f"{'depot' if place.data.kind == 'DEPOT' else 'place'} {place.data.code}"


def point_names(inputs: PlanningInput, point) -> list[str]:
    """Human-readable owners of one coordinate: depots or places and/or delivery IDs."""
    key = coordinates(point)
    names = [place_name(inputs, row) for row in (inputs.depot, *inputs.depots) if coordinates(row.data.location) == key]
    return names + [f'delivery {row.data.external_id}' for row in inputs.orders
                    if row.data.location is not None and coordinates(row.data.location) == key]


def listed(names: list[str]) -> str:
    return ', '.join(names[:10]) + (f' and {len(names)-10} more' if len(names) > 10 else '')


def ends_of(inputs: PlanningInput, vehicles) -> dict[tuple[float, float], list]:
    """The start and end places of these vehicles by coordinates, each with the vehicles using it."""
    found = {}
    for vehicle in vehicles:
        for place in (inputs.start_of(vehicle), inputs.end_of(vehicle)):
            users = found.setdefault(coordinates(place.data.location), [])
            if (place, vehicle) not in users:
                users.append((place, vehicle))
    return found


def roadless_points(inputs: PlanningInput, engine: Valhalla, points, profile: Truck, deadline: Deadline) -> set[int]:
    """Indexes of pins with no road usable by this truck within the snap limit.

    The engine answers this through its own location search with the truck's
    costing; one such pin otherwise makes it reject a whole matrix or route. A
    depot or place where a vehicle of this profile starts or ends without a usable
    road stops the calculation with the affected vehicles named. Deliveries become
    unavailable to this profile only, so other compatible profiles can still serve
    them and the rest remain explicitly unassigned.
    """
    missing = set(engine.unsnappable(points, profile, inputs.routing_policy, deadline))
    ends = ends_of(inputs, [v.data for v in inputs.vehicles if v.data.routing == profile])
    blocked = [users for index in sorted(missing) for key, users in ends.items() if coordinates(points[index]) == key]
    if blocked:
        names = list(dict.fromkeys(place_name(inputs, place) for users in blocked for place, _ in users))
        fleet = list(dict.fromkeys(vehicle.fleet_number for users in blocked for _, vehicle in users))
        if names == ['the depot']:
            raise EngineError(f'No road usable by vehicle {listed(fleet)} lies within {engine.snap_limit_m} m of the depot pin. '
                              'Correct the depot location or deselect these vehicles, then recalculate.')
        raise EngineError(f'No road usable by vehicle {listed(fleet)} lies within {engine.snap_limit_m} m of the pin of '
                          f'{listed(names)}. Correct that location or deselect these vehicles, then recalculate.')
    return missing


def require_routable_assignments(inputs: PlanningInput, engine: Valhalla, assignments, deadline: Deadline) -> None:
    """Name explicitly assigned pins that the assigned truck cannot reach by road.

    A manual sequence fixes each delivery to one vehicle, so there is no other
    profile to fall back on. The recalculation is rejected with a precise reason
    instead of the engine's generic failure for the whole request.
    """
    vehicles = {row.id: row.data for row in inputs.vehicles}
    orders = {row.id: row.data for row in inputs.orders}
    profiles = []
    for vehicle_id, _ in assignments:
        if vehicles[vehicle_id].routing not in profiles:
            profiles.append(vehicles[vehicle_id].routing)
    for profile in profiles:
        routed = [(vehicle_id, ids) for vehicle_id, ids in assignments if vehicles[vehicle_id].routing == profile]
        used = []
        for vehicle_id, ids in routed:
            vehicle = vehicles[vehicle_id]
            for point in [inputs.start_of(vehicle).data.location, inputs.end_of(vehicle).data.location,
                          *(orders[key].location for key in ids)]:
                if coordinates(point) not in [coordinates(p) for p in used]:
                    used.append(point)
        missing = roadless_points(inputs, engine, used, profile, deadline)
        if missing:
            keys = {coordinates(used[index]) for index in missing}
            fleet = [vehicles[v].fleet_number for v, ids in routed if any(coordinates(orders[k].location) in keys for k in ids)]
            names = [f'delivery {orders[k].external_id}' for _, ids in routed for k in ids if coordinates(orders[k].location) in keys]
            raise EngineError(f'No road usable by vehicle {listed(fleet)} lies within {engine.snap_limit_m} m of {listed(names)}. '
                              'Assign these deliveries to a vehicle that can use the nearby road or correct the pins.')


def points_and_profiles(inputs: PlanningInput):
    """Route start and end places first (the main depot at index zero), then delivery points."""
    points, profiles = list(inputs.route_ends()), []
    ends = len(points)
    for row in inputs.orders:
        if coordinates(row.data.location) not in [coordinates(p) for p in points]:
            points.append(row.data.location)
    if len(points) - ends > 250 or len(points) > MAX_MATRIX_POINTS:
        raise EngineError('Calculations support at most 301 distinct points: 250 for deliveries and the rest for depots and places.')
    # UUID/source order scatters every tile across the entire area. Group nearby
    # service points for bounded matrix requests; route ends stay first.
    # Solver jobs and manual visit sequences still use their original order/IDs.
    points[ends:] = sorted(points[ends:], key=coordinates)
    for row in inputs.vehicles:
        if row.data.routing not in profiles:
            profiles.append(row.data.routing)
    if len(profiles) > 3:
        raise EngineError('Calculations support at most three exact truck profiles.')
    return points, profiles


def compatible(inputs: PlanningInput, vehicle, order) -> bool:
    """Equipment, every load dimension and the depot the delivery ships from allow this pairing."""
    return (set(order.required_skills) <= set(vehicle.skills) and inputs.carries(vehicle, order)
            and all(d <= c for d, c in zip(order.demand.vector(), vehicle.capacity.vector())))


def profile_matrices(inputs: PlanningInput, engine: Valhalla, points, profiles, deadline: Deadline) -> list[Matrix]:
    """Request only vertices that some selected vehicle of this profile can serve or start/end at.

    Excluded rows/columns stay forbidden in the solver's global index space. They
    are eligibility exclusions, not a claim that no road exists. Pins for which
    the engine finds no road usable by the profile within the snap limit are also
    excluded and recorded in ``Matrix.no_road``. Hard-window bounds can omit
    impossible order-to-order arcs; route start and end places are never bounded.
    All potentially usable directed cells come from the engine; its null mask stays unchanged.
    """
    route_ends = len(inputs.route_ends())
    matrices = []
    for profile in profiles:
        vehicles = [v.data for v in inputs.vehicles if v.data.routing == profile]
        eligible_orders = [o.data for o in inputs.orders if any(compatible(inputs, v, o.data) for v in vehicles)]
        eligible = {coordinates(o.location) for o in eligible_orders}
        ends = ends_of(inputs, vehicles)
        selected = [i for i, point in enumerate(points)
                    if (i < route_ends and coordinates(point) in ends) or coordinates(point) in eligible]
        bounds = []
        for i in selected:
            if i < route_ends:
                bounds.append(None)
                continue
            orders = [o for o in eligible_orders if coordinates(o.location) == coordinates(points[i])]
            # Shared coordinates retain the union of every eligible order's
            # window, including its service time. This is a lower/upper bound,
            # not a chosen schedule; nonnegative travel makes strict gaps safe.
            bounds.append((min(inputs.seconds(o.time_window.start)+o.service_seconds for o in orders),
                           max(inputs.seconds(o.time_window.end) for o in orders)))
        roadless = roadless_points(inputs, engine, [points[i] for i in selected], profile, deadline)
        kept = [position for position in range(len(selected)) if position not in roadless]
        no_road = frozenset(selected[position] for position in roadless)
        selected, bounds = [selected[position] for position in kept], [bounds[position] for position in kept]
        matrix = engine.matrix([points[i] for i in selected], profile, inputs.routing_policy, deadline,
                               time_bounds=bounds)
        if len(selected) == len(points):
            matrices.append(matrix)
            continue
        expanded = Matrix([[None] * len(points) for _ in points], [[None] * len(points) for _ in points], no_road)
        for row, global_row in enumerate(selected):
            for column, global_column in enumerate(selected):
                expanded.durations[global_row][global_column] = matrix.durations[row][column]
                expanded.distances[global_row][global_column] = matrix.distances[row][column]
        matrices.append(expanded)
    return matrices


def calculate(inputs: PlanningInput, engine: Valhalla, *, executable='/usr/local/bin/vroom', deadline=None,
              on_stage=lambda stage: None) -> Draft:
    deadline = deadline or Deadline.after(120)
    on_stage('PRECHECK')
    points, profiles = points_and_profiles(inputs)
    on_stage('MATRIX')
    matrices = profile_matrices(inputs, engine, points, profiles, deadline)
    mapping = prepare(inputs, points, profiles, matrices)
    on_stage('SOLVE')
    raw = run_vroom(mapping.payload, inputs.solver_time_limit_seconds, deadline, executable)
    decoded, unassigned = decode_solution(raw, mapping)
    return build_routes(inputs,engine,decoded,unassigned,points,profiles,matrices,deadline,on_stage)


def build_routes(inputs,engine,decoded,unassigned,points,profiles,matrices,deadline,on_stage,*,manual=False) -> Draft:
    if matrices is None and not manual:
        raise EngineError('Optimized routes require their original reachability matrices.')
    on_stage('ROUTE')
    orders = {row.id: row.data for row in inputs.orders}
    vehicles = {row.id: row.data for row in inputs.vehicles}
    routes = []
    for vehicle_id, departure, ids in decoded:
        vehicle = vehicles[vehicle_id]
        first, last = inputs.start_of(vehicle), inputs.end_of(vehicle)
        chain = [None, *ids, None]
        stops, requests = [], []
        load = [sum(orders[key].demand.vector()[i] for key in ids) for i in range(5)]
        # Optimized routes must retain the solver's original forbidden-arc mask.
        # Manual routes have no solver matrix; warning-free road legs prove access.
        matrix = matrices[profiles.index(vehicle.routing)] if matrices is not None else None
        indexes = {coordinates(point): i for i, point in enumerate(points)}
        for a, b in zip(chain, chain[1:]):
            start_point = first.data.location if a is None else orders[a].location
            end_point = last.data.location if b is None else orders[b].location
            if matrix is not None and matrix.durations[indexes[coordinates(start_point)]][indexes[coordinates(end_point)]] is None:
                raise EngineError('Solver returned a forbidden connection; result rejected.')
            requests.append((start_point, end_point, a, b))
        try:
            legs = engine.legs(requests, vehicle.routing, inputs.routing_policy, deadline)
        except SnapError as error:
            a, b = chain[error.request_index], chain[error.request_index+1]
            key = a if error.endpoint == 'start' else b
            place = first if error.request_index == 0 else last
            name = place_name(inputs, place) if key is None else f'delivery {orders[key].external_id}'
            raise EngineError(f'The road path for {name} ends more than {engine.snap_limit_m} m from its reviewed pin. '
                              'Correct the pin and recalculate.') from None
        # Matrix estimates can make the solver choose a departure that is too
        # late for the actual roads. Keep its sequence and move departure only
        # as far back as the final legs require, within vehicle availability.
        # If even the earliest departure is infeasible, validation still fails.
        if departure is None:
            # Manual sequence: leave as late as the fixed sequence allows without
            # lengthening the route, as the solver does when it avoids waiting.
            # From the earliest schedule, a later departure absorbs waiting until
            # it would push any stop (or the return) past its closing time.
            earliest = inputs.seconds(vehicle.available_window.start)
            now, waited, slack = earliest, 0, None
            for b, leg in zip(chain[1:], legs):
                now += leg.duration_seconds
                if b is None:
                    start, close = now, inputs.seconds(vehicle.available_window.end)
                else:
                    order = orders[b]
                    start, close = max(now, inputs.seconds(order.time_window.start)), inputs.closes(order)
                    waited += start-now
                slack = waited+close-start if slack is None else min(slack, waited+close-start)
                if b is not None:
                    now = start+orders[b].service_seconds
            departure = earliest+max(0, min(waited, slack))
        # The latest departure keeps every stop inside its window less the safety margin; when the
        # roads allow no such departure, the earliest one is used and the real windows decide validity.
        latest = inputs.seconds(vehicle.available_window.end)
        for key, leg in reversed(list(zip(ids, legs[1:]))):
            order = orders[key]
            latest = min(inputs.closes(order),
                         latest-leg.duration_seconds-order.service_seconds)
        departure = max(inputs.seconds(vehicle.available_window.start),
                        min(departure, latest-legs[0].duration_seconds))
        now, waiting, service = departure, 0, 0
        for b, leg in zip(chain[1:], legs):
            now += leg.duration_seconds
            if b is not None:
                order = orders[b]
                service_start = max(now, inputs.seconds(order.time_window.start))
                leave = service_start + order.service_seconds
                load = [x-y for x, y in zip(load, order.demand.vector())]
                stops.append(Stop(order_id=b, arrival=now, service_start=service_start, departure=leave, load_after=load[:]))
                waiting += service_start-now; service += order.service_seconds; now = leave
        distance = sum(leg.distance_m for leg in legs)
        # Display operating cost is independent of the solver's driving-cost objective.
        from decimal import Decimal, ROUND_HALF_UP
        distance_cost = int((Decimal(distance)*vehicle.cost.per_km_minor/1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        time_cost = int((Decimal(now-departure)*vehicle.cost.per_hour_minor/3600).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        routes.append(Route(vehicle_id=vehicle_id, profile=vehicle.routing, departure=departure, end=now,
                            stops=stops, legs=legs, distance_m=distance,
                            driving_seconds=sum(leg.duration_seconds for leg in legs), waiting_seconds=waiting,
                            service_seconds=service, fixed_minor=vehicle.cost.fixed_minor,
                            distance_minor=distance_cost, time_minor=time_cost,
                            known_cost_minor=vehicle.cost.fixed_minor+distance_cost+time_cost))
    unassigned_rows = []
    indexes = {coordinates(point): i for i, point in enumerate(points)}
    def unreachable(vehicle, point):
        durations = matrices[profiles.index(vehicle.routing)].durations
        out, back = (indexes[coordinates(inputs.start_of(vehicle).data.location)],
                     indexes[coordinates(inputs.end_of(vehicle).data.location)])
        return durations[out][point] is None or durations[point][back] is None
    for key in unassigned:
        order = orders[key]
        able = [v for v in vehicles.values() if compatible(inputs, v, order)]
        reason = 'MANUALLY_UNASSIGNED' if manual else 'SOLVER_UNASSIGNED'
        if not manual and not able:
            reason = 'NO_COMPATIBLE_VEHICLE'
        elif not manual and all(indexes[coordinates(order.location)] in matrices[profiles.index(v.routing)].no_road
                                for v in able):
            reason = 'NO_ROAD_NEAR_PIN'
        elif not manual and all(unreachable(v, indexes[coordinates(order.location)]) for v in able):
            reason = 'NO_ROUTE_IN_DATASET'
        unassigned_rows.append(Unassigned(order_id=key, reason=reason))
    result = Draft(organization_id=inputs.organization_id, data_kind=inputs.data_kind, objective=inputs.objective,
                   routing_policy=inputs.routing_policy, dataset=engine.dataset, routes=routes,
                   unassigned=unassigned_rows, validation='FAIL', blocking_issues=[])
    on_stage('VALIDATE')
    issues = validate(inputs, result, points, profiles, matrices, engine.snap_limit_m)
    result.blocking_issues = issues
    result.validation = 'FAIL' if issues else 'PASS'
    deadline.remaining()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('tests/fixtures/paris-ten.json'))
    parser.add_argument('--valhalla-url', default='http://valhalla:8002')
    parser.add_argument('--dataset', default='Geofabrik Ile-de-France downloaded 2026-09-22; static OSM speeds')
    parser.add_argument('--objective', choices=['TRAVEL_TIME', 'MODELED_TRAVEL_COST'])
    args = parser.parse_args()
    if args.input.stat().st_size > 2_000_000:
        raise SystemExit('Input exceeds spike budget.')
    payload = json.loads(args.input.read_text())
    if args.objective:
        payload['objective'] = args.objective
    inputs = SyntheticInput.model_validate(payload)
    try:
        result = calculate(inputs, Valhalla(args.valhalla_url, args.dataset))
    except EngineError as error:
        raise SystemExit(str(error)) from None
    print(SyntheticDraft.model_validate(result.model_dump()).model_dump_json(indent=2))
    if result.validation != 'PASS':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
