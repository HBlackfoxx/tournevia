# Installing and operating Tournevia

This is the operator and command reference: installation, routing data, settings,
backups, upgrades and the tested commands. Every setting is listed in
[SETTINGS.md](SETTINGS.md).

| To | Read |
|---|---|
| Try it on your own computer | [First route on your computer](#first-route-on-your-computer), then [local workspace and manual configuration](#local-workspace-and-manual-configuration) |
| Install on a server with HTTPS | [HTTPS server launcher](#https-server-launcher) or [behind an existing HTTPS proxy](#behind-an-existing-https-proxy); [optional HTTPS installation](#optional-https-installation) for the overlay itself |
| Run day-to-day commands on an installation | [Day-to-day commands](#day-to-day-commands): logs, restart, user accounts, retention |
| Prepare road data and routers | [Routing service in Compose](#routing-service-in-compose), [requirements by scenario](#requirements-by-scenario), [routing for all of France](#routing-for-all-of-france), [address lookup](#address-lookup-optional) |
| Back up, restore and upgrade | [Database roles, upgrades and recovery](#database-roles-upgrades-and-recovery), [cutover to a restored copy](#cutover-to-a-restored-copy), [data retention and erasure](#data-retention-and-erasure) |
| Monitor and diagnose | [Monitoring and alerts](#monitoring-and-alerts), [bounded diagnostic logs](#bounded-diagnostic-logs), [durable calculations and recovery](#durable-calculations-and-recovery) |
| Understand accounts and security | [Named accounts and sessions](#named-accounts-and-sessions) |
| Develop and test | [Host development and tests](#host-development-and-tests), [implemented API and contracts](#implemented-api-and-contracts), [build the local graphs](#build-the-local-graphs), [quality benchmarks](#quality-benchmarks), [real routing spike](#real-routing-spike) |

## What runs

The local stack provides PostgreSQL, migration setup and FastAPI serving the
compiled React planning workspace, plus a singleton durable worker from the same
image. Fresh installations create a company/depot and named administrator through
the browser. Company workspaces support source preparation, calculation, manual
review and guarded READY/CSV export and route sheets. Example records (fictional
deliveries a company adds to try the planner) are planned only with other examples and
every output of their plans says it is not for driving.
Company-use acceptance remains pending. Finalization/export
gates reject all synthetic plans. Worker and CLI use Valhalla and the native VROOM
executable included in the application image; manual edits use Valhalla without VROOM.

## First route on your computer

From a copy of this repository to a first calculated route, on Windows, macOS or Linux.
You need Docker (Docker Desktop on Windows and macOS, Docker Engine with the Compose
plugin on Linux), Python 3 and about 2 GB of free disk. The two scripts below use only
Python's standard library. Run every command from the repository folder; they are the
same in a terminal on Linux or macOS and in PowerShell on Windows, except that Linux
and macOS usually name Python `python3`.

```sh
docker build -f deploy/Dockerfile.vroom -t tournevia-vroom:1.14.0 .
docker build --target runtime -t tournevia-app:local .
python scripts/prepare_routing.py
python scripts/start_local.py --routing-config region/valhalla.json
```

1. The two `docker build` commands compile the VROOM optimizer and build the application
   image. The first takes several minutes.
2. `prepare_routing.py` downloads the Île-de-France road data from Geofabrik (about
   340 MB) into `deploy/runtime` and builds the routing graph into
   `deploy/runtime/region` with the pinned Valhalla image. Run once; it stops early
   when the graph exists. Geofabrik replaces its file regularly, so the download is
   usually newer than the extract the tests and benchmarks used (see
   [engines.json](engines.json)): it is built anyway, and plans name the extract and its
   download date. On 12 November 2026 the build took about 7 minutes with two threads on
   a Windows desktop with Docker Desktop. It prints `sqlite3_step() error` lines about
   `admin_access` and a warning about elevation, which Valhalla itself says to ignore for
   a regional extract.
3. `start_local.py --routing-config region/valhalla.json` creates a private workspace in
   `deploy/runtime/local-workspace`, with its own database, generated passwords and the
   router running inside it, then waits until everything answers (up to three minutes
   while the router loads the graph). It prints the path of a private first-use
   shortcut. On Windows, open that file; elsewhere it holds the address after `URL=`,
   which you open in a browser.

In the browser, create the company and its administrator, then, in the setup guide,
add example deliveries. Select the example deliveries (Deliveries) and the example
vehicles (Fleet), start the calculation on Planning and review the routes on the map.

Later, run `start_local.py` again (without options) to start the same workspace. It
keeps its database, port and router. Useful options and messages:

- **Port 8007 is already in use**: the launcher says so before starting anything. Add
  `--port 8010` (any free port from 1024); given to an existing workspace, it moves that
  workspace to the new port.
- **A workspace created before `--routing-config` existed**: rerun with
  `--routing-config region/valhalla.json` to add the router to it.
- **Your own router on port 8002 of this computer**: leave out `--routing-config`. The
  workspace reaches it at `host.docker.internal:8002`, which the launcher also maps on
  Linux Docker Engine. [Build the local graphs](#build-the-local-graphs) starts such a
  router, as the engine tests need.
- When a Docker command fails, the launcher shows its error message with the generated
  passwords blanked out, and names the Compose project, whose `docker compose -p
  PROJECT logs` show what each service reported.

## Start with Docker Compose

### Account language

Named users select English or French under Settings → Personal preferences.
The preference is saved on their account and applied on subsequent sign-in from
any device. Dispatchers can change their own language without administrator
access. Company data and API field names are not translated. Migration
`0007_language` gives existing accounts English by default.

### HTTPS server launcher

For a Linux VPS, use a built kit image, an operator-managed trusted certificate
directory (`fullchain.pem` and `privkey.pem`) and a private reachable Valhalla URL:

```sh
python3 scripts/start_server.py --directory /srv/tournevia/private --origin https://planner.example.com --certificates /srv/tournevia/certs --valhalla-url http://routing.internal:8002 --dataset "Actual extract, date and speed assumptions" --image IMAGE_FROM_KIT --prepare-only
```

Replace the example values. The prepare-only command validates merged Compose
configuration without starting services. Repeat without `--prepare-only` to
start. This adds `docker-compose.vps.yml` after the HTTPS overlay and exposes
only proxy port 443. The certificate key must be readable by proxy UID 101 using
restricted ownership/ACLs. The helper does not provision DNS, certificates or
routing graphs. Reruns preserve credentials; changed installation settings are
rejected rather than silently upgrading. Test browser trust and actual routing
on the target server before use. This certificate path has not been run on a VPS;
the proxied path below has.

To move an existing installation to a newly built image, let current work finish,
then run `python3 scripts/start_server.py --directory PRIVATE_PATH --upgrade-image NEW_IMAGE`
(`start_local.py` accepts the same option for local workspaces). The helper checks
the image exists, writes a backup to `PRIVATE_PATH/backups/before-upgrade-*.dump`
(this needs the backend Python packages), stops API and worker, records the new
and previous image, and starts the stack. Setup runs migrations before API and
worker start; if it fails they stay stopped and the database is preserved. To back
out, restore the backup into an isolated database and start the recorded previous
image as described under [upgrade and backout](RELEASE.md#upgrade-and-backout).
This flow was exercised locally from the 27 September image to the current one with
a company workspace, records and a real calculation. On the VPS described under
[behind an existing HTTPS proxy](#behind-an-existing-https-proxy) it upgraded the
installation to a newer development revision in 19 s, including the pre-upgrade backup,
and a calculation afterwards passed validation; the backout itself was not run there.

For a launcher-created installation, select its private directory explicitly:

```sh
python scripts/database_backup.py --workspace PRIVATE_PATH backup --output backups/before-upgrade.dump
python scripts/database_backup.py --workspace PRIVATE_PATH restore backups/before-upgrade.dump
```

Run with the kit matching the database schema and the Python development
dependencies. Restore creates a new isolated database; it never switches the
live installation. After validating the copy, switch to it by rerunning the
launcher with `--directory PRIVATE_PATH --cutover-to vrp_restore_...` (see
[cutover](#cutover-to-a-restored-copy) below). Preserve the workspace directory
and image selection with the backup. The existing upgrade/recovery procedure
below still applies.

### Behind an existing HTTPS proxy

When ports 80 and 443 already belong to a reverse proxy that obtains certificates,
such as the Traefik of Coolify or Dokploy, give the launcher that proxy's Docker
network instead of a certificate directory (`docker network ls` lists it; Coolify's is
`coolify`), and let the router run inside the installation:

```sh
python3 scripts/start_server.py --directory /srv/tournevia/private --origin https://planner.example.com --behind-proxy coolify --routing-config region/valhalla.json --dataset "Actual extract, date and speed assumptions" --image IMAGE_FROM_KIT --prepare-only
```

This uses [docker-compose.proxied.yml](docker-compose.proxied.yml) instead of the
HTTPS and VPS overlays, and [docker-compose.routing.yml](docker-compose.routing.yml)
with `deploy/runtime/region/valhalla.json` from `build_graph.py region`. The bundled
nginx keeps its 1 MiB body limit, sign-in rate limit and header rules, but serves
plain HTTP on port 8080 of the proxy network only; nothing is published on the host
and only this `edge` service joins the shared network. Its Traefik labels route the
origin's hostname to it, redirect HTTP to HTTPS and request the certificate from the
resolver in `workspace.env`: `PROXY_CERT_RESOLVER`, `PROXY_HTTPS_ENTRYPOINT` and
`PROXY_HTTP_ENTRYPOINT` default to Coolify's `letsencrypt`, `https` and `http`
(Dokploy's entrypoints are `websecure` and `web`). Another proxy forwards the hostname
to `http://edge:8080` on that network itself. The proxy must replace or append to
`X-Forwarded-For` with the visitor's address, as Traefik and Caddy do: nginx takes
its last entry, from private container addresses only, as the visitor for sign-in
limits. `ROUTING_MEMORY` (default `4g`) and `ROUTING_THREADS` (default 2) in
`workspace.env` size the router; edit them after `--prepare-only`, or stop the
stack, edit and rerun the launcher. Behind the proxy, an upload over 1 MiB is still
refused by nginx, but the client may see the connection close instead of the JSON
message; the import screen checks the size before sending.
`scripts/test_proxied.py --image IMAGE` repeats the checks locally behind a real
Traefik configured like Coolify's: label discovery, TLS, the HTTP redirect, sign-in,
CSRF, Host/Origin, headers, the body limit, and sign-in limits per visitor rather than
per proxy, with forged forwarding headers ignored.

This path was run on 20 October 2026 on a 2 vCPU / 8 GB Ubuntu 24.04 VPS that also
runs Coolify 4.0.0, its Traefik 3.6 and about a dozen other containers. On that
server the solver and application images built
in 1 min 55 s and 33 s, the 340 MB Île-de-France extract downloaded in 5 min 49 s
(Geofabrik's rate) and its graph built in 2 min 16 s under a 3 GB / 2 CPU cap, with
`--accept-new-extract` because the pinned September file is no longer published.
With `ROUTING_MEMORY=3g` the stack started in 25 s; Traefik obtained a Let's Encrypt
certificate; first-use setup with the sample data, sign-in, a ten-delivery
calculation (4.6 s, validation PASS, road geometry on every leg), per-visitor sign-in
limits with forged forwarding headers, a backup and an isolated restore passed. At
least 5.6 GiB of memory stayed available during the first calculation; after three
calculations the router held 0.9 GiB, Tournevia about 1.1 GiB in all, and 4.9 GiB
stayed available. The other sites on the server
answered as before. A 250-delivery day and the France graph were not run there; see
[requirements by scenario](#requirements-by-scenario).

Coolify's Docker cleanup (forced nightly on that server; otherwise when disk use
passes its threshold) removes unused images it did not create, clears the build
cache, and also removes unused volumes and networks when its server settings enable
that. Running Tournevia containers keep their images and volumes, but the
solver build image and the previous application image recorded for backout are
deleted. Before an upgrade, `docker save -o PRIVATE_PATH/backups/IMAGE.tar IMAGE`
the current image. If Coolify deletes unused volumes, stop Tournevia with
`docker compose ... stop`, never `down`, which removes the containers and leaves the
database volume unused. Keep backups off the server as well.

The backup and upgrade helpers need the backend Python packages on the host. On
Ubuntu, `apt install python3-venv`, then
`python3 -m venv /srv/tournevia/venv && /srv/tournevia/venv/bin/pip install -r backend/requirements.txt`,
and run `scripts/database_backup.py` and `start_server.py --upgrade-image` with
`/srv/tournevia/venv/bin/python`.

### Local workspace and manual configuration

For a separate local workspace with generated secrets, build the runtime image and
run `python scripts/start_local.py` as described in the [root README](../README.md#start-a-local-workspace).
The helper keeps configuration in `deploy/runtime/local-workspace`, publishes only
the API on loopback port 8007, and resumes the same database on subsequent runs.
`--directory` selects another persistent workspace; `--image` applies when creating
it, and `--port` and `--routing-config` also change an existing one (see
[first route on your computer](#first-route-on-your-computer)). Keep the directory and private setup shortcut operator-only;
Windows files inherit their parent directory ACL. After setup, use the ordinary URL.
The setup code cannot create another account or replace existing data.

For manual configuration instead:

From the repository root, with Docker running:

```bash
cp deploy/env.example .env
# Set distinct POSTGRES_PASSWORD, RUNTIME_DB_PASSWORD and MIGRATION_DB_PASSWORD.
# Runtime/migration passwords require at least 20 characters; keep .env private.
# Set SETUP_TOKEN to a random private value of at least 32 characters.
docker build -f deploy/Dockerfile.vroom -t tournevia-vroom:1.14.0 .
docker compose --env-file .env -f deploy/docker-compose.example.yml config --quiet
docker compose --env-file .env -f deploy/docker-compose.example.yml up --build -d
docker compose --env-file .env -f deploy/docker-compose.example.yml ps
```

PowerShell also accepts `Copy-Item deploy/env.example .env`. Do not overwrite an
existing `.env`. Open [localhost:8000](http://localhost:8000). Database and API bind
to `127.0.0.1` on the host. The application listens on all interfaces **inside** its
container so Docker can forward that loopback port. Do not change the host port
bindings or expose local mode through a public proxy. Open the first-use wizard,
enter the company and depot, and the private SETUP_TOKEN on the account step.

Setup runs `python -m app.setup_database` in the same image as the API. It prepares
managed database roles, runs Alembic as the migration owner and grants explicit
runtime privileges. Local/shared modes leave a fresh database empty for first-use
setup. Only explicit legacy `APP_MODE=local_demo` automatically provisions a
synthetic company/depot and bypasses sign-in; never use that mode for company data.
Upgrades preserve existing IDs and edits. Migration `0016_examples` turns a sample
workspace created by first-use setup into a company workspace whose vehicles and
deliveries are examples; the legacy local demonstration (no user account) stays a sample. API startup waits for setup to
succeed. Normal API startup does not run migrations or insert data.

The image runs as UID 10001. API and worker use a restricted non-owner database
role; only setup receives bootstrap and migration credentials. Local backup and
isolated restore are tested; the HTTPS overlay has real TLS/API tests. Operator
certificate/browser and fleet acceptance remain gates. Setting
`APP_MODE=shared` requires an HTTPS `PUBLIC_ORIGIN` and enforces named sessions;
there is no shared-mode authentication bypass. Local mode rejects non-loopback
`PUBLIC_ORIGIN`; both modes reject unrecognized Hosts and foreign Origins.

Stop without deleting stored data:

```bash
docker compose --env-file .env -f deploy/docker-compose.example.yml stop
```

Do not use `down -v` for routine restarts: it destroys the database volume. A
bootstrap password change in `.env` does not rotate that password in an existing volume.
Setup does update its managed runtime/migration role passwords; stop API/worker
before changing those secrets and restart them with the matching new configuration.
Build bases and service images are digest-pinned where supplied by upstream;
application tags refer to locally built images. See [source release candidates](RELEASE.md)
for packaging, exact version locks, generated notices and publication boundaries.

## Day-to-day commands

A Docker Compose command acts on the right installation only when it names the same
project, settings file and Compose files the installation was started with. The
commands elsewhere in this document are written for a Compose installation made by
hand from the repository root (`--env-file .env -f deploy/docker-compose.example.yml`
plus its overlays). For an installation the launchers created, build the prefix from
its private directory `DIR`: the project is `"project"` in `DIR/workspace.json`
(`docker compose ls` also lists it), and the settings are in `DIR/workspace.env`.

Local workspace (`start_local.py`), from the repository folder; add the routing
overlay only when `workspace.json` has `"routing_config"`:

```sh
docker compose -p PROJECT --env-file DIR/workspace.env -f deploy/docker-compose.example.yml -f deploy/docker-compose.routing.yml -f DIR/workspace.yml -f DIR/image.json COMMAND
```

Server (`start_server.py`): the routing overlay as above, then either the proxy overlay
(`"proxy_network"` set) or the HTTPS and VPS overlays:

```sh
docker compose -p PROJECT --env-file DIR/workspace.env -f deploy/docker-compose.example.yml -f deploy/docker-compose.routing.yml -f deploy/docker-compose.proxied.yml -f DIR/image.json COMMAND
docker compose -p PROJECT --env-file DIR/workspace.env -f deploy/docker-compose.example.yml -f deploy/docker-compose.https.yml -f deploy/docker-compose.vps.yml -f DIR/image.json COMMAND
```

| Task | `COMMAND` |
|---|---|
| State of each service | `ps` |
| Worker log | `logs --tail 50 worker` (or `api`, `valhalla`) |
| Restart the API | `restart api`, then `restart https` or `restart edge` if the installation has the bundled proxy, which looks up the API's address only when it starts |
| Stop without deleting anything | `stop` (never `down -v`, which deletes the database volume) |
| Retention and erasure | `run --rm setup python -m app.retention ...`, as in [data retention and erasure](#data-retention-and-erasure) |
| Recover an administrator account | `run --rm setup python -m app.manage_users reset-password NAME`, in an interactive terminal (it asks for the new password) |

To start the installation again with its current settings, rerun its launcher with
the same `--directory`. A variable set in your shell overrides `workspace.env`: unset
any of its names before running these commands by hand. These forms were checked on a
local workspace on 12 November 2026 (`ps`, `logs`, `restart api`, `stop`, and the
retention dry runs).

## Database roles, upgrades and recovery

| Credential | Purpose |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | Bootstrap administration; setup and isolated test/restore operator only |
| `MIGRATION_DB_USER` / `MIGRATION_DB_PASSWORD` | Own the application database/schema and run migrations/backups; no superuser, CREATEDB or CREATEROLE |
| `RUNTIME_DB_USER` / `RUNTIME_DB_PASSWORD` | API/worker: explicit reads and required inserts/updates; delete only sessions and throttle rows |

Use three distinct role names and passwords. Runtime/migration passwords must be
at least 20 characters. Keep `DB_NAME` equal to `POSTGRES_DB` and host `DB_USER` /
`DB_PASSWORD` equal to the runtime values. The runtime role cannot create tables,
temporary tables, schemas, roles or databases, truncate tables, delete source
records, write the migration version or assume the migration role. It has an
organization insert grant for first-use setup and an ID-column update grant for the company row lock. Organization
and user isolation still depend on application authorization; this is not row-level
security. New tables require an explicit review of the grant list.

Setup adopts only known application tables and refuses existing runtime/migration
roles without its managed-role comment or with role memberships. It preserves
records while transferring ownership to the migration role. Do not reuse names
belonging to unrelated applications. These permissions apply to this application
database, not a general hardening policy for an existing PostgreSQL cluster.

For an upgrade, let current work finish, take a backup, stop API/worker, build and
start the stack. Keep them stopped if setup fails; correct the reported configuration
or schema issue before retrying. Never downgrade or reset the volume as a routine fix.

```powershell
.venv\Scripts\python scripts/database_backup.py backup
docker compose --env-file .env -f deploy/docker-compose.example.yml stop api worker
docker compose --env-file .env -f deploy/docker-compose.example.yml up --build -d
```

When upgrading from a version that used the bootstrap account, first take a trusted
PostgreSQL backup with that existing owner credential, then configure the new
runtime/migration secrets. The new backup command requires the managed roles to
have been set up. Changing `POSTGRES_PASSWORD` in a persistent installation requires
an explicit database password rotation; updating Compose environment alone is insufficient.

The host backup helper requires the backend runtime dependencies
(`backend/requirements.txt`) in the host Python and the running Compose `postgres` service. It executes that service's PostgreSQL 16 tools over
loopback inside the container; it is not a backup driver for an arbitrary `DB_HOST`.
It passes passwords through the subprocess environment, never command arguments.

```powershell
.venv\Scripts\python scripts/database_backup.py backup --output backups/manual-check.dump
.venv\Scripts\python scripts/database_backup.py restore backups/manual-check.dump
```

Choose a new output filename on each run. Backups use a consistent PostgreSQL custom
archive and exclude login sessions. They include accounts/password hashes, sources,
jobs and saved plans. A failed dump is not published and existing files are never
overwritten. On Windows the destination inherits its folder ACL: use an operator-only
protected folder. POSIX mode requests do not establish a Windows ACL.

Encrypt archives that leave the host with [age](https://age-encryption.org): pass a
public recipient with `--encrypt-to`. The verified archive is encrypted into the
destination (`.dump.age`) and the plaintext partial file is removed; only the private
identity, kept away from the server, can restore it. Old archives are never removed
automatically; `prune --keep N` deletes all but the newest N archives that this
helper named in a directory and never touches other files.

```powershell
.venv\Scripts\python scripts/database_backup.py backup --encrypt-to age1...recipient
.venv\Scripts\python scripts/database_backup.py prune --keep 14
.venv\Scripts\python scripts/database_backup.py restore backups/vrp-...dump.age --identity C:/private/backup-key.txt
```

Schedule backups (cron, a systemd timer or Windows Task Scheduler), copy each
encrypted archive off the host (for example with rclone or rsync to separate
storage), then prune. The helper exits non-zero on any failure without publishing
an archive: alert on that exit status, and on the newest archive's age, so a silently
stopped schedule is noticed. Agree the retention period with the operator before
real data.

Restore accepts only a fresh generated `vrp_restore_<32 hex digits>` database, owned
by the migration role. It restores in one transaction, checks the schema, clears
sessions and reapplies runtime grants. It never overwrites the live database,
changes `.env`, starts a worker or switches the application. Failures after database
creation leave the isolated target for inspection. Restore **trusted archives only**:
[PostgreSQL warns that archives can contain executable SQL](https://www.postgresql.org/docs/16/app-pgrestore.html).

The printed row counts are a first check, not complete recovery acceptance. Validate
named login, source/plan reads and worker recovery in the isolated copy before any
separately planned cutover. Tests cover invalidation of old cookies, preservation
of accounts/drafts, interrupted edit release and new job completion. The live
synthetic demo also has a restored copy with its saved plan preserved. Operational
READY/export recovery remains a company-use acceptance requirement.

Keep the matching application version/image, private configuration, role credentials,
routing configuration and usable graph separately. A
[single-database dump](https://www.postgresql.org/docs/16/app-pgdump.html) does not
save cluster roles or those files. On a replacement installation, provision roles
with the matching application first, then restore and validate a copy.

### Cutover to a restored copy

Switch the installation to a validated restored copy with `cutover`. Pass the
additional Compose files the installation runs with, in order, as `--overlay`; the
base `deploy/docker-compose.example.yml` always comes first:

```powershell
.venv\Scripts\python scripts/database_backup.py cutover vrp_restore_<32 hex digits> --overlay deploy/docker-compose.routing.yml
```

The command checks that the target exists and matches this application's schema,
stops the API and worker so neither database is written during the switch, and
points `POSTGRES_DB` (and `DB_NAME`, when set) in `.env` at the target. It then starts
the installation and waits until every service is healthy: setup reapplies
migrations and runtime grants first, and the HTTPS proxy is restarted so it reaches
the new API container. If the installation does not become healthy, the previous
name is put back and the installation is started on it again. No database is
changed or removed; the command prints how to switch back. Shell variables named
`POSTGRES_DB` or `DB_NAME` are ignored for the switch, so `.env` alone decides.

Launcher-created installations switch with their launcher instead:
`python3 scripts/start_server.py --directory PRIVATE_PATH --cutover-to vrp_restore_...`
(`start_local.py` accepts the same option). The HTTPS recovery test below runs this
cutover on a disposable installation.

Keep the previous database until named sign-in, plans and a new calculation have
been checked. The recovery point is the newest archive; choose the backup interval
from how much planning work may be lost.

Run the permission/backup/restore suite explicitly against disposable databases:

```powershell
$env:RUN_DATABASE_OPS_TESTS='1'
$opsTemp='deploy/runtime/ops-tests-'+[guid]::NewGuid().ToString('N')
.venv\Scripts\python scripts/test_backend.py -m database_ops --basetemp $opsTemp -p no:cacheprovider
Remove-Item Env:RUN_DATABASE_OPS_TESTS
```

These tests require the Compose database and its bootstrap credentials. They create
and remove only their own random test databases and roles; saved operator restore
copies and the configured demo database are preserved.

### Clean-source installation and recovery acceptance

Run the HTTPS test with `--recovery` to exercise a fresh installation and a restored
copy through actual HTTP handlers and worker processes. It needs Docker, host Python
development dependencies, OpenSSL, the built `tournevia-vroom:1.14.0` image and the
existing regional road service reachable from containers at
`http://host.docker.internal:8002`. It does not need a domain or browser-trusted
certificate. Only the test client trusts its temporary certificates.

To build from committed files without local configuration, notes or dependencies:

```powershell
$installSource=Join-Path $PWD ('deploy/runtime/install-source-'+[guid]::NewGuid().ToString('N'))
git archive --format=zip --output="$installSource.zip" HEAD
Expand-Archive -LiteralPath "$installSource.zip" -DestinationPath $installSource
docker build --target runtime -t tournevia-install-test:local $installSource
.venv\Scripts\python "$installSource/scripts/test_https.py" --image tournevia-install-test:local --recovery
```

The test creates a random Compose project, private database and loopback-only HTTPS
port. Through the private setup API it creates a named administrator and company,
adds the example deliveries, verifies setup cannot be reused, then signs in again.
The two-vehicle/ten-order fixture uses the current Paris service date. Authenticated
source updates and a real-road worker calculation must pass. The test also fetches
the built JavaScript/CSS assets.
It claims an edit without completing it, reruns setup to check preservation, and uses
the ordinary backup helper to restore into a fresh database. Existing archives and
restore targets must be refused.

The disposable installation then switches to the restored copy with the ordinary
`cutover` command, which must also refuse to switch to the database already in
use. The old session
must be rejected; a new login must retrieve the same sources, input and road result.
The worker must release the interrupted edit and complete a new calculation. The
source database must retain its original plan version and unfinished edit, and
synthetic operational exports remain rejected. Cleanup removes only the test's
containers, network and owned volume; its synthetic archive and private test files
remain under ignored `deploy/runtime`.

This checks a clean source tree, new schema and restored application workflow using
available build dependencies and an existing graph. It does not prove installation
on a fresh operating system, graph acquisition/rebuild, off-host archive recovery
or a real operator's deployment and certificate trust.

## Host development and tests

Tested with Python 3.14.5 and Node 24.15.0 on Windows; the image and CI build the
frontend with Node 26, where its checks also pass (26.13.1). Start PostgreSQL with Compose
and set `DB_PASSWORD` in `.env` equal to `RUNTIME_DB_PASSWORD`. Match the host-side
`DB_PORT`, `DB_NAME` and `DB_USER` if their Compose equivalents were customized.

From the repository root in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r backend/requirements-dev.txt
docker compose --env-file .env -f deploy/docker-compose.example.yml up -d postgres
.venv\Scripts\python scripts/test_backend.py
.venv\Scripts\python scripts/export_contracts.py --check
.venv\Scripts\python scripts/validate_pack.py
cd web
npm.cmd ci
npm.cmd test
npm.cmd run api:check
npm.cmd run build
```

On Unix, the virtual environment interpreter is `.venv/bin/python` and npm is
`npm`; the host-side Unix development sequence has not been separately tested.
The Docker build does install dependencies and build the frontend on Linux.

`scripts/test_backend.py` uses `POSTGRES_USER`/`POSTGRES_PASSWORD` from the private
operator environment (requires CREATEDB), creates random `vrp_test_...` databases, and drops only those test
databases. It does not reset the configured demo database. To run without
PostgreSQL, use `.venv\Scripts\python -m pytest -c backend/pytest.ini backend/tests --basetemp
.pytest-tmp -q`; PostgreSQL integration tests will be explicitly skipped unless
`TEST_DATABASE_URL` is set. Engine and Linux worker tests also skip unless explicitly
enabled with real engines below. Do not interpret skipped tests as integration evidence.
`scripts/test_backend.py` also runs the launcher/release tests in `scripts/tests`.
The engine-test image sets `RUN_ENGINE_TESTS=1` and the Docker worker runner sets
`RUN_WORKER_TESTS=1`, so those runs cannot pass by skipping everything.

[.github/workflows/ci.yml](../.github/workflows/ci.yml) runs the backend and launcher
tests against a disposable PostgreSQL service, the contract and specification
checks, the frontend tests/type checks/build, a Linux image build with the
engine-free routing checks, the HTTPS overlay test against the built runtime image
and the `database_ops` role/backup/restore suite against a disposable Compose
database. [.github/workflows/engines.yml](../.github/workflows/engines.yml) runs on
demand and weekly: it builds the regional graph from the current Geofabrik extract,
records that extract's size and SHA-256 in the job summary, then runs the
real-engine suite, the Linux worker-process checks and the HTTPS recovery
acceptance, including cutover. Because Geofabrik serves only its latest extract,
that run is not a run on the file pinned in `deploy/engines.json`.

For direct backend development, stop the Compose API to free port 8000, then from
`backend` (using the virtual environment on PATH):

```bash
python -m app.setup_database
python -m app.main
```

Setup needs all three database credentials; normal API startup uses only `DB_USER`
and `DB_PASSWORD`. These commands are exercised by container setup/API startup. For a frontend
development server, run `npm run dev` from `web`, open
[localhost:5173](http://localhost:5173) and set `PUBLIC_ORIGIN=http://localhost:5173`
before starting the backend if the browser sends Origin headers. Vite proxies
`/api` to the loopback backend; no permissive CORS configuration is needed. The
Vite development-server workflow has not been separately exercised.

## Implemented API and contracts

| Route | Behavior |
|---|---|
| `GET /api/v1/health` | Minimal process liveness; no database or routing claim |
| `GET /api/v1/ready` | Expected schema ready, including an empty database awaiting onboarding; legacy demo also requires company/depot |
| `GET/POST /api/v1/setup` | Public setup status / private-code-authorized first company, depot and administrator; refuses existing installations |
| `POST/GET/DELETE /api/v1/session` | Sign in / current identity and CSRF token / revoke current session |
| `GET/POST /api/v1/users` | Admin-only company user list / create account; no public registration |
| `PUT /api/v1/users/{id}` | Admin-only role/display name/active state; revokes that user's sessions |
| `GET /api/v1/depot` | Current organization's depot and version ETag; owner is server-controlled |
| `PUT /api/v1/depot` | Update the main depot with quoted `If-Match` |
| `GET/POST /api/v1/depots` | Every depot and place, main depot first / add one (admin; its point starts unreviewed) |
| `PUT/DELETE /api/v1/depots/{id}` | Change (code fixed while named) / delete a depot or place nothing names (admin, `If-Match`) |
| `POST /api/v1/depots/{id}/main` | Make a depot the main one (admin, `If-Match`) |
| `GET/POST /api/v1/vehicles` | Stable paginated list / create owned vehicle |
| `GET/PUT /api/v1/vehicles/{id}` | Read / version-checked edit; use active state rather than deletion |
| `GET/POST /api/v1/orders` | Date-filtered paginated list / create owned delivery |
| `GET/PUT /api/v1/orders/{id}` | Read / version-checked edit with coordinate-review reset |
| `POST /api/v1/orders/activation` | Cancel (inactive) or restore several deliveries, each at its current version |
| `POST /api/v1/orders/delete` | Delete deliveries for good, each at its current version; refused while a waiting or running calculation uses one |
| `POST /api/v1/imports/{vehicles\|orders}?dry_run=true` | Exact-header UTF-8 CSV preview (optional trailing `priority,customer,ships_from`, or `max_stops,max_distance_m,max_driving_seconds,start,end`); `false` revalidates and commits atomically; `update_existing=true` lets rows change existing records, listed as `UPDATE` with their changed fields |
| `GET /api/v1/orders/{id}/access-check?vehicle_id=...` | Bounded saved-point road check using the selected owned truck |
| `GET /api/v1/configuration` | Owned company name, SYNTHETIC/UNVERIFIED data kind and replaceable `MAP_STYLE_URL`; no credentials |
| `POST /api/v1/optimization-jobs` | 202 with durable owned job, `Idempotency-Key` and atomic source snapshot; company/sample origin comes from the server-owned workspace |
| `GET /api/v1/jobs?service_date=...` | Latest-first owned job list; bounded `limit`/`offset` |
| `GET /api/v1/jobs/{id}` | Current stage, status, safe failure or saved plan ID |
| `GET /api/v1/system-status` | Worker guard held, routing service answering, company queue counts and oldest queue age |
| `GET /api/v1/plans?service_date=...` | Paginated draft summaries without large input/result payloads |
| `GET /api/v1/plans/{id}` | Saved input snapshot and current draft result, with version ETag and `sources_current` |
| `POST /api/v1/plans/{id}/edits` | Queue an exact assignment/sequence on the same plan; requires `If-Match` and `Idempotency-Key` |
| `POST /api/v1/plans/{id}/finalize` | Version/source/re-validation/provenance gates and Ready-conflict check; requires `If-Match` and explicit partial-plan acceptance |
| `GET /api/v1/plans/{id}/export.csv` | Current READY plan only; requires `If-Match`, rechecks sources and validation, neutralizes spreadsheet formulas; `?layout=spreadsheet` for French Excel |
| `POST /api/v1/examples` | Add the example deliveries for `service_date`, and the example depot and vehicles if missing (admin) |
| `POST /api/v1/examples/remove` | Delete every example record and the plans of example vehicles; refused while a calculation waits (admin) |
| `GET /api/v1/plans/{id}/sheets` | Drivers' route sheets of the current READY plan (`?vehicle=` for one); same checks as the CSV export |
| `GET /api/openapi.json` | Generated implemented API contract (not served in shared mode) |
| `GET /api/docs` | FastAPI developer documentation (not served in shared mode) |

To update generated contracts after model changes, run
`python scripts/export_contracts.py` from the root, then `npm run api:generate`
from `web`. Commit `backend/openapi.json`, the generated `schemas/`, CLI
schemas and `web/src/api-types.ts` together. The two `--check`/`api:check` commands
detect drift; there is no separate handwritten API specification. The application
tests check the examples; [examples/README.md](../examples/README.md) explains how to
regenerate the saved-plan example with the real engines.

## Monitoring and alerts

Set `MONITOR_TOKEN` (at least 32 random characters) to enable `GET /api/v1/metrics`,
Prometheus text metrics for a monitor that sends `Authorization: Bearer <token>`.
Without the setting the endpoint does not exist (404); it is independent of user
accounts and sessions. It reports installation-wide counts only, never delivery or
user data: database readiness, worker and routing state, queued and running
calculations, the oldest wait, successes and failures in the last hour and the age
of the last successful calculation. Behind the HTTPS overlay it is served at the same
origin; locally it is on the loopback API port. A scrape job:

```yaml
scrape_configs:
  - job_name: tournevia
    scheme: https
    metrics_path: /api/v1/metrics
    authorization: { type: Bearer, credentials_file: /etc/prometheus/tournevia-token }
    static_configs: [{ targets: ['planner.example.com'] }]
```

[prometheus-alerts.example.yml](prometheus-alerts.example.yml) holds starting alert rules
(planner unreachable, database not ready, worker stopped, routing unavailable,
calculations waiting over ten minutes, repeated failures). Add disk-space and backup
age alerts from the host's own exporter.

## Durable calculations and recovery

The worker touches a heartbeat file on every database guard check (about every
0.25 s while calculating, every 2 s while idle); its Compose health check reports it
unhealthy when the heartbeat is more than 60 s old. `docker compose ps` therefore
shows a hung or crash-looping worker, and the API's `/api/v1/ready` covers the
database and schema.

The UI retains selected saved records across the 50-row delivery and fleet pages
and sends their versions, including the depot. Use the page selection buttons to
add active vehicles or reviewed active deliveries; the running totals include
off-page selections. Changing the service date clears the selection. Returning to
a page refreshes its selected records; a changed off-page source returns 412 and
requires reloading/reselecting.
API clients may omit `source_versions` to request a fresh snapshot; when supplied it
must match every selected record and the depot. Unsupported, inactive, foreign or
unreviewed inputs fail before queueing. The calculation bounds are 25 vehicles,
250 deliveries, 301 distinct points (250 for deliveries, the rest for the depots and
places the vehicles use), and three exact profiles.

`Idempotency-Key` is 1–128 printable ASCII characters without spaces. It is scoped
to server-controlled company, signed-in user (or local-demo identity) and operation. UUID selections
are sorted before direct normalized-JSON comparison. Identical retries return the
same job, including after source edits or completion; different content returns
409. Keys remain valid for the lifetime of their job; this local version has no
automatic pruning. A new explicit calculation uses a new key and current sources.
The UI keeps a pending request/key in session storage for ambiguous-response and
page-reload retries. Failed calculations are never automatically resubmitted.

Queued/failed jobs hold one input copy. A successful transaction transfers it to
the new draft and saves its result together with job completion. The job retains
the normalized selection request and plan reference, not a second input copy.
New drafts start at version 1. Each edit reservation and completion advances the
same plan's integer version. No matrices, raw uploads, activity ledger or automatic
version tree are retained. Synthetic results stay DRAFT; PASS with zero served
orders still cannot become an operational dispatch.

The Linux worker holds one dedicated PostgreSQL session advisory lock and uses
that same connection for all job updates. A second worker exits. Lost connections
are terminal: the process stops its calculation child and exits without reconnecting.
Compose restarts the worker, API, database and HTTPS proxy after failures and host
reboots (`restart: unless-stopped`; an explicit stop is respected). The replacement
worker acquires the guard and marks abandoned RUNNING jobs `WORKER_INTERRUPTED`.
QUEUED jobs continue normally. Stage polling is sampled, so very short engine
stages may pass between UI updates. API readiness checks the database/schema (and
legacy demo depot), not worker or graph availability, so a stopped worker never
makes the API unhealthy. `GET /api/v1/system-status` reports the worker guard,
routing-service availability and queue age, and the planning screen warns about
them. Inspect worker logs below.

Failed calculations show the adapter's own explanation, such as the deliveries
whose pins have no road usable by the selected trucks within 50 m, or Valhalla's
numeric error code. Engine stderr, Valhalla's error text and driver exceptions are
never shown. The calculation child receives routing settings only, not database or
setup credentials. The API and worker run with a read-only root filesystem, all
Linux capabilities dropped and `no-new-privileges`.

`JOB_TIMEOUT_SECONDS` defaults to 180 (range 1–600) and covers the whole job,
including saving the result: computation stops early enough to leave up to five
seconds for the save, and the solver's search limit shrinks to what the deadline
leaves after the routing matrices. Regional 250-delivery jobs measured 118–123 s,
which the earlier 120 s default could not hold. A database statement failure (for
example a lock wait) fails only that job with `DATABASE_ERROR`; the worker exits
only when its guard connection or singleton lock is actually lost.
`MATRIX_CONCURRENCY` defaults to 1 and accepts 1–4. Set it above one only with
matching provisioned routing workers and memory; it does not raise the ten-second
HTTP timeout, whole-job deadline or 48-by-48 tile size. The
[regional resource profile](#regional-routing-resources) below exercises four.
Each guard query has a 2-second statement timeout, with bounded connection/keepalive
settings. The worker container has 2 GiB, two CPUs, 64 PIDs and a 96 MiB temporary
filesystem. Its calculation child has a 1.5 GiB address-space limit; logs and
result files are bounded. Linux parent-death signals also stop orphaned solver
processes after forced termination. Error records/logs use IDs, stages, safe codes
and durations rather than engine stderr, source records or connection secrets.

```powershell
docker compose --env-file .env -f deploy/docker-compose.example.yml logs --tail 50 worker
docker compose --env-file .env -f deploy/docker-compose.example.yml stop worker
docker compose --env-file .env -f deploy/docker-compose.example.yml start worker
docker build --target engine-test -t tournevia-engine-test:local .
docker run --rm --network container:tournevia-matrix-capacity --memory 2g --cpus 2 --env-file .env -e DB_HOST=host.docker.internal -e RUN_WORKER_TESTS=1 -e MATRIX_CONCURRENCY=4 -e REGIONAL_URL=http://127.0.0.1:8002 tournevia-engine-test:local python -m tests.run_worker_checks
```

The worker acceptance runner uses the same local database role as host tests to
create/drop fresh random test databases. Start the four-worker
[regional routing profile](#regional-routing-resources) before this acceptance
command. `REGIONAL_URL` refers to port 8002 inside that router. The command shares the existing
routing container's network namespace to avoid Docker Desktop host-port forwarding
for thousands of road requests. Nineteen checks exercise public 250-delivery
submissions for both objectives, paginated sources, over-limit/stale-source
rejection, idempotent replay and saved results after reopening the API, plus real
ten-delivery output, all-unassigned output, unavailable engines, and both optimization
and edit recovery after whole-job timeout, SIGTERM, SIGKILL or PostgreSQL session
termination during a native child process. A trusted ROUTED fixture, seeded only
inside an isolated test database, exercises READY, CSV, partial acceptance and
source-change rejection. Public clients cannot select ROUTED provenance.
Do not scale the worker service; stop/restart the single worker for maintenance.
Migrations are additive. Downgrading `0003_jobs` drops job/plan tables and is only
used in disposable migration tests; it is not a routine backout procedure.
`0004_edits` preserves existing drafts. Its downgrade refuses databases containing
edit jobs or READY plans; restore a pre-edit backup for that backout.
`0005_accounts` adds users/sessions/throttles without changing existing plans. Its
downgrade refuses to discard any named accounts; restore a pre-account backup instead.

## Named accounts and sessions

The default Compose service uses authenticated `local` mode on loopback. It needs
no browser certificate. Shared access uses `APP_MODE=shared` with HTTPS. Both modes
offer first-use onboarding only while no organization or account exists. A private
SETUP_TOKEN authorizes atomic creation of the first company, depot and administrator.
There is no default password, public signup, email service or client-selected owner.
The default choice starts empty, then a short guide helps the administrator add
vehicles from typical presets, choose delivery numbers and import or add the first
deliveries, or add example deliveries; every step can be skipped. Examples are a
fictional Paris depot, two vehicles and ten deliveries for a chosen day, marked as
examples by the server: they are planned only with other examples, their route sheets
and CSV rows say "Example data, not for driving", and an administrator removes them,
with their plans, through one database function (`POST /api/v1/examples/remove`).

For legacy provisioned installations, or when no administrator can sign in, use the
operator CLI. Fresh installations should use the browser wizard. From
`backend` with the virtual environment active (or inside an interactive application
container), run:

```bash
python -m app.manage_users create-admin dispatcher-admin --name "Dispatch administrator"
python -m app.manage_users reset-password dispatcher-admin
```

Each command prompts twice for a 15–128 character password without echoing it.
Passwords are never command arguments or environment variables. Creation refuses
existing usernames; reset only updates the named account in this installation and
revokes its sessions. These paths are covered by isolated database tests. Actual
operator credentials should be entered by the operator, not copied from test fixtures.

For authenticated deployment, use the optional HTTPS overlay below or provide
equivalent trusted TLS termination with `APP_MODE=shared` and an exact HTTPS
`PUBLIC_ORIGIN`. Keep the backend, database and engines private. Preserve the
external Host header when proxying. The application does not trust forwarded
headers to choose identity or origin.

The UI then requires sign-in. Admins maintain depot settings and create/disable
users or change roles under **Manage users**. Both admins and planners maintain
fleet/orders/imports and calculate/edit plans. Company workspaces can finalize and
export passing current plans. Users belong to one company and
usernames are case-insensitive and installation-wide. Only an administrator in
the same company can change another account; self-disable/demotion is rejected.
Role, name or active-state changes revoke that user's sessions. Users change their
own password under **Personal preferences** by entering the current one; their other
sessions end and failed attempts count toward the sign-in limits. An administrator
sets a new password for another user of the same company from **Edit user**, which
revokes all of that user's sessions; administrators cannot reset their own password
this way. An administrator who cannot sign in needs another administrator or the
operator command above. Revocation blocks subsequent requests; an already
authorized in-flight request or queued calculation may finish.

Passwords use [argon2-cffi's Argon2id profile](https://argon2-cffi.readthedocs.io/en/stable/howto.html).
Each login issues a fresh random 256-bit opaque token; PostgreSQL stores only its
SHA-256 hash. Shared mode uses `__Host-vrp_session`, Secure, HttpOnly, SameSite=Strict,
Path=/ with no Domain. Loopback local mode uses the separate `vrp_local_session`
cookie with the same controls except Secure, allowing local HTTP. Shared mode
never accepts the local cookie. Server-side expiry is eight hours absolute and 30 minutes
without authenticated requests (background status polling does not count as activity). Login
rotates the presented session; logout revokes it. Expired rows are pruned on login.
These controls follow the [OWASP session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

All data/configuration endpoints require a session in local and shared modes. Setup status, health/readiness,
the sign-in page and public API documentation remain accessible without private
records. Login requires the configured Origin and `X-VRP-Request: 1`. Other writes
also require the session-bound `X-CSRF-Token` returned by GET/POST `/session`; the
browser keeps it in memory. Neither passwords nor session bearer tokens are stored
in browser storage. Logout/expiry clears pending private retry data from session
storage and removes the workspace. Request bodies, including chunked login input,
are limited to 1 MiB; validation responses omit raw input values.

Sign-in failures use a generic message. Database-backed limits count failed
attempts only: five per normalized username from one client address per 15
minutes (a successful sign-in clears that client's count) and 50 per username from
all addresses per 15 minutes. One client therefore cannot lock a named user out for
everyone, and there is no installation-wide limit that attackers spraying many names
could use to lock out every user. Password hashing runs outside the database
transaction, at most two at a time per API process, so a burst of sign-ins neither
holds pooled connections nor exhausts memory. In shared mode behind the bundled
HTTPS proxy, the client address is the `X-VRP-Client` header that the proxy sets
from the connection and overwrites on every request (`TRUST_PROXY_CLIENT_HEADER=true`
in the HTTPS overlay). Behind any other reverse proxy, leave that setting off unless
the proxy also overwrites the header; otherwise the direct peer address is used. The
address is used only for throttling, never authorization, and is stored only hashed.
The proxy also limits sign-in requests per address (20 per minute, burst 10).
Limits survive API restarts. A 429 advises retrying later; operators should not clear
counters to bypass a suspected attack. Automatic polling sends `X-VRP-Background: 1`;
such reads are authenticated but do not extend the 30-minute idle timeout. Session/cookie behavior,
CSRF, expiry/revocation, role checks, account recovery, user-scoped retries and
two-company CRUD/import/job/plan/edit/export isolation have automated coverage.

## Optional HTTPS installation

[docker-compose.https.yml](docker-compose.https.yml) is an opt-in overlay using the
official Nginx 1.30.5 Alpine image pinned by digest. Use Docker Compose 2.24.4 or
newer for port-reset semantics. It forces shared mode for setup/API/worker, removes
the API/database host ports and publishes only `127.0.0.1:8443` by default. It does
not publish to a LAN or the Internet, obtain a domain, issue certificates or change
machine trust. Broader exposure requires a separately reviewed installation.

The proxy sends `Strict-Transport-Security: max-age=15552000` (180 days, this host
only), so browsers refuse plain HTTP and certificate click-through for that period:
use a certificate the intended browsers trust before opening the site. The
application adds a same-origin script Content-Security-Policy, `frame-ancestors
'none'` and `X-Frame-Options: DENY`. Keep Valhalla on a private network: plain
HTTP to a remote router carries delivery coordinates unencrypted.

Supply a certificate valid for the exact hostname and trusted by the intended
browsers. Put the leaf certificate plus intermediate chain in `fullchain.pem` and
its private key in `privkey.pem` inside a private directory outside the repository.
Only these server files are mounted; do not include a CA signing key. Proxy UID/GID
101 must be able to read the directory and files. On Linux, operator ownership with
group 101 and key mode 0640 is one option; on Windows, restrict the host directory
ACL to authorized operators and Docker. The mount is read-only. No private key or
certificate is baked into the image or included in Git.

Add these operator-specific values to private `.env` (example loopback origin):

```dotenv
TLS_PUBLIC_ORIGIN=https://localhost:8443
TLS_CERT_DIR=C:/absolute/private/certificates
HTTPS_PORT=8443
```

The origin must match the browser's scheme, hostname and port exactly. The default
local configuration remains unchanged until the overlay is selected. Drain work and
take a backup before switching an existing installation. From PowerShell:

```powershell
$httpsCompose=@('--env-file','.env','-f','deploy/docker-compose.example.yml','-f','deploy/docker-compose.https.yml')
docker compose @httpsCompose config --quiet
docker compose --env-file .env -f deploy/docker-compose.example.yml stop api worker
docker compose @httpsCompose up --build -d
docker compose @httpsCompose exec -T https nginx -t
```

Open the configured HTTPS origin and complete the first-use wizard using the
private SETUP_TOKEN; existing installations show sign-in. Host database tools cannot connect to the unpublished database
port; use container commands or the Compose-based backup helper. TLS supports 1.2
and 1.3, preserves the external Host, strips forwarded identity headers and enforces
the same 1 MiB body bound before forwarding. The API still checks Origin, sessions
and CSRF. TLS does not make synthetic plans operational.

After renewal, validate the new certificate/key and run `nginx -t` followed by
`docker compose @httpsCompose restart https`. If preflight fails, restore the
previous matching files and repeat `nginx -t`; do not restart with the rejected
pair. The running proxy retains its previously loaded certificate until restarted.
After a successful restart, verify the served certificate, HTTPS readiness and
authenticated read/write access from a client that trusts the issuing CA. Restarting
only the proxy preserves application sessions and does not require an API, worker
or database restart. The restart briefly interrupts proxy connections; this is
not a zero-downtime renewal procedure. For upgrades use the overlay on every
Compose command that changes services. Its proxy restarts when Compose updates its
API dependency. The existing API/worker stop and backup guidance still applies.
There is no automatic certificate renewal or public HTTP listener in this overlay.
Automate renewal with an ACME client using a DNS-01 challenge, which needs no HTTP
listener (for example certbot or lego with your DNS provider's plugin). Its deploy hook
copies the new `fullchain.pem` and `privkey.pem` into `TLS_CERT_DIR` and runs the
preflight and proxy restart above. Alert well before expiry, for example when
`openssl x509 -checkend 1209600 -noout -in fullchain.pem` (14 days) fails.

The repeatable test starts its own random Compose project/database, generates
one-day synthetic certificates, and trusts them only through the test client's explicit
CA file. It requires Docker, the built application image, host Python development
dependencies and OpenSSL (Git for Windows' bundled executable is detected):

```powershell
docker build --target runtime -t tournevia-https-test:local .
.venv\Scripts\python scripts/test_https.py --image tournevia-https-test:local
# If needed: scripts/test_https.py --openssl C:/path/to/openssl.exe
```

The test checks real certificate verification (and rejection without its test trust),
HTTPS login and cookie flags, authenticated reads and writes, missing CSRF, logout,
Host/Origin rejection, body limits, applied port bindings/log rotation and secret
canaries absent from collected logs. It removes only its own newly created test
containers/network/volume, retaining test files under ignored `deploy/runtime`.
Windows test files inherit directory ACLs; they contain synthetic credentials only.
Renewal coverage stages a mismatched key and requires preflight rejection while the
old certificate remains served. It then installs a matching replacement, restarts
only the proxy and verifies the exact replacement certificate on a new TLS
connection. The old self-signed test certificate and default client trust must
reject it. The original login session must still support authenticated reads,
CSRF-protected writes and logout, with unchanged API/worker/database start times.
It does not install a trust root or bypass a browser certificate warning. A browser
sign-in using the operator's trusted certificate, renewal on that installation and
the actual installation's network boundary remain acceptance work.

## Bounded diagnostic logs

Every Compose service uses Docker's
[local logging driver](https://docs.docker.com/engine/logging/drivers/local/), with
10 MiB files and three retained files per container. This is size-based retention,
not a guaranteed number of days; compression/metadata affect actual disk use.
Recreate containers to apply changed log settings. Separately launched routing
containers need the same `--log-driver local --log-opt max-size=10m --log-opt max-file=3`
options when created. Existing graph containers must be recreated to change their
driver settings; stopping/starting alone is insufficient. Preserve their graph mounts.

API request logs contain a generated request ID (also returned as `X-Request-ID`),
server route template, method, status, duration and safe exception class. Unknown
paths are logged as `unmatched`; raw URLs/query strings, headers, bodies and ordinary
exception tracebacks are excluded. Unexpected handler errors return a generic 500
with the request ID. Worker logs retain IDs, stages, durations and safe error codes.
No request/activity ledger is added.

The proxy logs status, upstream status, duration, response size and the API's request
ID. Its raw runtime error log is disabled because Nginx can include request URIs in
it; use status records, `nginx -t`, container state and API logs for diagnosis.
PostgreSQL statement logging is disabled and server messages are limited to FATAL
and PANIC, since ordinary SQL errors may echo private data. Application database
errors retain safe exception classes and availability status. Keep container/admin
diagnostic access private; enabling verbose upstream logs needs a controlled
diagnostic session with synthetic data. Logs are not an audit ledger or a backup.

## Data retention and erasure

Plans and calculation jobs copy delivery addresses, instructions and coordinates.
The runtime role cannot delete them; an operator runs the retention command with the
migration role in the setup container. Every command prints counts only, takes
`--dry-run`, and never deletes a waiting or running calculation:

```powershell
docker compose --env-file .env -f deploy/docker-compose.example.yml run --rm setup python -m app.retention purge --older-than-days 90 --dry-run
docker compose --env-file .env -f deploy/docker-compose.example.yml run --rm setup python -m app.retention erase-order --external-id ORDER-123
docker compose --env-file .env -f deploy/docker-compose.example.yml run --rm setup python -m app.retention erase-customer --code CLI-1
docker compose --env-file .env -f deploy/docker-compose.example.yml run --rm setup python -m app.retention erase-user --username j.martin
```

`purge` deletes plans not updated, and finished calculations not finished, within the
period (`--orders` also deletes deliveries dated before it). `erase-order` replaces one
delivery's address with `[erased]`, clears its instructions and coordinates,
deactivates it and deletes the plans and calculations that copied it; it refuses
while a waiting or running calculation, or an edit of a plan, includes the delivery. A
delivery already deleted in the application is found by its identifier in the plans and
calculations that copied it, and those are deleted. `erase-customer` replaces a saved
customer's name and address with `[erased]`, clears its instructions and entrance,
deactivates it, and erases every delivery that names it as `erase-order` does.
`erase-user` replaces an account's sign-in name and display name, disables it and ends
its sessions; it refuses to erase the last active administrator. Backups keep earlier
copies until they are pruned, so set the backup retention within the agreed period.

## Manual route review and dispatch gates

The route map uses saved engine geometry and numbered stops, with route visibility
and selection synchronized to the tables. Tables show Paris times, distance,
driving/service/waiting time and remaining/free capacity in all five dimensions.
Unassigned work remains visible. Basemap or WebGL failure leaves the tables usable.

Move a delivery to another selected vehicle or the unassigned list, then use the
sequence controls and **Recalculate these routes**. The worker follows that exact
sequence, departing at each vehicle's availability start, reroutes every leg and
independently validates it. It does not optimize the edit. An infeasible result is
saved as a visibly failing draft so it can be corrected, never accepted for dispatch.

The API reserves the current plan version, clears READY immediately and marks the
old result stale while the edit runs. Duplicate retries return the same job;
overlapping edits, changed versions and changed source records are rejected.
Interrupted edits release their reservation with a visible error and stale results.
An explicit new edit retries recalculation. A new optimization creates another plan
and does not preserve manual edits. A source change requires a fresh optimization.

Finalization and CSV require current sources, current version, completed passing
validation, served work and server-controlled operational provenance. Both re-run the
independent validator on the saved inputs and final legs instead of trusting a stored
PASS. Partial plans require explicit acceptance for the current version. Only one
READY plan per day may dispatch a given vehicle or delivery: finalizing another plan
that overlaps returns 409 `READY_CONFLICT` unless the request sets
`replace_conflicting_ready`, which returns the overlapping plans to DRAFT in the same
transaction. `GET /plans/{id}` reports `sources_current: false` once the depot or a
selected record changed, before Ready/export refuse with `SOURCE_CHANGED`.

CSV rows carry the plan ID and version, export timestamp, the vehicle's departure and
return, the codes of the depot or place it starts and ends at (`start_place`,
`end_place`), stop sequence, arrival, service start and stop departure (all Europe/Paris
ISO 8601 with offset) and a planning-estimate notice in the `planning_notice` column,
in the signed-in user's language. Each stop also carries the delivery's reviewed
`latitude`/`longitude`, its service window, service duration, the five quantities and
required equipment, under the same column names as the delivery CSV import. The
default is comma-separated UTF-8; `?layout=spreadsheet` adds a UTF-8 byte-order mark,
semicolons and a decimal comma in coordinates for spreadsheet programs that use a
decimal comma, such as French Excel. Every download rechecks these
gates and neutralizes formula-leading text cells.
Editing a READY plan returns it to DRAFT; previously distributed files must be replaced.
The local demo and CLI remain SYNTHETIC, including when real roads are used, so these
entrypoints cannot finalize or export operational routes. Company submissions use
server-controlled ROUTED input origin, which is not itself a validation or READY
claim. The worker must obtain actual road geometry and pass independent checks.
Clients cannot override origin, and a new company does not relabel old sample plans.
Operator fleet/constraint review and shadow-use acceptance remain deployment tasks.

Run the company lifecycle against a disposable installation with fictional records:

```powershell
.venv\Scripts\python scripts/test_https.py --image tournevia-app:local --company
```

This uses browser-onboarding's API to create an empty company and named admin,
then creates/reviews sources through authenticated APIs. The actual worker must
calculate ten deliveries, produce a READY CSV, clear READY on manual edits, require
partial-plan acceptance, reject invalid and zero-served results, and reject stale
sources/versions. No stored provenance or result is edited to obtain READY. It uses
the same local road-service and temporary-certificate prerequisites as the recovery
check; run `--company` and `--recovery` separately. It does not touch existing previews.

## Prepare source data

Choose the delivery date in the browser, then import `examples/vehicles.csv` and
`examples/orders.csv` or use the create forms. Preview shows row errors and
insert/unchanged counts; commit rechecks all rows. An identifier conflict never
overwrites an existing row. These CSVs contain two vehicles/two deliveries; they
are separate from the ten-delivery CLI fixture. Setup does not repeatedly seed
fleet/orders or overwrite user edits. Deliveries can be deleted for good
(`POST /api/v1/orders/delete`, each at its version), depots and places that nothing
names with `DELETE /api/v1/depots/{id}`, and example records all together
(`POST /api/v1/examples/remove`); vehicles and customers are deactivated instead.

Move a pin using **Pick on map** or edit its coordinate fields. Save the new point,
inspect it, then mark the saved point reviewed and save again. A road check uses
the selected saved vehicle profile and shows actual snapped endpoints separately;
it does not automatically review coordinates or prove legal access. Unresolved or
unreviewed points remain excluded by the core planning validator. Tables, forms
and numeric coordinates work when tiles/WebGL fail.

Direct local API writes require `Origin: http://localhost:8000` (or configured
`PUBLIC_ORIGIN`) and `X-VRP-Request: 1`. The browser supplies them. JSON writes use
`application/json`; imports use raw `text/csv`, with no multipart wrapper. Updates
also need `If-Match: "<version>"` from the current record/ETag. Missing, stale or
conflicting writes return 428, 412 or 409; invalid CSV returns 422 with row/field
errors and no writes. Lists default to 50 rows, maximum 250, with `limit`/`offset`;
orders require `service_date=YYYY-MM-DD`. Server configuration determines the
company; no owner field is accepted in create/update bodies.

`VALHALLA_URL` defaults to `127.0.0.1:8002` for host Python and
`host.docker.internal:8002` in Docker Desktop Compose. An empty setting uses those
defaults. A non-Desktop Linux host needs an operator-supplied reachable local
engine URL; that host setup is not tested. Leave the graph services private. A
failed point/engine request is visible as `ACCESS_CHECK_FAILED`, distinct from a
valid matrix with no round trip. This single-point check has a ten-second budget;
full calculations run in the separate worker or the synthetic CLI.

## Quality benchmarks

With the regional and controlled routing services running, rebuild the test image
and run the actual engine suite (including the independent tiny optima):

```powershell
docker build --target engine-test -t tournevia-engine-test:local .
docker run --rm --memory 2g --cpus 2 -e RUN_ENGINE_TESTS=1 tournevia-engine-test:local python -m pytest -q -m engines --basetemp /tmp/vrp-tests -p no:cacheprovider --tb=short
docker run --rm --memory 2g --cpus 2 tournevia-engine-test:local python -m benchmarks.run
```

The final command prints a JSON metrics report for three baseline runs and three
runs of each objective. `--repeats 2..10`, `--budget 1..60`, `--valhalla-url` and
`--dataset` are explicit options. Dataset labels must describe the graph actually
in use; engine versions are queried. It requires real Linux VROOM/Valhalla, uses no
database and retains no matrices. Do not run other engine workloads concurrently
when collecting comparable timings. No timing threshold is imposed across machines.
An invalid/full-coverage failure aborts; valid objective regressions produce a report
and exit code 2. See [reference scope, measured results and memory limits](../docs/06-testing-and-operations.md#implemented-synthetic-quality-checks).

To reproduce the separate 25/250/3 matrix diagnostic:

```powershell
docker run --rm --memory 2g --cpus 2 tournevia-engine-test:local python -m benchmarks.scaling
```

This loads 250 fixed service pins originally sampled from synthetic-seed road geometry and requests
three profile matrices under a shared 120-second deadline. It prints a JSON report
and exits 1 on rejection. The current pinned graph returns nonzero self-arcs for
some sampled pins. The adapter requires zero-length route confirmation and then
recalculates every finite incoming/outgoing connection for those pins through the route
endpoint. Null/malformed self-arcs and any failed road request still reject the
whole calculation. Original null connections remain forbidden. No restrictions
are disabled or coordinates filtered out.
A `MATRICES_PASS` result alone does not establish full solver, road-path,
quality, queued-worker or UI acceptance; see the
[measured workflow checks](../docs/06-testing-and-operations.md#250-delivery-workflow-and-matrix-ordering).
The probe stores no source records, road geometry or matrices.

For repeated full offline domain calculations on this exact synthetic workload,
first start the [regional routing resource profile](#regional-routing-resources):

```powershell
docker run --rm --network container:tournevia-matrix-capacity --memory 2g --cpus 2 tournevia-engine-test:local python -m benchmarks.run --workload paris-250 --matrix-concurrency 4 --repeats 2 --valhalla-url http://127.0.0.1:8002
```

The baseline assigns ten consecutive sampled deliveries to each of 25 vehicles.
Each optimization rebuilds all three matrices and routes its result. The fixed
manual baseline routes only its assigned legs. Every trial independently checks
the modeled constraints within 120 seconds. The original pins are committed as a
benchmark input fixture so routing changes cannot change the workload. The full
input is shared as one temporary copy across trials, then removed. This experimental benchmark
uses the domain functions offline, including the same point/profile preparation
as public jobs. It is separate from queued-worker and browser acceptance.
The command shares the existing routing container's network namespace to avoid
Docker Desktop host-port forwarding during thousands of road confirmations; it
does not change the engine configuration or expose new ports. An earlier attempt
through `host.docker.internal` stopped on a network error after three completed
trials. Failed repeats now retain completed metrics and a failure detail in the
JSON report, exit 1 and make no quality-acceptance claim.

The wider regional synthetic workload uses 250 fixed local-road points across a
five-by-five grid (about 42 km corner to corner), three vehicle profiles, varied
loads in all five dimensions, morning/afternoon windows, equipment requirements,
priorities and stop/distance/driving limits:

```powershell
docker run --rm --network container:tournevia-matrix-capacity --memory 2g --cpus 2 tournevia-engine-test:local python -m benchmarks.run --workload regional-250 --matrix-concurrency 4 --repeats 2 --valhalla-url http://127.0.0.1:8002
```

Start the [regional routing resource profile](#regional-routing-resources) before
running this command. Its baseline assigns the ten points in each grid cell to the corresponding
vehicle, in fixture order. Both candidates use the ordinary calculation function;
the baseline uses the ordinary manual reroute function. Optimizations rebuild
their matrices; fixed manual assignments route only their selected legs. Each
trial uses the same 120-second deadline. This fixture broadens synthetic
coverage; it is not an actual company's planning day or approval of unloading
access. See the [regional measurements](../docs/06-testing-and-operations.md#regional-synthetic-workload).

The separate `regional-equipped-250` candidate replaces REGION-01 and REGION-25
with stated refrigerated 7.5-tonne vehicles, including the medium profile's
capacities and rates. All 250 deliveries, pins, required equipment, windows and
route limits are retained. The ten-consecutive-deliveries baseline remains fixed
before objective measurements. This synthetic alternative is not a claim about
the equipment, payload or operating costs of any real truck. Run it by replacing
`--workload regional-250` above with `--workload regional-equipped-250`.
Six repeated full-coverage quality trials pass; see the
[measured destination-access recovery](../docs/06-testing-and-operations.md#destination-access-matrix-recovery)
for timings, resource limits and comparison metrics.

To diagnose every unassigned delivery in the original scenario:

```powershell
docker run --rm --network container:tournevia-matrix-capacity --memory 2g --cpus 2 tournevia-engine-test:local python -m benchmarks.feasibility --matrix-concurrency 4 --objective TRAVEL_TIME --valhalla-url http://127.0.0.1:8002
```

Repeat with `--objective MODELED_TRAVEL_COST` for the other objective. The JSON
records each selected vehicle's equipment/capacity exclusions, matrix-versus-route
reachability and single-delivery schedule/route-limit checks. Only explicit engine
no-path responses are classified that way; outages and unexpected errors fail the
diagnostic. Matrices and road geometry are not retained. Diagnostics have a separate
stated budget after the application calculation; that time is not included in a
successful 120-second calculation claim. Individually feasible deliveries are not
proof of joint fleet feasibility. See the
[findings and rejected experiments](../docs/06-testing-and-operations.md#regional-feasibility-diagnosis-and-separate-fleet-scenario).

The committed pins are reused unchanged on each run. The one-time constructor in
`backend/benchmarks/regional.py` correlates a predefined grid to local-road points
before optimization, writes only a new output file and is not run by acceptance.

The reference's own host-side tests do not require engines; native comparisons are
explicitly skipped unless enabled:

```powershell
.venv\Scripts\python -m pytest -c backend/pytest.ini backend/tests/test_quality.py -q -p no:cacheprovider
```

## Real routing spike

### Regional routing resources

The regional calculations below were measured with a **separate eight-CPU/8 GiB
routing container and four routing workers**. The application/benchmark container
still has two CPUs/2 GiB. This larger routing allocation is part of the measurement,
not an assertion that the earlier two-CPU/2 GiB routing service meets the same
regional deadline. The pinned engine, graph and truck constraints are unchanged.
The matrix service now enables CostMatrix's destination-access second pass, retains
buffers for 48 locations and caps continuation after connections are found at 1280
iterations. This changes heuristic matrix estimates; final legs still require
ordinary warning-free road routing and independent validation.

With the existing graph prepared, this starts a separate loopback-only router:

```powershell
$enginePins = Get-Content deploy/engines.json | ConvertFrom-Json
.venv\Scripts\python deploy/configure_matrix.py deploy/runtime/region/valhalla.json deploy/runtime/regional-matrix.json
docker run -d --rm --name tournevia-matrix-capacity --memory 8g --cpus 8 -p 127.0.0.1:8004:8002 --mount "type=bind,source=${PWD}/deploy/runtime,target=/data,readonly" --entrypoint valhalla_service $enginePins.valhalla_image /data/regional-matrix.json 4
docker run --rm --network container:tournevia-matrix-capacity --memory 2g --cpus 2 tournevia-engine-test:local python -m benchmarks.run --workload regional-equipped-250 --matrix-concurrency 4 --repeats 2 --valhalla-url http://127.0.0.1:8002
docker run --rm --network container:tournevia-matrix-capacity --memory 2g --cpus 2 --env-file .env -e DB_HOST=host.docker.internal -e RUN_WORKER_TESTS=1 -e MATRIX_CONCURRENCY=4 -e REGIONAL_URL=http://127.0.0.1:8002 tournevia-engine-test:local python -m tests.run_worker_checks
```

The command above tests the equipped fleet alternative. The original
`regional-250` benchmark rejects its predefined baseline because of truck-access
conflicts; a valid partial draft is not a quality-gate pass.
The worker suite adds four regional public-API cases when `MATRIX_CONCURRENCY=4`:
both objectives on the original negative fixture and on the equipped alternative.
The original preserves explicit unassigned work; the alternative requires all 250
deliveries. Both check validation and replay behavior. These cases are skipped by
the default sequential-resource test command.
The configuration tool refuses to overwrite an existing output and never edits
graph tiles or the input configuration. Reuse the generated file on later runs.
New graph builds use the same settings. The current 250-delivery acceptance
measurements use this eight-CPU/8 GiB router; the smaller graph-service example
below is for the initial ten-delivery smoke check. For existing controlled test graphs, run
the tool with their configuration as input and start a separate test router using
the new output before running engine acceptance with `RESTRICTIONS_URL` set to it.
For a Compose installation using this router, configure
`VALHALLA_URL=http://host.docker.internal:8004` and `MATRIX_CONCURRENCY=4` together
before starting its worker. Existing `.env` files and running services are not
changed by the new defaults. Stop the separate test router with
`docker stop tournevia-matrix-capacity` when its calculations have finished.

### Routing service in Compose

For a server, run the router as part of the Compose stack instead of a separately
started container. The [routing overlay](docker-compose.routing.yml) adds a `valhalla`
service on an `internal` network that only the API and worker join. It publishes no
port, runs as an unprivileged user with a read-only filesystem and no capabilities,
has a health check, and the API and worker wait for it. Prepare the graph below, then
write the matrix configuration and start the stack with the overlay (add the HTTPS
overlays after it on a server):

```powershell
.venv\Scripts\python deploy/configure_matrix.py deploy/runtime/region/valhalla.json deploy/runtime/regional-matrix.json
docker compose --env-file .env -f deploy/docker-compose.example.yml -f deploy/docker-compose.routing.yml up -d
```

`ROUTING_CONFIG` (default `regional-matrix.json`, relative to `deploy/runtime`),
`ROUTING_THREADS` (default 2) and `ROUTING_MEMORY` (default 4g) select the
configuration and resources.
`start_server.py --routing-config region/valhalla.json` adds this overlay and writes
those three values to the installation's `workspace.env`.

### Requirements by scenario

Measured on 20 October 2026 with `scripts/benchmark_sizing.py` (raw results in
[backend/benchmarks/results](../backend/benchmarks/results/sizing-2026-10-20.md)).
Each server size was emulated with Docker on an AMD Ryzen 9 7945HX: the router and the
calculation were pinned to the same hardware threads (one thread per vCPU, as most
VPS vCPUs are) with memory limits, and every run was a whole calculation under the
180 s job deadline with the 5 s solver budget, two runs each, on fictional workloads.
Server CPUs can be slower per thread than this desktop CPU, so keep a margin.

| Scenario | 2 vCPU / 8 GB | 4 vCPU / 16 GB | 8 vCPU / 32 GB | Router peak |
|---|---|---|---|---|
| Starter: 2 vehicles, 10 deliveries (Paris) | 0.4–2.6 s | 0.4–4.1 s | 0.5–4.0 s | 0.7–1.5 GiB |
| Small fleet: 5 vehicles, 50 deliveries (dense Paris) | 3.5–4.6 s | 3.4–5.2 s | 2.6–4.0 s | 1.8–3.6 GiB |
| Medium fleet: 10 vehicles, 100 deliveries (dense Paris) | 9.4–9.8 s | 8.3–8.7 s | 7.0–7.3 s | 1.8–3.6 GiB |
| Large city: 25 vehicles, 250 deliveries (dense Paris) | 45–47 s | 38 s | 30 s | 1.9–3.7 GiB |
| Regional: 25 vehicles, 250 deliveries (Île-de-France graph) | fails at 180 s | 154–157 s | 94–95 s | 3.3–6.6 GiB |
| Regional on the France graph | fails at 180 s | 152–158 s | 99–102 s | 4.0–7.7 GiB |

The routing matrix is almost all of the time (for example 87 of 94 s on the 8 vCPU
regional day); the solver uses its 5 s budget and the route and validation steps take
about 2 s. Router memory grows with its threads (2 threads on the 2 vCPU size, 4 on
the others), so a router with fewer threads uses less memory. The calculation process
peaked at 112 MiB. With one calculation's data in the database, the API used 79 MiB,
the worker 59 MiB and PostgreSQL 41 MiB; the Compose limits (1, 2 and 1 GiB) leave
room for growth. Address lookup adds about 5.2 GiB for its index.

Recommended sizes, from these measurements:

| Use | Recommended server | Graph and options |
|---|---|---|
| Up to about 100 deliveries a day, one city | 2 vCPU / 8 GB | Île-de-France graph; address lookup does not fit alongside |
| Up to 250 deliveries in a dense city area | 2 vCPU / 8 GB works (45–47 s); 4 vCPU / 16 GB for margin | Île-de-France graph |
| Up to 250 deliveries across a region | 8 vCPU / 32 GB (94–102 s). 4 vCPU / 16 GB passes only just (152–158 s): raise `JOB_TIMEOUT_SECONDS` if you use it | Regional or France graph, 4 router threads, `MATRIX_CONCURRENCY` 4 on 8 vCPU (2 on 4 vCPU, as measured) |
| France-wide installation with address lookup | 8 vCPU / 32 GB | France graph (router about 8 GB) and geocoder (about 6 GB); build the France graph on a 16 GB machine if the server has less |

Disk: the images take about 1.1 GB (application 0.2, router 0.64, PostgreSQL 0.28) and
the Île-de-France graph 0.32 GB. A France-wide installation adds 4.1 GB of graph, 1.1 GB
of geocoder images and 3.2 GB of address data; building the France graph on the server
needs the 5.1 GB extract and about 20 GB of free space while it runs. Add room for the
operating system, logs and backups, which grow with your data.

A 2 vCPU / 8 GB server cannot run a 250-delivery regional day: the matrix alone exceeds
the 180 s deadline. One worker runs one calculation at a time, so several waiting
calculations run one after another. Nothing here measures many simultaneous users;
the interface itself is light (the API used 79 MiB).

### Routing for all of France

The tests and benchmarks run on the pinned Île-de-France extract. For operation
elsewhere, `build_graph.py region --extract france` builds the pinned France extract
listed under `extracts` in [engines.json](engines.json) into `deploy/runtime/france`
(or `/data/france` in a volume). Its routes serve all of metropolitan France, but one
calculation must stay within the router's matrix distance: by default 200 km between
any two of its points (the depot included), set with `--max-matrix-km` at build time.
Set `MATRIX_MAX_KM` to the same value: a calculation whose points are farther apart
is refused before it is queued (`AREA_TOO_WIDE`, naming the two farthest places)
instead of failing in the router.

```powershell
$enginePins = Get-Content deploy/engines.json | ConvertFrom-Json
Invoke-WebRequest $enginePins.extracts.france.source -OutFile deploy/runtime/france.osm.pbf
docker volume create tournevia-france
docker run --rm -v tournevia-france:/data -v "${PWD}/deploy/runtime:/src:ro" alpine:3.20 cp /src/france.osm.pbf /data/
docker run --rm --memory 16g --cpus 8 --entrypoint python3 -v tournevia-france:/data -v "${PWD}/deploy:/workspace:ro" $enginePins.valhalla_image /workspace/build_graph.py region --extract france --jobs 8
docker run -d --restart unless-stopped --name tournevia-valhalla-france --memory 8g --cpus 4 -p 127.0.0.1:8002:8002 -v tournevia-france:/data:ro --entrypoint valhalla_service $enginePins.valhalla_image /data/france/valhalla.json 4
```

Build inside a Docker volume on Windows and macOS: through a bind mount from the host
the build's memory-mapped sorting was too slow to finish (no progress for 25 minutes),
while in a volume the whole build took about 11 minutes with 8 threads and a 16 GB
limit on a 32-CPU machine, producing 4.1 GB of tiles. On a Linux host a bind mount is
fine. The extract is 5.1 GB; keep about 20 GB free for the build's temporary files.
`build_graph.py` refuses an extract whose SHA-256 differs from the pin unless
`--accept-new-extract` is given; rerun the checks below and update the pin after a
refresh. After the real-engine suite, the router held 6.1 GiB; allow 8 GB.

Checks on the 18 October 2026 extract: the real-engine suite passes against the France
graph (63 tests, the Île-de-France cases unchanged), and
`backend/tests/test_france_coverage.py` (run with `FRANCE_URL`) plans a ten-delivery
Lyon day with both objectives (all served, independently validated), finds routes in
both directions in Lille, Bordeaux, Marseille, Strasbourg, Rennes, Toulouse and Nice,
and confirms that a Paris–Lyon matrix is refused at 200 km. No timing or quality
benchmark has been run outside Île-de-France.

```powershell
docker run --rm --network ROUTER_NETWORK -e RUN_ENGINE_TESTS=1 -e FRANCE_URL=http://ROUTER:8002 tournevia-engine-test:local python -m pytest -q tests/test_france_coverage.py
```

### Address lookup (optional)

Without address lookup, every delivery and the depot are placed with a pin on the map
or, as an exception, typed coordinates. With it, address fields suggest French addresses
while typing, a chosen address places the pin, and CSV import proposes coordinates for
rows that have none. A proposal is always unreviewed: an address points at the building,
so the entrance still has to be checked.

There are two ways to turn it on:

- **IGN's public service, chosen by an administrator.** In **Settings › Address search**
  an administrator ticks **Use the IGN address service (France)**. No server setting or
  account is needed. IGN's Géoplateforme then receives the text of every looked-up address
  and of every imported row without coordinates, with the depot's position so that nearby
  matches come first; the page says so before it is turned on.
  It is off for every company until an administrator chooses it.
- **The installation's own geocoder**, set in `GEOCODER_URL`, so that addresses never
  leave the server. When `GEOCODER_URL` is set, the application always uses it for every
  company and the Settings page only names it.

The [geocoder overlay](docker-compose.geocoder.yml) runs
[addok](https://github.com/addok/addok) with the Base Adresse Nationale on an internal
network that only the API joins: no published port, read-only containers, and no
request log (the image's default command logs every query, and so every address).
Pointing `GEOCODER_URL` at a public service would send each looked-up delivery address
to it for every company on the installation; do that only with the agreement of whoever
owns the data.

IGN's public Base Adresse Nationale service answers in the same format at
`https://data.geopf.fr/geocodage`, which the Settings switch uses. It needs no account and
is limited to 50 requests per second per address. A server can also force it for every
company with `GEOCODER_URL=https://data.geopf.fr/geocodage` and
`GEOCODER_DATASET=Base Adresse Nationale via IGN Géoplateforme (Licence Ouverte 2.0)`.
On 21 October 2026 a test installation holding fictional data only used it: lookups
through that installation returned the expected house numbers in Cergy and Lyon
(scores 0.97 and 0.98).

The BAN bundle is about 2.5 GB to download and 3.2 GB unpacked; addok needs at least
6 GB of memory, so the overlay limits Redis to `GEOCODER_MEMORY` (default 8g). Loading
the index takes one to two minutes after each start.

```powershell
New-Item -ItemType Directory -Force deploy/runtime/geocoder | Out-Null
Invoke-WebRequest https://adresse.data.gouv.fr/data/ban/adresses/latest/addok/addok-france-bundle.zip -OutFile deploy/runtime/geocoder/addok-france-bundle.zip
Expand-Archive deploy/runtime/geocoder/addok-france-bundle.zip deploy/runtime/geocoder/addok-data
docker compose --env-file .env -f deploy/docker-compose.example.yml -f deploy/docker-compose.geocoder.yml up -d
```

Set `GEOCODER_DATASET` to the source and date of the bundle, for example
`Base Adresse Nationale 2026-10-19 (Licence Ouverte 2.0)`: the interface shows it next
to the results, as the licence requires. To refresh the addresses, unpack a newer
bundle into a new directory, point the overlay's bind mounts at it and restart the
two geocoder services. Lookups and the import proposals were checked against this
overlay with the 19 October 2026 bundle (Paris, Lyon and Marseille addresses; 4–28 ms
per lookup inside the stack).

### Build the local graphs

This section prepares what the engine tests and benchmarks need: the regional graph
and the artificial restriction graph, each behind its own router on ports 8002 and
8003. To use Tournevia, [first route on your computer](#first-route-on-your-computer)
is enough.

The following PowerShell sequence was exercised on Docker Desktop with Linux
containers. Public road data and generated graphs stay in ignored `deploy/runtime`.
Allow several minutes and several GB for graph construction. The graph used for the
recorded tests and benchmarks was built on 22 September 2026 from a 338,509,338-byte
extract whose SHA-256 is pinned in [engines.json](engines.json). Geofabrik no longer
publishes that file: its `latest` URL serves a newer one, which `build_graph.py region`
refuses unless `--accept-new-extract` is given. `scripts/prepare_routing.py` downloads
and builds the regional graph and passes that option only for a file that differs from
the pin; results recorded on the pinned extract then need rerunning on the new one
before you rely on them, and the pin in `engines.json` updating.

#### Weight limits tagged `maxweightrating`

`build_graph.py` builds every graph with [valhalla-graph.lua](valhalla-graph.lua):
Valhalla's own tag file with three lines changed, so that a road whose gross-weight
limit is tagged `maxweightrating` keeps that limit when it has no `maxweight` tag.
Valhalla 3.9.0 reads `maxweight` only. In OpenStreetMap, France's roads moved from one
tag to the other: the two weight-limited roads of the engine tests received
`maxweightrating` on 2 February 2026 and lost `maxweight` on 7 November 2026, in
automated edits named "FR: migration `maxweight=*` vers `maxweightrating=*`". In the
Île-de-France extract of 17 November 2026, 6,658 roads carry `maxweightrating` and none
carries `maxweight`; in the pinned extract of 22 September, 6,005 carried `maxweight`
and 593 carried `maxweightrating` alone.

**A graph built with version 0.8.0 from an extract downloaded after 7 November 2026
has none of these limits**: a 19-tonne truck is routed over roads limited to 15
tonnes, and nothing reports it. Build that graph again, in a new directory, with this
version. A graph built from an earlier extract kept the limits that still had
`maxweight`.

Checked on 18 November 2026: on graphs built this way from the pinned extract and
from the 17 November extract, the real-engine suite passes (65 passed, 10 skipped, as
in the weekly workflow). With Valhalla's own tag file, the suite's `maxweightrating`
test fails and, on the 17 November extract, so do three regional tests. On the pinned
extract the tag file adds the limit to the 593 roads that had `maxweightrating` alone;
the benchmarks recorded on that extract were not rerun.

The router compares the limit with each vehicle's gross weight. Variants such as
`maxweightrating:hgv` and conditional limits are not read, as before. `build_graph.py`
stops when `valhalla-graph.lua` does not match the tag file inside the Valhalla image
it runs in; [upgrading Valhalla](../docs/developer/upgrading.md#valhalla) says how to
make the file again.

```powershell
.venv\Scripts\python -m pip install -r requirements-engines.txt
.venv\Scripts\python scripts/create_engine_fixtures.py
$enginePins = Get-Content deploy/engines.json | ConvertFrom-Json
# Downloads the extract only when deploy/runtime has none, and builds deploy/runtime/region.
.venv\Scripts\python scripts/prepare_routing.py
docker build -f deploy/Dockerfile.vroom -t tournevia-vroom:1.14.0 .
docker run --rm --memory 2g --cpus 2 --entrypoint python3 -v "${PWD}/deploy/runtime:/data" -v "${PWD}/deploy:/workspace:ro" $enginePins.valhalla_image /workspace/build_graph.py restrictions
# The application's matrix settings (second pass, 48 reserved locations); the engine suite expects them.
.venv\Scripts\python deploy/configure_matrix.py deploy/runtime/region/valhalla.json deploy/runtime/regional-matrix.json
docker run -d --restart unless-stopped --name tournevia-valhalla --memory 2g --cpus 2 -p 127.0.0.1:8002:8002 -v "${PWD}/deploy/runtime:/data:ro" --entrypoint valhalla_service $enginePins.valhalla_image /data/regional-matrix.json 2
docker run -d --restart unless-stopped --name tournevia-restrictions --memory 1g --cpus 1 -p 127.0.0.1:8003:8002 -v "${PWD}/deploy/runtime:/data:ro" --entrypoint valhalla_service $enginePins.valhalla_image /data/restrictions/valhalla.json 1
```

Build scripts refuse to replace existing graph tiles. Complete both builds before
starting services. On subsequent sessions use `docker start tournevia-valhalla
tournevia-restrictions`; stop with `docker stop tournevia-valhalla
tournevia-restrictions`. The restriction PBF contains deliberately artificial
roads and must never replace the regional graph. For graph updates, drain/stop all
calculations, build in a new runtime directory, switch the mount and rerun tests;
never modify tiles underneath a running calculation.

```powershell
docker build --target engine-test -t tournevia-engine-test:local .
docker run --rm --memory 2g --cpus 2 -e RUN_ENGINE_TESTS=1 tournevia-engine-test:local python -m pytest -q -m engines --basetemp /tmp/vrp-tests -p no:cacheprovider --tb=short
docker compose --env-file .env -f deploy/docker-compose.example.yml up --build -d
docker run --rm --memory 2g --cpus 2 tournevia-app:local python -m app.spike --valhalla-url http://host.docker.internal:8002
```

The CLI uses the bundled synthetic fixture by default and prints JSON. Add
`--objective MODELED_TRAVEL_COST` to exercise native fixed, distance and driving
costs; displayed operating time costs include service and waiting. Use `--input`
with a mounted synthetic input file and `--dataset` with its actual dataset label.
Exit 0 means independently valid **synthetic DRAFT**, exit 2 means a draft with
blocking validation issues; engine failures stop without accepting a result.
Neither objective asserts optimality. Toll and all-in totals remain unknown.

Current explicit calculation bounds: 25 vehicles, 250 deliveries, 301 distinct
points (250 for deliveries, the rest for depots and places), three exact truck profiles, at most `MATRIX_MAX_KM`
(200 km by default) between any two points, 50 m snapping, one Paris planning day
with hard windows, a whole-calculation
deadline of `JOB_TIMEOUT_SECONDS` (180 s by default) and 1–60 s solver budget. The adapter supports bounded 48-by-48 request
tiles, with 1–4 in-flight matrix requests according to `MATRIX_CONCURRENCY`.
Each bounded request group finishes and is parsed before the next starts, to
avoid internal routing queue buildup. Small depot-only rectangles run last;
response indexes preserve every directed connection. Large matrices balance point counts across
the same number of tiles to reduce the largest requests; the 48-point maximum
and hard-window boundaries remain.
Profiles omit only vertices excluded by joint equipment/capacity eligibility for
all selected vehicles of that profile; every delivery stays in the solver input.
Optimization also omits order-to-order rectangles ruled out by hard-window bounds,
including service time. Shared locations retain all eligible windows, equality
remains allowed, and depot connections are always queried. Manual reroutes request
only the fixed sequence's road legs, without all-pairs matrices or optimization.
Warning-free paths and independent geometry, schedule, load, equipment and cost
checks still apply; violations remain explainable in the saved draft.
Nearby service points are grouped by longitude/latitude for requests,
keeping the depot at index zero; job IDs and manual visit order are preserved.
Unknown constraints/fields and inconsistent time offsets are
rejected. Null directed arcs alone receive a solver-only `H+1` duration, where `H`
is the finite vehicle horizon; original reachability is checked again on output.
HTTP errors, missing points and malformed matrices are calculation failures.
Only the pinned CostMatrix warning 400 with its exact second-pass text and valid
connection indexes is supported. It records additional matrix search, not road
approval. Unknown warnings, clamping and every route warning reject the calculation;
remaining null cells stay forbidden. No destination-only transit or conditional
restriction coverage is claimed by accepting this heuristic matrix notice.

Actual engines: Valhalla `3.9.0-0baa5c687`, image pinned by digest in
[engines.json](engines.json); VROOM `1.14.0` from the checked commit in
[Dockerfile.vroom](Dockerfile.vroom), built with `USE_ROUTING=false`. These are
static OSM speeds with no traffic feeds. Builds warned about missing timezone
data, clipped administrative boundaries and absent elevation. Conditional/legal
restriction coverage and extract-boundary routes are not established by this
spike. Controlled tests verify explicit size/weight/axle/access tags and one-way
arcs on this build; synthetic Paris success is not permission to dispatch trucks.

OpenFreeMap Liberty remains the replaceable `MAP_STYLE_URL` default. The current
page loads its external basemap and preserves attribution. Tile requests reveal
the viewed area/network metadata; names, addresses, identifiers and road overlays
stay local. No external geocoder is used unless an administrator turns on IGN address
search or `GEOCODER_URL` names a public service. MapLibre 6.11.0's worker is explicitly
bundled as a local Vite worker asset. The Paris inputs remain synthetic.
