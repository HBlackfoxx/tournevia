"""Small synthetic numeric matrices, explicitly not road-network evidence."""
from copy import deepcopy
import json
from pathlib import Path

from app.planning import DIMENSIONS, PlanningInput
from app.routing import Matrix

FIXTURE = Path(__file__).resolve().parents[1] / 'tests/fixtures/paris-ten.json'


def tiny_case(name: str, objective: str) -> tuple[PlanningInput,list[Matrix]]:
    data = json.loads(FIXTURE.read_text())
    data['orders'] = data['orders'][:(4 if name == 'constrained' else 3)]
    data['vehicles'] = data['vehicles'][:(1 if name == 'directed' else 2)]
    for v in data['vehicles']:
        v['data'].update(capacity=dict.fromkeys(DIMENSIONS,10),skills=[],limits={},
                         available_window={'start':'2026-09-23T08:00:00+02:00','end':'2026-09-23T09:00:00+02:00'})
        v['data']['cost'].update(fixed_minor=7,per_hour_minor=3600,per_km_minor=1000)
    for o in data['orders']:
        o['data'].update(demand=dict.fromkeys(DIMENSIONS,1),required_skills=[],priority=0,service_seconds=10,
                         time_window={'start':'2026-09-23T08:00:00+02:00','end':'2026-09-23T09:00:00+02:00'})
    n = len(data['orders'])+1
    times = [[0 if a==b else 10 if b==(a+1)%n else 70 for b in range(n)] for a in range(n)]
    matrices = [Matrix(times,[[t*10 for t in row] for row in times])]
    if len(data['vehicles']) == 2:
        matrices.append(deepcopy(matrices[0]))
    if name == 'constrained':
        truck, van = [v['data'] for v in data['vehicles']]
        truck.update(capacity=dict.fromkeys(DIMENSIONS,2),skills=['TAIL_LIFT'],limits={'max_stops':2,'max_distance_m':3000,'max_driving_seconds':300})
        van.update(capacity=dict.fromkeys(DIMENSIONS,2),limits={'max_stops':2})
        for o in data['orders'][:2]:o['data']['required_skills']=['TAIL_LIFT']
        data['orders'][0]['data']['time_window']['end']='2026-09-23T08:01:00+02:00'
        matrices[1].durations[3][4]=None;matrices[1].distances[3][4]=None
    elif name == 'cost-choice':
        # Second profile drives more slowly but has far shorter distances.
        matrices[1] = Matrix([[t*2 for t in row] for row in times],deepcopy(times))
        data['vehicles'][1]['data']['cost']['fixed_minor']=2
    elif name != 'directed':
        raise ValueError('Unknown benchmark case.')
    data.update(objective=objective,solver_time_limit_seconds=1)
    return PlanningInput.model_validate(data),matrices
