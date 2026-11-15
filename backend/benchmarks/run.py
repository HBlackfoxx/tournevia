"""Repeated real-road synthetic benchmarks. JSON metrics only; no route/matrix archive."""
import argparse
from datetime import datetime,timezone
from fractions import Fraction
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from app.edits import EditRequest,reroute
from app.planning import Draft,PlanningInput
from app.routing import Deadline,EngineError,Valhalla
from app.spike import calculate
from benchmarks.cases import FIXTURE


class TrialFailure(RuntimeError):
    def __init__(self, detail: dict):
        super().__init__(detail['error'])
        self.detail = detail


def metrics(inputs: PlanningInput, result: Draft) -> dict:
    served = {stop.order_id for route in result.routes for stop in route.stops}
    priority = {}
    for order in inputs.orders:
        bucket = priority.setdefault(str(order.data.priority),{'requested':0,'served':0})
        bucket['requested'] += 1; bucket['served'] += int(order.id in served)
    vehicles = {v.id:v.data for v in inputs.vehicles}
    monetary = sum((Fraction(r.fixed_minor) + Fraction(r.distance_m*vehicles[r.vehicle_id].cost.per_km_minor,1000)
                    + Fraction(r.driving_seconds*vehicles[r.vehicle_id].cost.per_hour_minor,3600)
                    for r in result.routes),Fraction())
    return {'validation':result.validation,'served':len(served),'unassigned':len(result.unassigned),
            'priority_coverage':priority,'vehicles_used':len(result.routes),
            'distance_m':sum(r.distance_m for r in result.routes),
            'driving_seconds':sum(r.driving_seconds for r in result.routes),
            'service_seconds':sum(r.service_seconds for r in result.routes),
            'waiting_seconds':sum(r.waiting_seconds for r in result.routes),
            'elapsed_seconds':sum(r.end-r.departure for r in result.routes),
            'modeled_elapsed_cost_minor':sum(r.known_cost_minor for r in result.routes),
            'unrounded_driving_cost_minor':float(monetary)}


def fixed_baseline(inputs: PlanningInput, workload: str) -> EditRequest:
    counts = {'paris-ten': (2, 5), 'paris-250': (25, 10), 'regional-250': (25, 10), 'regional-equipped-250': (25, 10)}
    vehicles, stops = counts[workload]
    if len(inputs.vehicles) != vehicles or len(inputs.orders) != vehicles * stops:
        raise ValueError('Workload does not match its predefined baseline assignment.')
    # Consecutive input groups are fixed before optimization, never solver output.
    return EditRequest(routes=[{'vehicle_id': v.id, 'order_ids': [o.id for o in inputs.orders[i*stops:(i+1)*stops]]}
                               for i, v in enumerate(inputs.vehicles)], unassigned_order_ids=[])


def trial(kind: str, url: str, dataset: str, budget: int, input_path: Path = FIXTURE,
          workload: str = 'paris-ten', matrix_concurrency: int = 1) -> dict:
    import resource
    payload = json.loads(input_path.read_text())
    payload['solver_time_limit_seconds'] = budget
    payload['objective'] = 'TRAVEL_TIME' if kind == 'baseline' else kind
    inputs = PlanningInput.model_validate(payload)
    stages = {}
    started = previous = time.perf_counter()
    current = None
    def stage(name: str) -> None:
        nonlocal previous,current
        now = time.perf_counter()
        if current is not None:stages[current] = now-previous
        current,previous = name,now
    engine = Valhalla(url,dataset,matrix_concurrency=matrix_concurrency)
    try:
        if kind == 'baseline':
            result = reroute(inputs,fixed_baseline(inputs,workload),engine,deadline=Deadline.after(120),on_stage=stage)
        else:
            result = calculate(inputs,engine,deadline=Deadline.after(120),on_stage=stage)
    except (EngineError, RuntimeError, ValueError) as error:
        failed_stage = current
        stage('FAILED')
        raise TrialFailure({'kind':kind, 'status':'FAILED', 'error':str(error),
                            'failed_stage':failed_stage, 'stage_seconds':stages,
                            'total_seconds':time.perf_counter()-started}) from error
    stage('DONE')
    if result.validation != 'PASS' or len(result.unassigned) or sum(len(r.stops) for r in result.routes) != len(inputs.orders):
        raise TrialFailure({'kind':kind, 'status':'FAILED',
                            'error':'Benchmark requires independently valid full coverage; candidate rejected.',
                            'failed_stage':'ACCEPTANCE', 'stage_seconds':stages,
                            'total_seconds':time.perf_counter()-started,
                            'metrics':metrics(inputs,result), 'blocking_issues':result.blocking_issues})
    return {'kind':kind,'stage_seconds':stages,'total_seconds':time.perf_counter()-started,
            'python_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'largest_child_peak_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            **metrics(inputs,result)}


