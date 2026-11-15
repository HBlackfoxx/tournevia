# Implementation decisions

The project's main build decisions, kept short: the code and its tests carry the detail.

| Choice | Reason | Accepted tradeoff |
|---|---|---|
| Modular FastAPI monolith plus worker | Suits this standalone map/API planner | Authentication and administration are implemented in the application; Django remains an alternative, not a second runtime |
| PostgreSQL as the one application store | Owned records, jobs and current plans fit one transactional database | No scale-out queue or spatial subsystem yet |
| Valhalla and local VROOM | Reuse road routing and optimization capabilities | Exact adapter and build behavior are proved on real-engine fixtures |
| Native objectives, priorities and route limits | Useful solver control without a custom optimization framework | Not a complete monetary or driver-compliance model |
| Five capacity dimensions and independent validation | These are core operational requirements | Aggregate quantities do not prove physical packing |
| Finite-horizon forbidden-arc adapter | Handle truck-specific unavailable connections without rejecting unrelated work | Rests on the hard-window assumptions in [section 4](04-routing-and-validation.md#bounded-forbidden-arc-adapter); covered by routing-core and real-engine tests |
| Mutable draft plus READY/export | Simple dispatcher workflow | Previously downloaded routes must be redistributed after edits |
| One input copy, no certification records | Stable calculations without permanent history infrastructure | No audit-grade reconstruction guarantee |
| Organization-owned records, one company per installation | Avoid a hard-coded global data model | Shared SaaS still needs a separate tested isolation/lifecycle design |
| One complete Community edition | A complete self-hosted product; any later service follows [section 9](09-open-source-and-paid-plans.md) | One routing core; no billing or developer portal |
| Apache-2.0 in `LICENSE` | Easy adoption by companies and developers | Others can commercialize under its terms; third-party notices are kept in [NOTICE.md](../NOTICE.md) |
| Synthetic regional delivery fleet | A van and rigid-truck delivery workload gives tests and benchmarks a concrete baseline | A real customer's break, pickup or other requirements still gate its pilot |
| Île-de-France / Geofabrik OSM first | Matches the Paris examples without a national graph build | A France graph is prepared too; other regions need their own coverage checks |
| OpenFreeMap Liberty through `MAP_STYLE_URL` | Public basemap without an account/key dependency | External service without an SLA; preserve attribution and allow replacement |
| Reviewed coordinates and manual pins; address lookup optional | No geocoding dependency by default | An address match is only a proposal; the vehicle entrance is still reviewed |

The project keeps one plan, direct implementation and tests. It does not trade model
correctness for fewer modules, or add paperwork to make a prototype look ready for
production.
