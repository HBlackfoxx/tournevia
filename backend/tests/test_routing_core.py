from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.optimization import UINT32, decode_solution, prepare
from app.planning import DIMENSIONS, PlanningInput, Window
from app.routing import EngineError, Matrix, costing, parse_matrix

FIXTURE = Path(__file__).parent / 'fixtures/paris-ten.json'


def inputs():
    return PlanningInput.model_validate_json(FIXTURE.read_text())


def test_full_workflow_point_boundary_and_profile_limit():
    from benchmarks.scaling import scaled_input
    from app.spike import points_and_profiles
    model=scaled_input(inputs())
    points,profiles=points_and_profiles(model)
    assert len(points)==251 and len(profiles)==3
    assert points[0]==model.depot.data.location
    model.orders.reverse()
    assert points_and_profiles(model)[0]==points
    model.orders[-1].data.location=model.orders[0].data.location.model_copy()
    assert len(points_and_profiles(model)[0])==250
    model.vehicles[-1].data.routing.height_mm+=1
    with pytest.raises(EngineError,match='three exact truck profiles'):points_and_profiles(model)


def small_input():
    data = json.loads(FIXTURE.read_text())
    data['vehicles'] = data['vehicles'][:1]
    data['orders'] = data['orders'][:1]
    data['vehicles'][0]['data']['skills'] = []
    data['vehicles'][0]['data']['capacity'] = dict.fromkeys(DIMENSIONS, 10)
    data['orders'][0]['data']['required_skills'] = []
    data['orders'][0]['data']['demand'] = dict.fromkeys(DIMENSIONS, 1)
    return data


def mapped(data=None, matrix=None):
    model = PlanningInput.model_validate(data or small_input())
    return prepare(model, [model.depot.data.location, model.orders[0].data.location],
                   [model.vehicles[0].data.routing], [matrix or Matrix([[0,10],[20,0]], [[0,100],[200,0]])])


@pytest.mark.parametrize('constraint',[*DIMENSIONS,'skills'])
def test_profile_pruning_keeps_all_jobs_and_preserves_usable_directed_nulls(constraint):
    from types import SimpleNamespace
    from uuid import UUID
    from app.routing import Deadline,coordinates
    from app.spike import points_and_profiles,profile_matrices
    model=PlanningInput.model_validate(small_input())
    extra=model.orders[0].model_copy(deep=True);extra.id=UUID(int=99)
    extra.data.external_id='INELIGIBLE';extra.data.location.longitude+=.01
    if constraint=='skills':extra.data.required_skills=['TAIL_LIFT']
    else:setattr(extra.data.demand,constraint,11)
    model.orders.append(extra);before=model.model_dump()
    points,profiles=points_and_profiles(model);calls=[]
    def matrix(selected,*args,**kwargs):
        calls.append(selected)
        assert len(selected)==2
        return Matrix([[0,11],[None,0]],[[0,110],[None,0]])
    matrices=profile_matrices(model,SimpleNamespace(matrix=matrix,unsnappable=lambda *a:[],snap_limit_m=50),points,profiles,Deadline.after(5))
    included=next(i for i,p in enumerate(points) if coordinates(p)==coordinates(model.orders[0].data.location))
    excluded=next(i for i,p in enumerate(points) if coordinates(p)==coordinates(extra.data.location))
    assert matrices[0].durations[0][included]==11 and matrices[0].durations[included][0] is None
    assert matrices[0].distances[0][included]==110 and matrices[0].distances[included][0] is None
    assert all(matrices[0].durations[excluded][i] is None and matrices[0].durations[i][excluded] is None for i in range(3))
    assert len(prepare(model,points,profiles,matrices).payload['jobs'])==2
    assert model.model_dump()==before and len(calls)==1


