"""Synthetic unassigned-work diagnostics; no claim of fleet-wide infeasibility."""
import argparse
import json
import time

from app.planning import DIMENSIONS, Draft, PlanningInput
from app.routing import Deadline, EngineError, Valhalla
from app.spike import calculate
from benchmarks.cases import FIXTURE
from benchmarks.regional import regional_input
from benchmarks.run import metrics


def route_probe(engine, start, end, profile, policy, deadline):
    try:
        leg = engine.leg(start, end, profile, policy, deadline)
        return {'found': True, 'seconds': leg.duration_seconds, 'distance_m': leg.distance_m}
    except EngineError as error:
        detail = getattr(error, 'engine_detail', None)
        if getattr(error, 'engine_status', None) == 400 and isinstance(detail, dict):
            if detail.get('error_code') == 442 and not detail.get('warnings'):
                return {'found': False, 'error_code': 442}
        raise


def diagnose(inputs: PlanningInput, result: Draft, engine: Valhalla, deadline: Deadline) -> list[dict]:
    """Check each unassigned job alone; passing is not a multi-job feasibility proof.

    Query even equipment-ineligible profiles to expose equipment/access conflicts.
    Engine failures propagate, rather than being reported as road unreachability.
    Matrices and geometries remain transient; only diagnostic facts are returned.
    """
    orders = {row.id: row.data for row in inputs.orders}
    profiles = list({v.data.routing.model_dump_json(): v.data.routing for v in inputs.vehicles}.values())
    reports = []
    for unassigned in result.unassigned:
        order = orders[unassigned.order_id]
        checks = []
        for profile in profiles:
            points = [inputs.depot.data.location, order.location]
            matrix = engine.matrix(points, profile, inputs.routing_policy, deadline)
            outward, inward = matrix.durations[0][1], matrix.durations[1][0]
            legs = [route_probe(engine, points[a], points[b], profile, inputs.routing_policy, deadline)
                    for a, b in [(0, 1), (1, 0)]]
            roads = {'matrix_outward_found': outward is not None, 'matrix_return_found': inward is not None,
                     'outward_route': legs[0], 'return_route': legs[1]}
            routed = all(leg['found'] for leg in legs)
            disagreement = (outward is not None) != legs[0]['found'] or (inward is not None) != legs[1]['found']
            vehicles = []
            for row in inputs.vehicles:
                v = row.data
                if v.routing != profile:
                    continue
                blockers = [f'EQUIPMENT:{s}' for s in sorted(set(order.required_skills) - set(v.skills))]
                blockers += [f'CAPACITY:{d}' for d in DIMENSIONS if getattr(order.demand, d) > getattr(v.capacity, d)]
                if disagreement: blockers.append('MATRIX_ROUTE_DISAGREEMENT')
                if not legs[0]['found']: blockers.append('NO_OUTWARD_PATH_IN_DATASET')
                if not legs[1]['found']: blockers.append('NO_RETURN_PATH_IN_DATASET')
                if routed:
                    start = max(inputs.seconds(v.available_window.start) + legs[0]['seconds'],
                                inputs.seconds(order.time_window.start))
                    end = start + order.service_seconds + legs[1]['seconds']
                    if start > inputs.seconds(order.time_window.end): blockers.append('WINDOW')
                    if end > inputs.seconds(v.available_window.end): blockers.append('RETURN_WINDOW')
                    driving = sum(leg['seconds'] for leg in legs)
                    if v.limits.max_distance_m is not None and sum(leg['distance_m'] for leg in legs) > v.limits.max_distance_m:
                        blockers.append('DISTANCE_LIMIT')
                    if v.limits.max_driving_seconds is not None and driving > v.limits.max_driving_seconds:
                        blockers.append('DRIVING_LIMIT')
                vehicles.append({'fleet_number': v.fleet_number, 'blockers': blockers})
            checks.append({'profile': profile.model_dump(), 'roads': roads, 'vehicles': vehicles})
        candidates = [v['fleet_number'] for c in checks for v in c['vehicles'] if not v['blockers']]
        reports.append({'external_id': order.external_id, 'solver_reason': unassigned.reason,
                        'single_delivery_candidates': candidates, 'profiles': checks})
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--valhalla-url', default='http://host.docker.internal:8002')
    parser.add_argument('--matrix-concurrency', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--objective', choices=['TRAVEL_TIME', 'MODELED_TRAVEL_COST'], default='TRAVEL_TIME')
    args = parser.parse_args()
    inputs = regional_input(PlanningInput.model_validate_json(FIXTURE.read_text()))
    inputs.objective = args.objective
    inputs.solver_time_limit_seconds = 5
    engine = Valhalla(args.valhalla_url, 'Pinned regional synthetic graph', matrix_concurrency=args.matrix_concurrency)
    started = time.perf_counter()
    result = calculate(inputs, engine, deadline=Deadline.after(120))
    elapsed = time.perf_counter() - started
    report = {'workload': 'regional-250', 'objective': args.objective, 'calculation_seconds': elapsed,
              'metrics': metrics(inputs, result),
              'diagnostic_scope': 'Separate 120-second diagnostic budget after calculation; each unassigned delivery alone. No fleet-wide feasibility or physical-road impossibility claim.',
              'unassigned': diagnose(inputs, result, engine, Deadline.after(120))}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
