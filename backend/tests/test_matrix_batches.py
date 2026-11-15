import pytest

from app.fleet import Location
from app.planning import Leg
from app.routing import Deadline, EngineError, Valhalla, parse_matrix
from benchmarks.cases import tiny_case


def test_rectangular_parser_uses_global_diagonal_not_block_diagonal():
    data={'units':'kilometers','sources_to_targets':{'durations':[[3,None,5],[6,7,8]],'distances':[[.003,None,.005],[.006,.007,.008]]}}
    assert parse_matrix(data,2,3,source_offset=0,target_offset=10).durations==[[3,None,5],[6,7,8]]
    with pytest.raises(EngineError):parse_matrix(data,2,3,source_offset=10,target_offset=10)
    with pytest.raises(EngineError):parse_matrix(data,2,2,source_offset=0,target_offset=10)


@pytest.mark.parametrize('count',[95,251])
@pytest.mark.parametrize('concurrency',[1,4])
def test_tiles_preserve_every_directed_cell_profile_policy_and_null(monkeypatch,count,concurrency):
    engine=Valhalla('http://unused','synthetic',matrix_concurrency=concurrency)
    points=[Location(latitude=48+i/10000,longitude=2,reviewed=True) for i in range(count)]
    truck=tiny_case('directed','TRAVEL_TIME')[0].vehicles[0].data.routing
    calls=[];deadline=Deadline.after(10)
    def post(action,payload,received):
        assert action=='sources_to_targets' and received is deadline
        assert payload['costing_options']['truck']['height']==truck.height_mm/1000
        assert payload['costing_options']['truck']['use_tolls']==0
        sources=[round((p['lat']-48)*10000) for p in payload['sources']]
        targets=[round((p['lat']-48)*10000) for p in payload['targets']]
        calls.append((len(sources),len(targets)))
        times=[[0 if a==b else None if (a,b)==(49,2) else 100*a+b for b in targets] for a in sources]
        return {'units':'kilometers','sources_to_targets':{'durations':times,'distances':times}}
    monkeypatch.setattr(engine,'post',post)
    result=engine.matrix(points,truck,'TRUCK_PREFER_NO_TOLLS',deadline)
    parts=(count+47)//48
    low,high=count//parts,(count+parts-1)//parts
    assert len(calls)==parts**2 and (low,low) in calls
    assert (high,low) in calls and (low,high) in calls
    assert all(low<=a<=high and low<=b<=high for a,b in calls)
    for a in range(count):
        for b in range(count):
            expected=0 if a==b else None if (a,b)==(49,2) else 100*a+b
            assert result.durations[a][b]==expected
            assert result.distances[a][b]==(None if expected is None else expected*1000)


@pytest.mark.parametrize('failure',['missing','unpaired','deadline','http'])
def test_late_tile_failure_aborts_whole_matrix(monkeypatch,failure):
    inputs,_=tiny_case('directed','TRAVEL_TIME')
    engine=Valhalla('http://unused','synthetic',matrix_batch_size=2)
    points=[inputs.depot.data.location]+[o.data.location for o in inputs.orders]
    calls=0;deadline=Deadline.after(10)
    def post(*args):
        nonlocal calls
        calls+=1
        if calls==2:
            if failure=='http':raise EngineError('HTTPError')
            if failure=='missing':return {}
            if failure=='deadline':deadline.end=0
            if failure=='unpaired':return {'units':'kilometers','sources_to_targets':{'durations':[[None,1],[1,1]],'distances':[[0,1],[1,1]]}}
        return {'units':'kilometers','sources_to_targets':{'durations':[[0,1],[1,0]],'distances':[[0,1],[1,0]]}}
    monkeypatch.setattr(engine,'post',post)
    with pytest.raises(EngineError):engine.matrix(points,inputs.vehicles[0].data.routing,'TRUCK_STANDARD',deadline)
    assert calls==2


def test_matrix_limit_rejected_before_request(monkeypatch):
    engine=Valhalla('http://unused','synthetic')
    inputs,_=tiny_case('directed','TRAVEL_TIME')
    def forbidden(*args):raise AssertionError('No engine call expected')
    monkeypatch.setattr(engine,'post',forbidden)
    with pytest.raises(EngineError):engine.matrix([inputs.depot.data.location]*302,inputs.vehicles[0].data.routing,'TRUCK_STANDARD',Deadline.after(1))


