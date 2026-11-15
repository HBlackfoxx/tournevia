# 2. Backend

[Developer guide](README.md) › Backend

The backend is a FastAPI application in [backend/app/](../../backend/app/), written as plain modules with
typed functions. There is no service layer, repository pattern or dependency-injection framework: HTTP
handlers open a database session and call domain functions that take a `Session` and an organization id.

Python 3.14. Main libraries (pinned in [backend/requirements.txt](../../backend/requirements.txt)):
FastAPI, Pydantic 2 and pydantic-settings, SQLAlchemy 2 with psycopg 3, Alembic, argon2-cffi, uvicorn.

## Module map

### Wiring and configuration

| Module | Role |
|---|---|
| [main.py](../../backend/app/main.py) | `create_app()`: middleware, security headers, error handlers, routers, `/health`, `/ready`, `/configuration`, `/depot`, the static frontend. `SCHEMA_REVISION` names the migration the code expects. |
| [config.py](../../backend/app/config.py) | `Settings`: the environment variables of the API and worker, with validation (loopback origin in local modes, HTTPS in shared mode, token lengths). |
| [db.py](../../backend/app/db.py) | The SQLAlchemy `Base` and `make_engine` (5 s statement timeout for requests, none for migrations). |
| [http_limits.py](../../backend/app/http_limits.py), [http_logging.py](../../backend/app/http_logging.py) | Request body limit; one bounded log line per request. |
| [messages.py](../../backend/app/messages.py) | Turns Pydantic validation errors into fixed, translatable sentences with their values kept separately. |

### Accounts and first use

| Module | Role |
|---|---|
| [accounts.py](../../backend/app/accounts.py) | Tables: `organizations` (with the company settings), `users`, `login_sessions`, `login_throttles`. |
| [auth.py](../../backend/app/auth.py) | Passwords (Argon2id), sign-in throttling, sessions and CSRF tokens, `current_session`, `who`, `require_admin`. |
| [auth_api.py](../../backend/app/auth_api.py) | `/session`, `/preferences`, `/session/password`, `/users`. |
| [onboarding.py](../../backend/app/onboarding.py) | `/setup`: the one-time creation of the company, its depot and the first administrator. |
| [manage_users.py](../../backend/app/manage_users.py) | Operator command line to create or recover accounts; passwords read from the terminal. |

### Source records

| Module | Role |
|---|---|
| [planning.py](../../backend/app/planning.py) | **The domain model.** Pydantic models for capacity, trucks, windows, rates, limits, vehicles (`VehicleSource` saved, `VehicleInput` for one day), deliveries (`OrderInput`), customers, the calculation input (`PlanningInput`) and result (`Draft`, `Route`, `Stop`, `Leg`). Every API body and JSON column is validated by these. |
| [fleet.py](../../backend/app/fleet.py) | Depots and other places: `Location`, `DepotInput`, the `depots` table. |
| [records.py](../../backend/app/records.py) | Tables `vehicles`, `orders`, `customers`; `DataError`; listing, filters and search; `write_record` (create or version-checked update); depot changes; deleting deliveries. |
| [data_api.py](../../backend/app/data_api.py) | HTTP handlers for depots, vehicles, deliveries, customers, imports, address lookup, road access checks and company settings. |
| [imports.py](../../backend/app/imports.py) | CSV import: exact headers, per-cell errors, location warnings, preview (`dry_run`) and atomic commit sharing the same validation. |
| [geocoding.py](../../backend/app/geocoding.py) | Address search through the installation's addok, or IGN's public service when a company allows it. |
| [numbering.py](../../backend/app/numbering.py) | Delivery number formats such as `LIV-{YYYY}-{N:4}` and the next free number. |
| [examples.py](../../backend/app/examples.py) | The fictional example depot, vehicles and deliveries a company can add and remove. |

### Calculation

| Module | Role |
|---|---|
| [jobs.py](../../backend/app/jobs.py) | Tables `planning_jobs` and `plans`; `submit` (checks and input copy), idempotent replay, queue limit, `save_result`. |
| [jobs_api.py](../../backend/app/jobs_api.py) | `/optimization-jobs`, `/jobs`, `/plans`, edits, finalize, CSV export, route sheets, `/system-status`. |
| [worker.py](../../backend/app/worker.py) | The worker loop, singleton lock, recovery of abandoned jobs, and the calculation child (`--compute`). |
| [spike.py](../../backend/app/spike.py) | The calculation itself: `calculate`, `profile_matrices`, `build_routes`. Also a command line that runs a calculation on a synthetic JSON file (the name is historical). |
| [routing.py](../../backend/app/routing.py) | The Valhalla adapter: truck costing, snapping checks, batched directed matrices, road legs, `Deadline`, `EngineError`. |
| [optimization.py](../../backend/app/optimization.py) | The VROOM adapter: builds the custom-matrix problem, runs the executable with limits, decodes the answer. |
| [validation.py](../../backend/app/validation.py) | Independent checks of a result against the inputs and the final road legs. |
| [edits.py](../../backend/app/edits.py) | Manual edits of a plan, `reroute`, finalize, CSV export and route sheets. |
| [processes.py](../../backend/app/processes.py) | Linux parent-death signal so no child process outlives its parent. |

