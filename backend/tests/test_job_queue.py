"""Per-company queue limit and explicit cancellation of waiting or running calculations."""
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.edits import cancel_job
from app.jobs import MAX_ACTIVE_JOBS, Job, Plan
from app.main import create_app
from app.records import DataError
from app.worker import CalculationFailure, Worker
from test_edits import edit_job, saved_plan
from test_jobs import WRITE, enqueue, seed

pytestmark = pytest.mark.postgres


def cancel(database, key):
    engine, _, settings = database
    with Session(engine) as session, session.begin():
        return cancel_job(session, settings.demo_organization_id, key)


def test_a_company_can_queue_only_a_few_calculations_but_replays_still_answer(database):
    request = seed(database)
    jobs = [enqueue(database, request, f'key-{n}') for n in range(MAX_ACTIVE_JOBS)]
    with pytest.raises(DataError) as error:
        enqueue(database, request, 'one-too-many')
    assert error.value.status == 429 and error.value.code == 'QUEUE_FULL'
    assert enqueue(database, request, 'key-0').id == jobs[0].id  # A lost-response retry is not refused.
    cancel(database, jobs[0].id)
    assert enqueue(database, request, 'after-cancel').status == 'QUEUED'


def test_cancelling_a_queued_calculation_fails_it_once(database):
    engine, _, settings = database
    job = enqueue(database, seed(database))
    cancelled = cancel(database, job.id)
    assert cancelled.status == 'FAILED' and cancelled.error.code == 'CANCELLED'
    with pytest.raises(DataError) as error:
        cancel(database, job.id)
    assert error.value.status == 409 and error.value.code == 'JOB_FINISHED'
    with pytest.raises(DataError) as error:
        cancel(database, uuid4())
    assert error.value.status == 404
    worker = Worker(settings)
    try:
        worker.acquire()
        assert worker.claim() is None
    finally:
        worker.close()


def test_cancelling_a_running_calculation_stops_it_at_the_next_check(database):
    _, _, settings = database
    job = enqueue(database, seed(database))
    worker = Worker(settings)
    try:
        worker.acquire()
        claimed, _ = worker.claim()
        assert claimed == job.id
        cancel(database, job.id)
        with pytest.raises(CalculationFailure) as error:
            worker.tick(job.id, 'MATRIX')
        assert error.value.code == 'JOB_NOT_RUNNING'
    finally:
        worker.close()


def test_cancelling_an_edit_releases_its_plan(database):
    engine, _, _ = database
    plan_id, _, edit = saved_plan(database)
    job = edit_job(database, plan_id, edit)
    cancel(database, job.id)
    with Session(engine) as session:
        plan = session.get(Plan, plan_id)
        assert plan.active_job_id is None and plan.validation == 'FAIL' and plan.result_stale
        assert plan.edit_error['code'] == 'CANCELLED'
        assert session.get(Job, job.id).status == 'FAILED'


def test_cancel_and_queue_full_over_http(database):
    _, _, settings = database
    request = seed(database)
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        submit = lambda key: client.post('/api/v1/optimization-jobs', json=request.model_dump(mode='json'),
                                         headers={**WRITE, 'Idempotency-Key': key})
        ids = [submit(f'http-{n}').json()['id'] for n in range(MAX_ACTIVE_JOBS)]
        full = submit('http-full')
        assert full.status_code == 429 and full.json()['code'] == 'QUEUE_FULL' and full.headers['Retry-After'] == '30'
        assert client.post(f'/api/v1/jobs/{ids[0]}/cancel').status_code == 403  # Writes need Origin and X-VRP-Request.
        response = client.post(f'/api/v1/jobs/{ids[0]}/cancel', headers=WRITE)
        assert response.status_code == 200 and response.json()['error']['code'] == 'CANCELLED'
        assert client.post(f'/api/v1/jobs/{uuid4()}/cancel', headers=WRITE).status_code == 404
        assert submit('http-after').status_code == 202
