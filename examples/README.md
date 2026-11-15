# Synthetic contract examples

These JSON/CSV files demonstrate the contract generated from the application ([API](../backend/openapi.json) and [JSON schemas](../schemas/)), not operational travel, existing API records or benchmark results. The plan example is explicitly SYNTHETIC and cannot be finalized for real dispatch.

`depot.json`, the two basic vehicle files and two order files supply small inputs. CSV headers are unchanged from the basic format; imports start coordinates unreviewed even when a JSON example demonstrates a reviewed point. Database creation returns real IDs/organization IDs/versions; replace the sample references before making dependent requests.

`vehicle-with-limits.json`, `order-priority.json` and `optimization-cost-request.json` demonstrate optional core model controls. CSV imports accept them as optional trailing columns: orders `priority`; vehicles `max_stops,max_distance_m,max_driving_seconds`.

`optimization-request.json` chooses a date, source IDs, road policy, objective and search budget. `job.json` and `route-plan.json` show the completed calculation in the form `GET /jobs/{key}` and `GET /plans/{key}` return. The plan is a real Valhalla/VROOM calculation of that request with the example depot, the two basic vehicles and the two basic orders on the Île-de-France graph with static OSM speeds. It has a current integer version, one input copy and the actual truck inputs on each route, with no content-certification payload.

`edit-plan.json` submits the complete assignments and unassigned set for an in-place update. Send the current quoted version in If-Match and a retry-stable Idempotency-Key. On completion, refresh the plan and new ETag.

`finalize-request.json` uses `accept_unassigned: false`; set true only to accept visible unassigned work on a real validated candidate. No request can override failed validation or promote synthetic results to ROUTED.

`backend/tests/test_examples.py` validates every JSON example with the application models, checks that the CSV rows import to the same values as their JSON twins, and runs the application's independent route validation (coverage, loads, skills, schedules, geometry and cost arithmetic) on the plan example. `scripts/validate_pack.py` checks the examples against the generated JSON schemas without the application. Neither runs a routing engine.

When the plan or input models change, regenerate the plan example from a real calculation. This needs the engine-test image and the local routing service on port 8002 (see [deploy/README.md](../deploy/README.md)). From the repository root in a POSIX shell (in Git Bash, prefix `MSYS_NO_PATHCONV=1`):

```bash
docker run --rm --add-host host.docker.internal:host-gateway -v "$PWD/backend:/app/backend:ro" -v "$PWD/examples:/app/examples:ro" -v "$PWD/scripts:/app/scripts:ro" tournevia-engine-test:local python /app/scripts/create_plan_example.py > deploy/runtime/route-plan.json && mv deploy/runtime/route-plan.json examples/route-plan.json
```

The script prints nothing and exits with an error if the engines fail or the result does not pass validation, so the existing example is kept. Review the diff before committing; solver or road-data changes can legitimately change the routes.