### Operations

| Module | Role |
|---|---|
| [setup_database.py](../../backend/app/setup_database.py) | Setup entry point: roles, migrations, grants. |
| [database_roles.py](../../backend/app/database_roles.py) | Role creation and the exact grants of the runtime role. |
| [provision.py](../../backend/app/provision.py) | Creates the legacy demonstration company; never runs at API startup. |
| [monitoring.py](../../backend/app/monitoring.py) | `/metrics` in Prometheus format, enabled by `MONITOR_TOKEN`. |
| [retention.py](../../backend/app/retention.py) | Operator commands to purge old calculations and erase one delivery's, customer's or account's personal data. |

Database migrations live in [backend/migrations/versions/](../../backend/migrations/versions/), numbered
`0001` to `0017`. Benchmarks live in [backend/benchmarks/](../../backend/benchmarks/).

## How a handler is written

A typical update handler, from [data_api.py](../../backend/app/data_api.py):

```python
@api.put('/vehicles/{key}', response_model=VehicleRecord, operation_id='updateVehicle')
def update_vehicle(key: UUID, data: VehicleSource, request: Request, response: Response,
                   if_match: Annotated[str | None, Header()] = None):
    version = expected_version(if_match)
    with Session(request.app.state.engine) as session, session.begin():
        return etag(response, write_record(session, who(request).organization_id, data, key, version))
```

The conventions it shows:

- **The router adds authentication.** The `data_api.py` and `jobs_api.py` routers are created with
  `dependencies=[Depends(current_session)]`. In `auth_api.py` each route declares its own dependency
  (sign-in itself has none), `/setup` in `onboarding.py` is public and authorized by the setup token, and
  `/metrics` checks a bearer token. Admin-only actions call `require_admin(who(request))` first.
- **The body is a domain model.** FastAPI validates `data` with the same Pydantic model the database and
  worker use. Models inherit `Model` from `planning.py`, or set `extra='forbid'` themselves
  as the depot models in `fleet.py` do, so a field the server does not implement is refused, not ignored.
- **One transaction per write.** `with Session(...) as session, session.begin():` commits on success
  and rolls back on any exception.
- **Ownership is an argument.** The organization id always comes from `who(request)`, and every query in
  the domain function filters on it. `find_row(session, owner, table, key)` returns 404 for another
  company's id, exactly as for a missing one.
- **Edit conflicts use integer versions.** Reads return `ETag: "<version>"`. Updates require
  `If-Match: "<version>"` (missing: 428 `VERSION_REQUIRED`; stale: 412). The version increments on every
  write. There is no other history.
- **Concurrent writes are serialized per company.** Domain functions that check something and then write
  call `lock_owner(session, owner)` first, which locks the company's `organizations` row.
- **Retries are safe.** Requests that start work (`POST /optimization-jobs`, `POST /plans/{id}/edits`)
  require an `Idempotency-Key`. The same key with the same body returns the first job; the same key with a
  different body is refused with 409.
- **`operation_id` names the operation** in the OpenAPI contract and therefore in the frontend's
  generated types. Choose it when you add a route and do not rename it lightly.

## Errors

Raise `DataError(status, code, message, identifiers=None)` from [records.py](../../backend/app/records.py)
for every expected refusal. The handler in `main.py` returns:

```json
{"code": "VEHICLE_OFF_DAY", "detail": "Some selected vehicles do not work on the planning day. ...", "identifiers": ["VUL-03"]}
```

- `code` is stable and in capitals; the interface uses it to decide what to offer the user.
- `detail` is a **fixed English sentence**. The frontend translates it by looking it up in
  `web/src/locales/fr.json`. Do not put record values into the sentence; pass them in `identifiers`,
  which are shown next to it unchanged. `scripts/export_contracts.py` collects every literal message
  passed to `DataError`, `EngineError`, `fail_job`, `fail_work`, `CellError` and `CalculationFailure`
  into `web/src/locales/server-messages.json`, and a frontend test fails if one has no French translation.
- Inside a calculation, raise `EngineError` (from `routing.py`) with a sentence written for the
  dispatcher. Any other exception in the calculation child is reported as a generic failure: its text
  never reaches the user, because it might contain inputs or engine output.

