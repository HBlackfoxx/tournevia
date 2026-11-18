"""Acceptance tests against actual local engines, never replaced by HTTP mocks."""
from copy import deepcopy
import json
import os
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.fleet import Location
from app.optimization import decode_solution, prepare, run_vroom
from app.planning import DIMENSIONS, PlanningInput, Truck, Unassigned, SyntheticDraft
from app.routing import Deadline, EngineError, Matrix, Valhalla, coordinates, parse_matrix
from app.spike import calculate
from app.validation import validate
from app.edits import EditRequest, reroute
from test_routing_core import FIXTURE, mapped, small_input

pytestmark = [pytest.mark.engines, pytest.mark.skipif(os.environ.get('RUN_ENGINE_TESTS') != '1',
                                                     reason='Set RUN_ENGINE_TESTS=1 with real engines running.')]


def restrictions():
    return Valhalla(os.environ.get('RESTRICTIONS_URL', 'http://host.docker.internal:8003'), 'Controlled synthetic roads')


def van():
    return Truck(height_mm=2700, width_mm=2100, length_mm=6200, gross_weight_kg=3500, axle_load_kg=2000, axle_count=2)


def points():
    return [Location(latitude=48, longitude=2.0005, reviewed=True), Location(latitude=48, longitude=2.0055, reviewed=True)]


@pytest.mark.parametrize('field,value', [('height_mm',3600),('width_mm',2500),('length_mm',9000),
                                       ('gross_weight_kg',19000),('axle_load_kg',5000),('axle_count',3)])
def test_actual_truck_restrictions(field, value):
    engine = restrictions()
    small = engine.matrix(points(), van(), 'TRUCK_STANDARD', Deadline.after(20))
    assert small.durations[0][1] is not None
    large = Truck.model_validate({**van().model_dump(), field:value})
    blocked = engine.matrix(points(), large, 'TRUCK_STANDARD', Deadline.after(20))
    assert blocked.durations[0][1] is None and blocked.durations[1][0] is None


def test_weight_limit_tagged_maxweightrating_blocks_a_heavier_truck():
    # France's roads carry the limit in this tag; Valhalla's own tag file reads maxweight only.
    engine = restrictions()
    p = [Location(latitude=48.02, longitude=2.0005, reviewed=True), Location(latitude=48.02, longitude=2.0055, reviewed=True)]
    small = engine.matrix(p, van(), 'TRUCK_STANDARD', Deadline.after(20))
    assert small.durations[0][1] is not None and small.durations[1][0] is not None
    heavy = van().model_copy(update={'gross_weight_kg': 19000})
    blocked = engine.matrix(p, heavy, 'TRUCK_STANDARD', Deadline.after(20))
    assert blocked.durations[0][1] is None and blocked.durations[1][0] is None


def test_actual_directed_oneway_and_hgv_access():
    engine = restrictions()
    p = [points()[0], Location(latitude=48.0035, longitude=2, reviewed=True)]
    matrix = engine.matrix(p, van(), 'TRUCK_STANDARD', Deadline.after(20))
    assert matrix.durations[0][1] is not None and matrix.durations[1][0] is None
    p[1] = Location(latitude=47.9945, longitude=2, reviewed=True)
    matrix = engine.matrix(p, van(), 'TRUCK_STANDARD', Deadline.after(20))
    assert matrix.durations[0][1] is None and matrix.durations[1][0] is None


@pytest.mark.parametrize('profile_index',[0,1,2])
@pytest.mark.parametrize('concurrency',[1,4])
def test_real_rectangular_batches_match_single_matrix(profile_index,concurrency):
    model=PlanningInput.model_validate_json(FIXTURE.read_text())
    truck=model.vehicles[profile_index%2].data.routing.model_copy(deep=True)
    if profile_index==2:truck.height_mm=3000;truck.gross_weight_kg=7500
    points=[model.depot.data.location]+[o.data.location for o in model.orders]
    url=os.environ.get('VALHALLA_URL','http://host.docker.internal:8002')
    single=Valhalla(url,'regional').matrix(points,truck,'TRUCK_STANDARD',Deadline.after(30))
    batched=Valhalla(url,'regional',matrix_batch_size=4,matrix_concurrency=concurrency).matrix(points,truck,'TRUCK_STANDARD',Deadline.after(30))
    assert batched==single


