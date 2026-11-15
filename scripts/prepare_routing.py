"""Download the Île-de-France road data and build its routing graph, on any system with Docker.

    python scripts/prepare_routing.py

Downloads Geofabrik's current Île-de-France extract into deploy/runtime (kept for later
runs), builds the graph with the pinned Valhalla image into deploy/runtime/region, and
records which extract it was built from in region/source.json, which
`start_local.py --routing-config region/valhalla.json` uses to label plans. Run it once;
it stops early when the graph already exists.

The tests and benchmarks were run on one pinned extract (see deploy/engines.json).
Geofabrik replaces its file regularly, so a new download is usually newer: it routes the
same way for daily use, but rerun the engine tests before relying on recorded results.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((ROOT / 'deploy/engines.json').read_text(encoding='utf-8'))
EXTRACT = 'ile-de-france.osm.pbf'
LABEL = 'Geofabrik Ile-de-France'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path) -> None:
    """Into `target` through a partial file, so an interrupted download is never mistaken for the extract."""
    partial = target.with_name(target.name + '.partial')
    with urllib.request.urlopen(url, timeout=60) as response, partial.open('wb') as out:
        total, done, shown = int(response.headers.get('Content-Length') or 0), 0, -1
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and (step := done * 10 // total) != shown:
                shown = step
                print(f'  {done >> 20} of {total >> 20} MB', flush=True)
    partial.replace(target)


def docker(*args: str) -> None:
    """Run a Docker command with its own output visible, so a failure explains itself."""
    if subprocess.run(['docker', *args]).returncode:
        raise SystemExit(f'docker {args[0]} failed; see its message above.')


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runtime', type=Path, default=ROOT / 'deploy/runtime',
                        help='Folder for the road data and graph (default deploy/runtime, which the launchers use).')
    parser.add_argument('--memory', default='6g', help='Memory limit for the graph build (default 6g).')
    parser.add_argument('--jobs', type=int, default=2, help='Build threads (default 2).')
    args = parser.parse_args(argv)
    if shutil.which('docker') is None:
        raise SystemExit('Docker is not installed or not on PATH. Install Docker Desktop (Windows, macOS) or Docker Engine (Linux).')
    runtime = args.runtime.resolve()
    graph = runtime / 'region'
    if (graph / 'tiles').exists():
        print(f'The graph already exists in {graph}; nothing to do.')
        print('Next: python scripts/start_local.py --routing-config region/valhalla.json')
        return
    runtime.mkdir(parents=True, exist_ok=True)
    extract = runtime / EXTRACT
    if extract.exists():
        print(f'Using the extract already downloaded: {extract}')
    else:
        print(f'Downloading the Île-de-France road data (about {PINS["region_bytes"] >> 20} MB) from Geofabrik...', flush=True)
        download(PINS['region_source'], extract)
    digest = sha256(extract)
    pinned = digest == PINS['region_sha256']
    downloaded = datetime.fromtimestamp(extract.stat().st_mtime, timezone.utc).date()
    if not pinned:
        print(f'This extract (downloaded {downloaded}) is not the one the tests and benchmarks used '
              f'({PINS["downloaded_on"]}); it is built anyway and plans name it.', flush=True)
    docker('pull', PINS['valhalla_image'])
    # On Linux and macOS the graph files belong to you, not to root, so you can move or delete them.
    owner = ['--user', f'{os.getuid()}:{os.getgid()}'] if hasattr(os, 'getuid') else []
    build = ['run', '--rm', *owner, '--memory', args.memory, '--cpus', str(args.jobs), '--entrypoint', 'python3',
             '-v', f'{runtime}:/data', '-v', f'{ROOT / "deploy"}:/workspace:ro', PINS['valhalla_image'],
             '/workspace/build_graph.py', 'region', '--jobs', str(args.jobs),
             *([] if pinned else ['--accept-new-extract'])]
    print('Building the graph; this takes a few minutes...', flush=True)
    docker(*build)
    source = {'label': LABEL, 'source': PINS['region_source'], 'downloaded_on': str(downloaded), 'sha256': digest,
              'bytes': extract.stat().st_size, 'pinned': pinned, 'built_on': str(date.today())}
    (graph / 'source.json').write_text(json.dumps(source, indent=2) + '\n', encoding='utf-8')
    print(f'Built {graph}.')
    print('Next: python scripts/start_local.py --routing-config region/valhalla.json')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
