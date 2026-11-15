"""Verify docker-compose.proxied.yml behind a real Traefik configured like Coolify's.

Starts a disposable Compose project, a disposable Traefik that discovers it through its
Docker labels, and a disposable network; removes only those. Uses a one-day self-signed
certificate trusted only by the test client. Traefik's ACME resolver points at a closed
local port, so no certificate authority is contacted.
"""
import argparse
import json
import os
import secrets
import shutil
import socket
import ssl
import time
from uuid import uuid4

import httpx

from test_https import ROOT, check_site, issue_test_certificate, run

TRAEFIK = 'traefik:v3.6.25@sha256:31267173a15b4944e797a76ffd9c419707c8d8b32fe5b610f80cd0cfa05f372d'
# A second visitor: signs in from a container on the proxy network, so Traefik sees another address.
OTHER_VISITOR = '''
import json, ssl, sys, urllib.error, urllib.request
traefik, origin = sys.argv[1:3]
context = ssl.create_default_context(); context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
request = urllib.request.Request('https://' + traefik + '/api/v1/session', method='POST',
    data=json.dumps({'username': sys.argv[3], 'password': 'incorrect'}).encode(),
    headers={'Host': 'localhost', 'Origin': origin, 'X-VRP-Request': '1', 'Content-Type': 'application/json'})
try:
    print(urllib.request.urlopen(request, context=context, timeout=10).status)
except urllib.error.HTTPError as error:
    print(error.code)
'''


