import json
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.config import ROOT, Settings
from app.fleet import DepotInput, DepotRecord
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, app_mode='local_demo', public_origin='http://localhost:8000', web_dist=tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        yield client


def test_liveness_and_public_configuration_do_not_require_database(client, settings):
    assert client.get('/api/v1/health').json() == {'status': 'ok'}
    assert client.get('/api/v1/configuration').json() == {
        'mode': 'local_demo', 'data_kind': 'SYNTHETIC', 'map_style_url': settings.map_style_url, 'company_name':'Sample workspace',
        'address_lookup': None, 'address_lookup_source': None, 'examples': False, 'safety_margin_minutes': 15}


@pytest.mark.parametrize('origin', ['http://localhost:8000', 'http://planner.example'])
def test_shared_mode_fails_closed(origin):
    with pytest.raises(ValidationError, match='HTTPS'):
        Settings(_env_file=None, app_mode='shared', public_origin=origin)


@pytest.mark.parametrize('origin', ['http://0.0.0.0:8000', 'https://planner.example', 'http://localhost.evil', 'http://localhost/path',
                                    'http://[::1]:8000'])
def test_local_demo_rejects_non_loopback_origin(origin):
    with pytest.raises(ValidationError, match='loopback|without credentials or a path'):
        Settings(_env_file=None, app_mode='local_demo', public_origin=origin)


def test_environment_shared_mode_cannot_fall_back_to_demo(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'shared')
    with pytest.raises(ValidationError, match='authentication'):
        create_app()


def test_invalid_configuration_does_not_print_database_password():
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, app_mode='shared', db_password='do-not-print-this-password')
    assert 'do-not-print-this-password' not in str(error.value)


def test_external_host_and_origin_rejected(client):
    assert client.get('/api/v1/depot', headers={'Host': 'attacker.example'}).status_code == 400
    assert client.get('/api/v1/depot', headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert client.get('/api/v1/health', headers={'Origin': 'http://localhost:8000'}).status_code == 200


def test_database_failure_is_unavailable_without_driver_details(client, monkeypatch, caplog):
    def unavailable(*args):
        raise OperationalError('SELECT', {}, Exception('secret-password-and-customer-data'))
    monkeypatch.setattr('app.main.database_ready', unavailable)
    monkeypatch.setattr('app.main.read_depot', unavailable)
    readiness = client.get('/api/v1/ready')
    assert readiness.status_code == 503
    assert readiness.json() == {'status': 'unavailable'}
    depot = client.get('/api/v1/depot')
    assert depot.status_code == 503
    assert 'secret-password' not in depot.text + readiness.text + caplog.text
    assert client.get('/api/v1/health').status_code == 200


def test_request_cannot_select_another_company(client, settings, monkeypatch):
    owners = []
    def depot_for_owner(session, owner):
        owners.append(owner)
        return DepotRecord(id=UUID('00000000-0000-4000-8000-000000000002'), organization_id=owner,
                           version=1, data=DepotInput.model_validate_json((ROOT / 'examples/depot.json').read_text()))
    monkeypatch.setattr('app.main.read_depot', depot_for_owner)
    response = client.get('/api/v1/depot?organization_id=other', headers={'X-Organization-ID': 'other'})
    assert response.status_code == 200
    assert response.json()['organization_id'] == str(settings.demo_organization_id)
    assert owners == [settings.demo_organization_id]
    assert response.headers['etag'] == '"1"'


def test_unprovisioned_depot_is_missing(client, monkeypatch):
    monkeypatch.setattr('app.main.read_depot', lambda *args: None)
    assert client.get('/api/v1/depot').status_code == 404


def test_frontend_build_and_unknown_api_routes(settings):
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        assert client.get('/').status_code == 503
    (settings.web_dist / 'assets').mkdir()
    (settings.web_dist / 'assets/app.js').write_text('console.log("built")')
    (settings.web_dist / 'index.html').write_text('<html>Built React entrypoint</html>')
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        assert 'Built React entrypoint' in client.get('/').text
        assert client.get('/assets/app.js').status_code == 200
        assert client.get('/api/v1/not-implemented').status_code == 404


def test_pages_forbid_framing_and_foreign_scripts(settings, tmp_path):
    (settings.web_dist / 'index.html').write_text('<html>Built React entrypoint</html>')
    plain = settings.model_copy(update={'map_style_url': 'http://tiles.internal:8080/style.json'})
    for config, origin in [(settings, 'https:'), (plain, 'http://tiles.internal:8080')]:
        with TestClient(create_app(config), base_url='http://localhost:8000') as client:
            for path in ['/', '/api/v1/health']:
                headers = client.get(path).headers
                policy = headers['content-security-policy']
                assert headers['x-frame-options'] == 'DENY' and headers['referrer-policy'] == 'no-referrer'
                assert "script-src 'self';" in policy and "frame-ancestors 'none'" in policy
                assert 'connect-src \'self\' https:' in policy and origin in policy.split('connect-src')[1]
            assert 'content-security-policy' not in client.get('/api/docs').headers
            assert client.get('/').headers['cache-control'] == 'no-cache'  # Upgrades reach browsers.


def test_shared_mode_serves_no_developer_docs_and_denies_browser_features(settings):
    shared = settings.model_copy(update={'app_mode': 'shared', 'public_origin': 'https://planner.example'})
    with TestClient(create_app(shared), base_url='https://planner.example') as client:
        assert client.get('/api/docs').status_code == 404
        assert client.get('/api/openapi.json').status_code == 404
        policy = client.get('/api/v1/health').headers['permissions-policy']
        assert all(f'{feature}=()' in policy for feature in ('camera', 'geolocation', 'microphone', 'payment'))
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        assert client.get('/api/docs').status_code == 200 and client.get('/api/openapi.json').status_code == 200


def test_password_characters_are_not_interpolated_into_database_url():
    settings = Settings(_env_file=None, db_password='p@ss:/?#%word')
    assert settings.database_url.password == 'p@ss:/?#%word'
    assert 'p@ss' not in repr(settings)


def test_depot_example_validates_and_rejects_client_ownership():
    data = json.loads((ROOT / 'examples/depot.json').read_text())
    assert DepotInput.model_validate(data).location.latitude == 48.835
    with pytest.raises(ValidationError):
        DepotInput.model_validate({**data, 'organization_id': 'client-picked'})


def test_generated_contract_matches_application(settings):
    exported = json.loads((ROOT / 'backend/openapi.json').read_text(encoding='utf-8'))
    assert create_app(settings).openapi() == exported


def test_contract_requires_the_version_and_retry_headers_the_api_enforces(settings):
    headers = [(path, p['name'], p['required']) for path, item in create_app(settings).openapi()['paths'].items()
               for operation in item.values() for p in operation.get('parameters', [])
               if p['in'] == 'header' and p['name'] in ('if-match', 'idempotency-key')]
    assert len(headers) == 14 and all(required for *_, required in headers)
    assert ('/api/v1/depots/{key}', 'if-match', True) in headers
    assert ('/api/v1/orders/{key}/customer', 'if-match', True) in headers
    assert ('/api/v1/plans/{key}/edits', 'idempotency-key', True) in headers
