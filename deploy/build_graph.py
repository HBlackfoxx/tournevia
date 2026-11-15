"""Run inside the pinned Valhalla image with /data and /workspace mounts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from configure_matrix import configure_matrix

pins = json.loads((Path(__file__).parent / 'engines.json').read_text())
parser = argparse.ArgumentParser()
parser.add_argument('dataset', choices=['region', 'restrictions'])
parser.add_argument('--extract', choices=sorted(pins.get('extracts', {})),
                    help='Build this pinned extract (for example france) into /data/<extract> instead of the '
                         'Île-de-France test region in /data/region.')
parser.add_argument('--accept-new-extract', action='store_true',
                    help='Build from a regional extract other than the pinned one; rerun engine acceptance and update engines.json.')
parser.add_argument('--jobs', type=int, default=2, help='Build threads (default 2).')
parser.add_argument('--max-matrix-km', type=int, default=200,
                    help='Longest straight-line distance between two points of one calculation (default 200 km).')
args = parser.parse_args()
if args.extract and args.dataset != 'region':
    parser.error('--extract applies to the region dataset only.')
directory = Path('/data') / (args.extract or args.dataset)
if (directory / 'tiles').exists():
    raise SystemExit('Graph already exists; use a new empty runtime directory for a rebuild.')
if args.extract:
    pinned = pins['extracts'][args.extract]
    pbf, expected = Path('/data') / pinned['file'], pinned['sha256']
else:
    pbf, expected = (Path('/data/ile-de-france.osm.pbf'), pins['region_sha256']) if args.dataset == 'region' else (Path('/data/restrictions.osm.pbf'), None)
if expected and not args.accept_new_extract:
    # Geofabrik's "latest" URL changes; tests and benchmarks hold for the pinned extract only.
    digest = hashlib.sha256()
    with pbf.open('rb') as source:
        while chunk := source.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise SystemExit(f'{pbf.name} is not the pinned extract (sha256 {expected}). '
                         'Use the pinned file, or pass --accept-new-extract and rerun engine acceptance.')
directory.mkdir(parents=True, exist_ok=True)
config = json.loads(subprocess.check_output(['valhalla_build_config']))
config['mjolnir'].update(tile_dir=str(directory / 'tiles'), tile_extract='',
                         admin=str(directory / 'admins.sqlite'),
                         timezone=str(directory / 'timezones.sqlite'), concurrency=args.jobs)
config['httpd']['service']['listen'] = 'tcp://*:8002'
config['service_limits']['truck'].update(max_matrix_distance=args.max_matrix_km * 1000, max_matrix_location_pairs=10000)
config['loki']['actions'] = ['route', 'sources_to_targets', 'locate', 'status']
configure_matrix(config)
path = directory / 'valhalla.json'
path.write_text(json.dumps(config, indent=2))
subprocess.run(['valhalla_build_admins', '-c', str(path), str(pbf)], check=True)
subprocess.run(['valhalla_build_tiles', '-c', str(path), '-j', str(args.jobs), str(pbf)], check=True)
print(f'Built {directory.name}; no traffic feeds installed.')
