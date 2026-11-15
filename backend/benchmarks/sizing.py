"""Sizing scenarios, from a starter day to an enterprise-size one, measured as whole calculations.

Each run is one `calculate()` call under the application's own whole-job deadline
(JOB_TIMEOUT_SECONDS, 180 s by default) with the default 5 s solver budget, as the
worker runs it. Run inside the engine-test image with the CPU and memory you want to
measure; scripts/benchmark_sizing.py does that with Docker limits and records the
router's memory. Workloads are fictional; quality is judged by independent validation
and full coverage, not by optimality.
"""
import argparse
import json
import resource
import sys
import time

from app.planning import PlanningInput
from app.routing import Deadline, EngineError, Valhalla
from app.spike import calculate
from benchmarks.cases import FIXTURE
from benchmarks.regional import equipped_regional_input
from benchmarks.scaling import scaled_input


def sliced(model: PlanningInput, vehicles: int, orders: int) -> PlanningInput:
    return model.model_copy(update={'vehicles': model.vehicles[:vehicles], 'orders': model.orders[:orders]})


def scenario(name: str) -> PlanningInput:
    seed = PlanningInput.model_validate_json(FIXTURE.read_text())
    if name == 'starter':
        return seed  # 2 vehicles, 10 deliveries in Paris.
    if name == 'regional':
        return equipped_regional_input(seed)  # 25 vehicles, 250 deliveries across Île-de-France.
    dense = scaled_input(seed)  # 25 vehicles, 250 deliveries in dense Paris; at most 10 stops each.
    return {'small-fleet': sliced(dense, 5, 50), 'medium-fleet': sliced(dense, 10, 100), 'large-paris': dense}[name]


SCENARIOS = ['starter', 'small-fleet', 'medium-fleet', 'large-paris', 'regional']


def run(name: str, url: str, concurrency: int, timeout: int) -> dict:
    inputs = scenario(name).model_copy(update={'solver_time_limit_seconds': 5})
    stages, started, previous, current = {}, time.perf_counter(), time.perf_counter(), None

    def stage(next_stage: str) -> None:
        nonlocal previous, current
        now = time.perf_counter()
        if current is not None:
            stages[current] = round(now - previous, 2)
        current, previous = next_stage, now

    row = {'scenario': name, 'vehicles': len(inputs.vehicles), 'deliveries': len(inputs.orders), 'matrix_concurrency': concurrency}
    try:
        result = calculate(inputs, Valhalla(url, 'sizing', matrix_concurrency=concurrency),
                           deadline=Deadline.after(timeout), on_stage=stage)
        stage('DONE')
        served = sum(len(route.stops) for route in result.routes)
        row.update(status='OK' if result.validation == 'PASS' and served == len(inputs.orders) else 'INCOMPLETE',
                   validation=result.validation, served=served, unassigned=len(result.unassigned))
    except (EngineError, RuntimeError, ValueError, TimeoutError) as error:
        stage('FAILED')
        row.update(status='FAILED', error=' '.join(str(error).split())[:200])
    row.update(seconds=round(time.perf_counter() - started, 1), stage_seconds=stages,
               calculation_peak_rss_mib=round(max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                                  resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) / 1024))
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, required=True)
    parser.add_argument('--valhalla-url', required=True)
    parser.add_argument('--matrix-concurrency', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--timeout', type=int, default=180, help='Whole-calculation deadline (JOB_TIMEOUT_SECONDS).')
    parser.add_argument('--repeats', type=int, default=2, choices=range(1, 6))
    args = parser.parse_args()
    if sys.platform != 'linux':
        raise SystemExit('Run inside the Linux engine-test image (native VROOM).')
    for repeat in range(args.repeats):
        print(json.dumps({**run(args.scenario, args.valhalla_url, args.matrix_concurrency, args.timeout), 'repeat': repeat + 1}),
              flush=True)


if __name__ == '__main__':
    main()
