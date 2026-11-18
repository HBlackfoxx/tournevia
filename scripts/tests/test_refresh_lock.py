"""The lock writer keeps the committed file's form; installing and PyPI are not exercised here."""
import importlib.util
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('refresh_lock', ROOT/'scripts/refresh_lock.py')
refresh_lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh_lock)
ENTRY = re.compile(r'^(\S+)==(\S+) \\\n((?: {4}--hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)', re.MULTILINE)


def test_writer_reproduces_the_committed_lock_from_its_own_entries():
    text = refresh_lock.LOCK.read_text(encoding='utf-8')
    header = ''.join(line for line in text.splitlines(keepends=True) if line.startswith('#'))
    packages = {(name, version): re.findall(r'[0-9a-f]{64}', hashes)[::-1] for name, version, hashes in ENTRY.findall(text)}
    assert len(packages) > 20 and refresh_lock.render(header, packages) == text


def test_lock_is_built_in_the_image_the_application_runs_on():
    image = refresh_lock.python_image()
    assert re.fullmatch(r'python:3\.\d+-slim-\w+@sha256:[0-9a-f]{64}', image)
    assert f'FROM {image} AS runtime' in (ROOT/'Dockerfile').read_text(encoding='utf-8')
