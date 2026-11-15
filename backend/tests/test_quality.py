import os

import pytest

from app.optimization import prepare,decode_solution,run_vroom
from app.planning import DIMENSIONS
from app.routing import Deadline
from benchmarks.cases import tiny_case
from benchmarks.reference import TinyReference
from benchmarks.run import comparisons, fixed_baseline


@pytest.mark.parametrize('objective,score',[('TRAVEL_TIME',40),('MODELED_TRAVEL_COST',447)])
def test_exact_reference_has_hand_calculated_directed_optimum(objective,score):
    inputs,matrices=tiny_case('directed',objective)
    result=TinyReference(inputs,matrices).optimum()
    assert result.score==score and result.routes==((0,1,2),)


@pytest.mark.parametrize('dimension',DIMENSIONS)
def test_reference_rejects_overload_in_each_dimension(dimension):
    inputs,matrices=tiny_case('directed','TRAVEL_TIME')
    setattr(inputs.vehicles[0].data.capacity,dimension,2)
    assert TinyReference(inputs,matrices).optimum() is None


@pytest.mark.parametrize('constraint',['skills','window','return','distance','driving','stops','forbidden'])
def test_reference_independently_rejects_hard_violations(constraint):
    inputs,matrices=tiny_case('directed','TRAVEL_TIME')
    v=inputs.vehicles[0].data
    if constraint=='skills':inputs.orders[0].data.required_skills=['TAIL_LIFT']
    if constraint=='window':
        from datetime import timedelta
        inputs.orders[0].data.time_window.end=v.available_window.start+timedelta(seconds=5)
    if constraint=='return':
        from datetime import timedelta
        v.available_window.end=v.available_window.start+timedelta(seconds=69)
    if constraint=='distance':v.limits.max_distance_m=399
    if constraint=='driving':v.limits.max_driving_seconds=39
    if constraint=='stops':v.limits.max_stops=2
    if constraint=='forbidden':
        for i in range(1,4):matrices[0].durations[i][0]=None;matrices[0].distances[i][0]=None
    assert TinyReference(inputs,matrices).optimum() is None


def test_reference_refuses_unsupported_scoring_and_partial_comparisons():
    inputs,matrices=tiny_case('directed','MODELED_TRAVEL_COST')
    inputs.vehicles[0].data.cost.per_hour_minor=1
    with pytest.raises(ValueError,match='integral'):TinyReference(inputs,matrices)
    inputs.vehicles[0].data.cost.per_hour_minor=3600
    inputs.orders[0].data.priority=1
    with pytest.raises(ValueError,match='priority'):TinyReference(inputs,matrices)
    inputs.orders[0].data.priority=0
    with pytest.raises(ValueError,match='coverage'):TinyReference(inputs,matrices).candidate_score([],[])


@pytest.mark.parametrize('objective,score',[('TRAVEL_TIME',40),('MODELED_TRAVEL_COST',447)])
def test_reference_waiting_and_inclusive_boundaries_do_not_change_driving_score(objective,score):
    from datetime import timedelta
    inputs,matrices=tiny_case('directed',objective)
    start=inputs.vehicles[0].data.available_window.start
    inputs.orders[0].data.time_window.end=start+timedelta(seconds=10)
    inputs.orders[1].data.time_window.start=start+timedelta(seconds=600)
    inputs.vehicles[0].data.available_window.end=start+timedelta(seconds=640)
    assert TinyReference(inputs,matrices).optimum().score==score


@pytest.mark.parametrize('count',[10,250])
def test_benchmark_rejects_coverage_changes_and_flags_objective_regressions(count):
    common={'validation':'PASS','served':count,'unassigned':0,'priority_coverage':{'0':{'requested':count,'served':count}},
            'driving_seconds':100,'unrounded_driving_cost_minor':200}
    runs=[{**common,'kind':kind} for kind in ['baseline','TRAVEL_TIME','MODELED_TRAVEL_COST']]
    assert all(c['pass'] for c in comparisons(runs))
    runs[1]['driving_seconds']=101
    assert not comparisons(runs)[0]['pass']
    runs[1]['served']=count-1;runs[1]['unassigned']=1
    with pytest.raises(ValueError,match='coverage'):comparisons(runs)


