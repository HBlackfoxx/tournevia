# Settings reference

Every environment variable Tournevia, its Compose files and its launchers use. Values
go in one file:

- a **launcher installation** (`scripts/start_local.py`, `scripts/start_server.py`) keeps
  them in `workspace.env` inside its private directory, written with private permissions;
  the launchers generate the passwords and change the routing and port settings for you;
- a **Compose installation by hand** uses `.env` at the repository root, copied from
  [env.example](env.example).

How values reach the application:

- Compose passes only the variables its files name. A line in `.env` or `workspace.env`
  that no Compose file uses never reaches a container.
- The application ignores unknown variables, and names are not case-sensitive. A
  misspelt name is therefore silently ignored and its default applies.
- A variable set in your shell wins over the file: unset any of these names in a shell
  you use for Docker Compose commands.
- Invalid values stop the API, the worker and setup at start, without echoing the value.

## Database

| Variable | Default | What it does |
|---|---|---|
| `POSTGRES_DB` | `vrp` | Name of the application database. The application's `DB_NAME` follows it. |
| `POSTGRES_USER` | `vrp_owner` | Bootstrap administrator role, used only by setup. |
| `POSTGRES_PASSWORD` | required | Bootstrap password. Takes effect only when the database volume is first created. |
| `RUNTIME_DB_USER` | `vrp_app` | Restricted role the API and worker use. |
| `RUNTIME_DB_PASSWORD` | required | Its password: at least 20 characters. |
| `MIGRATION_DB_USER` | `vrp_migrator` | Owns the schema; runs migrations, backups and the retention commands. |
| `MIGRATION_DB_PASSWORD` | required | Its password: at least 20 characters. |
| `POSTGRES_PORT` | `5432` | Loopback port where a hand-made Compose stack publishes PostgreSQL for host-side tools. Launcher installations publish no database port. |