def test_profile_pruning_requires_one_jointly_compatible_vehicle_and_keeps_shared_points():
    from types import SimpleNamespace
    from uuid import UUID
    from app.routing import Deadline
    from app.spike import points_and_profiles,profile_matrices
    model=PlanningInput.model_validate(small_input())
    model.orders[0].data.required_skills=['TAIL_LIFT'];model.orders[0].data.demand.weight_kg=8
    extra=model.vehicles[0].model_copy(deep=True);extra.id=UUID(int=99)
    extra.data.fleet_number='SKILLED';extra.data.skills=['TAIL_LIFT'];extra.data.capacity.weight_kg=5
    model.vehicles.append(extra);counts=[]
    def matrix(selected,*args,**kwargs):
        counts.append(len(selected));return Matrix([[0]*len(selected) for _ in selected],[[0]*len(selected) for _ in selected])
    points,profiles=points_and_profiles(model)
    profile_matrices(model,SimpleNamespace(matrix=matrix,unsnappable=lambda *a:[],snap_limit_m=50),points,profiles,Deadline.after(5))
    assert counts==[1]  # Capacity from one vehicle cannot combine with another's equipment.
    shared=model.orders[0].model_copy(deep=True);shared.id=UUID(int=100)
    shared.data.external_id='SAME-POINT';shared.data.required_skills=[]
    model.orders.append(shared)
    profile_matrices(model,SimpleNamespace(matrix=matrix,unsnappable=lambda *a:[],snap_limit_m=50),points,profiles,Deadline.after(5))
    assert counts==[1,2]  # An eligible delivery keeps the shared vertex available.


def test_forbidden_duration_is_horizon_plus_one_and_mask_unchanged():
    matrix = Matrix([[0, None], [20, 0]], [[0, None], [200, 0]])
    mapping = mapped(matrix=matrix)
    window = mapping.payload['vehicles'][0]['time_window']
    assert mapping.payload['matrices']['p0']['durations'][0][1] == window[1]-window[0]+1
    assert mapping.payload['matrices']['p0']['distances'][0][1] == 0
    assert matrix.durations[0][1] is None
    assert mapping.payload['vehicles'][0]['speed_factor'] == 1
    assert mapping.payload['matrices']['p0']['durations'][1][0] == 20


def test_numeric_bounds_reject_large_matrices_and_costs():
    with pytest.raises(EngineError, match='numeric bound'):
        mapped(matrix=Matrix([[0,UINT32],[20,0]], [[0,100],[200,0]]))
    data = small_input(); data['objective'] = 'MODELED_TRAVEL_COST'
    data['vehicles'][0]['data']['cost']['per_km_minor'] = UINT32 + 1
    with pytest.raises(EngineError, match='cost numeric'):
        mapped(data)


def test_matrix_shape_null_pairs_units_and_rounding():
    response = {'sources_to_targets': {'durations': [[0, 1.1], [None, 0]], 'distances': [[0, .0011], [None, 0]]}, 'units':'kilometers'}
    matrix = parse_matrix(response, 2)
    assert matrix.durations == [[0,2],[None,0]]
    assert matrix.distances == [[0,2],[None,0]]
    for broken in [{}, {**response, 'units': 'miles'},
                   {**response, 'sources_to_targets': {'durations': [[0]], 'distances': [[0]]}}]:
        with pytest.raises(EngineError):
            parse_matrix(broken, 2)
    broken = deepcopy(response); broken['sources_to_targets']['distances'][1][0] = 0
    with pytest.raises(EngineError):
        parse_matrix(broken, 2)


@pytest.mark.parametrize('value', [True, -1, float('inf'), float('nan')])
def test_invalid_engine_numbers(value):
    data = {'sources_to_targets': {'durations': [[0,value],[1,0]], 'distances': [[0,1],[1,0]]}, 'units':'kilometers'}
    with pytest.raises(EngineError):
        parse_matrix(data, 2)


