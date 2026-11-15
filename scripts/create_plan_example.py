"""Print examples/route-plan.json from a real synthetic calculation.

The plan combines the example depot, vehicles, orders, optimization request and
job, then runs the application's Valhalla/VROOM calculation unchanged. It needs
the native solver, so run it in the engine-test image; see examples/README.md.
The output stays SYNTHETIC and DRAFT: it is a contract example, not dispatch data.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.config import Settings
from app.jobs import JobRecord, PlanRecord
from app.planning import PlanningInput
from app.routing import EngineError, Valhalla
from app.spike import calculate

# No example file carries a saved depot record; this ID completes the sample set.
DEPOT_ID = '00000000-0000-4000-8000-000000000001'


def read(name: str) -> dict:
    return json.loads((ROOT / 'examples' / f'{name}.json').read_text(encoding='utf-8'))


def example_inputs() -> tuple[PlanningInput, JobRecord]:
    request, job = read('optimization-request'), JobRecord.model_validate(read('job'))
    def owned(key, name):
        return {'id': key, 'organization_id': str(job.organization_id), 'version': 1, 'data': read(name)}
    inputs = PlanningInput.model_validate({
        'organization_id': job.organization_id, 'data_kind': 'SYNTHETIC', 'service_date': request['service_date'],
        'depot': owned(DEPOT_ID, 'depot'),
        'vehicles': [owned(key, name) for key, name in zip(request['vehicle_ids'], ['vehicle', 'vehicle-van'], strict=True)],
        'orders': [owned(key, name) for key, name in zip(request['order_ids'], ['order', 'order-second'], strict=True)],
        'routing_policy': request['routing_policy'], 'objective': request['objective'],
        'solver_time_limit_seconds': request['solver_time_limit_seconds']})
    return inputs, job


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--valhalla-url', default='http://host.docker.internal:8002')
    parser.add_argument('--dataset', default=Settings.model_fields['routing_dataset'].default)
    args = parser.parse_args()
    inputs, job = example_inputs()
    try:
        result = calculate(inputs, Valhalla(args.valhalla_url, args.dataset))
    except EngineError as error:
        raise SystemExit(str(error)) from None
    if result.validation != 'PASS':
        raise SystemExit('Example calculation failed validation: ' + '; '.join(result.blocking_issues))
    # The worker saves a new plan with the job's completion time and no active edit.
    plan = PlanRecord(id=job.plan_id, organization_id=job.organization_id, version=1, service_date=inputs.service_date,
                      created_at=job.finished_at, updated_at=job.finished_at, status='DRAFT', validation=result.validation,
                      result_stale=False, active_job_id=None, finalized_at=None, input_data=inputs, result=result,
                      edit_error=None, sources_current=True)
    sys.stdout.write(json.dumps(plan.model_dump(mode='json'), indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