def test_real_batches_preserve_oneway_and_profile_unreachability():
    url=os.environ.get('RESTRICTIONS_URL','http://host.docker.internal:8003')
    p=[*points(),Location(latitude=48.0035,longitude=2,reviewed=True)]
    for truck in [van(),van().model_copy(update={'height_mm':3600})]:
        single=Valhalla(url,'controlled').matrix(p,truck,'TRUCK_STANDARD',Deadline.after(20))
        batched=Valhalla(url,'controlled',matrix_batch_size=2,matrix_concurrency=4).matrix(p,truck,'TRUCK_STANDARD',Deadline.after(20))
        assert batched==single
        assert single.durations[2][0] is None


@pytest.mark.parametrize('concurrency',[1,4])
def test_real_window_pruning_preserves_usable_arcs_and_truck_restrictions(concurrency):
    url=os.environ.get('RESTRICTIONS_URL','http://host.docker.internal:8003')
    pins=[Location(latitude=48,longitude=lon,reviewed=True)
          for lon in [2.0002,2.0005,2.0015,2.0025,2.0035,2.0045,2.0055]]
    bounds=[None,*[(43320,57600) if i%2 else (28920,43200) for i in range(1,7)]]
    engine=Valhalla(url,'controlled',matrix_batch_size=2,matrix_concurrency=concurrency)
    for truck in [van(),van().model_copy(update={'height_mm':3600})]:
        reference=engine.matrix(pins,truck,'TRUCK_STANDARD',Deadline.after(20))
        pruned=engine.matrix(pins,truck,'TRUCK_STANDARD',Deadline.after(20),time_bounds=bounds)
        for i in range(7):
            for j in range(7):
                excluded=i%2 and j>0 and j%2==0
                assert pruned.durations[i][j]==(None if excluded else reference.durations[i][j])
                assert pruned.distances[i][j]==(None if excluded else reference.distances[i][j])


def test_regional_refrigerated_delivery_keeps_road_and_equipment_conflict_visible():
    from benchmarks.regional import regional_input
    model=regional_input(PlanningInput.model_validate_json(FIXTURE.read_text()))
    model.vehicles=model.vehicles[:3];model.orders=model.orders[:1]
    model.solver_time_limit_seconds=1
    assert model.orders[0].data.required_skills==['REFRIGERATED']
    assert [v.data.fleet_number for v in model.vehicles if 'REFRIGERATED' in v.data.skills]==['REGION-01']
    engine=Valhalla(os.environ.get('VALHALLA_URL','http://host.docker.internal:8002'),'regional',matrix_concurrency=4)
    p=[model.depot.data.location,model.orders[0].data.location]
    heavy=engine.matrix(p,model.vehicles[0].data.routing,model.routing_policy,Deadline.after(20))
    medium=engine.matrix(p,model.vehicles[1].data.routing,model.routing_policy,Deadline.after(20))
    assert heavy.durations[0][1] is None and medium.durations[0][1] is not None
    result=calculate(model,engine,deadline=Deadline.after(20))
    assert result.validation=='PASS' and not result.routes and not result.operational_ready
    assert [(u.order_id,u.reason) for u in result.unassigned]==[(model.orders[0].id,'NO_ROUTE_IN_DATASET')]


