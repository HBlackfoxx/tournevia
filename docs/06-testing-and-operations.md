# 6. Tests, solution quality and simple operations

## Test the planning behavior, not paperwork

| Test group | Required cases |
|---|---|
| Model | Each capacity dimension, zero service time, skills, priority bounds, stop/distance/driving limits |
| Time | Inclusive service-start boundaries, waiting, return deadline, inconsistent offsets, 23/25-hour local days |
| Data/import | UTF-8/quoted CSV, exact headers, duplicate identifiers, changed replay conflict, missing points, atomic failure |
| Routing | Correct units, asymmetric times, tall/short truck access, wrong snap, one-way direction, blocked connections |
| Missing connections | Bounded forbidden-arc proof assumptions, mixed profiles, no reachable work, engine outage not treated as unassignment |
| Solver | Both objectives, different vehicle rates, budget timeout, malformed output, unknown/omitted/duplicated IDs |
| Validator | Corrupted coverage, overload, wrong profile, forbidden leg, late start/return, metric disagreement |
| Plans | In-place edit, stale-write rejection, idempotent replay, PENDING gate, failed edit recovery, READY cleared by edits |
| Company boundary | Wrong organization on lists/details/jobs/exports/imports and cross-company referenced IDs |
| Access/recovery | Named users, role/CSRF/session behavior, worker interruption, database outage, isolated restore |
| End to end | Import → correct points → optimize → edit → mark ready → export |

These are application acceptance requirements. The supplied documentation validator does not execute them.

## Quality benchmark, before claiming a useful optimizer

A solver can return a feasible but operationally poor plan. Measure quality independently of whether the JSON parses.

Build tiny synthetic cases with an independently enumerable reference optimum for the supported model. Keep one known feasible assignment baseline and a small anonymized/operator-approved set of real planning days. Compare only equivalent constraints and coverage; a plan that skips difficult deliveries is not automatically an improvement.

Record vehicles/orders/profile counts, hardware, engine builds, search settings, matrix time, solve time, final routing/validation time, total time and peak memory. Report served/unassigned work, priority coverage, vehicles used, distance, driving time and modeled elapsed cost. Repeat heuristic runs enough to observe variation and establish a measured regression tolerance; do not require identical route order.

First pass: 2 vehicles/10 orders. Then exercise 25/250/3 as a target, tightening supported limits if needed. With 250 distinct delivery points plus one depot, a full directed matrix has 251² = 63,001 cells per profile; three profiles have 189,003 cells before any chunking. This arithmetic is not a throughput measurement. Check the routing engine's configured matrix limits and bound batching within the whole-job deadline.

Do not invent a savings percentage, convergence guarantee, benchmark result or maximum fleet size. A company-use claim needs measured results on the supported workload and a dispatcher review.

### Implemented synthetic quality checks

The offline [tiny reference](../backend/benchmarks/reference.py) exhaustively
enumerates every order permutation and partition among labeled vehicles, including
unused vehicles, for at most six orders and three vehicles. It independently checks
all five capacities, required skills, hard service-start/return windows, route limits
and directed unreachable arcs. It does not use the optimizer payload, solver totals
or production validator to establish its reference score.

Exact comparisons require full coverage, distinct service coordinates, zero task
priorities and integral per-second/per-meter monetary rates. Unsupported reference
cases are rejected. Earliest departure suffices for static-travel feasibility;
service/waiting affect feasibility but are excluded from both optimization scores.
This bounded reference does not establish native rounding behavior for fractional
rates or any optimality guarantee for partial coverage, priorities or larger cases.

Three numeric fixtures cover an asymmetric directed tour, mixed profiles with a
forbidden arc and capacity/skill/window/route limits, and a driving-versus-cost
vehicle choice. Both objectives matched their independently enumerated optimum in
three native VROOM runs per fixture. A hand-calculated directed case checks the
reference itself: 40 driving seconds, or 447 minor units with the fixture's integral
rates. Mutation tests check each capacity dimension and other modeled hard limits.
These matrices are artificial test costs, not invented road data.

The [Paris benchmark](../backend/benchmarks/run.py) separately uses the real regional
graph and the existing ten-delivery fixture. Its fixed baseline assigns deliveries
001–005 to the truck and 006–010 to the van in identifier order, departing at vehicle
availability start. The baseline is rerouted and independently validated before
comparison. It is a deliberately simple manual assignment, not a customer plan.

Every measured result must pass independent validation and serve all ten deliveries.
Comparisons require identical coverage and priority counts. The current regression
ceiling allows **no worsening against that feasible baseline**: driving seconds for
`TRAVEL_TIME`, and unrounded fixed + distance + driving-time cost for
`MODELED_TRAVEL_COST`. The latter uses final road legs, not native rounded solver
scores or displayed elapsed-time cost. A failed quality gate returns exit code 2
with its report; invalid/incomplete calculations fail instead of producing a savings
claim. This is a baseline regression check, not a Paris optimality proof.

Measured 23 September 2026, three runs per row, retaining every run:

| Full-coverage result | Distance | Driving | Driving-based cost, EUR | Displayed elapsed cost, EUR | Calculation wall time |
|---|---:|---:|---:|---:|---:|
| Fixed manual baseline | 38,977 m | 4,988 s | 189.2240 | 236.98 | 0.341–0.346 s |
| Travel-time objective | 28,490 m | 3,561 s | 173.5010 | 199.42 | 0.360–0.364 s |
| Modeled-travel-cost objective | 28,310 m | 3,578 s | 172.2868 | 198.20 | 0.352–0.369 s |

