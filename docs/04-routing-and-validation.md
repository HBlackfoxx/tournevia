# 4. Robust routing, optimization and maps

## The model is the product

Use one independently testable model and one solver adapter. VROOM documents multidimensional loads, skills, service windows, priorities, vehicle costs/limits and per-profile custom matrices. Valhalla documents truck costing and a time/distance matrix API. Those features support this design; documentation is not proof that the selected builds and graph meet our cases. [S1–S3](08-source-register.md)

The first engine task must exercise real Valhalla plus VROOM before a polished map or CRUD interface is treated as progress on optimization quality.

## Selected routing coverage

The first graph is built locally from Geofabrik's Île-de-France OSM PBF [S16](08-source-register.md), using the synthetic Paris baseline. The ten-delivery fixture stays inside the extract and separate controlled tagged roads test restrictions. See [engine setup, acceptance commands and limits](../deploy/README.md). Generated graphs stay outside versioned source.

Record the actual installed dataset label/date as ordinary configuration. Use the graph's actual coverage in readiness/input checks; do not assume every road shown by the global basemap is routable. Near extract edges, extend the graph if a valid road connection may leave the region rather than treating clipped roads as a genuine vehicle restriction. Mainland France is an expansion target, not an initial support claim. No live traffic, ferry/island, cross-border or overseas support is asserted.

## Objectives: name what is actually optimized

| Public option | Adapter behavior | User-facing limitation |
|---|---|---|
| `TRAVEL_TIME` | Native solver defaults for vehicle costs; travel-duration objective | Not shortest distance or lowest monetary total |
| `MODELED_TRAVEL_COST` | Native fixed cost, per-km rate and per-hour driving rate from the selected vehicles | Excludes service/waiting-time cost, tolls, taxes and any unmodeled expense |

Use VROOM's native task-priority/assignment behavior in both modes. Map order priority 0–100; do not advertise a strict “must serve” constraint or invent an exact multiobjective guarantee. Inspect unassigned work even for high-priority orders. Never relax a hard constraint to improve coverage.

For the travel-cost mode, supply both duration and distance matrices. Do not mix a custom cost matrix with incompatible native rate settings. Native solver scoring/rounding is not an invoice calculation. The separately reported operating estimate includes elapsed time and can rank plans differently. Test two-vehicle cases where the fixed, distance and driving-rate choices matter. [S1](08-source-register.md)

A returned route set is a heuristic candidate within a budget, not a proof of optimality. Compare coverage, priorities served, vehicle count, distance, driving/elapsed time and cost against a baseline; feasibility alone is not sufficient product quality.

## Pipeline

Read owned inputs → validate → group truck profiles → get directed matrices → solve → route selected legs → validate independently → calculate display metrics → save current draft.

Keep these stages inside the worker, as normal module calls. A manual edit follows the routing/validation stages without running optimization and without moving other stops automatically.

## Input and profile checks

Require active records, reviewed locations, the same local service date, explicit units and consistent windows. Keep every requested order in final coverage. A provable equipment/capacity mismatch may be preclassified as unassigned; use a factual code such as `NO_COMPATIBLE_VEHICLE`, not a guessed solver diagnosis.

Group vehicles only when all routing-affecting values match exactly after unit normalization: dimensions, gross routing weight, axle load/count and road policy. Capacity and cost differences alone do not change the road profile. Use request-local profile labels such as `p1`; do not add profile fingerprints or a shared cache framework.

Use Valhalla `truck` costing and explicitly convert millimeters to meters and kilograms to metric tonnes. Keep restriction/access/one-way ignoring disabled and do not lower truck-access penalties to force a route. Engine documentation describes these controls; acceptance must test them against known tagged restrictions in a controlled graph. An untagged real restriction cannot be discovered by a schema validator. [S2](08-source-register.md)

| Routing policy | Intended adapter preset |
|---|---|
| `TRUCK_STANDARD` | `use_tolls=0.5`, `use_highways=0.5` |
| `TRUCK_PREFER_NO_TOLLS` | `use_tolls=0`, `use_highways=0.5` |
| `TRUCK_PREFER_NO_MOTORWAYS` | `use_highways=0`, `use_tolls=0.5` |

These are preferences, not strict exclusions. The UI says “prefer avoiding,” not “guaranteed toll-free.” Verify actual supported parameters and warnings on the pinned build. Do not introduce a hard-avoid switch unless its road-level behavior is implemented and tested.

## Directed matrices and missing connections

Before requesting a profile's matrix, the adapter asks the engine's location
search, with that profile's truck costing and the same snap limit, which pins
have no usable road nearby. The engine otherwise rejects the whole matrix for a
single such pin. A depot pin without a usable road stops the calculation and
names the affected vehicles. A delivery pin without one is removed from that
profile only (its rows and columns stay forbidden, as for eligibility). When no
compatible profile can reach it, the delivery remains explicitly unassigned with
`NO_ROAD_NEAR_PIN`; correcting the pin is the fix. A manual sequence that assigns
such a pin to a vehicle is rejected with the vehicle and delivery named. Engine
HTTP failures and unready graphs still fail the calculation.

