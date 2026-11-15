"""Preparing the routing graph: download once, build with the pinned image, record the extract."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_routing  # noqa: E402


class Download(io.BytesIO):
    headers = {'Content-Length': '4'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def prepared(monkeypatch, runtime, content=b'road', pin=None):
    """Run the script with the network and Docker replaced; returns the Docker commands."""
    commands, fetched = [], []
    monkeypatch.setattr(prepare_routing.shutil, 'which', lambda name: 'docker')
    monkeypatch.setattr(prepare_routing.urllib.request, 'urlopen',
                        lambda url, timeout: fetched.append(url) or Download(content))

    def docker(args):
        commands.append(args)
        if 'run' in args:  # The build writes its graph.
            (runtime / 'region/tiles').mkdir(parents=True)
        return type('Done', (), {'returncode': 0})()
    monkeypatch.setattr(prepare_routing.subprocess, 'run', docker)
    monkeypatch.setitem(prepare_routing.PINS, 'region_sha256', pin or hashlib.sha256(b'pinned').hexdigest())
    prepare_routing.main(['--runtime', str(runtime)])
    return commands, fetched


def test_a_new_extract_is_downloaded_built_with_its_own_label_and_never_mistaken_for_the_pinned_one(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    commands, fetched = prepared(monkeypatch, runtime)
    assert fetched == [prepare_routing.PINS['region_source']]
    assert (runtime / 'ile-de-france.osm.pbf').read_bytes() == b'road'
    assert not (runtime / 'ile-de-france.osm.pbf.partial').exists()
    pull, build = commands
    assert pull == ['docker', 'pull', prepare_routing.PINS['valhalla_image']]
    assert build[-4:] == ['region', '--jobs', '2', '--accept-new-extract']
    assert f'{runtime.resolve()}:/data' in build
    assert ('--user' in build) == hasattr(os, 'getuid')
    source = json.loads((runtime / 'region/source.json').read_text(encoding='utf-8'))
    assert source['label'] == 'Geofabrik Ile-de-France' and source['pinned'] is False
    assert source['sha256'] == hashlib.sha256(b'road').hexdigest()


def test_the_pinned_extract_is_checked_as_pinned_and_a_built_graph_is_left_alone(tmp_path, monkeypatch, capsys):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'ile-de-france.osm.pbf').write_bytes(b'pinned')
    commands, fetched = prepared(monkeypatch, runtime, pin=hashlib.sha256(b'pinned').hexdigest())
    assert fetched == [] and '--accept-new-extract' not in commands[-1]
    assert json.loads((runtime / 'region/source.json').read_text(encoding='utf-8'))['pinned'] is True
    commands, _ = prepared(monkeypatch, runtime)
    assert commands == [] and 'already exists' in capsys.readouterr().out


def test_without_docker_it_says_what_to_install(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_routing.shutil, 'which', lambda name: None)
    with pytest.raises(SystemExit, match='Docker is not installed'):
        prepare_routing.main(['--runtime', str(tmp_path)])
