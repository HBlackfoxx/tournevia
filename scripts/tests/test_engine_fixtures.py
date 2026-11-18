"""The fixture generator and the committed planning fixture stay in step."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('create_engine_fixtures', ROOT/'scripts/create_engine_fixtures.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def test_generator_reproduces_the_committed_paris_fixture():
    # The real-roads workflow regenerates this file before it builds the image, whose frontend
    # build type-checks tests against it; key order is part of the file.
    committed = (ROOT/'backend/tests/fixtures/paris-ten.json').read_text(encoding='utf-8')
    assert json.dumps(fixtures.paris(), indent=2) + '\n' == committed