## The calculation pipeline

`calculate(inputs, engine, deadline, on_stage)` in [spike.py](../../backend/app/spike.py) runs inside
the calculation child. It reports its stage through `on_stage`, which the worker copies to the job.

1. **PRECHECK, `points_and_profiles`.** Builds the list of distinct points: the route start and end places
   first (the main depot at index 0, even when no selected vehicle uses it; it then takes part in no
   matrix), then delivery points sorted by coordinates so nearby points fall in the same matrix batch. Collects the distinct truck profiles (at most three). Refuses more than 301
   points.
2. **MATRIX, `profile_matrices`.** For each truck profile, keeps only the points some vehicle of that
   profile can serve (equipment, every capacity dimension, the depot a delivery ships from). It asks
   Valhalla which pins have no road usable by that truck within 50 m (`roadless_points`). Such a delivery
   is left out for that profile and recorded in `Matrix.no_road`; such a depot or place where a vehicle
   starts or ends stops the calculation with an `EngineError` naming the vehicles. It then requests the directed duration and distance matrix in
   batches (`Valhalla.matrix`). Time windows are passed as bounds so that pairs that can never follow each
   other are skipped. A cell the engine cannot fill stays `None`: it means "no usable connection",
   never zero.
3. **Mapping, `prepare`** in [optimization.py](../../backend/app/optimization.py). Builds VROOM's JSON:
   one job per delivery (location, five-dimension delivery amount, service time, skills, priority, time
   window shortened by the company's safety margin), one vehicle per vehicle (profile, start and end,
   capacity, skills, working window, optional cost rates and route limits) and one matrix per profile. A
   `None` cell becomes a duration longer than the whole day, so no feasible route can use it. Equipment
   and "ships from this depot" both become VROOM skills. Every number is checked against VROOM's integer
   limits.
4. **SOLVE, `run_vroom`.** Runs `/usr/local/bin/vroom` without a shell, in a private directory, with a
   memory limit, a search budget cut to what the deadline leaves, and a parent-death signal.
   `decode_solution` maps VROOM's integer ids back to vehicle and delivery UUIDs.
5. **ROUTE, `build_routes`.** Rejects any solver route that uses a `None` connection. Requests the actual
   road leg for every hop (`Valhalla.legs`), which may differ slightly from the matrix estimate, and
   recomputes the schedule from those legs: the latest departure that keeps every stop inside its window
   less the safety margin, arrival, wait, service start and departure at each stop, remaining load,
   distance, driving time and the operating cost estimate. Unassigned deliveries get a reason:
   `NO_COMPATIBLE_VEHICLE`, `NO_ROAD_NEAR_PIN`, `NO_ROUTE_IN_DATASET` or `SOLVER_UNASSIGNED`, and
   `MANUALLY_UNASSIGNED` after an edit.
6. **VALIDATE, `validate`** in [validation.py](../../backend/app/validation.py). Re-checks everything from
   the original inputs: ownership and context, every delivery exactly once, known vehicles and exact truck
   profiles, leg chain identity, forbidden connections, road path geometry and snapping, capacity in every
   dimension, real (not shortened) time windows, equipment, ships-from depot, vehicle hours, route limits,
   route totals and costs. Each failure is a string with a stable prefix, such as `WINDOW: service starts after its hard window
   (VUL-01, LIV-0042)`. Any issue sets `validation='FAIL'`.

A manual edit takes a shorter path: `reroute` in [edits.py](../../backend/app/edits.py) checks the
requested assignment, checks that every assigned pin has a road the truck can use within 50 m
(`require_routable_assignments`), then calls
`build_routes(..., manual=True)` without a solver or matrix, and validates the same way.

### The planning input and result

`PlanningInput` is the complete, self-contained description of one calculation: the organization, data
kind (`SYNTHETIC` for sample workspaces, `ROUTED` for a company), service date, main depot and the other
places used, vehicles as they are that day (`VehicleInput` with a dated `available_window`), deliveries,
routing policy, objective (`TRAVEL_TIME` or `MODELED_TRAVEL_COST`), solver budget and safety margin.
Times inside the calculation are integer seconds since midnight of the service date in Europe/Paris
(`inputs.seconds(...)`).

`Draft` is the result: routes (with stops and legs), unassigned deliveries with reasons, the dataset label,
`validation` and `blocking_issues`. Both are stored as JSON in the plan, and both are part of the
generated contract (`schemas/route-plan.schema.json`).

## The worker in detail

[worker.py](../../backend/app/worker.py) is written defensively because it is the only place where long,
memory-hungry work happens.