Use the route start and end places (the main depot first, then the other depots and places the selected vehicles use) plus distinct exact delivery coordinates as an ordered location list. Distinct orders at the same coordinate still remain separate service jobs. Preserve directed `i → j` versus `j → i` values. Validate response shape, index coverage and units; conservatively round fractional travel seconds up. Do not treat an omitted matrix cell, an HTTP error or an unready graph as a valid “unreachable” answer. Valhalla documents null values for connections not found and output distance units. [S3](08-source-register.md)

**Do not fail an entire planning day simply because one truck cannot use one connection.** Maintain a separate reachability mask for every profile and let suitable vehicles serve reachable work. A stop with no route found by the available compatible profiles remains explicitly unassigned; “no route found in this dataset” is not a proof that a physical route cannot exist.

The implementation requests a profile's road matrix only for points that at least
one selected vehicle of that profile can serve, considering required equipment and
each delivery's five capacity demands together on the same vehicle. The depots and
places where vehicles of that profile start or end are always included. An eligible delivery keeps a shared coordinate in that matrix.
All deliveries remain in the solver input; excluded profile rows/columns are
forbidden eligibility combinations, not assertions about roads that were never
queried. All potentially usable directed cells retain the engine's values and null
mask. Final routing and the independent capacity/equipment checks still apply.

Matrix tiles remain at most 48 by 48. For more than two tiles per axis, points
are distributed evenly across the same number of tiles, within each window group
when used. This reduces the largest requests without adding requests or changing
the set of eligible points. `MATRIX_CONCURRENCY` permits one to four
in-flight requests, defaulting to one. Each bounded group completes and is parsed
before the next starts, avoiding overlapping work in the pinned engine's internal
queues. Small depot-only rectangles run last. Responses are assembled by their original
indexes, with one shared calculation deadline. Failed HTTP calls, malformed tiles,
invalid self-arcs and expired deadlines reject the entire calculation; queued work
is cancelled and active calls are joined before returning a failure.

Optimization can omit order-to-order rectangles whose earliest possible departure
(window start plus service) is strictly later than every target's latest service
start. Nonnegative travel makes those connections infeasible even with zero road
time. Shared pins use the union of all eligible orders' bounds; equality and all
depot connections remain available to the engine. These are schedule exclusions,
not unavailable-road claims. Grouping never increases the normal tile count, and
spatial grouping is retained if no cells can be omitted. Matrix indexes are restored
before solving. Manual rerouting requests only its fixed sequence's actual road
legs, without an all-pairs matrix or solver. Road errors and warnings reject the
recalculation; independent schedule, geometry, load, equipment and cost checks
still apply. Invalid sequences produce fixable drafts with explicit violations.

### Bounded forbidden-arc adapter

VROOM's public matrix contract uses unsigned integer durations, so passing JSON null or infinity is not the contract. The adapter below is this project's own reasoning, not a VROOM feature claim. It is implemented in `prepare` in [optimization.py](../backend/app/optimization.py); the routing-core and real-engine tests check it, and final routes are validated independently. [S1](08-source-register.md)

Let all vehicle windows be hard and finite. In seconds on a common UTC timeline, define:

```text
A = earliest selected vehicle availability start
B = latest selected vehicle availability end
H = B - A
forbidden_duration = H + 1
```

For a valid engine response indicating a connection was not found, mark that arc unreachable in the profile mask. In the solver-only matrix, encode its duration as `H + 1`. The corresponding distance can be zero as an unused placeholder; neither value is a road estimate and neither is copied into route metrics. Keep `speed_factor = 1`; do not soften the vehicle windows.

Why this excludes the arc in this model: any route starts at or after A, ends at or before B, and has nonnegative driving, waiting and service time. A single leg longer than H cannot fit. The exclusion therefore comes from a hard finite horizon, not an arbitrary monetary penalty that the optimizer might accept.

Check numeric bounds against the selected VROOM build, including intermediate route sums. Fail the adapter rather than encoding values outside supported ranges. Reject unsupported unbounded windows, speed scaling or constraint-softening modes. Independently reject every returned route containing an arc marked unreachable even when engine output otherwise appears valid.

Mandatory engine cases: a connection blocked only for the tall vehicle; a stop unreachable for every vehicle; asymmetric reachability; every stop unassigned; a zero-monetary-rate vehicle; and a deliberately corrupted solver output using a forbidden arc. Missing graph/HTTP failures must still fail the job, not unassign all work as though the roads had been checked.

Until those real-engine tests pass, this feature is not pilot-ready. A temporary developer-only implementation may fail clearly on missing connections, but must not be shipped as robust mixed-profile routing. Changing the model assumptions requires revalidating this bound.

## Solver mapping and execution

Use request-local integer maps for order/vehicle/skill IDs. Map the five demand components to delivery vectors and vehicle capacities in the fixed order. Map skills and service-start windows; map vehicle availability, optional maximum tasks/distance/driving time and priority. Use timestamps converted to integer seconds relative to the local day's UTC start. Omitted limit fields stay omitted; defaults are explicit.