@pytest.mark.parametrize('order_number',[172,173,180,193,198,250])
def test_regional_second_pass_restores_destination_access(order_number):
    from types import SimpleNamespace
    from benchmarks.regional import regional_input
    from benchmarks.feasibility import diagnose
    model=regional_input(PlanningInput.model_validate_json(FIXTURE.read_text()))
    model.vehicles=model.vehicles[:3]
    result=SimpleNamespace(unassigned=[SimpleNamespace(order_id=model.orders[order_number-1].id,reason='NO_ROUTE_IN_DATASET')])
    engine=Valhalla(os.environ.get('VALHALLA_URL','http://host.docker.internal:8002'),'regional')
    report=diagnose(model,result,engine,Deadline.after(30))[0]
    for profile in report['profiles']:
        assert profile['roads']['outward_route']['found'] and profile['roads']['return_route']['found']
        assert profile['roads']['matrix_outward_found'] and profile['roads']['matrix_return_found']
        assert all('MATRIX_ROUTE_DISAGREEMENT' not in v['blockers'] for v in profile['vehicles'])


@pytest.mark.parametrize('order_number,heavy_blocker',[(1,'NO_OUTWARD_PATH_IN_DATASET'),(244,'DRIVING_LIMIT')])
def test_regional_diagnostic_distinguishes_equipment_and_driving_limit(order_number,heavy_blocker):
    from types import SimpleNamespace
    from benchmarks.regional import regional_input
    from benchmarks.feasibility import diagnose
    model=regional_input(PlanningInput.model_validate_json(FIXTURE.read_text()))
    model.vehicles=model.vehicles[:3]
    result=SimpleNamespace(unassigned=[SimpleNamespace(order_id=model.orders[order_number-1].id,reason='SOLVER_UNASSIGNED')])
    engine=Valhalla(os.environ.get('VALHALLA_URL','http://host.docker.internal:8002'),'regional')
    report=diagnose(model,result,engine,Deadline.after(30))[0]
    heavy,medium,van=report['profiles']
    assert heavy_blocker in heavy['vehicles'][0]['blockers']
    assert medium['vehicles'][0]['blockers']==van['vehicles'][0]['blockers']==['EQUIPMENT:REFRIGERATED']
    assert not report['single_delivery_candidates']


def test_40_point_batches_match_real_41_point_matrix():
    model=PlanningInput.model_validate_json(FIXTURE.read_text())
    anchors=[model.depot.data.location]+[o.data.location for o in model.orders]
    # Distinct synthetic pins clustered within a metre of reviewed seed locations.
    p=[point.model_copy(update={'latitude':point.latitude+offset/1_000_000})
       for offset in range(4) for point in anchors][:41]
    engine=Valhalla(os.environ.get('VALHALLA_URL','http://host.docker.internal:8002'),'regional',matrix_batch_size=40)
    truck=model.vehicles[0].data.routing
    locations=[engine.point(point) for point in p]
    raw=engine.post('sources_to_targets',{**engine.base(truck,model.routing_policy),'verbose':False,
                    'sources':locations,'targets':locations},Deadline.after(30))
    assert engine.matrix(p,truck,model.routing_policy,Deadline.after(30))==parse_matrix(raw,41)


def test_default_48_point_boundary_matches_every_raw_directed_tile():
    model=PlanningInput.model_validate_json(FIXTURE.read_text())
    anchors=[model.depot.data.location]+[o.data.location for o in model.orders]
    pins=[point.model_copy(update={'latitude':point.latitude+offset/1_000_000})
          for offset in range(5) for point in anchors][:49]
    engine=Valhalla(os.environ.get('VALHALLA_URL','http://host.docker.internal:8002'),'regional')
    truck=model.vehicles[0].data.routing
    actual=engine.matrix(pins,truck,model.routing_policy,Deadline.after(30))
    # This 49-point case produces different estimates when requested as one
    # matrix even at the pinned engine's original 2800-iteration setting. Verify
    # exact engine cells for all four rectangular requests, without a tolerance
    # or treating one heuristic request grouping as a shortest-path oracle.
    for start,end,left,right in [(0,48,0,48),(0,48,48,49),(48,49,0,48),(48,49,48,49)]:
        raw=engine.post('sources_to_targets',{**engine.base(truck,model.routing_policy),'verbose':False,
            'sources':[engine.point(p) for p in pins[start:end]],
            'targets':[engine.point(p) for p in pins[left:right]]},Deadline.after(20))
        expected=parse_matrix(raw,end-start,right-left,source_offset=start,target_offset=left)
        assert [row[left:right] for row in actual.durations[start:end]]==expected.durations
        assert [row[left:right] for row in actual.distances[start:end]]==expected.distances