The three role names must differ, and so must the three passwords. Database roles are
explained in [database roles, upgrades and recovery](README.md#database-roles-upgrades-and-recovery).

## Application

| Variable | Default | What it does |
|---|---|---|
| `APP_MODE` | `local` | `local`: named accounts on a loopback address. `shared`: named accounts behind HTTPS (the HTTPS and proxy overlays set it). `local_demo`: the fictional demonstration without accounts, which cannot mark plans ready. |
| `PUBLIC_ORIGIN` | `http://localhost:8000` | The exact address users open, for example `https://planner.example.com`: scheme and host, no path or trailing `/`. Every write must come from it. In local modes the host is `localhost` or `127.0.0.1`; in shared mode it is `https`. |
| `SETUP_TOKEN` | empty | Private first-use code, at least 32 characters. Empty disables first-use setup. |
| `MAP_STYLE_URL` | `https://tiles.openfreemap.org/styles/liberty` | Basemap style. Its origin is added to the page's Content-Security-Policy. |
| `MONITOR_TOKEN` | empty | Turns on `GET /api/v1/metrics` for a monitor sending it as a bearer token; at least 32 characters. See [monitoring](README.md#monitoring-and-alerts). |

## Road routing

| Variable | Default | What it does |
|---|---|---|
| `VALHALLA_URL` | `http://host.docker.internal:8002` in Compose | The router's base URL. The routing overlay replaces it with its own `http://valhalla:8002`. |
| `ROUTING_DATASET` | `Geofabrik Ile-de-France downloaded 2026-09-22; static OSM speeds` | The road-data label every plan records and shows. `start_local.py --routing-config` writes the label of the extract `prepare_routing.py` built. |
| `ROUTING_CONFIG` | `regional-matrix.json` | Routing overlay: the Valhalla configuration under `deploy/runtime`, for example `region/valhalla.json`. |
| `ROUTING_THREADS` | `2` | Routing overlay: router threads. |
| `ROUTING_MEMORY` | `4g` | Routing overlay: router memory limit. |
| `MATRIX_CONCURRENCY` | `1` | Matrix requests in flight at once, 1 to 4. Raise it only with router threads and memory to match. |
| `MATRIX_MAX_KM` | `200` | Largest distance between two points of one calculation, 1 to 2000. Must equal the graph build's `--max-matrix-km`. |
| `JOB_TIMEOUT_SECONDS` | `180` | Time budget for a whole calculation, 1 to 600. |

## Address lookup

| Variable | Default | What it does |
|---|---|---|
| `GEOCODER_URL` | empty | The installation's own Base Adresse Nationale geocoder. Empty: no installation geocoder (a company's administrator can still turn on IGN's public service in Settings). The geocoder overlay sets `http://addok:7878`. |
| `GEOCODER_DATASET` | `Base Adresse Nationale (Licence Ouverte 2.0)` | Source shown next to results, as the licence requires: set it to the bundle's source and date. |
| `GEOCODER_MEMORY` | `8g` | Geocoder overlay: memory limit of its Redis index. |
| `GEOCODER_WORKERS` | `2` | Geocoder overlay: addok worker processes. |

See [address lookup](README.md#address-lookup-optional).

## HTTPS and reverse proxy

| Variable | Default | What it does |
|---|---|---|
| `TLS_PUBLIC_ORIGIN` | required by the overlays | The HTTPS address; the HTTPS and proxy overlays use it as `PUBLIC_ORIGIN` and set `APP_MODE=shared`. |
| `TLS_CERT_DIR` | required by the HTTPS overlay | Directory holding `fullchain.pem` and `privkey.pem`, mounted read-only. |
| `HTTPS_PORT` | `8443` | Loopback port of the bundled HTTPS proxy. The VPS overlay publishes port 443 instead. |
| `PROXY_NETWORK` | required by the proxy overlay | Docker network of an existing HTTPS reverse proxy, for example `coolify`. |
| `PROXY_HOST` | required by the proxy overlay | The public host name the reverse proxy routes to Tournevia. |
| `PROXY_ROUTER` | `tournevia` | Name of the Traefik router, service and middleware; `start_server.py` uses the installation's project name. |
| `PROXY_HTTPS_ENTRYPOINT`, `PROXY_HTTP_ENTRYPOINT` | `https`, `http` | Traefik entrypoints (Coolify's names; Dokploy uses `websecure` and `web`). |
| `PROXY_CERT_RESOLVER` | `letsencrypt` | Traefik certificate resolver. |

`TRUST_PROXY_CLIENT_HEADER` is set to `true` by the HTTPS and proxy overlays, whose nginx
overwrites the client address header; no file variable controls it. See
[behind an existing HTTPS proxy](README.md#behind-an-existing-https-proxy).

## Host-side development only

Python run on the host reads these from `.env`; Compose sets its own values in the
containers.

| Variable | Default | What it does |
|---|---|---|
| `DB_HOST`, `DB_PORT` | `127.0.0.1`, `5432` | Where host-side Python finds PostgreSQL. |
| `DB_NAME`, `DB_USER` | `vrp`, `vrp_app` | Database and runtime role; match `POSTGRES_DB` and `RUNTIME_DB_USER`. |
| `DB_PASSWORD` | empty | Equal to `RUNTIME_DB_PASSWORD`. |
| `BIND_HOST`, `PORT` | `127.0.0.1`, `8000` | Where `python -m app.main` listens. In the image the API listens on `0.0.0.0:8000`. |
| `WEB_DIST` | `web/dist` | Where the API finds the built interface. |
| `DEMO_ORGANIZATION_ID` | `00000000-0000-4000-8000-000000000001` | ID of the installation's single company, chosen by the server. Do not change it on an existing installation. |

Test suites read their own variables (`TEST_DATABASE_URL`, `RUN_ENGINE_TESTS`,
`RUN_WORKER_TESTS`, `RUN_DATABASE_OPS_TESTS`, `REGIONAL_URL`, `RESTRICTIONS_URL`,
`FRANCE_URL`); see [host development and tests](README.md#host-development-and-tests).
