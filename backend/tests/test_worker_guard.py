"""Worker guard behaviour that needs PostgreSQL but no routing engine or Linux calculation child."""
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.worker as worker_module
from app.jobs import Job
from app.jobs_api import worker_running
from app.worker import GuardLost, StatementFailed, Worker
from test_jobs import empty_result, enqueue, seed

pytestmark = pytest.mark.postgres


def test_a_failed_statement_keeps_the_guard_but_a_lost_connection_stops_the_worker(database):
    engine, _, settings = database
    worker = Worker(settings)
    try:
        worker.acquire()
        with pytest.raises(StatementFailed), worker.transaction() as session:
            session.execute(text('SELECT pg_sleep(3)'))  # The worker's statement timeout is 2 s.
        assert not worker.broken
        with worker.transaction() as session:
            assert session.scalar(text('SELECT 1')) == 1
        with Session(engine) as other:
            assert worker_running(other)
        worker.connection.invalidate()
        with pytest.raises(GuardLost), worker.transaction() as session:
            session.execute(text('SELECT 1'))
        assert worker.broken
    finally:
        worker.close()


def test_a_failed_save_fails_only_that_job(database, monkeypatch):
    engine, _, settings = database
    job = enqueue(database, seed(database))

    def failing_save(session, job_id, result):
        session.execute(text('SELECT pg_sleep(3)'))

    monkeypatch.setattr(worker_module, 'save_result', failing_save)
    worker = Worker(settings)
    monkeypatch.setattr(worker, 'compute', lambda job_id, inputs, end: empty_result(inputs))
    try:
        worker.acquire()
        assert worker.process_one()
        assert not worker.broken
        with Session(engine) as session:
            row = session.get(Job, job.id)
            assert row.status == 'FAILED' and row.error['code'] == 'DATABASE_ERROR'
        assert worker.process_one() is False  # The guard still works for the next poll.
    finally:
        worker.close()


def test_acquiring_the_guard_removes_abandoned_workspaces(database, monkeypatch, tmp_path):
    _, _, settings = database
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    for name in ('vrp-job-abandoned', 'vrp-solve-abandoned'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'input.json').write_text('{"address": "private"}')
    (tmp_path / 'unrelated').mkdir()
    worker = Worker(settings)
    try:
        worker.acquire()
    finally:
        worker.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ['unrelated']


def test_guard_checks_refresh_the_health_heartbeat(database, monkeypatch, tmp_path):
    _, _, settings = database
    monkeypatch.setattr(worker_module, 'HEARTBEAT', tmp_path / 'alive')
    worker = Worker(settings)
    try:
        worker.acquire()
        assert not (tmp_path / 'alive').exists()
        worker.tick()
        first = (tmp_path / 'alive').stat().st_mtime_ns
        worker.tick()
        assert (tmp_path / 'alive').stat().st_mtime_ns >= first
    finally:
        worker.close()
