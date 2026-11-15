"""The published examples are checked by the application models, not a parallel checker."""
import csv
import json

import pytest

from app.config import ROOT
from app.edits import EditRequest, FinalizeRequest
from app.fleet import DepotInput
from app.imports import HEADERS, parse_row
from app.jobs import JobRecord, OptimizationRequest, PlanRecord
from app.planning import OrderInput, VehicleSource
from app.spike import points_and_profiles
from app.validation import validate

EXAMPLES = ROOT / 'examples'
MODELS = {
    'depot': DepotInput, 'vehicle': VehicleSource, 'vehicle-van': VehicleSource, 'vehicle-with-limits': VehicleSource,
    'order': OrderInput, 'order-second': OrderInput, 'order-priority': OrderInput,
    'optimization-request': OptimizationRequest, 'optimization-cost-request': OptimizationRequest,
    'edit-plan': EditRequest, 'finalize-request': FinalizeRequest, 'job': JobRecord, 'route-plan': PlanRecord,
}


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f'{name}.json').read_text(encoding='utf-8'))


def test_every_json_example_has_a_model():
    assert {path.stem for path in EXAMPLES.glob('*.json')} == set(MODELS)


@pytest.mark.parametrize('name', sorted(MODELS))
def test_example_validates_with_application_model(name):
    MODELS[name].model_validate(example(name))


@pytest.mark.parametrize('name', ['job', 'route-plan'])
def test_response_examples_are_exact_api_output(name):
    data = example(name)
    assert MODELS[name].model_validate(data).model_dump(mode='json') == data


@pytest.mark.parametrize(('kind', 'names'), [('vehicles', ['vehicle', 'vehicle-van']), ('orders', ['order', 'order-second'])])
def test_csv_rows_import_to_their_json_twins(kind, names):
    with (EXAMPLES / f'{kind}.csv').open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == HEADERS[kind]
        rows = list(reader)
    assert len(rows) == len(names)
    for row, name in zip(rows, names):
        expected = MODELS[name].model_validate(example(name)).model_dump(mode='json')
        if kind == 'orders':
            expected['location']['reviewed'] = False  # Imports never carry a coordinate review.
        assert parse_row(kind, row).model_dump(mode='json') == expected


def test_plan_example_is_a_valid_synthetic_draft_of_the_example_request():
    plan = PlanRecord.model_validate(example('route-plan'))
    inputs, result = plan.input_data, plan.result
    request = OptimizationRequest.model_validate(example('optimization-request'))
    job = JobRecord.model_validate(example('job'))
    assert (plan.status, inputs.data_kind, result.data_kind) == ('DRAFT', 'SYNTHETIC', 'SYNTHETIC')
    assert plan.validation == result.validation == 'PASS' and not result.blocking_issues
    assert not (plan.result_stale or plan.active_job_id or plan.finalized_at or plan.edit_error or result.operational_ready)
    assert (job.status, job.plan_id, job.organization_id) == ('SUCCEEDED', plan.id, plan.organization_id)
    assert plan.created_at == plan.updated_at == job.finished_at
    assert (inputs.service_date, inputs.routing_policy, inputs.objective, inputs.solver_time_limit_seconds) == (
        request.service_date, request.routing_policy, request.objective, request.solver_time_limit_seconds)
    assert [row.id for row in inputs.vehicles] == request.vehicle_ids
    assert [row.id for row in inputs.orders] == request.order_ids
    assert inputs.depot.data == DepotInput.model_validate(example('depot'))
    # A calculation copies each saved vehicle as it works that day: its weekly hours as a dated window.
    assert [row.data for row in inputs.vehicles] == [
        VehicleSource.model_validate(example(name)).on(inputs.service_date) for name in ('vehicle', 'vehicle-van')]
    assert [row.data for row in inputs.orders] == [OrderInput.model_validate(example(name)) for name in ('order', 'order-second')]
    # The independent validator recomputes coverage, loads, skills, schedule,
    # geometry and cost arithmetic from the saved inputs and final road legs.
    points, profiles = points_and_profiles(inputs)
    assert validate(inputs, result, points, profiles, None) == []


def test_edit_and_finalize_examples_fit_the_plan_example():
    plan = PlanRecord.model_validate(example('route-plan'))
    edit = EditRequest.model_validate(example('edit-plan'))
    assigned = [key for route in edit.routes for key in route.order_ids] + edit.unassigned_order_ids
    assert sorted(assigned, key=str) == sorted((row.id for row in plan.input_data.orders), key=str)
    assert {route.vehicle_id for route in edit.routes} <= {row.id for row in plan.input_data.vehicles}
    finalize = FinalizeRequest.model_validate(example('finalize-request'))
    assert not plan.result.unassigned or finalize.accept_unassigned