def test_benchmark_retains_completed_trials_when_later_trial_fails(monkeypatch,capsys):
    import io
    import json
    import subprocess
    from benchmarks import run
    monkeypatch.setattr(run.sys,'argv',['benchmark','--repeats','2'])
    monkeypatch.setattr(run.sys,'platform','linux')
    monkeypatch.setattr(run,'urlopen',lambda *args,**kwargs:io.StringIO('{"version":"test-only"}'))
    calls=[]
    def output(command,**kwargs):
        if '--version' in command:return 'test-only'
        calls.append(command)
        if len(calls)==1:return json.dumps({'kind':'baseline','validation':'PASS','served':10})
        raise subprocess.CalledProcessError(1,command,output=json.dumps({'status':'FAILED','error':'test engine rejection'}))
    monkeypatch.setattr(run.subprocess,'check_output',output)
    with pytest.raises(SystemExit) as stopped:run.main()
    assert stopped.value.code==1
    report=json.loads(capsys.readouterr().out)
    assert len(calls)==2 and len(report['runs'])==1
    assert report['failure']['repeat']==2 and report['failure']['detail']['error']=='test engine rejection'
    assert 'comparisons' not in report


def test_regional_fixture_and_fixed_baseline_cover_varied_supported_constraints():
    from benchmarks.cases import FIXTURE
    from benchmarks.regional import regional_input
    from app.planning import PlanningInput
    from app.edits import check_assignment
    inputs = regional_input(PlanningInput.model_validate_json(FIXTURE.read_text()))
    edit = fixed_baseline(inputs, 'regional-250')
    check_assignment(inputs, edit)
    assert len(inputs.orders) == 250 and len(inputs.vehicles) == 25
    assert inputs.data_kind == 'SYNTHETIC'
    assert len({v.data.routing.model_dump_json() for v in inputs.vehicles}) == 3
    points = [o.data.location for o in inputs.orders]
    assert len({(p.longitude, p.latitude) for p in points}) == 250
    assert max(p.longitude for p in points) - min(p.longitude for p in points) > .39
    assert max(p.latitude for p in points) - min(p.latitude for p in points) > .23
    assert {o.data.priority for o in inputs.orders} == {0, 50, 100}
    assert {o.data.time_window.start.hour for o in inputs.orders} == {8, 12}
    assert {o.data.service_seconds for o in inputs.orders} == {120, 180, 240, 300}
    assert {s for o in inputs.orders for s in o.data.required_skills} == {'TAIL_LIFT', 'REFRIGERATED'}
    orders = {o.id: o.data for o in inputs.orders}
    for vehicle, assignment in zip(inputs.vehicles, edit.routes):
        assigned = [orders[key] for key in assignment.order_ids]
        assert len(assigned) == 10 <= vehicle.data.limits.max_stops
        assert all(set(o.required_skills) <= set(vehicle.data.skills) for o in assigned)
        for dimension in DIMENSIONS:
            assert 0 < sum(getattr(o.demand, dimension) for o in assigned) <= getattr(vehicle.data.capacity, dimension)
        assert vehicle.data.limits.max_distance_m and vehicle.data.limits.max_driving_seconds
    # Count-based dispatch previously treated every 250-stop workload as Paris.
    with pytest.raises(ValueError, match='predefined baseline'):
        fixed_baseline(inputs, 'paris-ten')


def test_equipped_scenario_preserves_original_deliveries_and_only_replaces_two_vehicles():
    from benchmarks.cases import FIXTURE
    from benchmarks.regional import regional_input, equipped_regional_input
    from app.planning import PlanningInput
    from app.edits import check_assignment
    seed = PlanningInput.model_validate_json(FIXTURE.read_text())
    original = regional_input(seed)
    candidate = equipped_regional_input(seed)
    assert candidate.orders == original.orders and candidate.depot == original.depot
    assert len({v.data.routing.model_dump_json() for v in candidate.vehicles}) == 3
    assert [i for i, (a, b) in enumerate(zip(original.vehicles, candidate.vehicles)) if a != b] == [0, 24]
    for index in (0, 24):
        a, b = original.vehicles[index], candidate.vehicles[index]
        assert a.id == b.id and a.data.skills == b.data.skills
        assert a.data.limits == b.data.limits and a.data.available_window == b.data.available_window
        assert b.data.routing == original.vehicles[1].data.routing
        assert b.data.capacity == original.vehicles[1].data.capacity
    check_assignment(candidate, fixed_baseline(candidate, 'regional-equipped-250'))
    assert regional_input(seed) == original