@pytest.mark.parametrize('change', ['owner', 'unreviewed', 'inactive', 'pickup', 'speed', 'naive', 'wrong_day', 'truck_bound'])
def test_unsupported_or_unsafe_input_rejected(change):
    data = small_input()
    if change == 'owner': data['orders'][0]['organization_id'] = '00000000-0000-0000-0000-000000000999'
    if change == 'unreviewed': data['orders'][0]['data']['location']['reviewed'] = False
    if change == 'inactive': data['vehicles'][0]['data']['active'] = False
    if change == 'pickup': data['orders'][0]['data']['pickup'] = True
    if change == 'speed': data['vehicles'][0]['data']['speed_factor'] = 2
    if change == 'naive': data['orders'][0]['data']['time_window']['start'] = '2026-09-23T08:00:00'
    if change == 'wrong_day': data['orders'][0]['data']['service_date'] = '2026-09-24'
    if change == 'truck_bound': data['vehicles'][0]['data']['routing']['height_mm'] = 10001
    with pytest.raises(ValidationError): PlanningInput.model_validate(data)


def test_solver_partition_unknown_duplicate_missing_and_soft_mode():
    mapping = mapped()
    valid = {'code':0, 'routes':[{'vehicle':1, 'steps':[{'type':'start','arrival':27000}, {'type':'job','id':1}, {'type':'end'}]}], 'unassigned':[]}
    assert len(decode_solution(valid, mapping)[0]) == 1
    for mutation in ['duplicate', 'missing', 'unknown', 'violation', 'vehicle', 'break']:
        data = deepcopy(valid)
        if mutation == 'duplicate': data['unassigned'] = [{'id':1}]
        if mutation == 'missing': data['routes'] = []
        if mutation == 'unknown': data['routes'][0]['steps'][1]['id'] = 99
        if mutation == 'violation': data['routes'][0]['violations'] = [{'cause':'load'}]
        if mutation == 'vehicle': data['routes'][0]['vehicle'] = True
        if mutation == 'break': data['routes'][0]['steps'][1]['type'] = 'break'
        with pytest.raises(EngineError): decode_solution(data, mapping)


def test_truck_units_and_static_access_policy():
    options = costing(inputs().vehicles[0].data.routing, 'TRUCK_PREFER_NO_TOLLS')
    assert options['height'] == 3.6 and options['weight'] == 19 and options['axle_load'] == 11.5
    assert options['use_tolls'] == 0 and options['hgv_no_access_penalty'] == 43200
    assert options['speed_types'] == []
    assert not options['ignore_restrictions'] and not options['ignore_oneways'] and not options['ignore_access']


@pytest.mark.parametrize('start,end,seconds', [('2026-03-29T00:00:00+01:00','2026-03-29T23:59:59+02:00',82799),
    ('2026-10-25T00:00:00+02:00','2026-10-25T23:59:59+01:00',89999),
    ('2026-10-25T02:30:00+02:00','2026-10-25T02:15:00+01:00',2700)])
def test_dst_days_and_repeated_hour_use_actual_instants(start, end, seconds):
    window = Window(start=start,end=end)
    assert window.end.timestamp()-window.start.timestamp() == seconds


@pytest.mark.parametrize('start', ['2026-03-29T02:30:00+01:00','2026-09-23T08:00:00+00:00'])
def test_nonexistent_or_inconsistent_paris_times_rejected(start):
    with pytest.raises(ValidationError):
        Window(start=start,end='2026-09-23T17:00:00+02:00')


@pytest.mark.parametrize('margin,closes', [(0, 'end'), (900, 'end-900'), (10**9 // 10**6, 'end-1000'), (7200, 'start')])
def test_solver_windows_end_the_safety_margin_early_but_never_before_they_open(margin, closes):
    data = small_input()
    data['safety_margin_seconds'] = margin
    model = PlanningInput.model_validate(data)
    window = model.orders[0].data.time_window
    start, end = model.seconds(window.start), model.seconds(window.end)
    expected = {'end': end, 'end-900': end - 900, 'end-1000': end - 1000, 'start': max(start, end - 7200)}[closes]
    assert mapped(data).payload['jobs'][0]['time_windows'] == [[start, expected]]
    # A plan saved before the margin existed reads as no margin.
    del data['safety_margin_seconds']
    assert PlanningInput.model_validate(data).safety_margin_seconds == 0
