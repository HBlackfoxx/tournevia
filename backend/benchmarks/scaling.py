"""Fixed synthetic 25/250/3 workload and offline domain/matrix probes."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time
from uuid import UUID

from app.planning import PlanningInput
from app.routing import Deadline, EngineError, Valhalla
from benchmarks.cases import FIXTURE


def scaled_input(seed: PlanningInput) -> PlanningInput:
    # Freeze the original service pins so routing changes cannot alter the workload.
    points=[tuple(p) for p in json.loads(Path(__file__).with_name('paris-250-points.json').read_text())['coordinates']]
    depot=(seed.depot.data.location.longitude,seed.depot.data.location.latitude)
    if len(points)!=250 or len(set(points))!=250 or depot in points:
        raise ValueError('The fixed workload requires 250 distinct service pins excluding the depot.')
    data=seed.model_dump(mode='json');data['vehicles']=[];data['orders']=[]
    for i in range(25):
        vehicle=deepcopy(seed.vehicles[0 if i%3==0 else 1].model_dump(mode='json'))
        vehicle['id']=str(UUID(int=1000+i));v=vehicle['data']
        v.update(fleet_number=f'SCALE-{i+1:02}',skills=[],limits={'max_stops':10})
        if i%3==2:
            v['routing'].update(height_mm=3000,width_mm=2300,length_mm=7200,gross_weight_kg=7500,axle_load_kg=4000)
        data['vehicles'].append(vehicle)
    for i,(lon,lat) in enumerate(points):
        order=deepcopy(seed.orders[0].model_dump(mode='json'));order['id']=str(UUID(int=2000+i))
        order['data'].update(external_id=f'SCALE-ORDER-{i+1:03}',address='Synthetic road-geometry sample',
                             instructions='',required_skills=[],priority=0,service_seconds=30,
                             demand={'weight_kg':20,'volume_dm3':100,'pallet_eur_units':0,'pallet_ind_units':0,'loading_length_mm':100},
                             location={'longitude':lon,'latitude':lat,'reviewed':True})
        data['orders'].append(order)
    return PlanningInput.model_validate(data)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--valhalla-url',default='http://host.docker.internal:8002')
    parser.add_argument('--dataset',default='Geofabrik Ile-de-France downloaded 2026-09-22; static OSM speeds')
    args=parser.parse_args()
    engine=Valhalla(args.valhalla_url,args.dataset)
    report={'data_kind':'SYNTHETIC','dataset':args.dataset,'scope':'25/250/3 matrices only; fixed synthetic service pins, not capacity acceptance.',
            'status':'FAILED','completed_profiles':0}
    started=time.perf_counter()
    try:
        inputs=scaled_input(PlanningInput.model_validate_json(FIXTURE.read_text()))
        points=[inputs.depot.data.location]+[o.data.location for o in inputs.orders]
        profiles=list({v.data.routing.model_dump_json():v.data.routing for v in inputs.vehicles}.values())
        report['workload']={'vehicles':len(inputs.vehicles),'orders':len(inputs.orders),
                            'distinct_points':len({(p.longitude,p.latitude) for p in points}),'profiles':len(profiles)}
        deadline=Deadline.after(120)
        for profile in profiles:
            engine.matrix(points,profile,inputs.routing_policy,deadline)
            report['completed_profiles']+=1
        report['status']='MATRICES_PASS'
    except (EngineError,ValueError) as error:
        report['error']=str(error)
    report['total_seconds']=time.perf_counter()-started
    print(json.dumps(report,indent=2))
    if report['status']=='FAILED':raise SystemExit(1)


if __name__=='__main__':main()