@pytest.mark.parametrize('profile_index',[0,1,2])
def test_nearest_candidates_keep_matrix_and_road_times_consistent(profile_index):
    from benchmarks.scaling import scaled_input
    model=scaled_input(PlanningInput.model_validate_json(FIXTURE.read_text()))
    engine=Valhalla(os.environ.get('VALHALLA_URL','http://host.docker.internal:8002'),'regional')
    pins=[model.depot.data.location]+[o.data.location for o in model.orders]
    truck=model.vehicles[profile_index].data.routing
    # Original 250-pin regression: radius 50 estimated 4/61 seconds where final
    # routes took 299/201 seconds; another baseline arc was overestimated by 106 s.
    for a,b in [(34,30),(144,135),(87,88)]:
        matrix=engine.matrix([pins[a],pins[b]],truck,model.routing_policy,Deadline.after(20))
        for i,j in [(0,1),(1,0)]:
            leg=engine.leg(pins[[a,b][i]],pins[[a,b][j]],truck,model.routing_policy,Deadline.after(20))
            assert abs(matrix.durations[i][j]-leg.duration_seconds)<=1
            assert abs(matrix.distances[i][j]-leg.distance_m)<=2


def test_regional_complex_restriction_connections_are_recalculated_from_roads():
    # Reproduced during the 250-delivery probe on the pinned regional graph.
    # A single-point request also returns a loop, independently of batching.
    engine=Valhalla(os.environ.get('VALHALLA_URL','http://host.docker.internal:8002'),'regional')
    p=Location(latitude=48.843178,longitude=2.383811,reviewed=True)
    raw=engine.post('sources_to_targets',{**engine.base(van(),'TRUCK_STANDARD'),'verbose':False,
                    'sources':[engine.point(p)],'targets':[engine.point(p)]},Deadline.after(20))
    assert raw['sources_to_targets']['durations'][0][0]>0
    pins=[p,Location(latitude=48.84281,longitude=2.384589,reviewed=True),
          Location(latitude=48.84246,longitude=2.38533,reviewed=True)]
    matrix=engine.matrix(pins,van(),'TRUCK_STANDARD',Deadline.after(20))
    for i,a in enumerate(pins):
        for j,b in enumerate(pins):
            leg=engine.leg(a,b,van(),'TRUCK_STANDARD',Deadline.after(20))
            assert matrix.durations[i][j]==leg.duration_seconds
            assert matrix.distances[i][j]==leg.distance_m
    assert matrix.durations[0][1]<raw['sources_to_targets']['durations'][0][0]


@pytest.mark.parametrize('objective',['TRAVEL_TIME','MODELED_TRAVEL_COST'])
def test_recovered_complex_restriction_pins_produce_valid_full_coverage(objective):
    data=json.loads(FIXTURE.read_text());data['objective']=objective
    data['orders']=data['orders'][:3]
    for order,(lat,lon) in zip(data['orders'],[(48.843178,2.383811),(48.84281,2.384589),(48.84246,2.38533)]):
        order['data']['location']={'latitude':lat,'longitude':lon,'reviewed':True}
    model=PlanningInput.model_validate(data)
    engine=Valhalla(os.environ.get('VALHALLA_URL','http://host.docker.internal:8002'),'regional')
    result=calculate(model,engine)
    assert result.validation=='PASS',result.blocking_issues
    assert not result.unassigned and sum(len(r.stops) for r in result.routes)==3