@pytest.mark.parametrize('failure',[None,'http','malformed','deadline'])
def test_parallel_waves_do_not_overlap_and_drain_on_failure(monkeypatch,failure):
    import threading
    import time
    engine=Valhalla('http://unused','synthetic',matrix_batch_size=2,matrix_concurrency=4)
    inputs,_=tiny_case('directed','TRAVEL_TIME')
    points=[Location(latitude=48+i/10000,longitude=2,reviewed=True) for i in range(7)]
    barrier=threading.Barrier(4);others_done=threading.Event();replacement_started=threading.Event();lock=threading.Lock()
    active=peak=calls=other_count=0;completed=[];deadline=Deadline.after(5)
    def post(action,payload,received):
        nonlocal active,peak,calls,other_count
        assert received is deadline
        sources=[round((p['lat']-48)*10000) for p in payload['sources']]
        targets=[round((p['lat']-48)*10000) for p in payload['targets']]
        with lock:
            calls+=1;number=calls;active+=1;peak=max(peak,active)
        try:
            if number<=4:
                barrier.wait(timeout=2)
                if sources[0]==targets[0]==0:
                    if failure=='http':raise EngineError('test HTTP failure')
                    if failure=='malformed':return {}
                    if failure=='deadline':deadline.end=0
                    # Other slots are free, but feeding another request now can
                    # queue it behind this slow request in the routing service.
                    assert others_done.wait(2)
                    assert not replacement_started.wait(.1)
                else:
                    if failure:time.sleep(.03)
                    with lock:
                        other_count+=1
                        if other_count==3:others_done.set()
            elif number==5:
                replacement_started.set()
            times=[[0 if a==b else 100*a+b for b in targets] for a in sources]
            completed.append((sources[0],targets[0]))
            return {'units':'kilometers','sources_to_targets':{'durations':times,'distances':times}}
        finally:
            with lock:active-=1
    monkeypatch.setattr(engine,'post',post)
    if failure:
        with pytest.raises(EngineError):engine.matrix(points,inputs.vehicles[0].data.routing,'TRUCK_STANDARD',deadline)
        assert calls==4  # No replacement work after first tile fails.
    else:
        result=engine.matrix(points,inputs.vehicles[0].data.routing,'TRUCK_STANDARD',deadline)
        assert completed.index((0,0))>=3
        assert result.durations==[[0 if a==b else 100*a+b for b in range(7)] for a in range(7)]
    assert peak==4 and active==0


@pytest.mark.parametrize('concurrency',[0,5,True,1.5,None])
def test_matrix_concurrency_is_bounded(concurrency):
    with pytest.raises(ValueError,match='concurrency'):
        Valhalla('http://unused','synthetic',matrix_concurrency=concurrency)


@pytest.mark.parametrize('concurrency',[1,4])
@pytest.mark.parametrize('gap',[0,1])
def test_hard_window_tiles_keep_depot_equality_directed_nulls_and_original_indexes(monkeypatch,concurrency,gap):
    engine=Valhalla('http://unused','synthetic',matrix_batch_size=2,matrix_concurrency=concurrency)
    points=[Location(latitude=48+i/10000,longitude=2,reviewed=True) for i in range(7)]
    truck=tiny_case('directed','TRAVEL_TIME')[0].vehicles[0].data.routing
    # Input interleaves morning and afternoon; the result must keep these indexes.
    bounds=[None,*[(10+gap,20) if i%2 else (1,10) for i in range(1,7)]]
    calls=[]
    def post(action,payload,deadline):
        sources=[round((p['lat']-48)*10000) for p in payload['sources']]
        targets=[round((p['lat']-48)*10000) for p in payload['targets']]
        calls.extend((i,j) for i in sources for j in targets)
        times=[[0 if i==j else None if (i,j)==(2,1) else 100*i+j for j in targets] for i in sources]
        return {'units':'kilometers','sources_to_targets':{'durations':times,'distances':times}}
    monkeypatch.setattr(engine,'post',post)
    result=engine.matrix(points,truck,'TRUCK_STANDARD',Deadline.after(5),time_bounds=bounds)
    for i in range(7):
        for j in range(7):
            excluded=bool(gap and i%2 and j>0 and j%2==0)
            assert calls.count((i,j))==(0 if excluded else 1)
            expected=None if excluded or (i,j)==(2,1) else 0 if i==j else 100*i+j
            assert result.durations[i][j]==expected
            assert result.distances[i][j]==(None if expected is None else expected*1000)


def test_shared_point_timing_bounds_include_all_jointly_eligible_orders():
    from types import SimpleNamespace
    from datetime import timedelta
    from uuid import UUID
    from app.routing import Matrix,coordinates
    from app.spike import points_and_profiles,profile_matrices
    model,_=tiny_case('directed','TRAVEL_TIME')
    original=model.orders[0]
    extra=original.model_copy(deep=True);extra.id=UUID(int=9999)
    extra.data.external_id='SHARED-LATE'
    extra.data.time_window.start+=timedelta(hours=1)
    extra.data.time_window.end+=timedelta(hours=1)
    extra.data.service_seconds+=90
    model.orders.append(extra)
    points,profiles=points_and_profiles(model)
    calls=[]
    def matrix(selected,truck,policy,deadline,*,time_bounds):
        calls.append((selected,time_bounds))
        return Matrix([[0]*len(selected) for _ in selected],[[0]*len(selected) for _ in selected])
    profile_matrices(model,SimpleNamespace(matrix=matrix,unsnappable=lambda *a:[],snap_limit_m=50),points,profiles,Deadline.after(5))
    for selected,bounds in calls:
        assert bounds[0] is None
        i=next(i for i,p in enumerate(selected) if coordinates(p)==coordinates(original.data.location))
        assert bounds[i]==(model.seconds(original.data.time_window.start)+original.data.service_seconds,
                           model.seconds(extra.data.time_window.end))


