# 5. Making changes

[Developer guide](README.md) › Making changes

Recipes for the changes people make most often. Each one lists every place that must move together, the
traps that tests may not catch, and the checks to run. After any of them, run the checks in
[Development workflow](development.md#checks-before-every-commit).

- [Add a field to a vehicle, delivery, customer or depot](#add-a-field-to-a-vehicle-delivery-customer-or-depot)
- [Add a planning constraint](#add-a-planning-constraint)
- [Add a company setting (worked example: the safety margin)](#add-a-company-setting-worked-example-the-safety-margin)
- [Write a database migration](#write-a-database-migration)
- [Add an API endpoint](#add-an-api-endpoint)
- [Add or change a server message](#add-or-change-a-server-message)
- [Add interface text or a screen](#add-interface-text-or-a-screen)
- [Add a CSV import column](#add-a-csv-import-column)
- [Add an environment variable](#add-an-environment-variable)
- [Change the calculation limits](#change-the-calculation-limits)
- [Change the basemap or the routing area](#change-the-basemap-or-the-routing-area)

## Add a field to a vehicle, delivery, customer or depot

Vehicles, deliveries and customers are stored as JSON in a `data` column, so a new field on them needs no
database migration. **Depots and places are different**: the `depots` table has a real column per field,
so a depot field also needs a migration, a column on `Depot` in [fleet.py](../../backend/app/fleet.py),
and changes to `depot_record`, `create_depot` and `write_depot` in [records.py](../../backend/app/records.py)
and to `initialize` in [onboarding.py](../../backend/app/onboarding.py).

Either way it needs care, because **every record and every saved plan already in a database lacks the
field**.

1. **Model.** Add the field to the Pydantic model: `VehicleSource`/`VehicleBase`, `OrderInput` or
   `CustomerInput` in [planning.py](../../backend/app/planning.py), `DepotInput` in
   [fleet.py](../../backend/app/fleet.py). Use the API units (integers in mm, kg, dm³, m, s, cents) and
   `Field(ge=…, le=…)` bounds.
2. **Give it a default** that means "as before". Existing rows are validated with the same model when they
   are read, and saved plans keep a copy of every record in `plans.input_data`; without a default, reading
   them fails. If no default is right, write a data migration that fills existing rows, as
   [0012_weekly_hours](../../backend/migrations/versions/0012_weekly_hours.py) does, and accept that old
   saved plans must still load.
3. **Decide what the planner does with it.** If it changes which routes are valid, it is a constraint:
   follow [Add a planning constraint](#add-a-planning-constraint). If it is information only (a note, a
   phone number), make sure it is copied where people need it: the CSV export and route sheets in
   [edits.py](../../backend/app/edits.py).
4. **Import.** If companies will set it from a file, add an optional CSV column; see
   [Add a CSV import column](#add-a-csv-import-column).
5. **Contracts.** Run `python scripts/export_contracts.py` and `npm run api:generate`. The new field
   appears in `api-types.ts`, and its bounds in `field-limits.json`.
6. **Interface.** Add the input to the record editor in [SourceEditor.tsx](../../web/src/SourceEditor.tsx)
   (and the list column in `Workspace.tsx` if it should be visible there). Show the value in readable units
   with the helpers in [format.ts](../../web/src/format.ts) and convert back to API units before saving.
   Add the labels to `fr.json`.
7. **Examples and fixtures.** Update [examples/](../../examples/) if the field should appear in them, and
   the shared fixture `backend/tests/fixtures/paris-ten.json` if tests need it.
8. **Search.** If people should find records by it, extend `matching` in
   [records.py](../../backend/app/records.py).

## Add a planning constraint

This is the most delicate change in the project. The rule is simple: **a constraint is either enforced by
the solver and verified independently, or refused.** It is never accepted and ignored.

1. **Model the input** (previous recipe), with bounds and a default that keeps old data valid.
2. **Refuse what you cannot plan.** If only some forms of the constraint can be honored, refuse the others
   with a `DataError` (at save time) or an `EngineError` (at calculation time) whose message says what to
   change.
3. **Give it to the solver** in `prepare` in [optimization.py](../../backend/app/optimization.py). VROOM
   expresses capacities, skills, time windows, priorities, vehicle working windows, costs and route limits
   (`max_tasks`, `max_distance`, `max_travel_time`). Other rules are often expressible as skills, the way
   equipment and "ships from this depot" are. Check every number against VROOM's integer limits as the
   existing code does. If the solver cannot express the rule, do not approximate it silently.
4. **Keep it in the schedule.** If it affects times or loads, update `build_routes` in
   [spike.py](../../backend/app/spike.py), which recomputes the schedule from the final road legs, and the
   manual-sequence path used by edits.
5. **Verify it independently** in `validate` in [validation.py](../../backend/app/validation.py), from the
   original inputs and the final legs, never from a solver flag. Add an issue with a new stable prefix
   (`LIMIT:`, `WINDOW:`…) and the vehicle and delivery it concerns.
6. **Explain unassigned work.** If the constraint can leave deliveries unassigned for a reason the
   dispatcher should know, consider a new reason next to `NO_COMPATIBLE_VEHICLE` in `build_routes`, and
   show it in [RouteReview.tsx](../../web/src/RouteReview.tsx).
7. **Test at three levels:** unit tests of the mapping and schedule (`test_routing_core.py`,
   `test_final_schedule.py`), a validator test that a violating result is refused (`test_validator.py`),
   and an `engines` test that runs the real solver and router (`test_engines.py`). Then run the quality
   benchmarks and report any change in the results.
8. **Document it** in [3. Data contracts](../03-data-contracts.md) or
   [4. Routing and validation](../04-routing-and-validation.md), and in the changelog.

## Add a company setting (worked example: the safety margin)

Company settings are columns on `organizations`. The safety margin (minutes kept free before each
delivery window ends) went through every layer, and is a good model to copy. Search the code for
`safety_margin` to see each piece.

| Layer | File | What changed |
|---|---|---|
| Migration | [0017_safety_margin.py](../../backend/migrations/versions/0017_safety_margin.py) | Column `safety_margin_minutes`, `NOT NULL`, server default 15, check constraint 0 to 120 |
| Table | [accounts.py](../../backend/app/accounts.py) | `Organization.safety_margin_minutes` |
| Expected schema | [main.py](../../backend/app/main.py) | `SCHEMA_REVISION = '0017_safety_margin'` |
| Grants | [database_roles.py](../../backend/app/database_roles.py) | Column added to the runtime role's `UPDATE (…) ON organizations` list |
| API | [data_api.py](../../backend/app/data_api.py) | `SafetyMarginSetting` model and `PUT /workspace/safety-margin`, admin only, row locked while written |
| Read | [main.py](../../backend/app/main.py) | Exposed in `GET /configuration` |
| Calculation input | [jobs.py](../../backend/app/jobs.py), [planning.py](../../backend/app/planning.py) | `submit` copies it into `PlanningInput.safety_margin_seconds` (default 0, so plans saved before it existed still load) |
| Solver | [optimization.py](../../backend/app/optimization.py), [planning.py](../../backend/app/planning.py) | Delivery windows end at `inputs.closes(order)`, the real end less the margin |
| Schedule | [spike.py](../../backend/app/spike.py) | Departure chosen to keep the margin when the roads allow it |
| Validation | [validation.py](../../backend/app/validation.py) | Unchanged on purpose: it checks the **real** windows, so a schedule that cannot keep the margin is still valid |
| Settings screen | [CalculationSettings.tsx](../../web/src/CalculationSettings.tsx), `Workspace.tsx` | Field, hint, save |
| Plan review | [RouteReview.tsx](../../web/src/RouteReview.tsx) | Shows the margin a plan was calculated with |
| Translations | [fr.json](../../web/src/locales/fr.json) | Every new string |
| Tests | `test_routing_core.py`, `test_final_schedule.py`, `test_onboarding.py`, `test_database_operations.py`, `CalculationSettings.test.tsx` | Mapping, schedule, API, migration and screen |

Two lessons from it:

- **Copy the setting into the calculation input.** A plan must keep the value it was calculated with, so
  later changes to the setting do not change what a saved plan means.
- **Forget the grant and production breaks.** The tests and setup run as the owner role; only the
  runtime role is restricted. The `database_ops` suite checks grants against a real Compose database.

## Write a database migration

Migrations are hand-written Alembic files in
[backend/migrations/versions/](../../backend/migrations/versions/).

1. Copy the newest file and rename it with the next number and a short name, for example
   `0018_route_notes.py`. Set `revision = '0018_route_notes'` and `down_revision` to the previous
   revision id. Keep the docstring: one sentence saying what the migration does.
2. Write `upgrade()` with `op.*` calls or SQL. A new `NOT NULL` column needs a `server_default` so
   existing rows get a value. Data transformations run in the same transaction as the schema change;
   setup runs migrations with no statement timeout.
3. Write `downgrade()`. Downgrades exist for development; operators go back by restoring the backup the
   upgrade took, not by downgrading. A downgrade that would lose data must refuse, as
   [0015_depots](../../backend/migrations/versions/0015_depots.py) does.
4. Declare the same column or table in the SQLAlchemy model.
5. Set `SCHEMA_REVISION` in [main.py](../../backend/app/main.py) to the new revision, and update the
   revision asserted in `test_database_operations.py`.
6. Update [database_roles.py](../../backend/app/database_roles.py). A new table goes into `TABLES` (setup
   refuses a database with unknown tables, and the backup check compares against this list) and into
   `WRITABLE` or `EPHEMERAL` if the runtime role writes it. A column the runtime role updates, or a SQL
   function, goes into `grant_statements`. Deletions by the runtime role go through a function
   owned by the migration role with `EXECUTE` granted, as `delete_orders` does.
7. Run `python scripts/test_backend.py`: every PostgreSQL test migrates a fresh database from zero to head.
   Run the `database_ops` suite (see [Development workflow](development.md#continuous-integration)) when
   grants changed.
8. Add the migration to the changelog with what operators must do after upgrading, if anything. The
   website's upgrade page lists every migration with its effect.

## Add an API endpoint

1. Put the handler in the router of its domain: `data_api.py` (records and settings), `jobs_api.py`
   (calculations and plans), `auth_api.py` (sessions and users). Keep the handler short; put the logic in a
   function that takes a `Session` and an organization id, in the module that owns the data.
2. Declare request and response bodies as classes inheriting `Model` from
   [planning.py](../../backend/app/planning.py) (unknown fields refused). Give the route a
   `response_model`, a stable `operation_id`, and a status code (`201` for a creation, `202` for queued
   work).
3. Authentication is automatic on the `data_api.py` and `jobs_api.py` routers; in `auth_api.py`, declare
   the dependency on the route as its neighbours do. Call `require_admin(who(request))` for administrator
   actions. Take the organization only from `who(request).organization_id`.
4. For an update, require `If-Match` with `expected_version` and return the new `ETag` with `etag`. For a
   request that starts work, require an `Idempotency-Key` and use `find_replay`.
5. Refuse with `DataError` and a fixed sentence (next recipe).
6. Test it with FastAPI's `TestClient` against the `database` fixture, including another company's id
   (must answer 404) and a planner calling an admin route (must answer 403).
7. Regenerate the contracts and types. Add an alias in [api.ts](../../web/src/api.ts) if the frontend uses
   a new schema, then call it with `api<T>()` or `save()`.
8. Reads that the interface polls must accept `X-VRP-Background: 1` without extending the session; that is
   handled in `current_session`, nothing to do in the handler.

## Add or change a server message

1. Write a complete English sentence that tells the person what to do:
   `raise DataError(422, 'VEHICLE_OFF_DAY', 'Some selected vehicles do not work on the planning day. Check their weekly hours and days off in Fleet.', off_day)`.
2. Keep values out of the sentence; pass the records concerned as the fourth argument (`identifiers`).
   If a value must be inside the text, build the message with `+` after a fixed beginning ending with
   `: ` or `. `, for example `'Independent re-validation failed: ' + details`. The export collects that
   beginning and the interface translates it. An f-string's beginning is not collected, so no test would
   ask for its translation.
3. Choose a stable code in capitals. The interface may react to the code; never reuse a code for a
   different meaning.
4. Run `python scripts/export_contracts.py`, then add the sentence and its French translation to
   [fr.json](../../web/src/locales/fr.json). `server-messages.test.ts` fails until you do.
5. Messages from operator-only modules (command lines, metrics, configuration) are not collected; they are
   listed in `OPERATOR_ONLY` in [export_contracts.py](../../scripts/export_contracts.py).

## Add interface text or a screen

- Wrap every string in `t('…')` and add its French translation to `fr.json`. Use `{0}`, `{1}` placeholders
  for values, and `plural(count, one, other)` for counts.
- Follow the existing patterns: tokens from `style.css` for colours, `ask()` for confirmations, `api()`
  for requests, `ApiError.fields` to show field errors next to their inputs, `role="alert"` for errors and
  `role="status"` for confirmations.
- A new workspace area needs: its id in the `Area` type and `AREAS` in
  [Workspace.tsx](../../web/src/Workspace.tsx), an entry in `areas` (its English name), an icon path in
  `NavIcon` in [Brand.tsx](../../web/src/Brand.tsx), the branch that renders it, and its translations.
- A new settings section is usually a component like `CalculationSettings`. In `Workspace.tsx`, add its
  id to the settings state type (`'depot' | 'numbers' | … | 'personal'`), a button in the settings
  sub-navigation, and a branch that renders it with an `onChanged` callback that reloads the
  configuration (`onReconnect`).
- Write a `*.test.tsx` that finds elements by label, role and visible text, stubs `fetch`, and checks the
  request sent and what the person sees afterwards.
- Look at it at phone width and in dark mode before you submit.

## Add a CSV import column

The import accepts exact headers, so adding a **required** column breaks every company's existing file.
Add columns as **optional trailing** columns instead.

1. Append the column to `OPTIONAL_HEADERS[kind]` in [imports.py](../../backend/app/imports.py). Optional
   columns must keep their order; an empty cell keeps the model default.
2. Read it in `parse_row`. Optional columns are parsed as whole numbers unless they are listed in `CODES`
   (text such as a customer or depot code), and for vehicles they go into `limits`; a column of another
   kind needs its own handling there. Refuse a bad cell with `CellError(column, 'fixed sentence')` so the
   preview shows it on the right row and column.
3. If an existing record changes through an import with `update_existing`, check that `changed_fields`
   reports the new field in a readable way, and add its label in `ImportPanel.tsx`.
4. Regenerate the contracts: `web/src/csv-columns.json` updates the import screen and the template file
   it offers.
5. `scripts/validate_pack.py` checks the exact headers of [examples/orders.csv](../../examples/orders.csv)
   and [examples/vehicles.csv](../../examples/vehicles.csv) against the JSON examples. Those files use the
   required columns only; update them and the checker together if you change required columns.
6. Document the column in the website's CSV guide.

## Add an environment variable

1. Add a typed field with a safe default to `Settings` in [config.py](../../backend/app/config.py), with a
   validator if a wrong value could be dangerous.
2. Document it in [deploy/env.example](../../deploy/env.example) and in the operators' reference,
   [deploy/SETTINGS.md](../../deploy/SETTINGS.md), and pass it to the services that need it in
   [docker-compose.example.yml](../../deploy/docker-compose.example.yml). Compose passes only the variables
   its files name. `validate_pack.py` refuses a Compose variable that `env.example` does not list.
3. The calculation child receives only `CHILD_ENVIRONMENT` and the routing settings set in
   `child_environment` in [worker.py](../../backend/app/worker.py). A setting the calculation needs must
   be added there explicitly. Never pass database credentials or tokens to it.
4. Secrets use `SecretStr` and never appear in logs, errors or responses.

## Change the calculation limits

The limits (25 vehicles, 250 deliveries, 301 points, 3 truck profiles) come from measurements on the
reference machine, not from a commercial choice. To change them:

1. Backend: `PlanningInput` and `max_stops` in `VehicleLimits` in [planning.py](../../backend/app/planning.py),
   `OptimizationRequest` in [jobs.py](../../backend/app/jobs.py), `EditRequest` and its id lists in
   [edits.py](../../backend/app/edits.py) (otherwise edits of larger plans fail), `MAX_MATRIX_POINTS` in
   [routing.py](../../backend/app/routing.py), the delivery count and its message in `points_and_profiles`
   in [spike.py](../../backend/app/spike.py), and the `limit` maximum of the list endpoints in
   [data_api.py](../../backend/app/data_api.py), which the interface's "select all" relies on.
2. Frontend: the selection limits are written in [PlanningPanel.tsx](../../web/src/PlanningPanel.tsx)
   and `Workspace.tsx` (search for `250` and `25`), with their messages.
3. Measure before you claim: run the sizing benchmarks (`scripts/benchmark_sizing.py`,
   `backend/benchmarks/`) on the target hardware. Larger problems need more router memory, a longer
   `JOB_TIMEOUT_SECONDS` and possibly `MATRIX_CONCURRENCY`; the website's sizing page records the measured
   figures.
4. Update the limits wherever they are documented (README, the scope documents, the website).

## Change the basemap or the routing area

- **Basemap.** Set `MAP_STYLE_URL` to another MapLibre style served over HTTPS. No code change. Respect
  the provider's terms and attribution.
- **Routing area.** The router's graph decides where Tournevia can plan. Building a graph for another
  region or all of France is an operator task described in
  [Routing for all of France](../../deploy/README.md#routing-for-all-of-france). The code assumes the
  Europe/Paris time zone for the planning day and French address services; another country needs changes
  to `PARIS` in `planning.py`, address lookup in `geocoding.py`, and the interface's French-specific
  formats.
