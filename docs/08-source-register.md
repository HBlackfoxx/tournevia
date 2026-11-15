# 8. Primary sources and verification limits

**Version 0.4, updated 22 September 2026.** References below support engine/framework capabilities and licensing/service boundaries. Moving documentation does not select a tested dependency version. Pin actual builds during implementation and keep executable adapter tests.

| ID | Primary reference | Use |
|---|---|---|
| S1 | [VROOM API](https://github.com/VROOM-Project/vroom/blob/master/docs/API.md) | Matrices, tasks, native costs/limits and solver-mode semantics |
| S2 | [Valhalla route API](https://valhalla.github.io/valhalla/api/route/api-reference/) | Truck costing, access flags, units and preferences |
| S3 | [Valhalla matrix API](https://valhalla.github.io/valhalla/api/matrix/) | Directed source/target travel, null connections and time-awareness limitations |
| S4 | [VROOM CLI usage](https://github.com/VROOM-Project/vroom/wiki/Usage) | Executable interface and version-sensitive options |
| S5 | [MapLibre GL JS documentation](https://maplibre.org/maplibre-gl-js/docs/) | Browser map rendering and API |
| S6 | [OpenStreetMap copyright/license](https://www.openstreetmap.org/copyright) | Underlying map-data licensing and attribution |
| S7 | [OSMF tile policy](https://operations.osmfoundation.org/policies/tiles/) | Public tile hosting has separate capacity/usage terms |
| S8 | [Public Nominatim policy](https://operations.osmfoundation.org/policies/nominatim/) | Do not assume public geocoding is a production or autocomplete service |
| S9 | [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) | Selected Community license and redistribution conditions |
| S10 | [AGPL-3.0 text, Open Source Initiative](https://opensource.org/license/agpl-3.0) | Alternative network-source reciprocity and licensing tradeoffs |
| S11 | [FastAPI background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) | Separate heavier work from lightweight in-process tasks |
| S12 | [Django authentication](https://docs.djangoproject.com/en/5.2/topics/auth/) | Built-in authentication alternative |
| S13 | [Django admin](https://docs.djangoproject.com/en/5.2/ref/contrib/admin/) | Built-in business-administration alternative |
| S14 | [PostgreSQL explicit locks](https://www.postgresql.org/docs/current/explicit-locking.html) | Transaction versus session advisory-lock behavior |
| S15 | [Odoo 19 official licensing documentation source](https://raw.githubusercontent.com/odoo/documentation/19.0/content/legal/licenses.rst) | Historical reference from 0.3; not a dependency or selected project license |
| S16 | [Geofabrik Île-de-France download page](https://download.geofabrik.de/europe/france/ile-de-france.html) | Selected regional OSM PBF source and extract extent |
| S17 | [OpenFreeMap](https://openfreemap.org/) | Public instance, commercial use, self-hosting and absence of an SLA |
| S18 | [OpenFreeMap quick start](https://openfreemap.org/quick_start/) | Liberty style URL and MapLibre integration |
| S19 | [OpenFreeMap privacy policy](https://openfreemap.org/privacy/) | External hosting/data-flow review; no offline-privacy assumption |
| S20 | [OpenFreeMap terms of service](https://openfreemap.org/tos/) | As-is availability and operator review of external-service terms |
| S21 | [Geofabrik France download page](https://download.geofabrik.de/europe/france.html) | Later broader extract selection; no prepared graph or coverage claim |

References S1–S15 are retained from earlier versions of these documents; not all were rechecked for 0.4. The specification revision re-read S6, S9 and S16–S21 for the accepted licensing/data/provider defaults. Engine acceptance uses locally built regional and controlled graphs; [setup and test commands](../deploy/README.md) and [engine pins](../deploy/engines.json) identify the implementation. No provider agreement was accepted on the operator's behalf, and no uptime guarantee is asserted. A deployment must inspect the terms applicable when enabling its chosen service. No unverified latest-version claim is needed for the design.

The forbidden-duration bound in section 4 is our proposed adapter reasoning for this finite-window model, not a VROOM feature claim. It needs real-engine and numeric-bound tests. Priority/limits, travel-cost mapping, route restrictions, company isolation, deployment commands and benchmarks have not been exercised in a built application.

No current toll tariff, driver-hours legal rule, circulation calendar, low-emission-zone eligibility, national data coverage or tracking-retention period is asserted by these documents. Verify effective rules and licensed data with the operator when implementing those features.