def comparisons(runs: list[dict]) -> list[dict]:
    baseline = [r for r in runs if r['kind']=='baseline']
    if not baseline:
        raise ValueError('A feasible baseline is required.')
    requested=sum(bucket['requested'] for bucket in baseline[0]['priority_coverage'].values())
    for row in runs:
        if (row['validation']!='PASS' or row['served']!=requested or row['unassigned']
                or row['priority_coverage']!=baseline[0]['priority_coverage']):
            raise ValueError('Cannot compare different coverage or invalid routes.')
    results=[]
    for kind,metric in [('TRAVEL_TIME','driving_seconds'),('MODELED_TRAVEL_COST','unrounded_driving_cost_minor')]:
        values=[r[metric] for r in runs if r['kind']==kind]
        if not values:raise ValueError('Both objective measurements are required.')
        ceiling=min(r[metric] for r in baseline)
        results.append({'kind':kind,'metric':metric,'baseline_ceiling':ceiling,
                        'candidate_min':min(values),'candidate_max':max(values),
                        'worst_change_percent':100*(max(values)-ceiling)/ceiling if ceiling else None,
                        'pass':max(values)<=ceiling})
    return results


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--valhalla-url',default='http://host.docker.internal:8002')
    parser.add_argument('--dataset',default='Geofabrik Ile-de-France downloaded 2026-09-22; static OSM speeds')
    parser.add_argument('--repeats',type=int,default=3,choices=range(2,11))
    parser.add_argument('--budget',type=int,default=5,choices=range(1,61))
    parser.add_argument('--workload',choices=['paris-ten','paris-250','regional-250','regional-equipped-250'],default='paris-ten')
    parser.add_argument('--matrix-concurrency',type=int,choices=range(1,5),default=1)
    parser.add_argument('--input',type=Path,default=FIXTURE,help=argparse.SUPPRESS)
    parser.add_argument('--trial',choices=['baseline','TRAVEL_TIME','MODELED_TRAVEL_COST'],help=argparse.SUPPRESS)
    args=parser.parse_args()
    if sys.platform!='linux':raise SystemExit('Use the Linux application/test image for measured RSS and native VROOM.')
    if args.trial:
        try:
            result=trial(args.trial,args.valhalla_url,args.dataset,args.budget,args.input,args.workload,args.matrix_concurrency)
        except TrialFailure as error:
            print(json.dumps(error.detail))
            raise SystemExit(1)
        except (EngineError,RuntimeError,ValueError) as error:
            print(json.dumps({'kind':args.trial,'status':'FAILED','error':str(error)}))
            raise SystemExit(1)
        print(json.dumps(result))
        return
    def read(path: str) -> str | None:
        p=Path(path)
        return p.read_text().strip() if p.is_file() else None
    with urlopen(args.valhalla_url.rstrip('/')+'/status',timeout=10) as response:
        engine_status=json.load(response)
    cpu=next((line.split(':',1)[1].strip() for line in (read('/proc/cpuinfo') or '').splitlines() if line.startswith('model name')),'unknown')
    seed=PlanningInput.model_validate_json(FIXTURE.read_text())
    if args.workload=='paris-250':
        from benchmarks.scaling import scaled_input
        seed=scaled_input(seed)
    elif args.workload in {'regional-250','regional-equipped-250'}:
        from benchmarks.regional import regional_input,equipped_regional_input
        seed=(regional_input if args.workload=='regional-250' else equipped_regional_input)(seed)
    report={'recorded_utc':datetime.now(timezone.utc).isoformat(),'data_kind':'SYNTHETIC',
            'workload':{'name':args.workload,'vehicles':len(seed.vehicles),'orders':len(seed.orders),
                        'profiles':len({v.data.routing.model_dump_json() for v in seed.vehicles}),
                        'distinct_points':len({(p.longitude,p.latitude) for p in [seed.depot.data.location]+[o.data.location for o in seed.orders]}),
                        'solver_budget_seconds':args.budget,'solver_threads':1,'matrix_concurrency':args.matrix_concurrency,
                        'repeats':args.repeats},
            'environment':{'machine':platform.machine(),'kernel':platform.release(),'cpu_model':cpu,
                           'visible_cpus':os.cpu_count(),'cgroup_cpu_max':read('/sys/fs/cgroup/cpu.max'),
                           'cgroup_memory_max':read('/sys/fs/cgroup/memory.max'),
                           'vroom_version':subprocess.check_output(['/usr/local/bin/vroom','--version'],text=True).strip(),
                           'valhalla_version':engine_status.get('version','not reported'),'dataset':args.dataset},
            'memory_scope':'Linux per-trial Python peak and largest exited child peak, separately; not additive, excludes external Valhalla/OS graph cache.',
            'runs':[]}
    # Fresh Python process per measurement, shared warm graph service. First run
    # retained; no cold-cache claim and no unreported discarded warm-up samples.
    with tempfile.TemporaryDirectory(prefix='vrp-benchmark-') as directory:
        inputs=Path(directory)/'input.json';inputs.write_text(seed.model_dump_json())
        for kind in ['baseline','TRAVEL_TIME','MODELED_TRAVEL_COST']:
            for repeat in range(args.repeats):
                print(f'{args.workload}: {kind} {repeat+1}/{args.repeats}',file=sys.stderr,flush=True)
                try:
                    data=subprocess.check_output([sys.executable,'-m','benchmarks.run','--trial',kind,'--input',str(inputs),
                        '--valhalla-url',args.valhalla_url,'--dataset',args.dataset,'--budget',str(args.budget),
                        '--workload',args.workload,'--matrix-concurrency',str(args.matrix_concurrency)],text=True,timeout=150)
                except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as error:
                    report['failure']={'kind':kind,'repeat':repeat+1,'reason':type(error).__name__}
                    if isinstance(error,subprocess.CalledProcessError):
                        try:report['failure']['detail']=json.loads(error.output)
                        except (ValueError,TypeError):pass
                    print(json.dumps(report,indent=2))
                    raise SystemExit(1)
                report['runs'].append(json.loads(data))
    report['quality_gate']='Full coverage and no objective worsening against the fixed feasible baseline; not an optimality guarantee.'
    report['comparisons']=comparisons(report['runs'])
    print(json.dumps(report,indent=2))
    if not all(row['pass'] for row in report['comparisons']):raise SystemExit(2)


if __name__=='__main__':main()