Use supplied custom matrices for every profile. Invoke the selected executable with verified supported CLI options, without requesting solver-provided geometry. Bound execution, output bytes and memory; require a complete successful parse and known IDs. Do not invent deterministic-seed, partial-result or cancellation flags. [S4](08-source-register.md)

The solver's normal optimizing mode and its plan-check mode have different semantics; do not use a mode that softens constraints as though it establishes a valid dispatch plan. Manual editing in this release does not need that mode.

## Actual route paths and timing

Route each selected leg with the same truck inputs, graph, endpoints and static costing policy used for its matrix. Stop new jobs and drain active work before switching the graph. An edit may route against a newer graph only by recalculating its entire selected route set and replacing the small routing context together; do not mix datasets inside one result.

Store the actual road geometry and normalized leg distance/duration. Recompute schedule and limits from these leg values, not merely solver arrivals. The application works backward from hard service-start deadlines and vehicle return time using final leg durations and service times. It preserves the proposed departure when feasible, otherwise moves it earlier only as required, never before vehicle availability. Optimized routes propose the solver's departure; manual sequences propose arriving as the first stop's window opens, so an edit does not add avoidable waiting before the first stop or consume the window's slack. Final legs are requested with the same bounded concurrency as matrix tiles. Requests reuse one kept-alive HTTP connection per routing thread: a 250-delivery calculation with self-arc recovery can send several thousand route requests, and opening a TCP connection for each exhausted local ports in testing. A reused connection that the service closed while idle is resent once on a new connection; any other transport failure still rejects the calculation. Waiting and all displayed totals are then recomputed forward. This keeps the same assignment and stop sequence; it does not rerun optimization. A matrix-feasible candidate can become invalid when final paths differ; show the blocking issue and do not export it. Do not repeatedly retry the solver in a hidden repair loop.

**Safety margin.** Leaving as late as the windows allow puts the delivery whose window closes first at its last minute, so any delay makes it late. A company sets a safety margin in minutes (`PUT /api/v1/workspace/safety-margin`, 0–120, 15 by default); each calculation copies it into its input (`safety_margin_seconds`; plans calculated before it existed read 0). The solver receives every service window as `[start, max(start, end − margin)]`, and the backward departure computation and the manual-sequence departure use the same reduced deadline. When the final legs allow no departure that keeps a stop's margin, the earliest departure within vehicle availability is used. The independent validator still checks the real windows, so a plan that cannot keep the margin is valid with a smaller margin, and the review shows each stop's margin to its real window end. A delivery that can only be reached within the last minutes of its window may stay unassigned; the review says so when a margin was applied.

First-release travel uses one documented static speed policy for matrices and final legs, without predicted/current traffic feeds. Validate that policy in the actual deployment configuration. No static matrix promises accurate congestion or future conditional restrictions. Time-dependent routing is a separate model extension, not a date parameter pasted into one request.

## Independent feasibility check

Use a plain Python validator separate from adapter construction. Given the saved inputs and actual routed legs, check ownership, IDs, coverage, every constraint and displayed totals.

For each route, initial load is the componentwise sum of delivery demands. It must fit every capacity dimension. Subtract each served order at its stop and reject negative loads or incorrect reported loads. Every requested order is served once or explicitly unassigned; each selected vehicle appears on at most one route.

For each stop:

```text
arrival = previous_departure + actual_leg_duration
service_start = max(arrival, order_window_start)
departure = service_start + service_duration
```

For the first implementation, use the candidate's depot departure and earliest feasible service starts thereafter. Check every inclusive window end, vehicle departure/return bounds, required skill, maximum stop count, total driving limit and distance limit. Waiting before the first depot departure is not route elapsed time. Keep each route's geometry, timings and metrics from the same computation.

Check the reachability mask and exact vehicle/profile match independently; verify that actual leg endpoints correspond to the intended stops. Recompute cost components and totals. Tests must inject errors into solver results to show that validation catches them rather than merely echoing an empty engine violations array.

Passing means consistency with the supported model and supplied road data, not legal certification or complete real-world truck safety.

## Map/access-point requirements

The routing graph, the basemap renderer/tiles and address lookup are separate capabilities. A visually plausible line is not proof of road access.

Show the service pin, road geometry and material snap displacement. Use configurable, pilot-tested snap limits; do not increase the search radius silently to make a failed stop appear routable. Review wrong-side-of-road, parallel-road, fenced-site and gated-entry cases even when the straight-line snap distance is small. Correct the delivery access point and recalculate instead of drawing an invented connection through a barrier.

The first workflow accepts company-supplied coordinates and manual pin correction. Add one permitted geocoding provider only when its accuracy, terms and confidential-address handling are understood. Do not make public demonstration tiles or public address-lookup endpoints a hidden production dependency. MapLibre renders the map; it does not supply the routing graph or a guaranteed hosting service. [S5–S8](08-source-register.md)

If tiles fail, routes and order tables remain usable. If routing fails, do not replace the road path with a straight line. Keep selections synchronized between table and map, show numbered stops, and distinguish served/unassigned/invalid states with text as well as color.