All rows used two vehicles, served ten deliveries and left none unassigned, with
validation PASS. Metrics were identical across these three runs per mode; that small
sample does not establish deterministic heuristic output. Relative to this specific
baseline, driving fell 28.61% in time mode and driving-based cost fell 8.95% in cost
mode. Tolls and all-in costs remain unknown. These are synthetic fixture comparisons,
not customer savings or measured 25/250/3 capacity.

The final run used an AMD Ryzen 9 7945HX, x86_64 Docker Desktop/WSL2 Linux
6.6.114.1, a two-CPU quota and 2 GiB memory limit for the benchmark container, VROOM
1.14.0 with one solver thread and a five-second maximum solver budget, and Valhalla
3.9.0-0baa5c687 with the Île-de-France graph downloaded 22 September 2026. The graph
service was already warm and ran outside that container's resource limits. Each
measurement used a fresh Python process; timings cover calculation, excluding
interpreter startup, HTTP queueing and database work. Solves ended before their
maximum budget. No cold-start or whole-installation throughput claim is made.

For optimized runs, matrix/preparation took 0.219–0.230 s, native solve/mapping
0.0289–0.0292 s, final routing 0.103–0.108 s and independent validation
0.0018–0.0021 s. Python peak RSS was 67,096–67,568 KiB and the largest exited child's
peak was 53,376–53,992 KiB (including fork/setup). These separate high-water marks
must not be added; neither measures external Valhalla or graph-cache memory. An
earlier exploratory run retained a slower 1.758 s first baseline calculation; the
committed tooling does not discard warm-up samples or assert timing thresholds.