def controlled_input(tall_only=False):
    data = small_input()
    data['depot']['data']['location'] = points()[0].model_dump()
    data['orders'][0]['data']['location'] = points()[1].model_dump()
    data['vehicles'][0]['data']['routing'] = {**van().model_dump(), 'height_mm':3600}
    data['vehicles'][0]['data']['cost'].update(fixed_minor=0, per_km_minor=0, per_hour_minor=0)
    data['objective'] = 'MODELED_TRAVEL_COST'
    if not tall_only:
        other = deepcopy(data['vehicles'][0]); other['id'] = str(uuid4()); other['data']['fleet_number'] = 'SHORT'
        other['data']['routing'] = van().model_dump(); data['vehicles'].append(other)
    return PlanningInput.model_validate(data)


def test_actual_mixed_profiles_and_all_unassigned_with_zero_rates():
    model = controlled_input()
    result = calculate(model, restrictions())
    assert result.validation == 'PASS' and not result.unassigned
    assert len(result.routes) == 1 and result.routes[0].vehicle_id == model.vehicles[1].id
    blocked = calculate(controlled_input(tall_only=True), restrictions())
    assert not blocked.routes and blocked.unassigned[0].reason == 'NO_ROUTE_IN_DATASET'
    assert blocked.status == 'DRAFT' and blocked.operational_ready is False


def test_engine_http_errors_and_missing_graph_are_not_unassignment():
    p = [points()[0], Location(latitude=48, longitude=2.022, reviewed=True)]
    island = restrictions().matrix(p, van(), 'TRUCK_STANDARD', Deadline.after(10))
    assert island.durations[0][1] is None and island.durations[1][0] is None
    p[1] = Location(latitude=0, longitude=0, reviewed=True)
    with pytest.raises(EngineError, match=r'HTTP 400, engine error \d+'):
        restrictions().matrix(p, van(), 'TRUCK_STANDARD', Deadline.after(10))
    with pytest.raises(EngineError):
        Valhalla('http://127.0.0.1:9', 'missing').matrix(points(), van(), 'TRUCK_STANDARD', Deadline.after(3))


@pytest.mark.parametrize('dimension', DIMENSIONS)
def test_vroom_enforces_every_capacity_dimension(dimension):
    data = small_input(); data['orders'][0]['data']['demand'][dimension] = 11
    mapping = mapped(data)
    routes, unassigned = decode_solution(run_vroom(mapping.payload, 1, Deadline.after(5)), mapping)
    assert not routes and len(unassigned) == 1


@pytest.mark.parametrize('constraint', ['skills', 'window', 'distance', 'driving', 'stops'])
def test_vroom_hard_windows_equipment_and_route_limits(constraint):
    data = small_input()
    vehicle, order = data['vehicles'][0]['data'], data['orders'][0]['data']
    if constraint == 'skills': order['required_skills'] = ['MISSING']
    if constraint == 'window': vehicle['available_window']['end'] = '2026-09-23T08:00:00+02:00'
    if constraint == 'distance': vehicle['limits'] = {'max_distance_m':299}
    if constraint == 'driving': vehicle['limits'] = {'max_driving_seconds':29}
    if constraint == 'stops':
        other = deepcopy(data['orders'][0]); other['id'] = str(uuid4()); other['data']['external_id'] = 'SECOND'
        data['orders'].append(other); vehicle['limits'] = {'max_stops':1}
    model = PlanningInput.model_validate(data)
    mapping = prepare(model, [model.depot.data.location, model.orders[0].data.location],
                      [model.vehicles[0].data.routing], [Matrix([[0,10],[20,0]], [[0,100],[200,0]])])
    routes, unassigned = decode_solution(run_vroom(mapping.payload, 1, Deadline.after(5)), mapping)
    assert len(unassigned) == 1
    if constraint != 'stops': assert not routes