@pytest.mark.parametrize('workload',['paris-ten','paris-250','regional-250','regional-equipped-250'])
def test_child_trials_keep_explicit_workload(monkeypatch,capsys,workload):
    import io
    import json
    from benchmarks import run
    monkeypatch.setattr(run.sys,'argv',['benchmark','--workload',workload,'--repeats','2','--matrix-concurrency','4'])
    monkeypatch.setattr(run.sys,'platform','linux')
    monkeypatch.setattr(run,'urlopen',lambda *args,**kwargs:io.StringIO('{"version":"test-only"}'))
    commands=[]
    def output(command,**kwargs):
        if '--version' in command:return 'test-only'
        commands.append(command)
        assert command[command.index('--workload')+1] == workload
        assert command[command.index('--matrix-concurrency')+1] == '4'
        count = 10 if workload == 'paris-ten' else 250
        return json.dumps({'kind':command[command.index('--trial')+1], 'validation':'PASS',
                          'served':count,'unassigned':0,'priority_coverage':{'0':{'requested':count,'served':count}},
                          'driving_seconds':100,'unrounded_driving_cost_minor':100})
    monkeypatch.setattr(run.subprocess,'check_output',output)
    run.main()
    report=json.loads(capsys.readouterr().out)
    assert len(commands)==6 and report['workload']['name']==workload
    assert report['workload']['matrix_concurrency']==4
    assert all(c['pass'] for c in report['comparisons'])


def test_failed_trial_reports_stage_and_elapsed_time(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.routing import EngineError
    from benchmarks import run
    # Failure happens before RSS is read; keep this test portable to Windows.
    monkeypatch.setitem(sys.modules,'resource',SimpleNamespace())
    def fail(*args,**kwargs):
        kwargs['on_stage']('PRECHECK')
        kwargs['on_stage']('MATRIX')
        raise EngineError('test-only matrix timeout')
    monkeypatch.setattr(run,'reroute',fail)
    with pytest.raises(run.TrialFailure) as failed:
        run.trial('baseline','http://unused','test-only',5)
    detail=failed.value.detail
    assert detail['failed_stage']=='MATRIX' and detail['error']=='test-only matrix timeout'
    assert detail['stage_seconds']['MATRIX'] >= 0 and detail['total_seconds'] >= 0
    assert 'metrics' not in detail


@pytest.mark.engines
@pytest.mark.skipif(os.environ.get('RUN_ENGINE_TESTS')!='1',reason='Requires native VROOM in Linux image.')
@pytest.mark.parametrize('name',['directed','constrained','cost-choice'])
@pytest.mark.parametrize('objective',['TRAVEL_TIME','MODELED_TRAVEL_COST'])
@pytest.mark.parametrize('prune_profiles',[False,True])
def test_native_solver_matches_tiny_full_coverage_optimum(name,objective,prune_profiles):
    inputs,matrices=tiny_case(name,objective)
    oracle=TinyReference(inputs,matrices);optimum=oracle.optimum()
    assert optimum is not None
    if prune_profiles:
        from types import SimpleNamespace
        from app.routing import Matrix
        from app.spike import profile_matrices
        def selected_matrix(points,profile,*args,time_bounds=None):
            indexes=[oracle.points.index(point) for point in points]
            original=matrices[oracle.profiles.index(profile)]
            result=Matrix([[original.durations[i][j] for j in indexes] for i in indexes],
                          [[original.distances[i][j] for j in indexes] for i in indexes])
            for i in range(1,len(indexes)):
                for j in range(1,len(indexes)):
                    if i!=j and time_bounds and time_bounds[i][0]>time_bounds[j][1]:
                        result.durations[i][j]=result.distances[i][j]=None
            return result
        pruned=profile_matrices(inputs,SimpleNamespace(matrix=selected_matrix,unsnappable=lambda *a:[],snap_limit_m=50),oracle.points,oracle.profiles,Deadline.after(5))
    else:pruned=matrices
    mapping=prepare(inputs,oracle.points,oracle.profiles,pruned)
    for _ in range(3):
        routes,unassigned=decode_solution(run_vroom(mapping.payload,1,Deadline.after(5)),mapping)
        assert oracle.candidate_score(routes,unassigned)==optimum.score
