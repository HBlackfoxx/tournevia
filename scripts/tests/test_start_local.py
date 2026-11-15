"""The local launcher: readable failures without secrets, a free port, and the router inside the workspace."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import start_local  # noqa: E402


def test_a_failed_command_says_why_with_every_generated_secret_blanked(monkeypatch):
    monkeypatch.setattr(start_local, 'HIDDEN', set())
    start_local.hide(['generated-password-value', 'short'])
    failure = subprocess.CompletedProcess([], 1, stdout='POSTGRES_PASSWORD=generated-password-value',
                                          stderr='Error response from daemon: generated-password-value\nBind for 127.0.0.1:8007 failed: port is already allocated')
    monkeypatch.setattr(start_local.subprocess, 'run', lambda *args, **kwargs: failure)
    with pytest.raises(RuntimeError) as error:
        start_local.run(['docker', 'compose', '-p', 'vrp-local-x', 'up', '-d'])
    message = str(error.value)
    assert message.startswith('docker compose up failed (exit 1):') and 'port is already allocated' in message
    assert 'generated-password-value' not in message and 'POSTGRES_PASSWORD' not in message and '***' in message


def test_the_workspace_reaches_a_router_on_this_computer_on_linux_too():
    override = start_local.overrides(8010)
    assert '"127.0.0.1:8010:8000"' in override
    # Docker Desktop names the host itself; Docker Engine on Linux needs the host-gateway entry.
    assert override.count('extra_hosts: ["host.docker.internal:host-gateway"]') == 2


def test_settings_change_in_place_and_stay_private(tmp_path):
    (tmp_path / 'workspace.env').write_text('A=1\nPUBLIC_ORIGIN=http://localhost:8007\nB=2\n', encoding='utf-8')
    start_local.set_values(tmp_path, {'PUBLIC_ORIGIN': 'http://localhost:8010', 'C': '3'})
    assert (tmp_path / 'workspace.env').read_text(encoding='utf-8') == 'A=1\nB=2\nPUBLIC_ORIGIN=http://localhost:8010\nC=3\n'
    if os.name == 'posix':
        assert (tmp_path / 'workspace.env').stat().st_mode & 0o777 == 0o600


def test_a_router_inside_the_workspace_uses_a_built_graph_and_names_its_extract(tmp_path, monkeypatch):
    (tmp_path / 'region').mkdir()
    (tmp_path / 'region/valhalla.json').write_text('{}')
    monkeypatch.setattr(start_local, 'RUNTIME', tmp_path)
    values = start_local.routing_settings('region/valhalla.json')
    assert values == {'ROUTING_CONFIG': 'region/valhalla.json', 'ROUTING_THREADS': '2', 'ROUTING_MEMORY': '4g'}
    (tmp_path / 'region/source.json').write_text(json.dumps({'label': 'Geofabrik Ile-de-France', 'downloaded_on': '2026-11-12'}))
    assert start_local.routing_settings('region/valhalla.json')['ROUTING_DATASET'] == \
        'Geofabrik Ile-de-France downloaded 2026-11-12; static OSM speeds'
    for wrong in ('/srv/region/valhalla.json', '../region/valhalla.json', 'region/missing.json', 'region'):
        with pytest.raises(ValueError, match='routing-config'):
            start_local.routing_settings(wrong)


def launch(monkeypatch, tmp_path, *arguments, free=True, running=''):
    """Run the launcher's main() with Docker replaced; returns the commands it ran."""
    calls = []

    def docker(args):
        calls.append(args)
        if 'config' in args:
            return json.dumps({'services': {'postgres': {}, 'api': {'ports': [{'host_ip': '127.0.0.1'}],
                                                                    'environment': {'APP_MODE': 'local'}}}})
        return running if 'ps' in args else ''
    monkeypatch.setattr(start_local, 'run', docker)
    monkeypatch.setattr(start_local, 'port_free', lambda port: free)
    monkeypatch.setattr(sys, 'argv', ['start_local.py', '--directory', str(tmp_path / 'workspace'), *arguments])
    start_local.main()
    return calls


def test_a_new_workspace_runs_its_own_router_and_moves_to_another_port_on_request(tmp_path, monkeypatch, capsys):
    runtime = tmp_path / 'runtime'
    (runtime / 'region').mkdir(parents=True)
    (runtime / 'region/valhalla.json').write_text('{}')
    monkeypatch.setattr(start_local, 'RUNTIME', runtime)
    calls = launch(monkeypatch, tmp_path, '--routing-config', 'region/valhalla.json')
    up = next(c for c in calls if 'up' in c)
    assert str(ROOT / 'deploy/docker-compose.routing.yml') in up
    directory = tmp_path / 'workspace'
    env = (directory / 'workspace.env').read_text(encoding='utf-8')
    assert 'ROUTING_CONFIG=region/valhalla.json' in env and 'PUBLIC_ORIGIN=http://localhost:8007' in env
    # Later: the same workspace on another port, and its router kept.
    with (directory / 'workspace.env').open('a', encoding='utf-8') as file:
        file.write('\n# An operator note\n')  # Comments and blank lines are allowed.
    calls = launch(monkeypatch, tmp_path, '--port', '8010')
    assert str(ROOT / 'deploy/docker-compose.routing.yml') in next(c for c in calls if 'up' in c)
    assert 'PUBLIC_ORIGIN=http://localhost:8010' in (directory / 'workspace.env').read_text(encoding='utf-8')
    assert '127.0.0.1:8010:8000' in (directory / 'workspace.yml').read_text(encoding='utf-8')
    assert json.loads((directory / 'workspace.json').read_text())['origin'] == 'http://localhost:8010'
    assert 'now uses http://localhost:8010' in capsys.readouterr().out


def test_a_taken_port_or_a_missing_image_is_explained_before_anything_starts(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as stopped:
        launch(monkeypatch, tmp_path, '--port', '8011', free=False)
    assert stopped.value.code == 1
    assert 'Port 8011 is already in use by another program' in capsys.readouterr().err
    # A rerun while this workspace is already running is not refused for its own port.
    assert any('up' in c for c in launch(monkeypatch, tmp_path, free=False, running='container-id\n'))

    def no_image(args):
        if 'inspect' in args:
            raise RuntimeError('docker inspect failed (exit 1): No such image')
        return json.dumps({'services': {'postgres': {}, 'api': {'ports': [{'host_ip': '127.0.0.1'}],
                                                                'environment': {'APP_MODE': 'local'}}}}) if 'config' in args else ''
    monkeypatch.setattr(start_local, 'run', no_image)
    with pytest.raises(SystemExit):
        start_local.main()
    assert 'docker build --target runtime -t tournevia-app:local .' in capsys.readouterr().err
