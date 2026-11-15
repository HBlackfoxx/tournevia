"""Independent checks over original inputs and final road legs, never solver violation flags."""
from collections import Counter
import math

from app.planning import Draft, PlanningInput, Truck
from app.routing import Matrix, coordinates, meters


DIMENSION_NAMES = ('weight', 'volume', 'Euro pallets', 'industrial pallets', 'loading length')


def validate(inputs: PlanningInput, draft: Draft, points, profiles: list[Truck], matrices: list[Matrix] | None,
             snap_limit_m: int = 50) -> list[str]:
    issues = []
    if draft.organization_id != inputs.organization_id:
        issues.append('OWNERSHIP: result owner differs from inputs')
    if draft.objective != inputs.objective or draft.routing_policy != inputs.routing_policy:
        issues.append('CONTEXT: objective or road policy mismatch')
    if draft.data_kind != inputs.data_kind:
        issues.append('PROVENANCE: result origin differs from the server input')
    orders = {row.id: row.data for row in inputs.orders}
    vehicles = {row.id: row.data for row in inputs.vehicles}
    covered = [stop.order_id for route in draft.routes for stop in route.stops] + [row.order_id for row in draft.unassigned]
    if Counter(covered) != Counter(orders.keys()):
        issues.append('COVERAGE: every selected order must occur exactly once')
    used = [route.vehicle_id for route in draft.routes]
    if len(set(used)) != len(used):
        issues.append('VEHICLE: repeated vehicle route')
    index = {coordinates(p): i for i, p in enumerate(points)}
    for route in draft.routes:
        vehicle = vehicles.get(route.vehicle_id)
        if vehicle is None:
            issues.append('VEHICLE: unknown vehicle'); continue
        # The code prefix stays stable for clients; the suffix names what to inspect.
        where = f' ({vehicle.fleet_number})'
        if route.profile != vehicle.routing:
            issues.append('PROFILE: route truck inputs differ from vehicle' + where)
        if vehicle.routing not in profiles:
            issues.append('PROFILE: missing original reachability mask' + where); continue
        matrix = matrices[profiles.index(vehicle.routing)] if matrices is not None else None
        ids = [stop.order_id for stop in route.stops]
        if not ids or any(key not in orders for key in ids):
            issues.append('STOPS: empty route or unknown order' + where); continue
        if len(route.legs) != len(ids)+1:
            issues.append('LEGS: incorrect final-leg count' + where); continue
        load = [sum(orders[key].demand.vector()[d] for key in ids) for d in range(5)]
        for d, (amount, capacity) in enumerate(zip(load, vehicle.capacity.vector())):
            if amount > capacity:
                issues.append(f'CAPACITY: {DIMENSION_NAMES[d]} overloaded' + where)
        if route.departure < inputs.seconds(vehicle.available_window.start):
            issues.append('SCHEDULE: departure before vehicle availability' + where)
        now, driving, distance, waiting, service = route.departure, 0, 0, 0, 0
        chain = [None, *ids, None]
        # Each route runs from its vehicle's start place to its end place, read from the inputs.
        locations = [inputs.start_of(vehicle).data.location, *[orders[key].location for key in ids],
                     inputs.end_of(vehicle).data.location]
        for leg_index, leg in enumerate(route.legs):
            if leg.from_order_id != chain[leg_index] or leg.to_order_id != chain[leg_index+1]:
                issues.append('IDENTITY: leg endpoints do not match the stop chain' + where)
            a, b = locations[leg_index], locations[leg_index+1]
            try:
                if matrix is not None and matrix.durations[index[coordinates(a)]][index[coordinates(b)]] is None:
                    issues.append('FORBIDDEN_ARC: route uses a connection unavailable to this truck' + where)
            except (KeyError, IndexError):
                issues.append('MATRIX: missing point/index in reachability mask' + where)
            geometry = leg.geometry
            valid_geometry = len(geometry) >= 2 and all(len(p) == 2 and all(math.isfinite(x) for x in p)
                                      and -180 <= p[0] <= 180 and -90 <= p[1] <= 90 for p in geometry)
            if not valid_geometry:
                issues.append('GEOMETRY: invalid road path' + where)
            else:
                if meters(coordinates(a), geometry[0]) > snap_limit_m or meters(coordinates(b), geometry[-1]) > snap_limit_m:
                    issues.append('SNAP: final road path does not reach reviewed points within limit' + where)
                shape_distance = sum(meters(x, y) for x, y in zip(geometry, geometry[1:]))
                if abs(shape_distance-leg.distance_m) > max(20, 0.02*leg.distance_m):
                    issues.append('GEOMETRY: path length disagrees with road distance' + where)
            if leg.duration_seconds < 0 or leg.distance_m < 0:
                issues.append('METRICS: negative road duration/distance' + where)
            now += leg.duration_seconds
            driving += leg.duration_seconds; distance += leg.distance_m
            if leg_index < len(ids):
                order, stop = orders[ids[leg_index]], route.stops[leg_index]
                where_stop = f' ({vehicle.fleet_number}, {order.external_id})'
                start = max(now, inputs.seconds(order.time_window.start))
                end = start + order.service_seconds
                if start > inputs.seconds(order.time_window.end):
                    issues.append('WINDOW: service starts after its hard window' + where_stop)
                if not set(order.required_skills) <= set(vehicle.skills):
                    issues.append('SKILLS: vehicle lacks required equipment' + where_stop)
                if not inputs.carries(vehicle, order):
                    issues.append('SHIPS_FROM: vehicle does not start at the depot the delivery ships from' + where_stop)
                load = [amount-demand for amount, demand in zip(load, order.demand.vector())]
                if any(x < 0 for x in load) or stop.load_after != load:
                    issues.append('LOAD: incorrect remaining delivery load' + where_stop)
                if (stop.arrival, stop.service_start, stop.departure) != (now, start, end):
                    issues.append('SCHEDULE: reported stop time disagrees with final legs' + where_stop)
                waiting += start-now; service += order.service_seconds; now = end
        if now > inputs.seconds(vehicle.available_window.end):
            issues.append('RETURN: vehicle returns after availability' + where)
        if (route.end, route.distance_m, route.driving_seconds, route.waiting_seconds, route.service_seconds) != (now, distance, driving, waiting, service):
            issues.append('TOTALS: route totals disagree with final legs' + where)
        for limit, value in [(vehicle.limits.max_stops, len(ids)), (vehicle.limits.max_distance_m, distance),
                             (vehicle.limits.max_driving_seconds, driving)]:
            if limit is not None and value > limit:
                issues.append('LIMIT: route exceeds a vehicle route limit' + where)
        # Exact integer half-up accounting; elapsed time includes waiting and service.
        cost_distance = (2*distance*vehicle.cost.per_km_minor + 1000)//2000
        cost_time = (2*(now-route.departure)*vehicle.cost.per_hour_minor + 3600)//7200
        fixed = vehicle.cost.fixed_minor
        if (route.fixed_minor, route.distance_minor, route.time_minor, route.known_cost_minor) != (fixed, cost_distance, cost_time, fixed+cost_distance+cost_time):
            issues.append('COST: operating estimate does not match elapsed route values' + where)
    return issues
