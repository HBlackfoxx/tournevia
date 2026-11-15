# 4. Development workflow

[Developer guide](README.md) › Development workflow

This page explains how to run each part while you work on it, which checks to run before a commit, and
what continuous integration runs. The exact, tested commands for every situation are in
[deploy/README.md](../../deploy/README.md); this page tells you which ones you need and when.

## What you need

- Git, Docker (Docker Desktop on Windows or macOS, Docker Engine on Linux) with Compose.
- Python 3.14 and Node 24 for host development and tests.
- For calculations: a Valhalla graph of Île-de-France, built once (several GB of disk, several minutes,
  6 GB of memory for the build). See [Build the local graphs](../../deploy/README.md#build-the-local-graphs).

## Three ways to run it

### 1. The whole application in Docker

Closest to a real installation. Use it to try a change end to end, including calculations.

```bash
docker build -f deploy/Dockerfile.vroom -t tournevia-vroom:1.14.0 .
docker build --target runtime -t tournevia-app:local .
python scripts/prepare_routing.py
python scripts/start_local.py --routing-config region/valhalla.json
```

`prepare_routing.py` downloads the Île-de-France road data and builds its graph once. The launcher
creates a private workspace in `deploy/runtime/local-workspace` with generated secrets, its own database
and the router running inside it, prints a one-time setup link, and serves the application on
[localhost:8007](http://localhost:8007). Running it again resumes the same workspace. After changing the
code, rebuild the image under a new tag and move the workspace to it with
`python scripts/start_local.py --upgrade-image NEW_TAG`; the launcher backs up the database first.
[First route on your computer](../../deploy/README.md#first-route-on-your-computer) explains each step and
the launcher's options.

Without `--routing-config`, the workspace uses a router on port 8002 of your computer instead, such as
the `tournevia-valhalla` container that [Build the local graphs](../../deploy/README.md#build-the-local-graphs)
starts for the engine tests.

### 2. Backend on the host, frontend with hot reload

Fastest for editing API handlers and screens. From the repository root, with a `.env` made from
[deploy/env.example](../../deploy/env.example) (see
[Local workspace and manual configuration](../../deploy/README.md#local-workspace-and-manual-configuration)):

```bash
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt     # Windows: .venv\Scripts\python
docker compose --env-file .env -f deploy/docker-compose.example.yml up -d postgres
cd backend
../.venv/bin/python -m app.setup_database
../.venv/bin/python -m app.main                 # API on http://127.0.0.1:8000
```

In a second terminal:

```bash
cd web
npm ci
npm run dev                                     # http://localhost:5173, proxies /api to port 8000
```

Set `PUBLIC_ORIGIN=http://localhost:5173` in `.env` before starting the API, since the browser then sends
that origin. `DB_PASSWORD` must equal `RUNTIME_DB_PASSWORD`. The Vite workflow is convenient but less
exercised than the Docker build; check the final behavior with option 1 before you submit.

The worker only runs on Linux. For calculations during host development, run it from Compose against the
same database (`docker compose ... up -d worker` with the routing overlay), or test calculation code
through the engine test image described below.

### 3. Just the tests

Most changes can be developed against the tests alone; see the next sections.

## Checks before every commit

Run these from the repository root. They are what CI runs on every push.

```bash
# Backend: lint, tests (with an isolated PostgreSQL), contracts, documentation links
python -m ruff check .
python scripts/test_backend.py
python scripts/export_contracts.py --check
python scripts/validate_pack.py

# Frontend
cd web
npm run format:check
npm run lint
npm test
npm run api:check
npm run build
```

- `scripts/test_backend.py` reads the bootstrap PostgreSQL credentials from `.env`, creates random
  `vrp_test_…` databases and drops them afterwards. Your own workspace database is never touched.
  Without PostgreSQL, `python -m pytest -c backend/pytest.ini backend/tests --basetemp .pytest-tmp -q`
  runs everything else and reports the database tests as skipped.
- `npm run format` rewrites files with Prettier when `format:check` fails.
- `validate_pack.py` checks the JSON schemas and examples, the CSV examples, the Compose file's basic
  rules and that **every relative link in every Markdown file resolves**. Run it after editing any
  documentation.

### Regenerate contracts

Run this whenever you change a Pydantic model used by the API, a route, a server message, a field limit or
the CSV columns:

```bash
python scripts/export_contracts.py
cd web && npm run api:generate
```

It rewrites, from the code:

| File | Content |
|---|---|
| `backend/openapi.json` | The API contract, also served at `/api/openapi.json` outside shared mode |
| `schemas/*.schema.json` | JSON schemas for depots, vehicles, deliveries, requests, jobs, edits and plans |
| `backend/spike-input.schema.json`, `backend/spike-output.schema.json` | The synthetic command line's input and output |
| `web/src/field-limits.json` | Minimum, maximum and length of each form field |
| `web/src/csv-columns.json` | Required and optional CSV columns, for the import screen and template |
| `web/src/locales/server-messages.json` | Every fixed message the server can send to the interface |
| `web/src/api-types.ts` | TypeScript types (from `npm run api:generate`) |

Commit them together with the change that caused them. If [examples/](../../examples/) no longer
validate, update them; [examples/README.md](../../examples/README.md) explains how the saved-plan example is
regenerated with the real engines.

## Suites that need the engines

Ordinary tests replace Valhalla and VROOM. Changes to `routing.py`, `optimization.py`, `spike.py`,
`validation.py`, `edits.py` or `worker.py` also need the real-engine suites, which run inside the
`engine-test` image on Linux. They need **both** routers from
[Build the local graphs](../../deploy/README.md#build-the-local-graphs): the regional graph on port 8002
and the deliberately artificial restriction graph on port 8003. The tests reach them at
`host.docker.internal` by default (`VALHALLA_URL` and `RESTRICTIONS_URL` override that):

```bash
docker build --target engine-test -t tournevia-engine-test:local .
docker run --rm --memory 2g --cpus 2 -e RUN_ENGINE_TESTS=1 tournevia-engine-test:local \
  python -m pytest -q -m engines --basetemp /tmp/vrp-tests -p no:cacheprovider --tb=short
```

Docker Desktop resolves `host.docker.internal` by itself. On Linux Docker Engine, add
`--add-host=host.docker.internal:host-gateway`, or run on the routers' network and pass `-e VALHALLA_URL=…`
and `-e RESTRICTIONS_URL=…`, as [engines.yml](../../.github/workflows/engines.yml) does.

The worker-process suite (`python -m tests.run_worker_checks`) and the quality benchmarks
(`python -m benchmarks.run`) run the same way; their network and database options are in
[Durable calculations and recovery](../../deploy/README.md#durable-calculations-and-recovery) and
[Quality benchmarks](../../deploy/README.md#quality-benchmarks). A benchmark regression on a valid result
exits with code 2. Report the actual figures; never present a skipped or partial run as a pass.

Installation-level acceptance (fresh HTTPS install, company workflow, backup, restore, interrupted-job
recovery) uses `scripts/test_https.py --image IMAGE --company` and `--recovery`; see
[Installation acceptance](../../deploy/RELEASE.md#installation-acceptance).

## Continuous integration

[.github/workflows/ci.yml](../../.github/workflows/ci.yml) runs on every push and pull request:

| Job | What it proves |
|---|---|
| `backend` | Ruff lint; backend and launcher tests against a disposable PostgreSQL 16; contracts match the code; the documentation pack is valid |
| `frontend` | Prettier, ESLint, Vitest, generated types match the contract, production build |
| `image` | The VROOM and application images build; Linux-only process supervision and routing core tests; the HTTPS overlay (TLS, cookies, CSRF, headers, log redaction); the setup behind an existing proxy |
| `database-ops` | Role grants, `pg_dump` backup and isolated restore against a Compose database with random credentials |
| `dependency-audit` | `pip-audit` on the locked Python runtime packages, `npm audit` on production npm packages |

[.github/workflows/engines.yml](../../.github/workflows/engines.yml) runs on demand and every Monday. It
downloads the current Île-de-France extract, builds the graph, and runs the real-engine suite, the worker
suite and the HTTPS recovery acceptance. It records the extract's size and SHA-256 in the job summary.

[.github/dependabot.yml](../../.github/dependabot.yml) proposes weekly updates for pip, npm, GitHub Actions
and Docker images. See [Upgrading](upgrading.md) for how to review them.

## Commits and pull requests

- Keep a change focused, with the tests that show its behavior, and the regenerated contracts.
- Use fictional data only. Never commit real addresses, driver data, passwords, tokens, routing graphs or
  customer exports. `deploy/runtime/` and `.env` are ignored for that reason.
- Describe in the pull request what behavior changed, which checks you ran and their result, and any
  migration or upgrade note. Add a line to [CHANGELOG.md](../../CHANGELOG.md) for changes users or
  operators will notice, especially API changes and migrations.
- See [CONTRIBUTING.md](../../CONTRIBUTING.md) for licensing of contributions.
