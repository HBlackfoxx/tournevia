# 7. Product roadmap

This is the intended development order, not a list of available features or a
release-date commitment. Community remains the usable core; any later paid service
is described in [section 9](09-open-source-and-paid-plans.md). The roadmap adds no
empty services or feature flags: each addition updates the UI, API, solver adapter,
independent validator, documentation and tests together.

## Current baseline and evidence

Tournevia serves one company per installation and plans one day at a time,
delivery-only. A company keeps several depots and other places (such as a driver's
home); each vehicle starts and ends its day at its own depot or place, and a
delivery can name the depot it ships from. Locations use reviewed coordinates or
manual map pins; optional address lookup proposes a point that is still reviewed.
Five load dimensions are modeled. Optional route limits can be omitted, but there is
no switch to disable a load dimension and no parcel-count capacity. Zero means zero
quantity.

The toll and motorway preferences are implemented in Valhalla costing and remain
available. They influence route choice; they do not guarantee exclusion or price
tolls. Strict exclusions are a separate future capability below.

Recorded real-road quality and queued-worker checks cover the small Paris case,
dense Paris with 250 deliveries, and a wider fictional Île-de-France workload
with 250 deliveries. The regional fleet alternative achieves full coverage; the
original fleet remains a negative access/equipment case. These are different
workloads within one regional graph, not multi-region or long-haul acceptance.
Warm-service timings do not establish cold-start performance, simultaneous-company
capacity or suitability for arbitrary fleets. See the
[benchmark evidence](06-testing-and-operations.md#destination-access-matrix-recovery).

## Shipped since the first scope

These items were roadmap entries and are now in the product:

- **Different start and end places.** Each vehicle starts and ends at a reviewed
  depot or place (the main depot and its start by default). The final leg counts in
  time, distance, cost and validation; maps, edits, imports, exports and route sheets
  show both places.
- **Several depots.** Vehicles leaving from different depots are planned together,
  and a delivery that names the depot it ships from is carried only by a vehicle
  starting there. Reloading at a depot during the day is not modeled.
- **Address search.** Optional and off by default: the installation's own Base
  Adresse Nationale geocoder, or IGN's public service when a company's administrator
  turns it on. Forms suggest addresses; a delivery import proposes points for rows
  without coordinates and warns about vague, uncertain or distant matches. A match is
  never an approved vehicle entrance. Other geocoders and countries are not supported.
- **English and French.** Interface text, server messages, numbers and dates follow
  the user's language; imports and exports stay machine-readable. Date fields still
  follow the browser's locale, and a third language needs code changes.
- **Printable route sheets** for each vehicle of a Ready plan, saved customers with
  a reviewed entrance, and company delivery numbering.

## Before the first supported release

Local source packaging and installation tooling exist; the
[release guide](../deploy/RELEASE.md) describes them. Preparing an archive does not
establish production acceptance.

- Verify the final committed candidate from an extracted source kit: build,
  first-use setup, sign-in, source entry/import, calculation, manual edits,
  Ready/export, upgrade and isolated backup/restore. Record the exact image and
  results; earlier candidates' checks are useful evidence, not a substitute.
- Complete dispatcher acceptance on desktop and narrow screens, including invalid
  fields, CSV conflicts, expired sessions, service failures, stale results,
  concurrent edits, keyboard use and preservation of unsaved work.
- Add a concise dispatcher guide covering first use, location review, constraints,
  the Draft/Ready/export workflow and recovery from common errors, kept beside the
  installation and API documentation, with contextual UI links.
- Switch on GitHub private vulnerability reporting, which [SECURITY.md](../SECURITY.md)
  relies on. The notice and source review for the published application image is
  recorded in [NOTICE.md](../NOTICE.md#source-of-the-published-image); repeat it when
  the base image or a bundled library changes.
- Before recommending operational use, review an operator's real constraints and
  service area, then compare plans during a supervised pilot. Unsupported required
  constraints must be implemented or that workload excluded from the support claim.

The enhancements below are not all prerequisites for a clearly scoped release.
They are prerequisites when the intended workflow depends on them.

## Next: simpler daily planning

### Simple van workflow and explicit optional constraints

The setup guide already offers vehicle presets. Still to come: explicit choices for
which load dimensions and business constraints are modeled, without reinterpreting
zero or silently discarding saved constraints; parcel counts with clearly defined
vehicle capacity and delivery demand; and a day-wide service window when appointments
are not needed, while vehicle availability and return limits stay explicit.

The planner should explain which deliveries fit the selected modeled limits and
why others remain unassigned. How delivery priority interacts with serving as many
parcels or stops as possible needs defining before a new objective. Parcel counts
are not physical packing, and a disabled weight constraint must not produce a
claim that payload was checked. Required road properties remain separate from
optional load modeling. Tests cover migration of existing records, every
enabled/disabled combination, overloads, incomplete input, partial results and
UI/API agreement.

## Then: broader routing workflows and coverage

| Capability | Required behavior and acceptance |
|---|---|
| Open routes ending at the final delivery | Explicit choice distinct from an unknown end place. Omit the return leg consistently in optimization, validation and reporting. |
| More regions and longer journeys | Add fixed urban, rural and intercity fixtures on suitable graphs, including boundary crossings. Measure actual leg/route distances, both objectives, full/partial coverage, cold/warm timings, memory and queued-worker behavior. Validate smaller-host limits separately; do not infer them from the current router's eight-CPU/eight-GiB measurements. |
| Strict toll/motorway exclusions | Separate hard exclusions from existing preferences. Verify engine semantics and final road paths; report infeasibility when no permitted path exists. Never silently fall back to an excluded road. |
| Toll prices and broader cost estimates | Use maintained permitted sources with vehicle/date coverage and explicit unknown values. Keep modeled operating cost separate until toll totals are actually supported. |
| Multiple service windows and scheduled breaks | Model their timing and validate it independently; do not claim driver-hours compliance from a simple break setting. |
| Pickup/delivery pairs, split deliveries and depot reloads | Define precedence, quantities, load changes and trip boundaries before adding UI controls. Test each as a distinct model extension. |
| Route locks and replanning | Preserve explicitly fixed assignments/stops and define what can change. Keep executed work and previously exported plans distinguishable from a new result. |
| Easier imports and driver handoff | Add reviewed CSV column mapping, reusable mappings and a mobile route view; retain conflict checks and dispatch eligibility. Driver tracking or proof of delivery would require a separate workflow. |

These are legitimate core capabilities when needed, not a reason to reserve route
correctness for paying customers. Driver-hours histories, exact packing/axle
distribution, dangerous-goods rules, refrigerated compartments and EV charging
need specialized data and validation rather than a generic equipment tag.

## Platform work that waits

| Later item | Add only when |
|---|---|
| Multiple workers and a queue service | Measured queue pressure exceeds the single worker |
| Shared hosted tenancy | Isolation, lifecycle, recovery and resource-fairness tests cover multiple customers |
| Odoo/ERP connector or webhooks | A named integration needs a defined mapping and delivery/retry behavior |
| API keys and developer account section | A hosted developer API is being launched |
| Usage metering, billing, quotas | A paid hosted offer has a real charging policy |
| Enterprise identity or granular roles | A customer needs them beyond admin/planner |
| Advanced spatial database features | Actual geospatial queries justify PostGIS |
| Object storage | Working storage sizes or external file workflows require it |
| National graph automation or own tile serving | Coverage, installation, licensing or cost warrants it |
| Live traffic, current restrictions and toll pricing | Maintained suitable sources and verified model behavior exist |
| Second solver | A validated business constraint or measured quality gap cannot be solved with the selected engine |
| High availability or orchestration cluster | A real availability target and operating team justify it |

No plan-certification subsystem is part of this direction. Basic IDs, integer
edit-conflict protection, safe logs, authentication, backups and independent route
checks are ordinary product correctness.
