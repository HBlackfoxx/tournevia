"""Token-protected Prometheus metrics: hidden unless configured, exact bearer token, counts only."""
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.jobs import Job, fail_job
from app.main import create_app
from app.monitoring import METRICS
from test_jobs import enqueue, seed

TOKEN = 'monitor-' + 'x' * 32


def values(text: str) -> dict[str, float]:
    return {line.split()[0]: float(line.split()[1]) for line in text.splitlines() if line and not line.startswith('#')}


def test_metrics_are_hidden_unless_configured_and_need_the_exact_token(tmp_path):
    base = Settings(_env_file=None, app_mode='local_demo', public_origin='http://localhost:8000', web_dist=tmp_path,
                    db_port=1, valhalla_url='http://127.0.0.1:1')
    with TestClient(create_app(base), base_url='http://localhost:8000') as client:
        assert client.get('/api/v1/metrics', headers={'Authorization': f'Bearer {TOKEN}'}).status_code == 404
    configured = base.model_copy(update={'monitor_token': SecretStr(TOKEN)})
    with TestClient(create_app(configured), base_url='http://localhost:8000') as client:
        assert client.get('/api/v1/metrics').status_code == 401
        assert client.get('/api/v1/metrics', headers={'Authorization': f'Bearer {TOKEN}x'}).status_code == 401
        response = client.get('/api/v1/metrics', headers={'Authorization': f'Bearer {TOKEN}'})
        assert response.status_code == 200 and response.headers['content-type'].startswith('text/plain')
        assert response.headers['cache-control'] == 'no-store'
        reported = values(response.text)
        assert set(reported) == {name for name, _, _ in METRICS}
        # No database or router here: reported as unavailable, not as an error page.
        assert reported['tournevia_database_ready'] == 0 and reported['tournevia_routing_available'] == 0
    with pytest.raises(ValidationError, match='MONITOR_TOKEN'):
        Settings(_env_file=None, monitor_token='too-short')


@pytest.mark.postgres
def test_metrics_count_waiting_running_and_recent_calculations(database):
    engine, _, settings = database
    request = seed(database)
    queued = enqueue(database, request, 'queued')
    failed = enqueue(database, request, 'failed')
    with Session(engine) as session, session.begin():
        fail_job(session.get(Job, failed.id), 'ENGINE_FAILED', 'Router unavailable.')
    with TestClient(create_app(settings.model_copy(update={'monitor_token': SecretStr(TOKEN)})), base_url='http://localhost:8000') as client:
        reported = values(client.get('/api/v1/metrics', headers={'Authorization': f'Bearer {TOKEN}'}).text)
    assert reported['tournevia_database_ready'] == 1 and reported['tournevia_worker_running'] == 0
    assert (reported['tournevia_jobs_queued'], reported['tournevia_jobs_running']) == (1, 0)
    assert reported['tournevia_jobs_failed_last_hour'] == 1 and reported['tournevia_jobs_succeeded_last_hour'] == 0
    assert reported['tournevia_oldest_queued_job_age_seconds'] >= 0 and reported['tournevia_last_success_age_seconds'] == -1
    assert queued.status == 'QUEUED'