def free_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        return reservation.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openssl', default=None, help='OpenSSL executable, if not on PATH.')
    parser.add_argument('--image', default='tournevia-app:local', help='Already-built application runtime image.')
    args = parser.parse_args()
    project = 'vrp-proxy-test-' + uuid4().hex
    network, traefik = project + '-proxy', project + '-traefik'
    directory = ROOT / 'deploy/runtime' / project
    directory.mkdir(mode=0o700, parents=True)
    openssl = args.openssl or shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'
    cert, _key = issue_test_certificate(openssl, directory / 'certificates')
    (directory / 'dynamic').mkdir()
    (directory / 'dynamic/tls.yml').write_text(
        'tls:\n  stores:\n    default:\n      defaultCertificate:\n'
        '        certFile: /certs/fullchain.pem\n        keyFile: /certs/privkey.pem\n', encoding='utf-8')
    port, http_port = free_port(), free_port()
    origin = f'https://localhost:{port}'
    credentials = {name: secrets.token_urlsafe(32) for name in
                   ('POSTGRES_PASSWORD', 'RUNTIME_DB_PASSWORD', 'MIGRATION_DB_PASSWORD', 'SETUP_TOKEN')}
    configuration = {**credentials, 'POSTGRES_DB': 'vrp', 'POSTGRES_USER': 'vrp_owner', 'COMPOSE_PROJECT_NAME': project,
                     'RUNTIME_DB_USER': 'vrp_app', 'MIGRATION_DB_USER': 'vrp_migrator', 'TLS_PUBLIC_ORIGIN': origin,
                     'PROXY_NETWORK': network, 'PROXY_HOST': 'localhost', 'PROXY_ROUTER': project,
                     'PROXY_HTTPS_ENTRYPOINT': 'https', 'PROXY_HTTP_ENTRYPOINT': 'http', 'PROXY_CERT_RESOLVER': 'letsencrypt'}
    os.environ.update(configuration)
    env_file = directory / 'test.env'
    with os.fdopen(os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        output.write(''.join(f'{name}={value}\n' for name, value in configuration.items()))
    image_override = directory / 'image.json'
    image_override.write_text(json.dumps({'services': {name: {'image': args.image} for name in ('setup', 'api', 'worker')}}))
    compose = ['docker', 'compose', '--env-file', str(env_file), '-p', project,
               '-f', str(ROOT / 'deploy/docker-compose.example.yml'), '-f', str(ROOT / 'deploy/docker-compose.proxied.yml'),
               '-f', str(image_override)]
    volume = project + '_postgres-data'
    assert volume not in run(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines()
    started = False
    try:
        merged = json.loads(run([*compose, 'config', '--format', 'json']))
        for name in ('postgres', 'api', 'edge'):
            assert not merged['services'][name].get('ports')
        for name in ('setup', 'api', 'worker'):
            assert merged['services'][name]['environment']['APP_MODE'] == 'shared'
        started = True
        run(['docker', 'network', 'create', '--label', 'vrp-test=' + project, network])
        # The flags Coolify gives its proxy, with a test certificate instead of a reachable ACME server.
        run(['docker', 'run', '-d', '--name', traefik, '--label', 'vrp-test=' + project, '--network', network,
             '-p', f'127.0.0.1:{port}:443', '-p', f'127.0.0.1:{http_port}:80',
             '-v', '/var/run/docker.sock:/var/run/docker.sock:ro',
             '--mount', f'type=bind,source={directory / "certificates"},target=/certs,readonly',
             '--mount', f'type=bind,source={directory / "dynamic"},target=/dynamic,readonly', TRAEFIK,
             '--entrypoints.http.address=:80', '--entrypoints.https.address=:443',
             '--providers.file.directory=/dynamic/', '--providers.docker=true', '--providers.docker.exposedbydefault=false',
             '--certificatesresolvers.letsencrypt.acme.httpchallenge=true',
             '--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=http',
             '--certificatesresolvers.letsencrypt.acme.caserver=https://127.0.0.1:9/directory',
             '--certificatesresolvers.letsencrypt.acme.storage=/tmp/acme.json'])
        print('Starting isolated stack behind Traefik (no published application ports).', flush=True)
        run([*compose, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '180'], timeout=240)
        run([*compose, 'exec', '-T', 'edge', 'nginx', '-t'])
        for name in ('postgres', 'api', 'edge', 'worker'):
            container = run([*compose, 'ps', '-q', name]).strip()
            metadata = json.loads(run(['docker', 'inspect', container]))[0]
            assert not metadata['HostConfig']['PortBindings'], name
            assert metadata['HostConfig']['RestartPolicy']['Name'] == 'unless-stopped'
            if name != 'postgres':
                assert metadata['HostConfig']['ReadonlyRootfs'] and metadata['HostConfig']['CapDrop'] == ['ALL']
            if name == 'edge':  # Its health check goes through it to the API.
                assert metadata['State']['Health']['Status'] == 'healthy'
            # Only the edge joins the shared proxy network.
            assert (network in metadata['NetworkSettings']['Networks']) == (name == 'edge'), name
        context = ssl.create_default_context(cafile=str(cert))
        password = secrets.token_urlsafe(32)
        with httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=15) as client:
            for _attempt in range(60):
                try:
                    if client.get('/api/v1/ready').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError('The site did not become ready through Traefik.')
            redirect = httpx.get(f'http://localhost:{http_port}/api/v1/health', trust_env=False, follow_redirects=False)
            assert redirect.status_code in (301, 302, 307, 308) and redirect.headers['location'].startswith('https://localhost')
            site = check_site(client, origin, credentials['SETUP_TOKEN'], password, unknown_host=404, upload_may_close=True)
            write = site['write']
            # This visitor is now limited by nginx and, for the probe user, by the application.
            probe = {'username': 'tls-lock-probe', 'password': 'incorrect-' + site['canary']}
            assert client.post('/api/v1/session', json=probe, headers=write).status_code == 429
            # Another visitor is not: the limits key on each visitor's address, not on Traefik's.
            for username in ('tls-lock-probe', 'tls-burst-0'):
                status = run(['docker', 'run', '--rm', '--network', network, args.image, 'python', '-c', OTHER_VISITOR,
                              traefik, origin, username]).strip()
                assert status == '401', (username, status)
        logs = run([*compose, 'logs', '--no-color'])
        assert all(value not in logs for value in [password, site['token'], site['csrf'], site['canary'], *credentials.values()])
        assert 'request_id=' in logs
        # The edge refused the oversized upload itself, without passing it to the application.
        assert any('status=413' in line and 'upstream=-' in line for line in logs.splitlines())
        print('PASS: Traefik label discovery, trusted TLS through the proxy, HTTP-to-HTTPS redirect, no published application ports.', flush=True)
        print('PASS: shared login/cookies, CSRF, Host/Origin, body limit, security headers and log redaction behind the proxy.', flush=True)
        print('PASS: sign-in limits use each visitor address; forged forwarding headers are ignored.', flush=True)
    finally:
        if started:
            names = run(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines()
            if volume in names:
                metadata = json.loads(run(['docker', 'volume', 'inspect', volume]))[0]
                assert metadata['Labels']['com.docker.compose.project'] == project
            run([*compose, 'down', '--volumes', '--remove-orphans'])
            label = 'label=vrp-test=' + project
            if traefik in run(['docker', 'ps', '-a', '--filter', label, '--format', '{{.Names}}']).split():
                run(['docker', 'rm', '-f', traefik])
            if network in run(['docker', 'network', 'ls', '--filter', label, '--format', '{{.Name}}']).split():
                run(['docker', 'network', 'rm', network])
            print('Removed only this disposable stack, Traefik, network and volume.', flush=True)


if __name__ == '__main__':
    main()
