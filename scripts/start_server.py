"""Prepare or start a persistent HTTPS Tournevia installation on a Linux VPS.

Requires a built application image, a road router (a private Valhalla URL, or a graph
under deploy/runtime that runs inside the installation) and HTTPS: operator-managed TLS
certificates for the bundled proxy, or an HTTPS reverse proxy that already serves the
server (--behind-proxy). Does not purchase services, issue certificates or build graphs.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

from start_local import ROOT, hide, run, switch_database, upgrade_image

RUNTIME = ROOT / 'deploy/runtime'


def validate(origin: str, routing: str | None, certificate_dir: Path | None, *, proxy_network: str | None = None,
             routing_config: str | None = None) -> None:
    url = urlsplit(origin)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment or url.port not in (None, 443):
        raise ValueError('Origin must be an HTTPS hostname on port 443, without credentials, path or query.')
    if routing_config:
        path = Path(routing_config)
        if path.is_absolute() or '..' in path.parts or path.suffix != '.json' or not (RUNTIME / path).is_file():
            raise ValueError('The routing configuration must be an existing Valhalla JSON file, given relative to deploy/runtime.')
    else:
        router = urlsplit(routing or '')
        if router.scheme not in ('http', 'https') or not router.hostname or router.username or router.password or router.query or router.fragment:
            raise ValueError('Supply the private Valhalla HTTP(S) base URL, without credentials or query.')
    if proxy_network:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', proxy_network):
            raise ValueError("Name the HTTPS proxy's Docker network, for example coolify.")
        return
    if certificate_dir is None:
        raise ValueError('Supply a certificate directory, or the Docker network of the HTTPS proxy in front.')
    for name in ('fullchain.pem', 'privkey.pem'):
        if not (certificate_dir / name).is_file():
            raise ValueError(f'The certificate directory must contain {name}.')


def prepare(directory: Path, image: str, origin: str, routing: str | None, dataset: str, certificate_dir: Path | None, *,
            proxy_network: str | None = None, routing_config: str | None = None) -> dict:
    validate(origin, routing, certificate_dir, proxy_network=proxy_network, routing_config=routing_config)
    if routing_config:
        routing_config = Path(routing_config).as_posix()
    settings = dict(image=image, origin=origin.rstrip('/'), routing=None if routing_config else routing, dataset=dataset,
                    certificate_dir=None if proxy_network else str(certificate_dir), proxy_network=proxy_network,
                    routing_config=routing_config)
    if any('\n' in value or '\r' in value or '$' in value for value in settings.values() if value):
        raise ValueError('Configuration values cannot contain newlines or dollar interpolation.')
    state_path = directory / 'workspace.json'
    if state_path.exists():
        state = json.loads(state_path.read_text())
        # Installations from before the proxy and routing options have neither key.
        if any(state.get(key) != value for key, value in settings.items()):
            raise ValueError('Existing installation differs. Follow the backup/upgrade procedure; credentials and configuration were preserved.')
        return state
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(directory.iterdir()):
        raise ValueError('Use a new empty directory or the matching existing installation.')
    state = dict(project='tournevia-server-' + uuid4().hex, **settings)
    config = {key: secrets.token_urlsafe(32) for key in ('POSTGRES_PASSWORD', 'RUNTIME_DB_PASSWORD', 'MIGRATION_DB_PASSWORD', 'SETUP_TOKEN')}
    config.update(POSTGRES_DB='vrp', POSTGRES_USER='vrp_owner', RUNTIME_DB_USER='vrp_app', MIGRATION_DB_USER='vrp_migrator',
                  APP_MODE='shared', PUBLIC_ORIGIN=state['origin'], TLS_PUBLIC_ORIGIN=state['origin'],
                  ROUTING_DATASET=dataset, MATRIX_CONCURRENCY='1')
    if proxy_network:
        # Traefik labels; the entrypoint and resolver names are Coolify's. Edit them here for another Traefik.
        config.update(PROXY_NETWORK=proxy_network, PROXY_HOST=urlsplit(origin).hostname, PROXY_ROUTER=state['project'],
                      PROXY_HTTPS_ENTRYPOINT='https', PROXY_HTTP_ENTRYPOINT='http', PROXY_CERT_RESOLVER='letsencrypt')
    else:
        config.update(TLS_CERT_DIR=str(certificate_dir))
    if routing_config:
        # The router's threads and memory cap; lower the cap on a server shared with other services.
        config.update(ROUTING_CONFIG=routing_config, ROUTING_THREADS='2', ROUTING_MEMORY='4g')
    else:
        config.update(VALHALLA_URL=routing)
    with os.fdopen(os.open(directory/'workspace.env', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'w', encoding='utf-8') as file:
        file.write(''.join(f'{key}={value}\n' for key,value in config.items()))
    (directory/'image.json').write_text(json.dumps({'services':{name:{'image':image,'platform':'linux/amd64'} for name in ('setup','api','worker')}}))
    with os.fdopen(os.open(directory/'private-setup.url', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'w', encoding='utf-8') as file:
        file.write('[InternetShortcut]\nURL='+state['origin']+'/#setup='+config['SETUP_TOKEN']+'\n')
    state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    return state


def compose_files(state: dict) -> list[str]:
    names = ['docker-compose.example.yml', *(['docker-compose.routing.yml'] if state.get('routing_config') else []),
             *(['docker-compose.proxied.yml'] if state.get('proxy_network') else ['docker-compose.https.yml', 'docker-compose.vps.yml'])]
    return [part for name in names for part in ('-f', str(ROOT / 'deploy' / name))]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--origin')
    tls=parser.add_mutually_exclusive_group()
    tls.add_argument('--certificates', type=Path, help='Directory with fullchain.pem and privkey.pem; the bundled proxy publishes port 443.')
    tls.add_argument('--behind-proxy', metavar='NETWORK', help="Docker network of an HTTPS reverse proxy that already serves this server, such as Coolify's coolify network.")
    router=parser.add_mutually_exclusive_group()
    router.add_argument('--valhalla-url', help='Private URL of a separately run Valhalla router.')
    router.add_argument('--routing-config', metavar='PATH', help='Valhalla configuration under deploy/runtime, for example region/valhalla.json; the router runs in the installation.')
    parser.add_argument('--dataset', help='Actual routing extract, date and speed assumptions.')
    parser.add_argument('--image', default='tournevia-app:local')
    parser.add_argument('--prepare-only', action='store_true', help='Validate merged configuration without starting containers or opening ports.')
    parser.add_argument('--upgrade-image', help='Back up this existing installation, stop API/worker, then start it with this built image.')
    parser.add_argument('--cutover-to', metavar='DATABASE', help='Switch this existing installation to a validated restored database.')
    args=parser.parse_args()
    directory=args.directory.resolve()
    if args.upgrade_image and args.cutover_to:
        parser.error('Upgrade the image and switch the database in separate runs.')
    if args.upgrade_image or args.cutover_to:
        if args.prepare_only or not (directory/'workspace.json').exists():
            parser.error('--upgrade-image and --cutover-to need an existing installation directory and start it.')
    elif not (args.origin and (args.certificates or args.behind_proxy) and (args.valhalla_url or args.routing_config) and args.dataset):
        parser.error('--origin, --certificates or --behind-proxy, --valhalla-url or --routing-config, and --dataset are required '
                     'for a new or matching installation.')
    try:
        if args.upgrade_image or args.cutover_to:
            state=json.loads((directory/'workspace.json').read_text())
        else:
            state=prepare(directory,args.image,args.origin,args.valhalla_url,args.dataset,args.certificates and args.certificates.resolve(),
                          proxy_network=args.behind_proxy,routing_config=args.routing_config)
        config=dict(line.split('=',1) for line in (directory/'workspace.env').read_text().splitlines() if line.strip() and not line.startswith('#'))
        # Command errors are shown now; the generated credentials in them never are.
        hide(value for name, value in config.items() if name.endswith(('PASSWORD', 'TOKEN')))
        os.environ.update(config)
        compose=['docker','compose','--env-file',str(directory/'workspace.env'),'-p',state['project'],
                 *compose_files(state),'-f',str(directory/'image.json')]
        merged=json.loads(run([*compose,'config','--format','json']))['services']
        if any(merged.get(name, {}).get('ports') for name in ('api', 'postgres', 'valhalla', 'edge')):
            raise ValueError('Refusing to publish API, PostgreSQL, router or proxied-site ports.')
        if any(merged[name]['environment']['APP_MODE']!='shared' or merged[name]['environment']['PUBLIC_ORIGIN']!=state['origin'] for name in ('setup','api','worker')):
            raise ValueError('Shared mode and exact HTTPS origin are required for every application service.')
        if state.get('proxy_network'):
            try:
                run(['docker','network','inspect',state['proxy_network']])
            except RuntimeError:
                raise ValueError(f'Docker network {state["proxy_network"]} was not found. Name the network your HTTPS proxy '
                                 'uses (see docker network ls).') from None
        if args.cutover_to:
            print(switch_database(directory, compose, args.cutover_to))
        elif args.prepare_only:
            print('Configuration prepared and validated. No containers started; no ports opened.')
        else:
            if args.upgrade_image:
                state,backup=upgrade_image(directory,state,compose,args.upgrade_image)
                print('Pre-upgrade backup saved: '+str(backup),flush=True)
            run(['docker','image','inspect',state['image']])
            try:
                run([*compose,'up','-d','--no-build','--wait','--wait-timeout','180'])
            except RuntimeError:
                if args.upgrade_image:
                    raise RuntimeError('Upgrade did not start. If setup failed, API and worker stay stopped and the database is preserved. '
                                       f'Previous image: {state["previous_image"]}; see the upgrade and backout procedure.') from None
                raise
            print('Started '+state['origin']+'. Verify trusted TLS, sign-in and routing before use.')
        print('Private first-use shortcut: '+str(directory/'private-setup.url'))
        print('Keep this installation directory private. Reruns preserve its database and credentials.')
    except (ValueError, RuntimeError) as error:
        parser.exit(1, str(error)+'\n')


if __name__=='__main__':
    main()