def test_both_objectives_use_native_fixed_distance_and_driving_costs():
    data = small_input()
    other = deepcopy(data['vehicles'][0]); other['id'] = str(uuid4()); other['data']['fleet_number'] = 'SECOND'
    other['data']['routing']['height_mm'] = 2700; data['vehicles'].append(other)
    matrices = [Matrix([[0,10],[10,0]], [[0,10000],[10000,0]]), Matrix([[0,50],[50,0]], [[0,100],[100,0]])]
    for objective, fixed, hourly, per_km, expected in [('TRAVEL_TIME',0,0,100,0),
            ('MODELED_TRAVEL_COST',0,0,100,1), ('MODELED_TRAVEL_COST',0,3600,0,0),
            ('MODELED_TRAVEL_COST',10000,3600,0,1)]:
        data['objective'] = objective
        for i,v in enumerate(data['vehicles']):
            v['data']['cost'].update(fixed_minor=fixed if i==0 else 0, per_hour_minor=hourly, per_km_minor=per_km)
        model = PlanningInput.model_validate(data)
        mapping = prepare(model, [model.depot.data.location, model.orders[0].data.location],
                          [v.data.routing for v in model.vehicles], matrices)
        routes, unassigned = decode_solution(run_vroom(mapping.payload, 1, Deadline.after(5)), mapping)
        assert not unassigned and routes[0][0] == model.vehicles[expected].id


@pytest.fixture(scope='module')
def regional_result():
    model = PlanningInput.model_validate_json(FIXTURE.read_text())
    engine = Valhalla(os.environ.get('VALHALLA_URL', 'http://host.docker.internal:8002'), 'Geofabrik Ile-de-France 2026-09-22')
    return model, engine, calculate(model, engine)


def test_real_regional_ten_deliveries_both_objectives(regional_result):
    model, engine, result = regional_result
    for objective in ['TRAVEL_TIME', 'MODELED_TRAVEL_COST']:
        payload = model.model_dump(mode='json'); payload['objective'] = objective
        candidate = result if objective == 'TRAVEL_TIME' else calculate(PlanningInput.model_validate(payload), engine)
        assert candidate.validation == 'PASS', candidate.blocking_issues
        assert len(candidate.routes) == 2 and sum(len(r.stops) for r in candidate.routes) == 10
        assert not candidate.unassigned and not candidate.operational_ready
        assert any(len(leg.geometry)>2 for route in candidate.routes for leg in route.legs)
    for changes in [{'status':'READY'}, {'operational_ready':True}, {'data_kind':'ROUTED'}, {'status':'READY','operational_ready':True}]:
        with pytest.raises(ValidationError): SyntheticDraft.model_validate({**result.model_dump(), **changes})


def test_manual_recalculation_preserves_sequence_and_detects_hard_violations(regional_result,monkeypatch):
    model,engine,result=regional_result
    edit=EditRequest(routes=[{'vehicle_id':r.vehicle_id,'order_ids':[s.order_id for s in r.stops]} for r in result.routes],unassigned_order_ids=[])
    def forbidden(*args,**kwargs):raise AssertionError('Manual edits must never invoke matrices or the optimizer')
    monkeypatch.setattr('app.spike.run_vroom',forbidden)
    monkeypatch.setattr(engine,'matrix',forbidden)
    rebuilt=reroute(model,edit,engine,deadline=Deadline.after(40))
    assert rebuilt.validation=='PASS',rebuilt.blocking_issues
    assert {r.vehicle_id:[s.order_id for s in r.stops] for r in rebuilt.routes}=={r.vehicle_id:r.order_ids for r in edit.routes}
    overloaded=EditRequest(routes=[{'vehicle_id':model.vehicles[0].id,'order_ids':[o.id for o in model.orders]}],unassigned_order_ids=[])
    invalid=reroute(model,overloaded,engine,deadline=Deadline.after(40))
    assert invalid.validation=='FAIL' and not invalid.operational_ready
    assert any(s.startswith(('CAPACITY','SKILLS','LIMIT')) for s in invalid.blocking_issues)

    def no_road(*args,**kwargs):raise EngineError('No usable road path')
    monkeypatch.setattr(engine,'leg',no_road)
    with pytest.raises(EngineError,match='No usable road path'):
        reroute(model,edit,engine,deadline=Deadline.after(40))