def test_irregular_overlapping_windows_do_not_fragment_or_reorder_requests(monkeypatch):
    engine=Valhalla('http://unused','synthetic',matrix_batch_size=2)
    pins=[Location(latitude=48+i/10000,longitude=2,reviewed=True) for i in range(7)]
    truck=tiny_case('directed','TRAVEL_TIME')[0].vehicles[0].data.routing
    calls=[]
    def post(action,payload,deadline):
        a=[round((p['lat']-48)*10000) for p in payload['sources']]
        b=[round((p['lat']-48)*10000) for p in payload['targets']]
        calls.append((a,b))
        cells=[[0 if i==j else 100*i+j for j in b] for i in a]
        return {'units':'kilometers','sources_to_targets':{'durations':cells,'distances':cells}}
    monkeypatch.setattr(engine,'post',post)
    timed=engine.matrix(pins,truck,'TRUCK_STANDARD',Deadline.after(5),
                        time_bounds=[None,*[(1,100-i) for i in range(1,7)]])
    timed_calls=calls[:];calls.clear()
    plain=engine.matrix(pins,truck,'TRUCK_STANDARD',Deadline.after(5))
    assert timed==plain and timed_calls==calls and len(calls)==16


@pytest.mark.parametrize('value',[1,None])
def test_invalid_global_self_arc_reports_point_index(value):
    data={'units':'kilometers','sources_to_targets':{'durations':[[value]],'distances':[[value]]}}
    with pytest.raises(EngineError,match='self-arc at point 40'):
        parse_matrix(data,1,source_offset=40,target_offset=40)


@pytest.mark.parametrize('failure',[None,'nonzero','geometry','http','late_http','deadline','unreachable','malformed','algorithm'])
def test_self_arc_recovery_rebuilds_both_directions_or_rejects(monkeypatch,failure):
    inputs,_=tiny_case('directed','TRAVEL_TIME')
    points=[inputs.depot.data.location]+[o.data.location for o in inputs.orders[:2]]
    engine=Valhalla('http://unused','synthetic',matrix_batch_size=2)
    deadline=Deadline.after(10);calls=[]
    def post(action,payload,received):
        sources=[next(i for i,p in enumerate(points) if engine.point(p)==loc) for loc in payload['sources']]
        targets=[next(i for i,p in enumerate(points) if engine.point(p)==loc) for loc in payload['targets']]
        times=[[30 if (i,j)==(2,2) else 0 if i==j else None if (i,j)==(0,2) else 100+i*10+j
                for j in targets] for i in sources]
        if sources==[2] and targets==[2]:
            if failure=='unreachable':times=[[None]]
            if failure=='malformed':times=[['bad']]
        return {'algorithm':'unknown' if failure=='algorithm' else 'costmatrix','units':'kilometers',
                'sources_to_targets':{'durations':times,'distances':[row[:] for row in times]}}
    def leg(a,b,truck,policy,received,*ids):
        assert received is deadline and truck==inputs.vehicles[0].data.routing and policy=='TRUCK_STANDARD'
        i,j=points.index(a),points.index(b);calls.append((i,j))
        if failure=='http':raise EngineError('HTTPError')
        if failure=='late_http' and (i,j)==(1,2):raise EngineError('HTTPError after partial recovery')
        if failure=='deadline':deadline.end=0
        duration=0 if i==j else 10+i*3+j
        if failure=='nonzero':duration=1
        geometry=[(a.longitude,a.latitude),(b.longitude,b.latitude)]
        if failure=='geometry':geometry[-1]=(0,0)
        return Leg(from_order_id=None,to_order_id=None,duration_seconds=duration,distance_m=duration*10,geometry=geometry)
    monkeypatch.setattr(engine,'post',post);monkeypatch.setattr(engine,'leg',leg)
    if failure:
        with pytest.raises(EngineError):engine.matrix(points,inputs.vehicles[0].data.routing,'TRUCK_STANDARD',deadline)
    else:
        matrix=engine.matrix(points,inputs.vehicles[0].data.routing,'TRUCK_STANDARD',deadline)
        assert set(calls)=={(2,2),(1,2),(2,0),(2,1)}
        assert matrix.durations==[[0,101,None],[110,0,15],[16,17,0]]
        assert matrix.distances[0][2] is None and matrix.distances[2][0]==160
    if failure in {'unreachable','malformed','algorithm'}:assert not calls
