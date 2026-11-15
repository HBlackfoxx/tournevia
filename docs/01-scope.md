# 1. First product scope

## Outcome

A dispatcher imports deliveries and vehicles, corrects map locations, generates a candidate, adjusts stop assignments, marks the current plan ready, and exports routes. The community product must eventually support that complete workflow without a vendor account or subscription.

An installation serves one company. The company keeps one or more depots and other places, such as a driver's home; one depot is the main depot. Each selected vehicle starts its day at the main depot unless it names another depot or place, leaves loaded, and ends at its own end place (its start unless set), all on the same local planning day. A delivery may name the depot it ships from; only vehicles starting there carry it. Company ownership is explicit in storage; multiple companies sharing one hosted installation are not promised.

Start with 2 vehicles and 10 synthetic deliveries. Benchmark toward 25 selected vehicles, 250 deliveries and 3 exact routing profiles. These are provisional engineering bounds to measure and tune, not artificial free-edition restrictions.

## Accepted first build baseline

Use regional business deliveries around the existing synthetic Paris depot, with a van and a rigid-truck profile. Keep `Europe/Paris`, metric units and EUR. Vehicle dimensions, payload limits, equipment, windows and rates remain explicit inputs; the examples are not recommended specifications for a real fleet.

The routing core uses an Île-de-France graph and a two-vehicle/ten-delivery engine fixture. The first location workflow imports coordinates and supports reviewed manual pin correction; external address geocoding is off by default. OpenFreeMap Liberty is the replaceable initial basemap. These defaults do not establish customer suitability or nationwide routing coverage.

## Required capabilities

| Area | Requirement |
|---|---|
| Data | Depot, vehicles, dated orders, fixed CSV preview/commit, stable external identifiers and explicit units |
| Locations | Reviewed delivery access points; pin correction; no automatic approval of approximate geocoding |
| Capacity | Weight, volume, Euro pallets, industrial pallets and loading length, all enforced together |
| Schedule | Service durations, hard service-start windows, vehicle availability/return deadlines, waiting |
| Vehicle fit | Equipment skills, actual truck dimensions/weight/axles, optional stop/distance/driving limits |
| Optimization | Native order priorities; travel-time default and modeled travel-cost option; bounded execution |
| Road network | Directed per-profile travel, explicit inaccessible connections and verified final geometry |
| Review | Independent feasibility check, route/map agreement, clear unassigned orders and manual moves |
| Export | Simple READY state; current validated result to CSV; edits require recalculation before export |
| Company use | Named users, two roles, organization-scoped access, backups and basic operational logging |

Known hard violations block finalization. A plan serving no order cannot be finalized. Unassigned deliveries must remain visible and require one explicit acceptance of a partial plan, not individual compliance attestations.

## Supported model, without hidden assumptions

One hard service-start window per order; one availability window per vehicle; delivery-only; no split deliveries. Priorities influence what the solver attempts to serve and do not create a hard guarantee that an order will be served. A maximum driving duration is not a complete driver-hours compliance model.

Break scheduling, pickups paired with deliveries, reloading at a depot during the day, driver histories and live replanning are not in the current public schema. They are normal routing-domain extensions. When the selected customer's work needs one, implement and test that extension before calling the product suitable for that work. Do not drop a constraint during import, or misrepresent a break as an extra service time at an arbitrary customer.

Aggregate capacity limits do not prove physical loading, mixed pallet fit or axle distribution. Gross routing weight is separate from payload capacity. The application does not automatically certify current road restrictions, dangerous-goods eligibility, temporary restrictions, driver compliance or actual toll prices.

## Honest presentation

Show “Candidate found within the search budget,” not “optimal” or “lowest all-in cost.” Label elapsed operating cost as excluding tolls; unknown toll and all-in totals remain null. Routing estimates use the selected road dataset and static model, not a claimed live-traffic feed.

The dispatch screen and export carry a concise limitation: **“Planning estimate. Verify current road access and driver requirements. Tolls are not included.”** This is information, not a checkbox that makes an unsafe route acceptable.

## Success

Measure feasible deliveries served, unassigned work, total distance, driving/elapsed time, vehicle use and planning effort against an agreed baseline. Test the workflow with a real dispatcher in shadow use before replacing existing dispatch. Passing a schema check does not satisfy any of these product outcomes.