def test_manual_late_sequence_remains_a_fixable_window_violation():
    model=PlanningInput.model_validate_json(FIXTURE.read_text())
    model.orders=model.orders[:2]+model.orders[3:8]  # Seven distinct non-depot pins.
    model.vehicles=model.vehicles[:1]
    model.vehicles[0].data.limits.max_stops=None
    for i,order in enumerate(model.orders):
        window=order.data.time_window
        window.start=window.start.replace(hour=8 if i<4 else 12)
        window.end=window.end.replace(hour=12 if i<4 else 16)
    late=sorted(model.orders[4:],key=lambda o:coordinates(o.data.location))
    early=sorted(model.orders[:4],key=lambda o:coordinates(o.data.location))
    edit=EditRequest(routes=[{'vehicle_id':model.vehicles[0].id,'order_ids':[o.id for o in late+early]}],unassigned_order_ids=[])
    engine=Valhalla(os.environ.get('VALHALLA_URL','http://host.docker.internal:8002'),'regional',matrix_batch_size=2)
    result=reroute(model,edit,engine,deadline=Deadline.after(30))
    assert result.validation=='FAIL' and any(issue.startswith('WINDOW:') for issue in result.blocking_issues)
    assert [s.order_id for s in result.routes[0].stops]==[o.id for o in late+early]


def test_independent_validator_rejects_injected_violations(regional_result):
    model, engine, result = regional_result
    p = [model.depot.data.location]+[o.data.location for o in model.orders]
    profiles = [v.data.routing for v in model.vehicles]
    matrices = [engine.matrix(p, profile, model.routing_policy, Deadline.after(20)) for profile in profiles]
    assert validate(model, result, p, profiles, matrices) == []
    for kind in ['coverage','profile','timing','load','distance','cost','geometry','ownership','return','provenance']:
        bad = result.model_copy(deep=True)
        route = bad.routes[0]
        if kind == 'coverage': bad.unassigned.append(Unassigned(order_id=route.stops[0].order_id,reason='SOLVER_UNASSIGNED'))
        if kind == 'profile': route.profile.height_mm += 1
        if kind == 'timing': route.stops[0].arrival += 1
        if kind == 'load': route.stops[0].load_after[0] += 1
        if kind == 'distance': route.distance_m += 1
        if kind == 'cost': route.known_cost_minor += 1
        if kind == 'geometry': route.legs[0].geometry = [(0,0),(1,1)]
        if kind == 'ownership': bad.organization_id = uuid4()
        if kind == 'provenance': bad.data_kind = 'ROUTED'
        if kind == 'return': route.legs[-1].duration_seconds += 90000
        assert validate(model, bad, p, profiles, matrices), kind
    forbidden = deepcopy(matrices)
    route = result.routes[0]
    lookup = {o.id:o.data.location for o in model.orders}
    index = {coordinates(point):i for i,point in enumerate(p)}
    forbidden[profiles.index(route.profile)].durations[0][index[coordinates(lookup[route.stops[0].order_id])]] = None
    assert any('FORBIDDEN_ARC' in s for s in validate(model, result, p, profiles, forbidden))
    # Each load dimension is checked again independently of what VROOM accepted.
    for dimension in DIMENSIONS:
        bad_inputs = model.model_copy(deep=True)
        owner = next(v for v in bad_inputs.vehicles if v.id == result.routes[0].vehicle_id)
        setattr(owner.data.capacity, dimension, 0)
        # Industrial-pallet baseline is zero; inject actual demand into the source copy.
        first = next(o for o in bad_inputs.orders if o.id == result.routes[0].stops[0].order_id)
        setattr(first.data.demand, dimension, max(1,getattr(first.data.demand,dimension)))
        assert any('CAPACITY' in s for s in validate(bad_inputs, result, p, profiles, matrices))