- **One worker.** `acquire()` takes advisory lock `74682041` on a dedicated connection that is never
  pooled. When a statement fails, the worker checks that it still holds the lock; if the connection or
  the lock is lost it exits (`GuardLost`) and Compose restarts it.
- **Recovery happens only at start.** After taking the lock, the worker marks every `RUNNING` job
  `FAILED` with `WORKER_INTERRUPTED` and deletes leftover `vrp-job-*` and `vrp-solve-*` directories.
- **Each job runs in a child process** with `RLIMIT_AS` 1.5 GB and `RLIMIT_FSIZE` 16 MB, its own process
  group, an environment limited to `CHILD_ENVIRONMENT` plus four routing settings, and a deadline. The
  parent polls every 0.25 s, updates the stage, touches a heartbeat file used by the container health
  check, and enforces 4 MB of logs and a 12 MB result.
- **Saving is conditional.** `save_result` only writes when the job is still `RUNNING` and, for an edit,
  when the plan still has the reserved version and active job. A late result is discarded.

The worker only runs on Linux (it uses `prctl` and `resource`). On Windows or macOS, run it in Docker.

## Database and migrations

- Tables are declared with SQLAlchemy 2 typed mappings in the module that owns them (`accounts.py`,
  `fleet.py`, `records.py`, `jobs.py`). [migrations/env.py](../../backend/migrations/env.py) imports them.
- Migrations are written by hand, one file per change, with readable revision ids such as
  `0017_safety_margin`. They are applied by setup as the migration owner, before the API starts. The API
  never migrates.
- `SCHEMA_REVISION` in [main.py](../../backend/app/main.py) must equal the newest revision:
  `/api/v1/ready`, the backup command and the metrics all compare the database against it.
- After a migration adds a table, a column the runtime role must write, or a function, update
  [database_roles.py](../../backend/app/database_roles.py): a new table goes into `TABLES` (and `WRITABLE`
  or `EPHEMERAL` if the runtime role writes it), a column or function into `grant_statements`. Setup
  refuses a database with tables it does not know, the backup check compares against `TABLES`, and a
  missing grant gives "permission denied" in production even though the tests that run as the owner pass.
- Vehicles, deliveries and customers keep their record in a JSONB `data` column validated by Pydantic,
  so adding a field to one of them needs no migration. Depots use real columns. Columns are used for what the database must index, constrain
  or lock: identifiers, dates, versions, status and company settings.
- Constraints that protect correctness are in the database too: unique identifiers per company, version
  greater than zero, the job and plan state combinations.

## Settings

All configuration is environment variables read by `Settings` in [config.py](../../backend/app/config.py)
(and a root `.env` file in development). Setup's own credentials (`POSTGRES_*`, `RUNTIME_DB_*`,
`MIGRATION_DB_*`) are read by `DatabaseAdminSettings` in
[database_roles.py](../../backend/app/database_roles.py); the bootstrap and migration credentials never
reach the API or worker. Every setting is described in
[deploy/SETTINGS.md](../../deploy/SETTINGS.md), and [deploy/env.example](../../deploy/env.example) lists
them with their defaults.
Add a setting by adding a typed field to `Settings` with a validator if needed, then document it in
`deploy/env.example`. `scripts/validate_pack.py` refuses a Compose variable that `env.example` does not
document.

## Tests

[backend/tests/](../../backend/tests/) uses pytest. Markers in
[pytest.ini](../../backend/pytest.ini) select the suites that need more than Python:

| Marker | Needs | Enabled by |
|---|---|---|
| none | nothing | always |
| `postgres` | a PostgreSQL role with `CREATEDB` | `TEST_DATABASE_URL` |
| `engines` | a running Valhalla graph and the VROOM binary (Linux) | `RUN_ENGINE_TESTS=1` |
| `worker` | Linux, engines and PostgreSQL | `RUN_WORKER_TESTS=1` and `TEST_DATABASE_URL` |
| `database_ops` | Docker Compose and a bootstrap role | `RUN_DATABASE_OPS_TESTS=1` |

The `database` fixture in [conftest.py](../../backend/tests/conftest.py) creates a fresh
`vrp_test_<random>` database, migrates it to head and drops it afterwards; it never touches existing data.
API tests build the app with `create_app(settings)` and use FastAPI's `TestClient`. The shared synthetic
fixture is [tests/fixtures/paris-ten.json](../../backend/tests/fixtures/paris-ten.json): one depot, two
vehicles and ten deliveries in Paris, also used by the frontend tests and the example data.

A skipped suite is not evidence. When a change touches routing, solving, validation or the worker, run the
engine and worker suites as described in [Development workflow](development.md#suites-that-need-the-engines).
