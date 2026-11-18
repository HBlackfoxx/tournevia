"""The tag file graphs are built with stays Valhalla's own file plus the maxweightrating reading."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('graph_tags', ROOT/'deploy/graph_tags.py')
graph_tags = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph_tags)
OURS = graph_tags.FILE.read_text(encoding='utf-8')


def test_tag_file_is_the_pinned_valhalla_file_with_three_lines_changed():
    shipped = graph_tags.shipped(OURS)
    pins = json.loads((ROOT/'deploy/engines.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(shipped.encode()).hexdigest() == pins['valhalla_tag_file_sha256']
    assert 'maxweightrating' not in shipped
    assert [line.strip() for line in OURS.splitlines() if 'maxweightrating' in line and not line.startswith('--!')] == list(graph_tags.CHANGED)


def test_a_tag_file_without_the_reading_is_refused():
    with pytest.raises(ValueError, match='maxweightrating'):
        graph_tags.shipped(OURS.replace(' or kv["maxweightrating"]', ''))


def test_graph_build_refuses_a_builder_with_another_tag_file(tmp_path):
    builder = tmp_path/'valhalla_build_tiles'
    builder.write_bytes(b'\0binary\0' + graph_tags.shipped(OURS).encode() + b'\0more')
    assert graph_tags.tag_file(builder) == graph_tags.FILE
    builder.write_bytes(b'\0binary\0' + graph_tags.shipped(OURS).replace('maxaxleload', 'maxaxle_load').encode())
    with pytest.raises(SystemExit, match='not made from this Valhalla version'):
        graph_tags.tag_file(builder)