Reproduce using the commands in [deployment documentation](../deploy/README.md#quality-benchmarks).
Reports contain metrics, stage times, resource scope and engine versions, with no
saved matrices or route geometry. Keep generated reports in ignored runtime storage.
The adapter now assembles sequential rectangular tiles of at most 40 by 40 cells
under the original calculation deadline, preserving both directions and explicit
paired nulls. It rejects incomplete tiles and unreachable self-arcs; it never fills
a failed tile with unreachable connections. Real-engine tests compare
batched results with single requests across three profiles, controlled one-way and
restricted roads, and the default 40/41-point boundary. Adapter storage is bounded
to 251 points; the application uses that bound after the workflow checks below.

The separate [scaling matrix probe](../backend/benchmarks/scaling.py) generates
25 synthetic vehicles, 250 distinct deliveries plus a depot, and three exact truck
profiles. Pins were evenly sampled by vertex index from the original seed truck route
and are now a [fixed service-point fixture](../backend/benchmarks/paris-250-points.json),
so changing routing behavior cannot silently change the measured workload;
this dense single-tour sample is not a representative regional delivery workload.
It does not write source records or relax the application limit. The first run on
the pinned regional graph failed before completing a profile: three sampled pins
returned self-loops of 59/76/92 seconds and 411/551/685 metres. The first pin
(48.843178, 2.383811 latitude/longitude) also returns a loop in a one-point request,
so the failure is independent of batching. The pinned
[CostMatrix connection check](https://github.com/valhalla/valhalla/blob/0baa5c687/src/thor/costmatrix.cc)
returns early on edges marked as part of a complex turn restriction, before testing
trivial connections. Local locate responses mark the affected edges accordingly.
The route endpoint returns zero for each self-arc and direct 9/17-second journeys
between nearby affected pins, where the matrix instead reports 67–84-second loops.

For a finite nonzero self-arc specifically reported by `costmatrix`, the adapter
requires a real route with zero duration, zero distance and stationary geometry
within the existing snap limit. It then replaces **every finite incoming and outgoing
connection** for that point with metrics from actual route requests. Original null
connections remain forbidden. A failed/no-path request, invalid geometry, unconfirmed
self-arc or deadline expiry rejects the entire calculation. It does not merely
zero the diagonal, alter pins, disable turn/truck restrictions or archive matrices.
Logs report only the affected-point count. This correction addresses the detected
complex-restriction anomaly, not general matrix/route equivalence or optimality.
For `n` points and `k` affected points it needs at most `2nk − k²` road requests
per profile, including self confirmations. The original deadline still applies;
more affected points or slower road queries can exhaust it. No automatic retry
or timeout increase is applied to make a large workload appear successful.

The unchanged 251-point probe completed all three profiles in 72.871 seconds on
the existing regional graph with a two-CPU/2 GiB benchmark quota. This includes
seed generation and 1,497 route confirmations/recalculations per profile for three
affected points; the matrices share a 120-second deadline. This one probe is not
full solver, queued-worker, UI or regional-capacity acceptance.

Repeated full offline trials on 23 September 2026 used the same hardware/engine
versions above, a two-CPU/2 GiB benchmark container, one solver thread and a
five-second solver budget. The benchmark shared the routing container's network
namespace, avoiding host-port forwarding. Each trial rebuilt its matrices and
independently validated its final road paths. All six trials served 250 deliveries
with 25 vehicles, no unassigned work and no modeled hard violations:

| Candidate (two runs each) | Distance | Driving time | Driving-based cost, minor units | Calculation time |
|---|---:|---:|---:|---:|
| Fixed consecutive-sample baseline | 146,595 m | 17,214 s | 163,177.7209 | 57.36–57.44 s |
| Travel-time objective | 148,708 m | 17,289 s | 163,583.5846 | 60.01–60.30 s |
| Modeled-travel-cost objective | 146,621 m | 17,203 s | 162,611.6174 | 60.83–61.32 s |

**Quality acceptance failed** (runner exit 2): the travel-time objective was
75 seconds / 0.436% worse than the feasible baseline in both runs. The cost
objective improved its own metric by 0.347%. No tolerance was loosened, workload
filtered or inferior result reported as an accepted optimization. Displayed
elapsed costs were 200,722 / 169,893 / 168,918 minor units respectively; their
waiting/service components differ from the solver's driving-based objective.

Matrix/preparation took 54.64–56.54 seconds, native solve/mapping 4.53–4.70 seconds,
final routing 0.80–0.90 seconds and validation 0.015–0.019 seconds. Per-trial Python
peak RSS was 81,372–89,076 KiB; the largest exited solver-child peak was
72,048–72,056 KiB for optimized trials. These separate high-water marks are not
additive and exclude the external routing engine/graph cache, interpreter startup,
seed generation and database/queue work. Values are observations for this dense
synthetic sample, not fleet capacity or customer savings.

An earlier host-forwarded attempt failed on an engine-network error after two
baseline trials and one travel-time trial; it is not part of the successful runtime
measurements above. The runner now retains completed metrics when a later trial
fails.

The 24 September investigation reproduced the same inferior solution with a
15-second solver budget. Matrix estimates, rather than search time, explained the
regression: two selected connections scored 4 and 61 seconds but their final road
paths took 299 and 201 seconds. With a 50-metre candidate radius, the matrix could
select a different nearby street than the route endpoint. Both requests now use
`radius: 0` for nearest-candidate correlation, retaining the 50-metre search cutoff
and independent geometry-endpoint snap check. The
[Valhalla location documentation](https://valhalla.github.io/valhalla/api/route/api-reference/)
distinguishes candidate radius from search cutoff; radius zero does not require
pins to lie exactly on roads. Truck restrictions, directed reachability, deadline,
solver budget and the no-worsening gate remain unchanged. Regression tests compare
both directions of the problematic connections across all three exact profiles,
allowing only one second of engine rounding and two metres of distance rounding.

The corrected six-trial repeat on 24 September used the **same original 250 pins**,
vehicles, constraints, assignment baseline, engine versions, CPU/memory quota and
five-second solver budget. All runs served 250 deliveries with 25 vehicles, no
unassigned work and independent validation `PASS`. Both unchanged quality gates
passed (runner exit 0):

| Candidate (two runs each) | Distance | Driving time | Driving-based cost, minor units | Calculation time |
|---|---:|---:|---:|---:|
| Fixed consecutive-sample baseline | 146,628 m | 17,217 s | 163,182.5689 | 66.84–67.04 s |
| Travel-time objective | 144,893–145,143 m | 16,721–16,729 s | 162,329.5264–162,363.6018 | 70.51–70.92 s |
| Modeled-travel-cost objective | 141,241 m | 16,897 s | 162,069.0284 | 69.58–69.59 s |

The worst travel-time repeat improves by **488 seconds / 2.834%**; cost improves
by **0.682%** in both repeats. Baseline road metrics also change slightly because
the correction applies to every calculation. Matrix/preparation took 64.21–66.08
seconds, solve/mapping 4.30–4.54 seconds, final routing 0.93–1.02 seconds and
validation 0.015–0.021 seconds. Observed calculation times are higher than the
previous run but remain below the unchanged 120-second deadline; no speedup is
claimed. Python peak RSS was 81,384–88,972 KiB and solver-child peak was
71,820–72,096 KiB, with the same separate/non-additive memory scope described above.
Displayed elapsed costs were 200,723 for the baseline, 168,636–168,667 for travel
time and 168,375 for modeled travel cost; these are not the solver's cost metric.

This resolved the measured quality regression, not general matrix/route equivalence,
optimality or regional fleet capacity. Queued-worker acceptance subsequently exposed
the ordering problem below. Real-company acceptance remains separate; these
synthetic results cannot produce operational exports.

### 250-delivery workflow and matrix ordering

The first two public-API/worker trials with 250 saved deliveries hit the unchanged
120-second deadline and correctly saved no plan. Database UUID order scattered
points across matrix tiles. A one-profile diagnostic with the same service points
measured 22.10 seconds in original fixture order, 38.20 seconds in shuffled order,
and 23.80 seconds when grouped by longitude/latitude. Each used 49 tiles and 1,497
road confirmations/recalculations; the extra time was in the matrix requests.

Point preparation now groups distinct service coordinates by longitude/latitude,
keeping the depot at index zero. It neither filters coordinates nor changes job
IDs, manual visit sequences, truck constraints, deadlines or paired-null handling.
The benchmark uses the same preparation as queued jobs. The pinned matrix engine
is not strictly permutation invariant (54 duration cells changed from original
to spatial order in the diagnostic), so quality was remeasured rather than assumed.

The final six offline trials on 24 September used the same fixed inputs and
environment described above. All served 250 deliveries with 25 vehicles and
independent validation `PASS`; both unchanged quality gates passed:

| Candidate (two runs each) | Distance | Driving time | Driving-based cost, minor units | Calculation time |
|---|---:|---:|---:|---:|
| Fixed consecutive-sample baseline | 146,628 m | 17,217 s | 163,182.5689 | 71.55–76.06 s |
| Travel-time objective | 145,144 m | 16,727 s | 162,327.9633 | 77.27–79.34 s |
| Modeled-travel-cost objective | 141,241 m | 16,897 s | 162,069.0284 | 77.50–77.88 s |

Travel time improves by 490 seconds / **2.846%** and modeled travel cost by
**0.682%** against this baseline. Matrix/preparation took 70.47–75.04 seconds,
solve/mapping 4.58–4.65 seconds, final routing 1.00–1.07 seconds and validation
0.018–0.022 seconds. Python peak RSS was 81,292–89,044 KiB; solver-child peak was
71,804–72,068 KiB, with the same separate memory scope excluding the external
engine/cache. These measurements cover the dense synthetic workload, not an
arbitrary regional fleet or a whole-installation throughput guarantee.

The real queued-worker suite also creates fresh PostgreSQL source records with
ordinary generated IDs, submits both 250-delivery objectives through the public
API, restarts the API context and retrieves the saved drafts. Both jobs passed
in 79.821 / 76.806 seconds of worker elapsed time, with 25 routes, all 250 deliveries
served and independent validation `PASS`. Tests cover five 50-row pages plus the
empty final page, rejection of 251 deliveries or 26 vehicles, stale source versions,
idempotent retries before/after completion and transfer of the single input copy
from the job to its saved plan. All 15 worker/recovery checks pass.

Browser acceptance used an isolated local application and disposable database,
the original 250 pins and relative constraints, with all dates/windows shifted
equally to the current planning day. Selections accumulated across five pages,
retained checked rows on a return visit, and submitted 25 vehicles/250 deliveries/
three profiles. Reloading during computation preserved the running job. The saved
draft showed 250 served, zero unassigned and 25 route panels; all 250 numbered map
stops rendered, hiding one route hid its ten stops, and table selection highlighted
the corresponding map pin. A manual adjacent-stop reorder marked old results stale
and blocked duplicate recalculation. It completed on the same plan at version 3,
retaining the edited sequence, full coverage and `PASS` after another reload.
Browser-triggered optimization/recalculation took 75.173 / 75.568 worker seconds.
Synthetic finalization/export controls remained absent. The embedded browser's
native date picker crashed, so that interaction is not claimed as verified; the
service-date selection reset is covered by the workspace UI test.

These checks support raising the public ceiling to **251 points including the
depot**, with the existing 25-vehicle/250-delivery/three-profile bounds. (When several
depots and places arrived, the ceiling became 301 distinct points: 250 for deliveries
and the rest for depots and places, `MAX_MATRIX_POINTS` in `routing.py`.) All 134
host backend tests, 41 real-engine tests, 25 UI tests, nine smaller-workload quality
runs, frontend build and generated-contract checks pass. Existing upstream test
deprecations and the MapLibre bundle-size advisory remain. HTTPS and backup suites
were not rerun for this change. Representative regional planning days, operator
review and real-company acceptance remain required before operational claims.

### Regional synthetic workload

The [regional workload](../backend/benchmarks/regional.py) fixes 250 service pins
across a five-by-five grid, about 42.03 km corner to corner. The source points
span longitude 2.186454–2.615784 and latitude 48.744749–48.995934. The fixture is
synthetic engineering coverage, not a statistically representative company fleet
or a set of actual delivery premises. Pins were selected from local-road candidates
on the existing OSM graph before any solver measurements and are committed in
[the fixed point file](../backend/benchmarks/regional-250-points.json). No service
points were removed or resampled after the failed benchmark.

There are 25 vehicles and three exact profiles, heterogeneous capacities and rates,
250 varied demands using all five capacity dimensions, 120–300 second service times
(13.75 hours total), morning/afternoon windows and priorities 0/50/100. Sixty-eight
deliveries need a tail lift or refrigeration. Vehicles have 12-stop, 180 km and
five-hour driving limits. These are declared synthetic test inputs, not recommended
vehicle specifications or driver-hours rules. The predefined baseline assigns ten
points per grid cell to its corresponding vehicle; unit checks establish coverage,
capacity and equipment compatibility, but road/schedule feasibility still needs
the full independent validator.

**Regional acceptance failed on 24 September 2026.** With the same pinned engines,
two-CPU/2 GiB application quota, shared local routing service and 120-second whole
calculation deadline, the first baseline trial timed out in matrix construction.
The runner stops on that failure, retains the error and makes no comparison or
savings claim. Neither candidate objective ran, and the baseline's final road and
schedule feasibility has not been established. Failure reports now include the
failed stage and elapsed time; validation failures also retain coverage metrics
and blocking issues without storing route geometry or matrices.

A separate diagnostic gave each profile its own 120-second allowance solely to
locate the bottleneck; this was not a successful application calculation:

| Profile | Matrix time | Requests | Null directed cells |
|---|---:|---:|---:|
| 19-tonne rigid | 78.91 s | 49 | 5,842 |
| 7.5-tonne rigid | 80.49 s | 49 | 3,439 |
| 3.5-tonne van | 87.99 s | 49 | 2,706 |

All three matrices completed in 247.39 seconds combined, before solving or final
routing. There were no self-arc road-repair requests: regional matrix searches
dominated. Null cells stayed forbidden; these counts do not establish that every
delivery is reachable by a compatible vehicle. Offline 80- and 100-point tile
experiments also failed: the former on request two and the latter on request one,
both at the unchanged ten-second request timeout. Those sizes are not enabled in
the application, whose tiles remain bounded at 40 by 40.

These results led to the bounded-parallelism and eligibility work below. The dense
Paris acceptance remains valid for its measured workload; the 251-point input
ceiling is not a guarantee that arbitrary regional jobs complete within 120 seconds.
Operator review and real-company acceptance remain separate.

The final image reproduced failure in `MATRIX` at 120.005 seconds (exit 1).
All 139 host backend tests, 41 real-engine tests, nine ten-delivery quality trials,
image build and contract/specification checks pass. The smaller quality gates
retain 27.95% lower driving time and 8.84% lower modeled driving cost against their
baseline. Existing upstream deprecation warnings remain. Frontend, queued-worker,
HTTPS and backup suites were not rerun: this change adds benchmark data, runner
diagnostics and documentation, without changing the application calculation path.

### Bounded parallelism and profile eligibility

Matrix preparation now omits a vertex for a profile only when no selected vehicle
with that exact profile has both the required equipment and capacity for any
delivery at that coordinate. The depot is retained, as are coordinates shared with
an eligible delivery. This changes requested matrix vertices, not the road graph.
Every job remains in the solver input. Omitted profile rows/columns are forbidden
eligibility combinations; they do not claim that an unqueried road is unreachable.
All potentially usable directed cells, engine nulls, truck constraints and final
validation are preserved. For the fixed regional workload, the three profile
matrices need 251, 215 and 183 vertices: 142,715 cells / 110 tiles rather than
189,003 cells / 147 tiles.

`MATRIX_CONCURRENCY` defaults to one and is bounded at four. The adapter keeps at
most that many requests in flight, assembles results by source/target indexes and
shares the original deadline. HTTP, parsing, self-arc and timeout failures reject
the whole result, cancel queued work and drain active calls. Tests force responses
out of order, count simultaneous calls and verify that failures leave no active
requests. Native comparisons against the independent tiny reference cover both
objectives with and without profile exclusions; all original jobs remain present.

The regional performance measurements use the same pinned graph, engine and
configuration, with **four routing workers, four CPUs and an 8 GiB routing quota**.
The application retains two CPUs/2 GiB, a 120-second whole-job deadline and
ten-second request timeout. This larger routing allocation is explicit: the old
two-CPU/2 GiB router did not pass regional timing. Spatial regrouping, Dijkstra
search order and smaller/shared cache experiments were not adopted. Existing live
services and local configuration files were not changed by this implementation.

Before profile exclusions, four concurrent requests completed all three regional
matrices in 103.19 seconds. The baseline then failed on its first forbidden road
connection. Both objective diagnostics completed in 116.97 / 117.49 seconds with
validation `PASS`, 242 served and eight unassigned; the full-coverage benchmark
correctly returned failure rather than reporting savings. `REGION-ORDER-001`
requires refrigeration, available only on the 19-tonne vehicles. The engine returns
error 442 (no path found) for that approach, while the 7.5-tonne and 3.5-tonne
profiles have paths but lack the required equipment. A real-engine regression
checks that this delivery stays unassigned when considered with the three profiles.
These are dataset/model findings, not a claim that no physical route could exist.

With profile exclusions enabled, the real queued-worker/public-API tests measured:

| Workload | Travel-time job | Modeled-cost job | Validated result |
|---|---:|---:|---|
| Dense Paris, 250 deliveries | 57.651 s | 57.806 s | All 250 served |
| Fixed regional, 250 deliveries | 89.915 s | 90.822 s | Valid partial draft; all 250 accounted for |

Both regional drafts retained explicit unassigned work, including the refrigerated
delivery above, after API restart/reload and idempotent replay. All 157 host backend
tests, 51 real-engine tests and 17 worker tests pass, including process interruption,
deadline and database-session-loss recovery. Existing upstream deprecation warnings
remain. Frontend/browser, HTTPS and backup suites were not rerun for this backend
change. Regional full-coverage quality and company/operator acceptance remain open;
the next regional task is fleet/access feasibility review on the unchanged inputs.

Six repeated dense Paris-250 quality trials also pass with full coverage and
validation `PASS`, taking 48.94–60.20 seconds per calculation. Travel time remains
2.85% below the fixed baseline and modeled driving cost 0.68% below it. The separate
router's peak cgroup memory across these checks was 6.07 GiB, with no memory-limit
or out-of-memory events and no swap use. Nine repeated ten-delivery trials pass
both quality gates with full coverage, retaining 27.95% lower driving time and
8.84% lower modeled driving cost against their fixed baseline.

### Regional feasibility diagnosis and separate fleet scenario

The original regional fixture stays unchanged. Both objectives initially produced
242 served and eight unassigned deliveries. Checking the ordinary route endpoint
as well as matrix cells corrected the first interpretation of six of those cases:

| Delivery numbers | Evidence | Cause in the tested configuration |
|---|---|---|
| 172, 173, 180, 193, 198, 250 | Matrix omits at least one direction for every profile, but ordinary routes exist in both directions | CostMatrix's destination-only second pass was disabled |
| 001 | 19-tonne route returns error 442 in both directions; smaller profiles have paths but lack refrigeration | Selected fleet's road/equipment combination |
| 244 | Equipped 19-tonne round trip takes 20,243 seconds, above its 18,000-second driving limit; smaller trucks take 3,746 seconds but lack refrigeration | Selected fleet's equipment/driving-limit combination |

These findings are about this engine, graph and modeled fleet. Matrix nulls alone
did not prove that a physical road or even an ordinary engine route was absent.
The [diagnostic runner](../backend/benchmarks/feasibility.py) now compares both
endpoints and reports disagreement explicitly. It checks each unassigned delivery
alone against equipment, all five capacities, schedule and route limits; it does
not claim to solve joint fleet feasibility. Unexpected engine errors propagate.

An offline experiment enabling the second matrix pass restored the six
connections, but emitted a destination-only-access warning that the application
correctly rejects. A diagnostic-only adapter inspected that exact notice: it found
6,861 recovered finite connections, each also having an ordinary warning-free
route. Matrix construction took 108.08 seconds and exhaustive route verification
another 91.23 seconds. That is not a successful whole-job measurement. Broad
warning acceptance, altered road restrictions and longer timeouts were not adopted;
the application and graph-building defaults remain unchanged.

The separate `regional-equipped-250` scenario replaces REGION-01 and REGION-25
with explicitly modeled refrigerated 7.5-tonne vehicles, using the existing medium
profile's capacities and rates. All 250 orders, pins, equipment demands, windows,
priorities and service times remain identical; the other 23 vehicles, all route
limits and availability windows are unchanged. The ten-consecutive-deliveries
baseline was fixed before objective comparisons. Its two replacement routes take
4,418 and 5,416 driving seconds and fit their original windows and limits. This
scenario is a synthetic fleet alternative, not evidence that a real refrigerated
truck has those capacities or costs, and does not convert the original negative
fixture into a passing case.

Using the experimental notice handler, one equipped-scenario baseline passed
full coverage and independent validation in 117.22 seconds on the four-worker,
four-CPU/8 GiB router. Its repeat spent 119.05 seconds in matrix preparation and
hit the unchanged 120-second deadline during routing. This demonstrates a feasible
baseline, but fails repeated runtime acceptance and permits no quality comparison.

Further isolated experiments also failed their unchanged deadlines: eight
concurrent square requests, 32-point tiles at that concurrency, unidirectional
8-by-251 requests, more CPU headroom for four requests, and depot round-trip
prechecks before matrix construction. None became application code or deployment
defaults. The separate scenario remains an acceptance candidate, not a passed
regional benchmark. Full objective comparisons, its queued-worker acceptance and
its browser calculation/recalculation acceptance remain blocked on reliable
matrix compatibility/performance. The original dense Paris acceptance is unchanged.

The final committed-path check reproduced the equipped baseline's rejection in
`ROUTE` after 81.45 seconds (80.12 seconds in `MATRIX`): its assignment contains
a connection marked unavailable by the matrix. The runner exits 1 before any
objective comparison. Final diagnostic runs on the original fixture completed
calculation in 78.33 / 77.68 seconds, with both objectives reporting `PASS`,
242 served and the same eight explicitly unassigned deliveries.

Final verification passes 171 host backend tests, 59 real-engine tests (including
all eight regional diagnoses), 25 UI tests, image build and generated-contract/
specification checks. Existing upstream deprecation warnings remain. The browser
fixture selected all 250 deliveries across pages, but calculation/recalculation
acceptance was not attempted after the quality gate failed. Queued-worker, HTTPS
and backup suites were not rerun for the final diagnostic/benchmark-only changes.
No live application or routing service was restarted, and no production routing
configuration, input limit or deadline changed.

## Destination-access matrix recovery

The six matrix/road disagreements now have real-engine regression coverage.
The pinned Valhalla CostMatrix enables its destination-access second pass, retains
48-location buffers and caps post-connection refinement at 1280 iterations instead
of 2800. Only its exact code-400 second-pass notice is accepted. Null cells stay
forbidden; selected legs still require ordinary warning-free road routing. Truck
restrictions, hierarchy settings, reviewed pins and graph data are unchanged.
The configuration helper writes a new file and refuses overwriting an existing
output; new graph builds use the same settings.

Application requests are bounded at 48 points per axis and balanced across the
same number of tiles. Safe hard-window bounds omit only provably impossible
order-to-order rectangles. Shared eligible coordinates retain the union of their
windows; equality and every depot connection remain. Grouping cannot increase
the ordinary tile count, and spatial ordering is retained when no work is omitted.
Each group of up to four requests finishes before the next begins, and small
depot-only rectangles run last. This avoids internal queue buildup observed when
replacing individual completed requests. All
responses retain their directed indexes and the shared 120-second deadline;
HTTP requests remain limited to ten seconds. No workload constraint was relaxed.

Manual edits route only the requested legs, without an all-pairs matrix or solver.
Road failures reject recalculation. Independent geometry, schedule, capacity,
equipment, limits and cost checks remain; invalid sequences are fixable drafts.
The fixed benchmark baselines now use this same application function. Their
assignments, pins and objective metrics are unchanged. Manual recalculation starts
at vehicle availability, as the UI states, so waiting and elapsed operating costs
can differ from an optimized departure schedule.

The rejected configurations explain the selected limits. A 512-iteration candidate
failed six existing batch comparisons and 1024 failed four. The original 41-point
comparison remains unchanged at its explicit 40-point batch size. A new 49-point
case produces different estimates as a single matrix even with stock refinement;
its test compares every directed cell with the exact rectangular requests, with
no numeric tolerance. Matrix estimates remain heuristic, independently checked
against road paths and solution-quality benchmarks.

The 1536-refinement/48-point candidate passed six regional quality trials and a
browser walkthrough but failed queued-worker timing. A 1280/48 repeat also hit a
ten-second request timeout. Smaller 42-point tiles passed the captured synthetic
input at 113.60 seconds on four CPUs, leaving little worker margin. Even eight CPUs did not fix overlapping request
queueing: one request waited 5.7 seconds before its road search started. Grouped
48-point requests completed that same captured input in 109.77 seconds with full
coverage and validation PASS. The candidate
now uses a separate **eight-CPU/eight-GiB router with four workers** and a
**two-CPU/two-GiB application**. Earlier four-CPU acceptance measurements do not
establish this candidate's capacity. All 202 backend and 63 real-engine checks
pass, including original batch comparisons, six restored access pins, truck
restrictions, one-way roads, disconnected points and strict manual validation.

Six `regional-equipped-250` quality trials pass with full priority coverage,
250 served, zero unassigned and independent validation PASS. These use the fixed
refrigerated 7.5-tonne fleet alternative; the original fleet remains a separate
negative access/equipment case. The native solver retains its five-second budget.
All trials use fresh Python processes against a shared warm routing service; the
first trial is retained. This is not a cold-start or arbitrary-workload guarantee.

| Run, two repeats each | Whole calculation | Objective metric | Worst change from baseline |
|---|---:|---:|---:|
| Fixed manual assignment | 2.61–2.70 s | 106,686 driving seconds; 326,981.9473 driving-cost minor units | Baseline |
| Travel time | 109.30–109.58 s | 104,647 driving seconds | −1.91% |
| Modeled travel cost | 109.49–110.25 s | 288,600.0686 driving-cost minor units | −11.74% |

**What the cost figure measures.** The fixed baseline assigns ten consecutive
deliveries to each of the 25 vehicles; it is a mechanical assignment, not a
dispatcher's plan. The recorded runs use 25 vehicles for the baseline, 21 for
travel time and 22 for modeled travel cost. Fixed vehicle costs (4,000–8,500 minor
units each in this fleet, 151,500 in total) are about 46% of the baseline figure,
so roughly two-thirds of the −11.74% comes from serving the same work with three
fewer vehicles and about one-third from shorter routes (−9.8% distance, −4.1%
driving time). Each objective has one instance, and the single-threaded solver
repeats identical results, so the two repeats are not independent samples.

Optimized matrices take 101.79–102.53 seconds. Python peaks at 92,392 KiB and the
largest solver child at 74,544 KiB; these separate process peaks are not additive
and exclude the routing service and graph cache. The cost objective has 102,272
driving seconds. Every comparison requires identical full priority coverage;
none establishes optimality or operational suitability.

A queued cost calculation reproduced a distinct schedule issue: REGION-ORDER-041
started nine seconds late because the final road leg exceeded its matrix estimate.
The captured road-leg replay passes with only REGION-20 departing nine seconds
earlier; all assignments, sequences and road paths stay identical. Construction
now computes the latest feasible departure from final legs, service durations,
hard window ends and vehicle return time, then recalculates the forward schedule.
Vehicle availability remains a lower bound. Eight regressions cover inclusive
bounds, waiting, service, loads/costs, unchanged feasible departures and genuine
infeasibility, which still returns FAIL. Manual availability-start departure and
the driving-only benchmark objectives are unchanged.

All 19 queued-worker/process checks pass (four regional cases and the remaining
15 cases run in separate batches against the same build and router). Both
objectives submit through the public API, reopen saved plans after worker/API
restart and enforce the 120-second job limit. Reported worker elapsed times:

| Workload | Travel-time job | Modeled-cost job | Coverage |
|---|---:|---:|---|
| Dense Paris | 55.964 s | 56.569 s | 250 served, none unassigned |
| Original regional fleet | 107.991 s | 100.728 s | Valid partial drafts; access/equipment conflicts remain explicit |
| Equipped regional fleet | 109.773 s | 111.408 s | 250 served, none unassigned |

Six dense-Paris and nine Paris-ten quality regressions also pass with full
coverage. Dense optimization takes 53.58–54.81 seconds and improves driving time
2.8460% and modeled driving cost 0.6824%; its fixed manual baselines take
0.97–1.06 seconds. Paris-ten optimization takes 0.34–0.42 seconds, retaining
27.9498% / 8.8405% improvements. All 21 quality trials pass; the 25 frontend
checks also pass.

The remaining checks cover ten-delivery and all-unassigned cases, missing engines,
timeout/SIGTERM/SIGKILL/database-loss cleanup, stale edit release and guarded
finalization/export.

Browser acceptance also passes against a separate application and fresh disposable
database, with the equipped fixture's date and windows shifted uniformly to the
test day. Selecting 25 vehicles and all 250 deliveries across five pages produces
a saved version-1 draft with 250 served, zero unassigned and validation PASS. The
worker reports 112.855 seconds. The map renders all 250 numbered markers and saved
road geometry with attribution; hiding a 12-stop route leaves 238 markers.

Swapping the first two stops on REGION-15 shows an unsaved edit, then a version-2
pending draft with stale-map/totals guidance during recalculation. The edit worker
reports 4.884 seconds and saves version 3 with validation PASS and full coverage.
A full browser reload preserves the requested order (REGION-ORDER-113 followed by
REGION-ORDER-114), the saved totals and zero unassigned deliveries. Synthetic
finalization and operational-export guards remain in place. These are synthetic
workflow checks, not authorization for real dispatch. Operator-trusted HTTPS,
certificate renewal and real-operator constraint review remain separate gates;
the unchanged backup/restore suite was not rerun for this change.
The disposable application, routing services and test databases were removed after
acceptance; existing local services were left running. Configuration-helper checks
confirm that only the three matrix settings change and existing output is refused.
The existing graph was reused; a full graph rebuild was not repeated.

## Security that stays

Implemented sign-in limits count failures only: five per username from one client address in 15 minutes and 50 per username from all addresses in 15 minutes; there is no installation-wide limit, which an attacker spraying many names could use to lock everyone out. The bundled HTTPS proxy also limits sign-in requests per address and replaces any client-sent address header, so one client cannot lock a username for everyone. Automatic polling marks its requests as background reads, which do not extend the 30-minute idle timeout. Responses carry a same-origin script Content-Security-Policy, `frame-ancestors 'none'` and `X-Frame-Options: DENY`; the HTTPS proxy adds HSTS.

Before real customer data/shared access, use named users, an established password-hashing library and secure opaque sessions. Store protected session-token representations, not raw tokens; rotate/revoke and expire sessions. Use HttpOnly, SameSite and Secure cookies for shared mode, allowed-Origin/CSRF checks including login, generic login failures and throttling. Do not store browser bearer credentials in local storage.

Resolve organization ownership on every endpoint and in worker-loaded references. Parameterize database access. Separate runtime from migration/administration credentials; the application database account is not a superuser. Keep database and engines private and provide HTTPS at the host boundary. Reject user-controlled engine URLs, bound input/output sizes and subprocess resources, and never interpolate user data into a shell command.

Neutralize formula-like free text in CSV exports, including leading whitespace followed by `=`, `+`, `-`, `@`, tab or carriage return. Quoting alone is not sufficient. Test text rendering while preserving genuine typed numeric values.

## Logging: one useful layer

Write ordinary structured or consistently formatted logs to stdout with timestamp, level, request/job ID, stage/outcome, duration and a short error code. Rotate logs and apply a modest documented retention setting. Errors should be actionable without logging entire orders, addresses, route payloads or engine requests.

No dedicated event ledger, distributed tracing platform, compliance dashboard, proof chain or permanent request archive. Build versions and routing dataset information in operator diagnostics are enough to identify the installed system. Password/token protection and pinned dependency/build integrity are unrelated to planning-history overengineering and remain necessary.

Monitor only what an operator can act on: application availability, worker/queue health, failed jobs, disk space and backup completion. A public liveness endpoint must not leak configuration or pretend the routing graph is ready. Shared hosting may need more later; add it when a measured failure mode justifies it.

Implemented: signed-in users can read `GET /api/v1/system-status`, which reports
whether a worker holds the singleton guard, whether the configured routing
service answers its status request, and the company's queued/running job counts
and oldest queue age. The planning screen shows a warning when the worker is not
running, routing is unavailable or a job has waited more than a minute. Failed
calculations carry the adapter's own explanation (for example, the deliveries
whose pins have no usable road nearby); engine stderr and driver exceptions are
never shown. `/api/v1/ready` still checks only the database and schema, so that a
stopped worker never makes the API container unhealthy.

## Backups and ordinary recovery

Back up PostgreSQL and the routing configuration/dataset needed to restore service. Keep a previous usable routing graph during updates. Protect backups, maintain an off-host copy and test restoration into an isolated environment. Verify login, saved-plan reads and a current READY export. No content-fingerprint or activity-history verification is required.

Schedule the documented backup command (cron, a systemd timer or Windows Task
Scheduler) with a distinct output path per run, copy each archive off-host and
encrypt it at rest; dumps contain accounts' password hashes as well as company
records. The helper never overwrites an archive and deletes old ones only when asked, with `prune --keep N` (see [backups](../deploy/README.md#database-roles-upgrades-and-recovery)). Launcher upgrades
(`--upgrade-image`) take a backup before stopping the API and worker.

Choose retention for orders, saved plans, failed jobs, logs and backups with the operator before real data. The product does not need live driver location histories. Obtain appropriate privacy/security/legal review for actual deployment rather than claiming the documentation establishes compliance.

**Worker failure:** stop the old process, restart the single worker, fail interrupted jobs visibly and release affected draft recalculations. New attempts are explicit. Do not accept late results from a worker that lost its database guard.

**Engine outage:** fail new computation clearly; retain access to saved results. A stored READY result can export without engines while its source records remain unchanged. Never substitute synthetic or straight-line travel.

**Bad routing update:** stop/drain jobs, restore the previous graph, run known-access-path tests and resume. Updating the graph does not silently rewrite saved results.

**Invalid candidate:** keep it a draft, show the failing constraint, fix input/assignment and recalculate. A user acknowledgment cannot bypass a failed modeled safety constraint.

## Release gate

Test the actual regional graph, preservation of map attribution, absence of order payloads in provider requests and replacement of `MAP_STYLE_URL`. Public-basemap failure must leave tables usable. These checks do not make a public tile provider an SLA-backed service, and no commercial account is required for the selected baseline.

The application must pass the relevant model/engine, company-boundary, recovery and end-to-end checks, and install successfully from a clean checkout. Begin real use beside the operator's existing planning process. Licensing, installability and a reliable demo also gate an open-source release; billing does not.
