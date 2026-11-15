# Changelog

What changed for the people who use, install or build on Tournevia. Before 1.0, a new
minor version may change the API, the file formats or the settings; each entry says
what an existing installation has to do.

## Unreleased

Nothing yet.

## 0.8.0 — 15 November 2026

The first tagged release. Everything built so far is in it; the earlier numbers
(0.2 to 0.8, September 2026) were development milestones that were never tagged or
distributed. The limits of this version are listed in the
[README](README.md#what-it-does-not-do): it has been tested on fictional workloads,
not yet accepted against a real operator's fleet and working day.

### Planning

- A day of deliveries is planned for a company's own fleet: vehicles leave loaded
  from a depot or another place, deliver and end where they are told to. Each delivery
  can have a service window, a service duration, a priority, required equipment and
  the depot it ships from; each vehicle has weekly working hours, days off, five
  capacities, its dimensions and weights, and optional limits on stops, distance and
  driving time.
- Travel times and distances come from Valhalla on OpenStreetMap data, in both
  directions, with each truck's dimensions; VROOM assigns and orders the stops for
  the shortest travel time or the lowest modeled travel cost.
- An independent validator recomputes the schedule, loads, equipment and limits of
  every result from its final road legs. A plan with a known violation cannot be
  marked Ready.
- A company-wide safety margin (15 minutes by default) plans each delivery to start
  that long before its window closes.
- One calculation takes up to 25 vehicles, 250 deliveries and 3 truck profiles. A
  selection wider than `MATRIX_MAX_KM` (200 km by default) is refused before it is
  queued, naming the two farthest places. A delivery whose pin has no road its trucks
  can use within 50 m is left unassigned and named.
- A dispatcher reviews routes on the map, moves a stop to another vehicle or
  position, recalculates that sequence on the roads and marks the plan Ready. Only
  one Ready plan per day may dispatch a given vehicle or delivery.
- A Ready plan gives a dispatch CSV file and printable route sheets, one page per
  vehicle, with links that open each stop in a navigation app.

### Entering data

- CSV import of vehicles and deliveries with a preview before anything is saved:
  each row is marked New, Changed or Unchanged, and rows to check are listed with
  the reason. Files saved by a spreadsheet set to French conventions (semicolons,
  decimal commas) are read as they are.
- Saved customers, whose reviewed vehicle entrance is reused by their deliveries.
- Delivery numbers given by the server in a format the company chooses.
- Several depots and other places, such as a driver's home, each found by its address
  and reviewed on the map.
- Optional address lookup for France, through IGN's public service or the
  installation's own geocoder; it is off until an administrator turns it on.
- A setup guide after first use, and example deliveries and vehicles to try a
  calculation before entering your own.

### Interface and accounts

- English and French, light and dark themes, and layouts for a desktop, a tablet and a
  phone.
- Named accounts with administrator and planner roles, Argon2 password hashing,
  revocable sessions, CSRF protection and sign-in limits per user and address.
- Work in progress is kept when a session expires, and questions that discard work
  open in the application's own dialog.

### Installing and operating

- `scripts/prepare_routing.py` downloads the Île-de-France road data and builds its
  routing graph; `scripts/start_local.py` starts a private workspace on one computer;
  `scripts/start_server.py` starts a shared installation with HTTPS from your own
  certificate, or behind an existing reverse proxy such as Traefik.
- The road router can run inside the installation, on the Île-de-France graph or on
  one built for all of France.
- The API and the worker use a restricted database role; migrations run under a
  separate one. Backups, restore into a fresh database, a tested cutover to the
  restored copy, optional encryption of backups, and commands that purge or erase old
  data are included.
- Optional token-protected Prometheus metrics with example alert rules, a system
  status shown on the planning screen, and bounded, redacted logs.
- The API contract, the JSON schemas, the TypeScript types, the form limits and the
  CSV columns are generated from the application models.
- A release is a source kit with a checksum manifest, software bills of materials
  and optional signatures. Pushing a version tag also builds the application image
  and publishes it; see [release kits](deploy/RELEASE.md).

### Names

- Docker images are named `tournevia-app`, `tournevia-vroom` and
  `tournevia-engine-test`, the Compose project `tournevia-community`, the router
  containers in the guides `tournevia-valhalla…`, and release kits
  `tournevia-VERSION-REVISION.zip`. They used the prefix `france-vrp-`.
- The JSON schemas' `$id` values start with `https://schemas.invalid/tournevia/`.
- Database and role names (`vrp`, `vrp_owner`, `vrp_app`) are unchanged.

### Upgrading an installation made from an earlier build

- **Images.** Build the images under their new names as the README shows, or name the
  ones you have: `docker tag france-vrp-vroom:1.14.0 tournevia-vroom:1.14.0`. A
  workspace made by a launcher keeps the image recorded in its folder until you run
  the launcher with `--upgrade-image tournevia-app:TAG`, which takes a backup first.
- **Compose project.** An installation started by hand with
  `deploy/docker-compose.example.yml` must add `-p france-vrp-community` to its
  Compose commands; without it, Compose creates a new, empty project beside the old
  one. Workspaces made by the launchers carry their own project name and are not
  affected.
- **Router containers.** Containers you started under the old names
  (`france-vrp-valhalla` and the others) keep working; use their old names with
  `docker start` and `docker stop`, or recreate them with the commands in the guide.
- **Database.** Migrations `0008` to `0017` run during setup. After
  `0012_weekly_hours`, a vehicle keeps its former dated availability as an exception
  on that date and has no weekly hours: set each vehicle's week once in Fleet. The
  vehicle CSV has one column per weekday instead of `available_start,available_end`.
- **Sample workspaces.** A sample workspace created by first-use setup becomes a
  company workspace whose vehicles and deliveries are marked as examples.
