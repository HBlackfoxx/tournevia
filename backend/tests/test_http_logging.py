import logging
import re

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_request_logs_use_templates_and_generated_ids_without_private_inputs(caplog):
    app = create_app(Settings(_env_file=None))

    @app.get('/probe/{value}')
    def probe(value: str):
        return {'ok': True}

    with caplog.at_level(logging.INFO, logger='app.http_logging'), TestClient(app, base_url='http://localhost:8000') as client:
        response = client.get('/probe/private-path?secret=private-query', headers={
            'Cookie': 'secret=private-cookie', 'Authorization': 'private-token', 'X-Request-ID': 'private-id'})
        assert response.status_code == 200
        request_id = response.headers['x-request-id']
        assert re.fullmatch('[0-9a-f]{32}', request_id)
        assert client.get('/private-unmatched').status_code == 404
    logs = '\n'.join(r.getMessage() for r in caplog.records if r.name == 'app.http_logging')
    assert 'route=/probe/{value}' in logs and 'route=unmatched' in logs
    assert 'request_id=' + request_id in logs and 'status=200' in logs
    assert 'private-' not in logs


def test_unexpected_errors_return_safe_correlated_response_without_traceback(caplog):
    app = create_app(Settings(_env_file=None))

    @app.get('/error-probe')
    def probe():
        raise ValueError('private-error-record-and-password')

    with caplog.at_level(logging.INFO, logger='app.http_logging'), TestClient(app, base_url='http://localhost:8000') as client:
        response = client.get('/error-probe')
    assert response.status_code == 500
    assert response.json()['request_id'] == response.headers['x-request-id']
    assert response.headers['cache-control'] == 'no-store'
    logs = '\n'.join(r.getMessage() for r in caplog.records if r.name == 'app.http_logging')
    assert 'error_type=ValueError' in logs and 'status=500' in logs
    # Where it happened, so a self-hoster can report it: file, line and function, never the message.
    assert re.search(r'request_failed request_id=' + response.json()['request_id'] + r' error_type=ValueError trace=.*'
                     r'tests/test_http_logging\.py:\d+ probe', logs)
    assert 'private-error' not in logs + response.text


def test_origin_and_host_rejections_are_logged_without_header_values(caplog):
    app = create_app(Settings(_env_file=None))
    with caplog.at_level(logging.INFO, logger='app.http_logging'), TestClient(app, base_url='http://localhost:8000') as client:
        assert client.get('/api/v1/health', headers={'Host': 'private-host.test'}).status_code == 400
        assert client.get('/api/v1/health', headers={'Origin': 'https://private-origin.test'}).status_code == 403
    logs = '\n'.join(r.getMessage() for r in caplog.records if r.name == 'app.http_logging')
    assert 'status=400' in logs and 'status=403' in logs and 'private-' not in logs


class Driver(Exception):
    """A database driver error carrying PostgreSQL's error code and private text."""
    def __init__(self, sqlstate):
        super().__init__('private-row-value in SQL')
        self.sqlstate = sqlstate


def test_database_errors_say_busy_invalid_or_conflict_rather_than_unavailable(caplog):
    from sqlalchemy.exc import DataError, IntegrityError, OperationalError
    app = create_app(Settings(_env_file=None))
    failures = {'57014': OperationalError, '22001': DataError, '23505': IntegrityError, '08006': OperationalError}

    @app.get('/database-probe/{state}')
    def probe(state: str):
        raise failures[state]('SELECT private', {}, Driver(state))

    with caplog.at_level(logging.INFO), TestClient(app, base_url='http://localhost:8000') as client:
        answers = {state: client.get(f'/database-probe/{state}') for state in failures}
    assert {state: (r.status_code, r.json()['code']) for state, r in answers.items()} == {
        '57014': (409, 'DATABASE_BUSY'), '22001': (422, 'INVALID_VALUE'), '23505': (409, 'CONFLICT'),
        '08006': (503, 'DATABASE_UNAVAILABLE')}
    assert answers['57014'].headers['retry-after'] == '5'
    logs = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'database_error status=409 error_type=OperationalError sqlstate=57014 trace=' in logs
    assert 'test_http_logging.py' in logs and 'private' not in logs + ''.join(r.text for r in answers.values())
