# 5. API and dispatcher workspace

## One API for the product

Use `/api/v1`. The application models are the contract: `scripts/export_contracts.py` generates [backend/openapi.json](../backend/openapi.json) (also served at `/api/openapi.json` outside shared mode) and the [JSON schemas](../schemas/), plus the web form limits (`web/src/field-limits.json`), the CSV import columns (`web/src/csv-columns.json`) and server messages (`web/src/locales/server-messages.json`); its `--check` mode detects drift. `If-Match` and `Idempotency-Key` are marked required in the contract; a request without them is answered 428 with the missing header named. Do not maintain a second, handwritten specification; describe planned endpoints in the [product roadmap](07-deferred-scope.md) until they are implemented. Keep [examples](../examples/README.md) and compatibility tests alongside behavior changes; `backend/tests/test_examples.py` validates the examples with the application models.

In shared/company mode every endpoint requires a session except health, readiness and the first-use setup endpoints; the interactive API docs and `/api/openapi.json` are not served in shared mode (the contract is committed as `backend/openapi.json`). The current user has one organization and an `admin` or `planner` role. Both maintain operational fleet/orders and plan routes; only admins change depot settings and provision users. Organization ownership is resolved by the server. An ID belonging to another company returns 404, including job and export URLs.

Use same-origin browser sessions, an allowed Origin and CSRF tokens for writes. Login itself checks Origin. There is no public registration or developer API-key issuance in this release. Future API keys must use the same owned application functions, not bypass them.

| Area | Endpoints (summary; the generated contract is the complete list) |
|---|---|
| Health/setup | `GET /health`, `/ready` and `/configuration`; `GET/POST /setup` for first-use company onboarding |
| Session/accounts | `POST/GET/DELETE /session`; `PUT /preferences`; `GET/POST /users`, `PUT /users/{key}` |
| Examples | `POST /examples` (example deliveries for a day); `POST /examples/remove` |
| Data | `GET/PUT /depot` (the main depot); `GET/POST /depots`, `PUT/DELETE /depots/{key}`, `POST /depots/{key}/main`; vehicle/order list, create, read and update; `POST /orders/activation` and `/orders/delete`; `GET /orders/{key}/access-check` |
| Import | `POST /imports/{kind}` for `vehicles` or `orders`, with `dry_run` and `update_existing` |
| Compute | `POST /optimization-jobs`; `GET /jobs`; `GET /jobs/{key}`; `GET /system-status` |
| Review/edit | `GET /plans`; `GET /plans/{key}`; `POST /plans/{key}/edits` |
| Finalize/export | `POST /plans/{key}/finalize`; `GET /plans/{key}/export.csv`; `GET /plans/{key}/sheets` |

Finalization is a simple product state, not a compliance approval chain. The body is `{"accept_unassigned": false}` by default; require true to accept a partial plan. Warnings remain visible without a list of individual acknowledgment codes or declarations that the application cannot verify.

## Edit conflicts and retries

Return an integer `version` and quoted ETag for mutable records/plans. Writes that change an existing record, submit an edit or finalize require `If-Match`. Missing headers return 428; stale versions return 412. Every persisted plan state/result change increments its version. Finalizing an already READY plan with its current version is a no-op; retrying an old version gets 412, after which the client reads current state.

Optimization/edit submissions require `Idempotency-Key`. Scope it to organization, user and operation, including plan ID for edits. Store normalized request JSON and, for edits, the original expected plan version. Compare values directly. Same content returns the existing job; conflicting content returns 409. For an identical edit replay, return the original job before rechecking the original stale If-Match value. This handles a lost response without producing a second edit.

Edit submission reserves the same plan in one transaction: increment version, clear READY/finalized_at, set PENDING and create the job. A second in-flight edit returns 409 `PLAN_BUSY`. Only the result for the reserved version can be saved. The client refreshes the plan and its new ETag after completion. Failed/interrupted recalculation must also clear the active-edit state so the user can retry.

Compare selected source record versions before edits/finalization/export. A changed source returns `SOURCE_CHANGED` and requires a fresh solve against current values. Do not silently update the inputs of an old plan while retaining its old route calculation.

## Status and errors

Return 202 for jobs, 201 for creates, 200 for successful reads/updates and 204 for logout. Errors contain a stable code, a safe explanation, optional field/row details and a request ID. Public job errors do not include subprocess stderr, credentials or uploaded data.

Useful codes include `LOCATION_NOT_REVIEWED`, `SOURCE_CHANGED`, `PLAN_BUSY`, `UNSUPPORTED_MODEL`, `PROFILE_LIMIT`, `MATRIX_INVALID`, `ENGINE_TIMEOUT`, `WORKER_INTERRUPTED`, `VALIDATION_FAILED` and `UNASSIGNED_NOT_ACCEPTED`. A valid response with a connection not found is different from an engine outage. Evidence-based unassigned reasons are explained in [routing](04-routing-and-validation.md).

Lists use limit/offset with stable ID ordering, default 50 and maximum 250. Orders require a service date; plans can filter by it. Do not add a generic query language or endpoint framework.

## Workspace

**Setup and data.** Company/depot configuration, fleet and orders tables, CSV preview, unit labels and input errors. Show the address, point and review state together. Support manual pin correction and optional priority/route-limit fields. Do not hide unresolved points until the solve fails.

