"""VROOM 1.14 custom-matrix mapping and bounded native process execution."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from uuid import UUID

from app.planning import PlanningInput, Truck
from app.routing import Deadline, EngineError, Matrix, coordinates

UINT32 = 2**32 - 1
INT64 = 2**63 - 1
MAX_USER_COST = 3 * (UINT32 // 4)


@dataclass
class Mapping:
    payload: dict
    orders: dict[int, UUID]
    vehicles: dict[int, UUID]


def prepare(inputs: PlanningInput, points, profiles: list[Truck], matrices: list[Matrix]) -> Mapping:
    if len(profiles) != len(matrices):
        raise EngineError('Missing routing profile matrix.')
    n, jobs = len(points), len(inputs.orders)
    earliest = min(inputs.seconds(v.data.available_window.start) for v in inputs.vehicles)
    latest = max(inputs.seconds(v.data.available_window.end) for v in inputs.vehicles)
    forbidden = latest - earliest + 1
    if not 1 <= forbidden <= 90001:
        raise EngineError('Finite one-day hard-window assumptions are not satisfied.')
    solver_matrices = {}
    max_t = max_d = 0
    for index, matrix in enumerate(matrices):
        if len(matrix.durations) != n or len(matrix.distances) != n:
            raise EngineError('Matrix size mismatch.')
        times, distances = [], []
        for i in range(n):
            if len(matrix.durations[i]) != n or len(matrix.distances[i]) != n:
                raise EngineError('Matrix row size mismatch.')
            tr, dr = [], []
            for t, d in zip(matrix.durations[i], matrix.distances[i]):
                if (t is None) != (d is None):
                    raise EngineError('Incomplete unavailable connection.')
                if t is None:
                    t, d = forbidden, 0
                if type(t) is not int or type(d) is not int or not 0 <= t <= UINT32 or not 0 <= d <= UINT32:
                    raise EngineError('Matrix numeric bound exceeded.')
                tr.append(t); dr.append(d)
                max_t, max_d = max(max_t, t), max(max_d, d)
            times.append(tr); distances.append(dr)
        solver_matrices[f'p{index}'] = {'durations': times, 'distances': distances}
    # UserDuration/Distance are uint32; scaled intermediates in this build are int64.
    total_t = (jobs+1)*max_t + sum(o.data.service_seconds for o in inputs.orders) + latest
    total_d = (jobs+1)*max_d
    if max(total_t, total_d) > UINT32 or total_t*100 > INT64 or total_d*360 > INT64:
        raise EngineError('Conservative route-sum numeric bound exceeded.')
    if any(sum(o.data.demand.vector()[i] for o in inputs.orders) > INT64 for i in range(5)):
        raise EngineError('Aggregate load numeric bound exceeded.')
    skill_names = sorted({s for v in inputs.vehicles for s in v.data.skills} |
                         {s for o in inputs.orders for s in o.data.required_skills})
    skills = {s: i+1 for i, s in enumerate(skill_names)}
    # A delivery that ships from a depot needs a vehicle that starts there: one more skill per such depot.
    shipping = sorted({o.data.ships_from for o in inputs.orders if o.data.ships_from is not None})
    loads_at = {code: len(skills)+i+1 for i, code in enumerate(shipping)}
    point_index = {coordinates(p): i for i, p in enumerate(points)}
    result_jobs, result_vehicles = [], []
    for index, row in enumerate(inputs.orders, 1):
        o = row.data
        result_jobs.append({'id': index, 'location_index': point_index[coordinates(o.location)],
                            'delivery': o.demand.vector(), 'service': o.service_seconds,
                            'skills': [skills[s] for s in o.required_skills] + ([loads_at[o.ships_from]] if o.ships_from else []),
                            'priority': o.priority,
                            # The solver plans inside the window less the safety margin.
                            'time_windows': [[inputs.seconds(o.time_window.start), inputs.closes(o)]]})
    for index, row in enumerate(inputs.vehicles, 1):
        v = row.data
        if any(value > INT64 for value in v.capacity.vector()):
            raise EngineError('Vehicle capacity numeric bound exceeded.')
        start, end = inputs.start_of(v), inputs.end_of(v)
        vehicle = {'id': index, 'profile': f'p{profiles.index(v.routing)}',
                   'start_index': point_index[coordinates(start.data.location)],
                   'end_index': point_index[coordinates(end.data.location)], 'capacity': v.capacity.vector(),
                   'skills': [skills[s] for s in v.skills] + ([loads_at[start.data.code]] if start.data.code in loads_at else []),
                   'time_window': [inputs.seconds(v.available_window.start), inputs.seconds(v.available_window.end)],
                   'speed_factor': 1}
        if inputs.objective == 'MODELED_TRAVEL_COST':
            rates = v.cost
            upper_cost = rates.fixed_minor + total_t*rates.per_hour_minor//3600 + total_d*rates.per_km_minor//1000 + 2
            if (max(rates.fixed_minor, rates.per_hour_minor, rates.per_km_minor) > UINT32
                    or upper_cost > MAX_USER_COST or upper_cost*3600*100 > INT64):
                raise EngineError('Native monetary cost numeric bound exceeded.')
            vehicle['costs'] = {'fixed': rates.fixed_minor, 'per_hour': rates.per_hour_minor, 'per_km': rates.per_km_minor}
        for public, engine in [('max_stops', 'max_tasks'), ('max_distance_m', 'max_distance'), ('max_driving_seconds', 'max_travel_time')]:
            value = getattr(v.limits, public)
            if value is not None:
                vehicle[engine] = value
        result_vehicles.append(vehicle)
    return Mapping({'jobs': result_jobs, 'vehicles': result_vehicles, 'matrices': solver_matrices},
                   {i: o.id for i, o in enumerate(inputs.orders, 1)},
                   {i: v.id for i, v in enumerate(inputs.vehicles, 1)})


# Time VROOM may take after its search limit to write its solution before it is killed.
SOLVER_EXIT_MARGIN_SECONDS = 3


def run_vroom(payload: dict, budget: int, deadline: Deadline, executable: str = '/usr/local/bin/vroom') -> dict:
    if os.name != 'posix':
        raise EngineError('Run the native solver in the Linux application image.')
    if not 1 <= budget <= 60:
        raise EngineError('Solver budget must be 1–60 seconds.')
    # Search for less time rather than be killed when matrices used most of the job deadline.
    budget = min(budget, int(deadline.remaining()) - SOLVER_EXIT_MARGIN_SECONDS)
    if budget < 1:
        raise EngineError('Too little of the job deadline remains for the solver. Select fewer deliveries or ask the '
                          'operator to raise JOB_TIMEOUT_SECONDS.')
    import resource
    from app.processes import die_with_parent
    parent_pid = os.getpid()
    def limits():
        die_with_parent(parent_pid)
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8_000_000, 8_000_000))
        resource.setrlimit(resource.RLIMIT_CPU, (65, 65))
    with tempfile.TemporaryDirectory(prefix='vrp-solve-') as directory:
        path = Path(directory)
        (path / 'input.json').write_text(json.dumps(payload))
        with (path / 'stdout').open('wb') as stdout, (path / 'stderr').open('wb') as stderr:
            process = subprocess.Popen([executable, '-i', str(path / 'input.json'), '-t', '1', '-l', str(budget)],
                                       cwd=path, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                       start_new_session=True, preexec_fn=limits)
            try:
                stop = min(deadline.end, time.monotonic() + budget + SOLVER_EXIT_MARGIN_SECONDS)
                while process.poll() is None:
                    if time.monotonic() >= stop:
                        raise EngineError('Solver process deadline exceeded.')
                    if any((path / name).stat().st_size > 4_000_000 for name in ('stdout', 'stderr')):
                        raise EngineError('Solver output exceeds budget.')
                    time.sleep(0.02)
                if process.returncode != 0:
                    raise EngineError(f'Solver failed with exit code {process.returncode}.')
            finally:
                # Kill any remaining descendants too; wait reaps the direct child.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        if (path / 'stdout').stat().st_size > 4_000_000:
            raise EngineError('Solver output exceeds budget.')
        try:
            data = json.loads((path / 'stdout').read_text())
        except (ValueError, UnicodeError) as error:
            raise EngineError('Solver returned invalid JSON.') from error
        deadline.remaining()
        if not isinstance(data, dict) or data.get('code') != 0:
            raise EngineError('Solver did not return a successful result.')
        return data


def decode_solution(data: dict, mapping: Mapping) -> tuple[list[tuple[UUID, int, list[UUID]]], list[UUID]]:
    try:
        if data['code'] != 0 or not isinstance(data['routes'], list) or not isinstance(data['unassigned'], list):
            raise ValueError()
        served, used, routes = [], [], []
        for route in data['routes']:
            vehicle = route['vehicle']
            if type(vehicle) is not int or vehicle not in mapping.vehicles or vehicle in used or route.get('violations'):
                raise ValueError()
            steps = route['steps']
            if len(steps) < 3 or steps[0]['type'] != 'start' or steps[-1]['type'] != 'end':
                raise ValueError()
            ids = []
            for step in steps[1:-1]:
                key = step['id']
                if step['type'] != 'job' or type(key) is not int or key not in mapping.orders or step.get('violations'):
                    raise ValueError()
                ids.append(key)
            departure = steps[0]['arrival']
            if type(departure) is not int or departure < 0:
                raise ValueError()
            used.append(vehicle); served.extend(ids)
            routes.append((mapping.vehicles[vehicle], departure, [mapping.orders[key] for key in ids]))
        unassigned = [row['id'] for row in data['unassigned']]
        all_ids = served + unassigned
        if (any(type(key) is not int for key in all_ids) or len(all_ids) != len(set(all_ids))
                or set(all_ids) != set(mapping.orders)):
            raise ValueError()
        return routes, [mapping.orders[key] for key in unassigned]
    except (KeyError, TypeError, ValueError) as error:
        raise EngineError('Solver result has unknown/duplicated/missing IDs or unsupported route steps.') from error
