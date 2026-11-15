"""Verify the HTTPS overlay in a disposable Compose project, without installing trust roots."""
import argparse
from datetime import datetime
import json
from zoneinfo import ZoneInfo
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def run(args: list[str], *, content: str | None = None, timeout: int = 180) -> str:
    result = subprocess.run(args, input=content, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        # Commands, outputs and errors may contain ephemeral test credentials.
        raise RuntimeError(f'{Path(args[0]).name} failed (exit {result.returncode}); raw output suppressed.')
    return result.stdout


def issue_test_certificate(openssl: str, directory: Path) -> tuple[Path, Path]:
    directory.mkdir()
    cert, key = directory / 'fullchain.pem', directory / 'privkey.pem'
    run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '1',
         '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
         '-keyout', str(key), '-out', str(cert)])
    # Disposable synthetic key, under the private test directory. Proxy UID 101
    # must be able to read it; no browser or system trust is changed.
    key.chmod(0o644)
    return cert, key


def served_certificate(port: int, context: ssl.SSLContext) -> bytes:
    with socket.create_connection(('127.0.0.1', port), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname='localhost') as tls:
            return tls.getpeercert(binary_form=True)


def rejects_certificate(origin: str, context: ssl.SSLContext | bool = True) -> None:
    with httpx.Client(verify=context, trust_env=False, timeout=5) as client:
        try:
            client.get(origin + '/api/v1/health')
        except httpx.ConnectError as error:
            # A refused connection or timeout is not evidence of trust rejection.
            assert 'CERTIFICATE_VERIFY_FAILED' in str(error), 'Expected certificate verification failure.'
        else:
            raise AssertionError('Untrusted test certificate was unexpectedly accepted.')