**First use.** Setup creates the company, its main depot (found by its address, entrance reviewed) and the first administrator. A company workspace then opens a short setup guide for that administrator: vehicles from typical presets (van 3.5 t, rigid trucks 7.5 t and 19 t, articulated truck 44 t) with editable height, weight, payload and pallets, a fleet-number prefix numbered after existing vehicles, and Monday-to-Friday hours; the delivery-number format; and the first deliveries, imported or added by hand. Every step can be skipped and the guide closed; the Planning checklist then shows what is left. The deliveries step also offers example deliveries for the planning day: fictional records marked Example, planned only with other examples, shown with a notice while they exist, and removed in Settings › Example data. The presets are starting values to check against each registration document, not vehicle data.

**Planning.** Choose date, vehicles, orders, road preference, objective and budget. Explain objective scope next to the control. Disable duplicate submissions while retaining deliberate reruns. Show actual named job stages instead of an invented percentage.

**Review.** Show a route table and map with synchronized selection, numbered stops, vehicle/route visibility controls and a persistent unassigned panel. Show service start/departure, loads, available capacity, return time, distance, driving/service/waiting time and elapsed operating cost. Display errors in text, not color alone. “Move to vehicle” and up/down controls are sufficient before drag-and-drop.

**Editing.** Change the current draft in place. When recalculation is PENDING, clearly mark any existing route display as stale and disable finalization/export. A parseable invalid result remains a fixable draft. A routing failure states that the edit was not applied; do not show old geometry as a successful edit. A new optimize action warns that it does not preserve manual changes.

**Ready/export.** One “Mark ready” action checks the current result and enables export. Editing a ready plan clears that state. Show a warning that previously downloaded routes must be redistributed. Include the plan ID, integer version and export timestamp in the file, without creating an export activity ledger. Mark ready re-runs the independent validator. Only one Ready plan per day may dispatch a given vehicle or delivery; a second overlapping plan returns `READY_CONFLICT`, and the dispatcher can explicitly replace the earlier plan, which returns it to Draft. The file carries the full schedule (departure from the vehicle's start place and return to its end place, named by their codes in `start_place` and `end_place`, then arrival, service start and stop departure) in Europe/Paris time; a spreadsheet layout (byte-order mark, semicolons) serves French Excel.

**Route sheets.** A Ready plan also gives each vehicle with deliveries a driver's route sheet (`GET /plans/{key}/sheets`, optional `?vehicle=`), under the same checks as the CSV file. A sheet names the company, the day and plan version, where the vehicle leaves from and when, where it ends and when, its distance, driving time and load, then each delivery in driving order: delivery number, customer code, address, reviewed entrance point, service window, arrival, service start, departure, time on site, load, required equipment and instructions. The workspace prints one A4 page per vehicle and opens each stop and the end place in Google Maps or Waze at the reviewed entrance; those links stay clickable in a PDF saved from the print dialog. The sheet says that the navigation apps choose their own path, which may not suit the truck. A later driver application reads the same stop data.

## Map and cost failure states

The selected default is OpenFreeMap Liberty in MapLibre, configurable via `MAP_STYLE_URL`. Preserve the provider/data attribution. No API key is required for its public instance, and no uptime SLA is provided [S17–S18](08-source-register.md). Import coordinates and allow manual access-point correction; no external geocoder or autocomplete is enabled by default. Distinguish imported/service pins from road-snapped endpoints when needed. Route lines come only from actual engine geometry. When tiles or WebGL fail, keep route/order tables usable; do not fabricate a replacement road line. See [S5–S8](08-source-register.md).

Basemap requests go to the configured provider; do not include customer names, addresses, order identifiers or route GeoJSON in them. Keep route overlays local to the application/browser. External tile requests still reveal the viewed area and network metadata; do not describe the default as fully offline or as having no external data flow. Use an operator-controlled source when that is required [S19](08-source-register.md).

The cost panel says **“Modeled operating cost — tolls excluded.”** Unknown toll/all-in totals are “Not calculated,” never zero. The cost objective and this elapsed-time report are explicitly different measures. Executive dashboards and live vehicle tracking are not part of the initial workspace.

## Screens in the current build

Tournevia uses separate Planning, Deliveries, Customers, Fleet, Saved plans and
administrator Settings areas. The planning date scopes deliveries and saved plans;
fleet records are shared. Source searches and filters apply to the displayed page.
Planning selections survive navigation and source pagination, and reset on a date
change.

The route list and actual road map share the desktop workspace. Narrow screens
provide List/Map views. Drag stops or use keyboard-accessible vehicle, position
and up/down controls; recalculate before marking Ready or exporting. Previous
geometry and metrics remain explicitly stale during unsaved or failed edits.

Editors display metres, cubic metres, kilometres, minutes and euros while sending
the original integer API units. Paris clock-change times are validated, with an
explicit offset choice for repeated hours. All five capacity dimensions, exact
truck properties, priorities, equipment and optional route limits remain present.

Use **Saved plans** for the selected service date to reopen older work, with
**Older plans / Newer plans** pagination and READY/DRAFT, validation and version
columns. This list is independent of the latest 50 calculation jobs. Route review
shows arrival, service start and departure, plus each vehicle's fixed, distance
and elapsed-time cost components; unsaved edits label costs as previous results.
CSV preview rows expand through **Review values** so quantities, units, windows,
equipment and coordinates can be checked before committing. Administrators can
edit an existing user's display name in **Settings → Users**. Account changes revoke
that user's sessions; renaming yourself returns you to sign-in.

The Tournevia vector mark has a transparent gap between the route and location pin.
The interface uses the single-blue version; a gradient variant is included under
`web/public/assets` for larger branding.
