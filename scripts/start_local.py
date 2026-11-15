"""Start an isolated authenticated local workspace; preserve it on later starts.

    python scripts/start_local.py --routing-config region/valhalla.json

runs the router inside the workspace with the graph from `prepare_routing.py`. Without
--routing-config the workspace uses a router you started yourself on port 8002 of this
computer. Later runs restart the same workspace; --port and --routing-config change it.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'deploy/runtime'
# Generated passwords and tokens: never shown, even inside a command's error message.
HIDDEN: set[str] = set()
VERBS = ('config', 'up', 'stop', 'ps', 'inspect', 'exec', 'run', 'pull')


def hide(values) -> None:
    HIDDEN.update(value for value in values if len(value) >= 8)


def run(args: list[str]) -> str:
    """A command's output. On failure, its own error message (never its output, which for
    `compose config` holds the credentials), with every generated secret blanked."""
    result = subprocess.run(args, text=True, encoding='utf-8', capture_output=True, timeout=180)
    if result.returncode:
        name = ' '.join([Path(args[0]).name, *(['compose'] if args[1:2] == ['compose'] else []),
                         *[a for a in args[1:] if a in VERBS][:1]])
        detail = '\n'.join((result.stderr or '').strip().splitlines()[-15:])
        for value in HIDDEN:
            detail = detail.replace(value, '***')
        raise RuntimeError(f'{name} failed (exit {result.returncode})' + (f':\n{detail}' if detail else '.'))
    return result.stdout


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            return False
    return True


def overrides(port: int) -> str:
    """This workspace's Compose override: its port, no published database, and
    host.docker.internal on Linux Docker Engine too, for a router started on this computer."""
    return f'''services:
  postgres:
    ports: !reset []
  api:
    ports: !override ["127.0.0.1:{port}:8000"]
    extra_hosts: ["host.docker.internal:host-gateway"]
  worker:
    extra_hosts: ["host.docker.internal:host-gateway"]
'''


def set_values(directory: Path, values: dict[str, str]) -> None:
    """Change settings in workspace.env, keeping its other lines and its private permissions."""
    path = directory / 'workspace.env'
    lines = path.read_text(encoding='utf-8').splitlines()
    kept = [line for line in lines if line.split('=', 1)[0] not in values]
    partial = directory / 'workspace.env.partial'
    with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as file:
        file.write(''.join(line + '\n' for line in [*kept, *(f'{k}={v}' for k, v in values.items())]))
    partial.replace(path)


def routing_settings(config: str) -> dict[str, str]:
    """workspace.env values for a router inside the workspace, using this graph configuration."""
    path = Path(config)
    if path.is_absolute() or '..' in path.parts or path.suffix != '.json' or not (RUNTIME / path).is_file():
        raise ValueError('--routing-config must name an existing Valhalla JSON file under deploy/runtime, '
                         'for example region/valhalla.json from scripts/prepare_routing.py.')
    values = {'ROUTING_CONFIG': path.as_posix(), 'ROUTING_THREADS': '2', 'ROUTING_MEMORY': '4g'}
    # Plans name the road data they were calculated on: the extract prepare_routing.py built.
    source = RUNTIME / path.parent / 'source.json'
    if source.is_file():
        built = json.loads(source.read_text(encoding='utf-8'))
        values['ROUTING_DATASET'] = f"{built['label']} downloaded {built['downloaded_on']}; static OSM speeds"
    if any(c in v for v in values.values() for c in '\n\r$'):
        raise ValueError('The routing configuration path cannot contain newlines or dollar signs.')
    return values


def upgrade_image(directory: Path, state: dict, compose: list[str], image: str) -> tuple[dict, Path]:
    """Back up, stop API/worker and record a new image; the caller then starts setup.

    Setup runs migrations before API/worker start. If it fails they stay stopped,
    the database is preserved and the previous image remains recorded.
    """
    if not image or any(character in image for character in ('\n', '\r', '$')):
        raise ValueError('Image names cannot be empty or contain newlines or dollar interpolation.')
    if image == state['image']:
        raise ValueError('This installation already uses that image.')
    run(['docker', 'image', 'inspect', image])
    try:
        from database_backup import BackupError, workspace_database
    except ImportError:
        raise ValueError('The pre-upgrade backup needs the backend Python packages; install backend/requirements.txt.') from None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    try:
        backup = workspace_database(directory).backup(directory / 'backups' / f'before-upgrade-{stamp}.dump')
    except BackupError as error:
        raise ValueError(f'Pre-upgrade backup failed; nothing was changed. {error}') from None
    run([*compose, 'stop', 'api', 'worker'])
    images = json.loads((directory / 'image.json').read_text(encoding='utf-8'))
    for service in images['services'].values():
        service['image'] = image
    state = {**state, 'previous_image': state['image'], 'image': image}
    for name, value in (('image.json', images), ('workspace.json', state)):
        partial = directory / (name + '.partial')
        partial.write_text(json.dumps(value, indent=2), encoding='utf-8')
        partial.replace(directory / name)
    return state, backup


def switch_database(directory: Path, compose: list[str], target: str) -> str:
    """Point this workspace at a validated restored database and restart it (database_backup.cutover)."""
    from database_backup import BackupError, cutover, switched, workspace_database
    try:
        previous = cutover(workspace_database(directory), compose, directory / 'workspace.env', target)
    except BackupError as error:
        raise RuntimeError(str(error)) from None
    return switched(target, previous, f'rerun this launcher with --directory {directory} --cutover-to {previous}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='tournevia-app:local', help='Built runtime image for a new workspace.')
    parser.add_argument('--port', type=int, help='Local port (default 8007); given again later, it moves the workspace.')
    parser.add_argument('--routing-config', metavar='PATH',
                        help='Run the router inside the workspace with this graph configuration under deploy/runtime, '
                             'for example region/valhalla.json.')
    parser.add_argument('--directory', type=Path, default=ROOT / 'deploy/runtime/local-workspace')
    parser.add_argument('--upgrade-image', help='Back up this existing workspace, then switch it to this built image.')
    parser.add_argument('--cutover-to', metavar='DATABASE', help='Switch this existing workspace to a validated restored database.')
    args = parser.parse_args()
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error('Choose an unprivileged local port between 1024 and 65535.')
    try:
        routing = routing_settings(args.routing_config) if args.routing_config else None
    except ValueError as error:
        parser.error(str(error))
    directory = args.directory.resolve()
    state_file = directory / 'workspace.json'
    if args.upgrade_image and args.cutover_to:
        parser.error('Upgrade the image and switch the database in separate runs.')
    if (args.upgrade_image or args.cutover_to) and not state_file.exists():
        parser.error('--upgrade-image and --cutover-to need an existing workspace directory.')
    if not state_file.exists():
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(directory.iterdir()):
            parser.error('Use a new empty directory or an existing workspace directory.')
        project = 'vrp-local-' + uuid4().hex
        origin = f'http://localhost:{args.port or 8007}'
        config = {name: secrets.token_urlsafe(32) for name in
                  ('POSTGRES_PASSWORD', 'RUNTIME_DB_PASSWORD', 'MIGRATION_DB_PASSWORD', 'SETUP_TOKEN')}
        config.update(POSTGRES_DB='vrp', POSTGRES_USER='vrp_owner', RUNTIME_DB_USER='vrp_app',
                      MIGRATION_DB_USER='vrp_migrator', APP_MODE='local', PUBLIC_ORIGIN=origin,
                      MATRIX_CONCURRENCY='1', VALHALLA_URL='http://host.docker.internal:8002')
        with os.fdopen(os.open(directory/'workspace.env', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600),'w') as file:
            file.write(''.join(f'{name}={value}\n' for name,value in config.items()))
        (directory/'image.json').write_text(json.dumps({'services': {
            name: {'image':args.image} for name in ('setup','api','worker')}}))
        state_file.write_text(json.dumps({'project':project,'origin':origin,'image':args.image}))
    state = json.loads(state_file.read_text())
    port = int(state['origin'].rsplit(':', 1)[1])
    if args.port is not None and args.port != port:
        # Moving the workspace to another port changes its address, which the server checks on every write.
        port, state['origin'] = args.port, f'http://localhost:{args.port}'
        set_values(directory, {'PUBLIC_ORIGIN': state['origin']})
        state_file.write_text(json.dumps(state))
        print(f'This workspace now uses {state["origin"]}.', flush=True)
    if routing and state.get('routing_config') != routing['ROUTING_CONFIG']:
        set_values(directory, routing)
        state['routing_config'] = routing['ROUTING_CONFIG']
        state_file.write_text(json.dumps(state))
    # Rewritten on every start, so workspaces created by earlier versions gain what it adds.
    (directory/'workspace.yml').write_text(overrides(port))
    config = dict(line.split('=',1) for line in (directory/'workspace.env').read_text().splitlines()
                  if '=' in line and not line.lstrip().startswith('#'))  # An operator may add comments.
    hide(config[name] for name in ('POSTGRES_PASSWORD', 'RUNTIME_DB_PASSWORD', 'MIGRATION_DB_PASSWORD', 'SETUP_TOKEN')
         if name in config)
    os.environ.update(config)  # Workspace values win over caller shell defaults.
    compose = ['docker','compose','--env-file',str(directory/'workspace.env'),'-p',state['project'],
               '-f',str(ROOT/'deploy/docker-compose.example.yml'),
               *(['-f',str(ROOT/'deploy/docker-compose.routing.yml')] if state.get('routing_config') else []),
               '-f',str(directory/'workspace.yml'),'-f',str(directory/'image.json')]
    merged = json.loads(run([*compose,'config','--format','json']))
    assert not merged['services']['postgres'].get('ports')
    assert all(port['host_ip']=='127.0.0.1' for port in merged['services']['api']['ports'])
    assert merged['services']['api']['environment']['APP_MODE']=='local'
    if args.cutover_to:
        try:
            print(switch_database(directory, compose, args.cutover_to))
        except RuntimeError as error:
            parser.exit(1, str(error)+'\n')
        return
    if args.upgrade_image:
        try:
            state, backup = upgrade_image(directory, state, compose, args.upgrade_image)
        except (ValueError, RuntimeError) as error:
            parser.exit(1, str(error)+'\n')
        print('Pre-upgrade backup saved: '+str(backup), flush=True)
    try:
        run(['docker','image','inspect',state['image']])
    except RuntimeError:
        parser.exit(1, f'The image {state["image"]} does not exist yet. Build it first, from the repository folder:\n'
                       f'  docker build -f deploy/Dockerfile.vroom -t tournevia-vroom:1.14.0 .\n'
                       f'  docker build --target runtime -t {state["image"]} .\n')
    if not run([*compose,'ps','--status','running','--quiet','api']).strip() and not port_free(port):
        parser.exit(1, f'Port {port} is already in use by another program. Stop that program, or move this '
                       f'workspace to a free port with --port, for example --port {port + 1}.\n')
    print('Starting your local workspace at '+state['origin'],flush=True)
    try:
        run([*compose,'up','-d','--no-build','--wait','--wait-timeout','180' if state.get('routing_config') else '90'])
    except RuntimeError as error:
        if args.upgrade_image:
            parser.exit(1, 'Upgrade did not start. If setup failed, API and worker stay stopped and the database is preserved. '
                           f'Previous image: {state["previous_image"]}; see the upgrade and backout procedure.\n')
        parser.exit(1, f'The workspace did not start. {error}\n'
                       f'Its containers are in the Compose project {state["project"]}: '
                       f'`docker compose -p {state["project"]} logs` shows what each one reported.\n')
    # Fragment is never sent in HTTP requests or access logs. The frontend removes
    # it after loading; once initialized the server permanently rejects reuse.
    link = directory/'private-setup.url'
    with os.fdopen(os.open(link,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as file:
        file.write('[InternetShortcut]\nURL='+state['origin']+'/#setup='+config['SETUP_TOKEN']+'\n')
    print('Open the private first-use shortcut: '+str(link))
    print('After setup, use '+state['origin']+'. Records persist in this workspace; other installations are unchanged.')
    if not state.get('routing_config'):
        print('Calculations need a router on port 8002 of this computer; to run one inside this workspace instead, '
              'build the graph with scripts/prepare_routing.py and rerun with --routing-config region/valhalla.json.')


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:  # A Docker command failed: say which and why, without a traceback.
        raise SystemExit(str(error)) from None