def check_site(client: httpx.Client, origin: str, setup_token: str, password: str, company: bool = False,
               unknown_host: int = 400, upload_may_close: bool = False) -> dict:
    """First-use setup, sign-in, CSRF, Host/Origin, body limit, headers and sign-in limits through the proxy.

    Leaves the per-address sign-in allowance exhausted; returns the session values later steps use.
    """
    assert client.get('/').status_code == 200
    notices = client.get('/assets/THIRD_PARTY_NOTICES.txt')
    assert notices.status_code == 200 and 'text/html' not in notices.headers['content-type']
    inventory = client.get('/assets/dependency-inventory.json').json()
    assert inventory and all(item['name'] and item['version'] and item['files'] for item in inventory)
    for item in inventory:
        assert f'===== {item["name"]} {item["version"]} =====' in notices.text
    assert client.get('/api/v1/depot').status_code == 401
    setup_status = client.get('/api/v1/setup').json()
    assert setup_status['required'] is True and setup_status['enabled'] is True
    assert setup_status['map_style_url'] == 'https://tiles.openfreemap.org/styles/liberty'
    depot = json.loads((ROOT/'examples/depot.json').read_text())
    setup = client.post('/api/v1/setup', headers={'Origin':origin,'X-VRP-Request':'1'}, json={
        'setup_token':setup_token, 'depot':depot,
        'company_name':'TLS company acceptance' if company else 'TLS example workspace', 'display_name':'Synthetic TLS admin',
        'username':'tls-test-admin', 'password':password})
    assert setup.status_code == 201
    token, csrf = client.cookies.get('__Host-vrp_session'), setup.json()['csrf_token']
    if not company:
        # The fictional examples for today: two vehicles and ten deliveries, as the setup guide adds them.
        today = datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()
        examples = client.post('/api/v1/examples', json={'service_date': today},
                               headers={'Origin':origin,'X-VRP-Request':'1','X-CSRF-Token':csrf})
        assert examples.status_code == 201 and len(examples.json()['deliveries']) == 10
    assert client.get('/api/v1/setup').json() == {'required': False, 'enabled': False}
    assert client.post('/api/v1/setup', headers={'Origin':origin,'X-VRP-Request':'1'}, json={
        'setup_token':setup_token, 'depot':depot,
        'company_name':'Second company', 'display_name':'Second admin',
        'username':'other', 'password':password}).status_code == 409
    assert client.delete('/api/v1/session', headers={'Origin':origin,'X-VRP-Request':'1','X-CSRF-Token':csrf}).status_code == 200
    # The application refuses other hostnames; a proxy that routes by hostname refuses them first.
    assert client.get('/api/v1/health', headers={'Host': 'invalid.test'}).status_code == unknown_host
    assert client.get('/api/v1/health', headers={'Origin': 'https://invalid.test'}).status_code == 403
    write = {'Origin': origin, 'X-VRP-Request': '1'}
    login = {'username': 'tls-test-admin', 'password': password}
    assert client.post('/api/v1/session', json=login).status_code == 403
    response = client.post('/api/v1/session', json=login, headers=write)
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert all(flag in cookie for flag in ('Secure', 'HttpOnly', 'SameSite=strict', 'Path=/'))
    token = client.cookies.get('__Host-vrp_session')
    assert token and 'Domain=' not in cookie
    csrf = response.json()['csrf_token']
    depot = client.get('/api/v1/depot').json()
    headers = {**write, 'If-Match': f'"{depot["version"]}"'}
    assert client.put('/api/v1/depot', json=depot['data'], headers=headers).status_code == 403
    assert client.put('/api/v1/depot', json=depot['data'], headers={**headers, 'X-CSRF-Token': csrf}).status_code == 200
    assert client.get('/api/v1/configuration').json()['data_kind'] == 'UNVERIFIED'
    canary = 'private-log-canary-' + uuid4().hex
    assert client.get('/' + canary, params={'secret': canary}, headers={'Authorization': canary}).status_code == 404
    try:
        too_large = client.post('/api/v1/session', content=canary * 60000, headers={**write, 'Content-Type': 'application/json'})
    except httpx.TransportError:
        # A proxy in front may close the connection while the refused body is still being
        # sent, so the client never reads the answer; the caller checks the refusal in the logs.
        assert upload_may_close
    else:
        assert too_large.status_code == 413 and too_large.json()['code'] == 'REQUEST_TOO_LARGE'
    page = client.get('/')
    assert page.headers['strict-transport-security'] == 'max-age=15552000'
    assert page.headers['x-frame-options'] == 'DENY' and "frame-ancestors 'none'" in page.headers['content-security-policy']
    # Exactly one copy each (repeated headers would be joined as "a, b"), also on API responses.
    for response in (page, client.get('/api/v1/health')):
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['referrer-policy'] == 'no-referrer'
    # The proxy replaces a forged client address, so a locked client cannot pose as another one.
    probe = {'username': 'tls-lock-probe', 'password': 'incorrect-' + canary}
    for _ in range(5):
        assert client.post('/api/v1/session', json=probe, headers=write).status_code == 401
    assert client.post('/api/v1/session', json=probe, headers={
        **write, 'X-VRP-Client': '198.51.100.7', 'X-Forwarded-For': '198.51.100.7'}).status_code == 429
    # Rapid sign-in attempts from one address are limited by the proxy itself.
    # Forged forwarding headers must not make each attempt look like a new address.
    burst = [client.post('/api/v1/session', json={'username': f'tls-burst-{index}', 'password': 'incorrect'},
                         headers={**write, 'X-Forwarded-For': f'198.51.100.{index}', 'X-Real-IP': f'198.51.100.{index}'})
             for index in range(30)]
    limited = [response for response in burst if response.status_code == 429]
    # The proxy's own limit answers in the application's JSON shape with its own code.
    assert limited and all(response.json()['code'] == 'SIGNIN_RATE_LIMITED' for response in limited)
    return dict(token=token, csrf=csrf, write=write, login=login, canary=canary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openssl', default=shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe')
    parser.add_argument('--image', default='tournevia-app:local', help='Already-built application runtime image.')
    parser.add_argument('--recovery', action='store_true', help='Also exercise real-road planning, backup and isolated restore.')
    parser.add_argument('--company', action='store_true', help='Exercise empty company setup and the real-worker READY/CSV lifecycle with fictional test inputs.')
    args = parser.parse_args()
    if args.company and args.recovery:
        parser.error('Run company workflow and sample recovery in separate disposable installations.')
    project = 'vrp-tls-test-' + uuid4().hex
    directory = ROOT / 'deploy/runtime' / project
    directory.mkdir(mode=0o700, parents=True)
    certificates = directory / 'certificates'
    cert, key = issue_test_certificate(args.openssl, certificates)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    origin = f'https://localhost:{port}'
    credentials = {name: secrets.token_urlsafe(32) for name in
                   ('POSTGRES_PASSWORD', 'RUNTIME_DB_PASSWORD', 'MIGRATION_DB_PASSWORD', 'SETUP_TOKEN')}
    configuration = {**credentials, 'POSTGRES_DB': 'vrp', 'POSTGRES_USER': 'vrp_owner',
                     'COMPOSE_PROJECT_NAME': project,
                     'RUNTIME_DB_USER': 'vrp_app', 'MIGRATION_DB_USER': 'vrp_migrator',
                     'TLS_PUBLIC_ORIGIN': origin, 'HTTPS_PORT': str(port),
                     'TLS_CERT_DIR': certificates.as_posix()}
    # Explicit test values also win over any caller's shell environment.
    os.environ.update(configuration)
    env_file = directory / 'test.env'
    with os.fdopen(os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        output.write(''.join(f'{name}={value}\n' for name, value in configuration.items()))
    # Explicit env file and random project ensure the user's demo is never selected.
    compose = ['docker', 'compose', '--env-file', str(env_file), '-p', project,
               '-f', str(ROOT / 'deploy/docker-compose.example.yml'),
               '-f', str(ROOT / 'deploy/docker-compose.https.yml')]
    image_override = directory / 'image.json'
    image_override.write_text(json.dumps({'services': {
        name: {'image': args.image} for name in ('setup', 'api', 'worker')
    }}), encoding='utf-8')
    compose.extend(['-f', str(image_override)])
    image_id = json.loads(run(['docker', 'image', 'inspect', args.image]))[0]['Id']
    assert re.fullmatch('vrp-tls-test-[0-9a-f]{32}', project)
    assert not run(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}']).strip()
    volume = project + '_postgres-data'
    assert volume not in run(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines()
    started = False
    try:
        merged = json.loads(run([*compose, 'config', '--format', 'json']))
        for name in ('postgres', 'api'):
            assert not merged['services'][name].get('ports')
        for name in ('setup', 'api', 'worker'):
            assert merged['services'][name]['environment']['APP_MODE'] == 'shared'
        for service in merged['services'].values():
            assert service['logging'] == {'driver': 'local', 'options': {'max-size': '10m', 'max-file': '3'}}
        print('Starting isolated HTTPS stack (private database and API ports).', flush=True)
        started = True
        run([*compose, 'up', '-d', '--no-build'], timeout=180)
        run([*compose, 'exec', '-T', 'https', 'nginx', '-t'])
        # Check applied runtime configuration, not just YAML intent.
        unchanged_services = {}
        for name in ('postgres', 'api', 'https', 'worker'):
            container = run([*compose, 'ps', '-q', name]).strip()
            metadata = json.loads(run(['docker', 'inspect', container]))[0]
            assert metadata['HostConfig']['LogConfig'] == {'Type': 'local', 'Config': {'max-size': '10m', 'max-file': '3'}}
            if name in ('postgres', 'api'):
                assert not metadata['HostConfig']['PortBindings']
            if name in ('api', 'worker'):
                assert metadata['Image'] == image_id
            # Long-running services come back after crashes and host reboots.
            assert metadata['HostConfig']['RestartPolicy']['Name'] == 'unless-stopped'
            if name in ('api', 'worker', 'https'):
                assert metadata['HostConfig']['ReadonlyRootfs'] and metadata['HostConfig']['CapDrop'] == ['ALL']
                assert 'no-new-privileges:true' in metadata['HostConfig']['SecurityOpt']
            if name != 'https':
                unchanged_services[name] = (container, metadata['State']['StartedAt'])
        password = secrets.token_urlsafe(32)
        context = ssl.create_default_context(cafile=str(cert))
        with httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=15) as client:
            for attempt in range(30):
                try:
                    response = client.get('/api/v1/ready')
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError('HTTPS readiness did not become available.')
            rejects_certificate(origin)
            site = check_site(client, origin, credentials['SETUP_TOKEN'], password, args.company)
            token, csrf, write, login, canary = (site[key] for key in ('token', 'csrf', 'write', 'login', 'canary'))
            time.sleep(35)  # Refill the per-address sign-in allowance for later steps.
        original_cert = ssl.PEM_cert_to_DER_cert(cert.read_text())
        assert served_certificate(port, context) == original_cert
        renewed_cert, renewed_key = issue_test_certificate(args.openssl, directory / 'renewed')
        expected_cert = ssl.PEM_cert_to_DER_cert(renewed_cert.read_text())
        assert expected_cert != original_cert
        # A staged mismatched pair must fail preflight while the running proxy
        # continues serving its already-loaded original certificate.
        shutil.copyfile(renewed_key, key)
        try:
            run([*compose, 'exec', '-T', 'https', 'nginx', '-t'])
        except RuntimeError:
            pass
        else:
            raise AssertionError('Mismatched certificate/key passed proxy preflight.')
        assert served_certificate(port, context) == original_cert
        shutil.copyfile(renewed_cert, cert)
        run([*compose, 'exec', '-T', 'https', 'nginx', '-t'])
        run([*compose, 'restart', 'https'])
        renewed_context = ssl.create_default_context(cafile=str(cert))
        with httpx.Client(base_url=origin, verify=renewed_context, trust_env=False, timeout=15) as client:
            for attempt in range(30):
                try:
                    if client.get('/api/v1/ready').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.25)
            else:
                raise RuntimeError('HTTPS readiness did not recover after certificate renewal.')
            assert served_certificate(port, renewed_context) == expected_cert
            rejects_certificate(origin, context)  # Previous self-signed test leaf is no longer sufficient.
            rejects_certificate(origin)
            session_headers = {'Cookie': '__Host-vrp_session=' + token}
            response = client.get('/api/v1/depot', headers=session_headers)
            assert response.status_code == 200  # Original authenticated session survived.
            depot = response.json()
            renewed_write = {**session_headers, **write, 'X-CSRF-Token': csrf}
            assert client.put('/api/v1/depot', json=depot['data'], headers={
                **renewed_write, 'If-Match': f'"{depot["version"]}"'
            }).status_code == 200
            assert client.delete('/api/v1/session', headers=renewed_write).status_code == 200
            assert client.get('/api/v1/depot', headers=session_headers).status_code == 401
        for name, expected in unchanged_services.items():
            container = run([*compose, 'ps', '-q', name]).strip()
            metadata = json.loads(run(['docker', 'inspect', container]))[0]
            assert (container, metadata['State']['StartedAt']) == expected
        recovery_secrets = []
        if args.recovery:
            from check_install_recovery import check_recovery
            recovery_secrets = check_recovery(compose, env_file, directory, origin, renewed_context, login)
        if args.company:
            from check_company_workflow import check_company
            recovery_secrets = check_company(origin, renewed_context, login)
        # Docker's bounded logs are captured only in memory; never print sensitive data.
        logs = run([*compose, 'logs', '--no-color'])
        assert all(value not in logs for value in [password, token, csrf, canary, *credentials.values(), *recovery_secrets])
        assert 'request_id=' in logs and 'status=413' in logs
        print('PASS: real TLS certificate validation, shared login/cookies, CSRF, write/read, logout, Host/Origin, body limit and log redaction.', flush=True)
        print('PASS: applied log rotation and no published API/database ports. No browser/OS trust root installed.', flush=True)
        print('PASS: HSTS/CSP/anti-framing headers, proxy-set sign-in client address and per-address sign-in limiting.', flush=True)
        print('PASS: mismatched certificate/key rejected; renewed certificate served after proxy-only restart; authenticated read/write/logout preserved without API, worker or database restart.', flush=True)
    finally:
        if started:
            # Delete only this run's new test volume after checking its ownership.
            names = run(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines()
            if volume in names:
                metadata = json.loads(run(['docker', 'volume', 'inspect', volume]))[0]
                assert metadata['Labels']['com.docker.compose.project'] == project
            run([*compose, 'down', '--volumes', '--remove-orphans'])
            print('Removed only this disposable test stack and volume; existing demo untouched.', flush=True)


if __name__ == '__main__':
    main()