def test_off_road_pin_is_explicitly_unassigned_and_named_in_manual_edits():
    # The Bois de Boulogne point has no truck-usable road within the 50 m snap
    # limit in the pinned extract; one such pin used to fail the entire matrix.
    model = PlanningInput.model_validate_json(FIXTURE.read_text())
    extra = model.orders[0].model_copy(deep=True)
    extra.id = uuid4(); extra.data.external_id = 'OFF-ROAD-PIN'
    extra.data.location = Location(latitude=48.8466, longitude=2.2522, reviewed=True)
    model.orders.append(extra)
    engine = Valhalla(os.environ.get('VALHALLA_URL', 'http://host.docker.internal:8002'), 'regional')
    assert engine.unsnappable([model.depot.data.location, extra.data.location], model.vehicles[0].data.routing,
                              model.routing_policy, Deadline.after(10)) == [1]
    result = calculate(model, engine, deadline=Deadline.after(90))
    assert {u.order_id: u.reason for u in result.unassigned}[extra.id] == 'NO_ROAD_NEAR_PIN'
    assert result.validation == 'PASS', result.blocking_issues
    others = [o.id for o in model.orders if o.id != extra.id]
    edit = EditRequest(routes=[{'vehicle_id': model.vehicles[0].id, 'order_ids': [extra.id]}], unassigned_order_ids=others)
    with pytest.raises(EngineError, match='TRUCK-DEMO-01 .*delivery OFF-ROAD-PIN'):
        reroute(model, edit, engine, deadline=Deadline.after(30))


def test_routes_start_and_end_at_their_own_depots_and_places_on_real_roads():
    """Two depots and a driver's home on the regional graph: each route leaves from and returns to its
    vehicle's own places, and deliveries shipping from a depot ride only a vehicle starting there."""
    from app.edits import EditRequest, RouteAssignment
    from app.routing import meters
    data = json.loads(FIXTURE.read_text())
    north, home = data['orders'][8]['data']['location'], data['orders'][9]['data']['location']
    data['orders'] = data['orders'][:8]
    data['depot']['main'] = True
    data['depots'] = [{'id': str(uuid4()), 'organization_id': data['organization_id'], 'version': 1, 'main': False,
                       'data': {'code': code, 'name': code.title(), 'kind': kind, 'address': '', 'location': point,
                                'timezone': 'Europe/Paris'}}
                      for code, kind, point in [('NORD', 'DEPOT', north), ('HOME', 'PLACE', home)]]
    data['vehicles'][0]['data']['start'] = 'NORD'
    data['vehicles'][1]['data'].update(start='HOME', end=data['depot']['data']['code'])
    for order in data['orders'][:3]:
        order['data']['ships_from'] = 'NORD'
    model = PlanningInput.model_validate(data)
    engine = Valhalla(os.environ.get('VALHALLA_URL', 'http://host.docker.internal:8002'), 'regional')
    result = calculate(model, engine)
    assert result.validation == 'PASS', result.blocking_issues
    assert len(result.routes) == 2
    for route in result.routes:
        vehicle = next(v.data for v in model.vehicles if v.id == route.vehicle_id)
        assert meters(coordinates(model.start_of(vehicle).data.location), route.legs[0].geometry[0]) <= 50
        assert meters(coordinates(model.end_of(vehicle).data.location), route.legs[-1].geometry[-1]) <= 50
    shipped, truck = {o.id for o in model.orders[:3]}, model.vehicles[0].id
    assert not [s for r in result.routes if r.vehicle_id != truck for s in r.stops if s.order_id in shipped]
    # A manual edit keeps the same sequences and recalculates the same ends on real roads.
    edit = EditRequest(routes=[RouteAssignment(vehicle_id=r.vehicle_id, order_ids=[s.order_id for s in r.stops])
                               for r in result.routes], unassigned_order_ids=[u.order_id for u in result.unassigned])
    edited = reroute(model, edit, engine, deadline=Deadline.after(60))
    assert edited.validation == 'PASS', edited.blocking_issues
