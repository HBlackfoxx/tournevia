"""Refresh backend/requirements.lock from backend/requirements.txt.

    python scripts/refresh_lock.py

Installs requirements.txt in the Python image the application runs on (the runtime stage of the
Dockerfile), records every version pip installed, and writes each one with the SHA-256 of every file
PyPI publishes for it. Needs Docker and access to PyPI. Afterwards build the image, which installs
the lock with --require-hashes, and run the checks in docs/developer/upgrading.md.
"""
import json
from pathlib import Path
import re
import subprocess
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'backend/requirements.lock'


def python_image() -> str:
    """The digest-pinned image of the Dockerfile's runtime stage."""
    stage = re.search(r'^FROM (python:\S+) AS runtime$', (ROOT / 'Dockerfile').read_text(encoding='utf-8'), re.MULTILINE)
    if not stage:
        raise SystemExit('The Dockerfile has no "FROM python:… AS runtime" line.')
    return stage.group(1)


def installed(image: str) -> list[tuple[str, str]]:
    """Name and version of everything pip installs for requirements.txt in that image."""
    frozen = subprocess.check_output(
        ['docker', 'run', '--rm', '-v', f'{ROOT / "backend/requirements.txt"}:/requirements.txt:ro', image, 'sh', '-c',
         'pip install --quiet --no-cache-dir --root-user-action ignore -r /requirements.txt && pip check && pip freeze'],
        text=True)
    return [tuple(line.split('==')) for line in frozen.splitlines() if '==' in line]


def published(name: str, version: str) -> list[str]:
    """SHA-256 of every file PyPI publishes for one version."""
    with urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as response:
        return [file['digests']['sha256'] for file in json.load(response)['urls']]


def render(header: str, packages: dict[tuple[str, str], list[str]]) -> str:
    """The lock file: its header, then each package in name order with its hashes in order."""
    lines = []
    for (name, version), hashes in sorted(packages.items(), key=lambda item: item[0][0].lower()):
        lines.append(' \\\n'.join([f'{name}=={version}', *(f'    --hash=sha256:{value}' for value in sorted(hashes))]))
    return header + '\n'.join(lines) + '\n'


def main():
    header = ''.join(line for line in LOCK.read_text(encoding='utf-8').splitlines(keepends=True) if line.startswith('#'))
    image = python_image()
    print(f'Installing backend/requirements.txt in {image.split("@")[0]}...', flush=True)
    packages = {}
    for name, version in installed(image):
        packages[name, version] = published(name, version)
        print(f'  {name}=={version}: {len(packages[name, version])} files', flush=True)
    LOCK.write_text(render(header, packages), encoding='utf-8', newline='\n')
    print(f'Wrote {LOCK.relative_to(ROOT)} with {len(packages)} packages. Build the image and run the checks.')


if __name__ == '__main__':
    main()
