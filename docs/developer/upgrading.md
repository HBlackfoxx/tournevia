# 6. Upgrading dependencies and engines

[Developer guide](README.md) › Upgrading

Everything Tournevia depends on is pinned to an exact version, and most container images to a digest, so
that a build today behaves like the build that was tested. This page lists where each pin lives and what
to check when you move it. Operators moving an **installation** to a new Tournevia image follow the
website's [upgrade guide](https://tournevia.com/en/docs/developer/upgrade/) instead.

The general rule: **one dependency per change, with its evidence.** Say which checks ran, on which
machine, with what result. A dependency that touches routes (VROOM, Valhalla, the road graph) needs the
real-engine suite and the benchmarks, not only the ordinary tests.

## Where versions are pinned

| What | Pinned in | Updated by Dependabot |
|---|---|---|
| Python packages, direct | [backend/requirements.txt](../../backend/requirements.txt), [requirements-dev.txt](../../backend/requirements-dev.txt) | yes |
| Python packages, full set with hashes | [backend/requirements.lock](../../backend/requirements.lock) | no, by hand |
| npm packages | [web/package.json](../../web/package.json) (exact versions), `web/package-lock.json` | yes |
| GitHub Actions | `.github/workflows/*.yml`, by commit SHA with the version in a comment | yes |
| Node, Python base images | root [Dockerfile](../../Dockerfile), by digest | yes |
| Debian build base for VROOM | [deploy/Dockerfile.vroom](../../deploy/Dockerfile.vroom), by digest | no (only the root `Dockerfile` is watched) |
| PostgreSQL | [deploy/docker-compose.example.yml](../../deploy/docker-compose.example.yml), `ci.yml`, `engines.yml`, by digest | no |
| nginx | `deploy/docker-compose.https.yml`, `deploy/docker-compose.proxied.yml`, by digest | no |
| addok and its Redis | `deploy/docker-compose.geocoder.yml`, by digest | no |
| VROOM | `deploy/Dockerfile.vroom` (tag and verified commit), [deploy/engines.json](../../deploy/engines.json) | no |
| Valhalla | `deploy/engines.json` (`valhalla_image`, `valhalla_tag_file_sha256`), `deploy/docker-compose.routing.yml`, `deploy/valhalla-graph.lua` | no |
| Road data (OpenStreetMap extract) | `deploy/engines.json` (source, date, size, SHA-256) | no |
| Tournevia's own version | `backend/app/main.py`, `web/package.json` and its lock | — |

To find every copy of a pin before changing it, search the repository for the old value, for example
`git grep -n "postgres:16-alpine"` or `git grep -n "1.14.0"`.

## Python packages

The Docker image installs **only** `backend/requirements.lock`, with `pip install --require-hashes` and then
`pip check`. `requirements.txt` lists the direct dependencies; the lock lists every package in the runtime
closure, with the SHA-256 of every file PyPI publishes for each version.

1. Change the version in `requirements.txt` (and `requirements-dev.txt` for test tools).
2. Refresh the lock to match. There is no script for this yet; the lock was produced by installing
   `requirements.txt` in the Linux Python 3.14 image, recording the exact versions installed
   (`pip freeze`), and adding every published file's SHA-256 for those versions (PyPI's JSON API lists
   them as `digests.sha256`). Keep its header comment.
3. Build the image (`docker build --target runtime .`): it fails if a hash or a dependency is wrong.
4. Run the backend checks. CI also runs `pip-audit` on the lock.

A Dependabot pull request for pip changes `requirements.txt` only. CI's backend job then tests the new
version, but the image would still install the old one from the lock: refresh the lock in the same pull
request before merging.

Notable packages:

- **FastAPI and Pydantic** shape the generated contract. After an upgrade, run
  `python scripts/export_contracts.py --check`: a difference means the OpenAPI output changed. Review the
  diff of `backend/openapi.json` and `api-types.ts` before accepting it.
- **SQLAlchemy, Alembic, psycopg**: run the PostgreSQL tests and the `database_ops` suite.
- **argon2-cffi**: existing password hashes must still verify; `test_auth.py` covers sign-in.

## npm packages

```bash
cd web
npm install --save-exact <package>@<version>
npm test && npm run lint && npm run build
```

Commit `package.json` and `package-lock.json` together. The build regenerates the third-party notices.
CI runs `npm audit --omit=dev --audit-level=high` on production dependencies.

- **React**: run the whole test suite and click through the main screens.
- **MapLibre GL**: check pins, route lines, selection and pin picking, at desktop and phone width, in light
  and dark mode. MapLibre's style specification evolves; test with the default `MAP_STYLE_URL`.
- **TypeScript, ESLint, Prettier, Vite, Vitest**: a formatting rule change can rewrite many files; run
  `npm run format` in its own commit.
- **openapi-typescript**: regenerate `api-types.ts` with `npm run api:generate` and review the diff.

## Base images and language versions

Images are pinned by digest so the tag cannot move under you. To update one:

```bash
docker pull python:3.14-slim-bookworm
docker image inspect --format '{{index .RepoDigests 0}}' python:3.14-slim-bookworm
```

Replace the old digest everywhere it appears and rebuild.

- **Python minor version** (for example 3.14 to 3.15): the root `Dockerfile`, `python-version` in
  `ci.yml` and `engines.yml`, then refresh the lock in the new image, since wheels differ per Python
  version. Update the tested versions stated in [deploy/README.md](../../deploy/README.md).
- **Node major version**: the `web` stage of the `Dockerfile`, `node-version` in `ci.yml`, and the tested
  version in `deploy/README.md`.
- **Debian release**: the Python image and `Dockerfile.vroom` should move together, since VROOM is compiled
  on one and runs in the other.

## PostgreSQL

The same image digest appears in the Compose file and in both workflows; keep them identical. A patch
update within major version 16 needs no data change.

A **major** version change (16 to 17) is different: a new major version cannot open the old data
directory. Existing installations must move their data with a backup and a restore into the new version
(`scripts/database_backup.py backup`, then `restore` and `cutover`, described in
[Database roles, upgrades and recovery](../../deploy/README.md#database-roles-upgrades-and-recovery)).
The backup tools run the PostgreSQL client of the `postgres` service, so they must match the server.
This path has not been exercised for a major version; test it on a copy and write the operator steps
before changing the default.

## VROOM

VROOM is compiled from source in [deploy/Dockerfile.vroom](../../deploy/Dockerfile.vroom), which checks
the exact commit of the release tag, and its binary is copied into the application image.

1. Read VROOM's changelog for changes to the JSON input and output used by
   [optimization.py](../../backend/app/optimization.py): custom matrices per profile, `skills`, `priority`,
   `time_windows`, `max_tasks`, `max_distance`, `max_travel_time`, `costs`, and the solution's routes,
   steps and unassigned jobs. Check the integer limits the mapping enforces (`UINT32`, `INT64`).
2. Update the tag and commit in `Dockerfile.vroom`, `vroom_version` and `vroom_commit` in
   `deploy/engines.json`, and the image tag `tournevia-vroom:<version>` in the root `Dockerfile`
   (`FROM … AS solver`), both workflows, the README, `deploy/README.md`, `scripts/release.py` and
   [6. Tests and operations](../06-testing-and-operations.md).
3. Build both images and run the real-engine suite and the quality benchmarks
   ([Development workflow](development.md#suites-that-need-the-engines)). Compare with the recorded
   results: a different but valid solution is expected; a worse objective or any validation failure is a
   finding to report, not to hide.

## Valhalla

1. Choose the new image and pin it by digest in `valhalla_image` and `valhalla_version` in
   `deploy/engines.json` and in `deploy/docker-compose.routing.yml`.
2. Make [deploy/valhalla-graph.lua](../../deploy/valhalla-graph.lua) again from the new version's
   `lua/graph.lua`: keep the `--!` header and name the new commit in it, change the three lines listed
   in [deploy/graph_tags.py](../../deploy/graph_tags.py), and set `valhalla_tag_file_sha256` in
   `deploy/engines.json` to the SHA-256 of the unchanged upstream file. `build_graph.py` stops when the
   file does not match the image. If the new version reads `maxweightrating` itself, remove the file,
   the module and the test instead ([why the file exists](../../deploy/README.md#weight-limits-tagged-maxweightrating)).
3. **Rebuild the graphs** with the new version: tiles built by one version are not guaranteed to work with
   another. Build into a new runtime directory with [deploy/build_graph.py](../../deploy/build_graph.py)
   and [deploy/configure_matrix.py](../../deploy/configure_matrix.py), as in
   [Build the local graphs](../../deploy/README.md#build-the-local-graphs).
4. Check what [routing.py](../../backend/app/routing.py) relies on: truck costing options (dimensions,
   weights, axles, toll and motorway preferences in `costing`), the matrix response and its warnings,
   the route response and its encoded shape, and the error codes it turns into messages.
5. Run the real-engine suite (which includes the deliberately artificial restriction graph), the worker
   suite and the benchmarks.

## Road data

The graph used for the recorded tests and benchmarks is built from a pinned Geofabrik extract, identified
by size and SHA-256 in `deploy/engines.json`. Geofabrik only serves its latest file, so updating the data
is a deliberate step:

1. Download the new extract and build it in a **new** runtime directory:
   `build_graph.py region --accept-new-extract` (the script refuses a file that does not match the pin
   without that flag). Never rebuild tiles under a running router.
2. Run the real-engine suite and benchmarks on the new graph. The weekly `engines.yml` run does this
   automatically on the latest extract and records its hash in the job summary; a passing run is the
   evidence for moving the pin.
3. Update `region_bytes`, `region_sha256` and `downloaded_on` in `engines.json`, and the dataset label
   that every plan records and shows: `ROUTING_DATASET` in `config.py`, `deploy/env.example` and the
   Compose file, and the `--dataset` defaults of `spike.py`, `benchmarks/run.py` and
   `benchmarks/scaling.py` (`git grep -n "downloaded 2026-09-22"` finds them all). Regenerate
   `examples/route-plan.json` with the real engines, as [examples/README.md](../../examples/README.md)
   explains; benchmark result files keep the label of the run that produced them.

The same applies to the France extract under `extracts` in `engines.json`.

## Releasing a new Tournevia version

1. Set the version in `backend/app/main.py` (`version=` of the FastAPI app) and `web/package.json`, then
   run `npm install` in `web` so the lock file follows. Regenerate the contracts: the version appears in
   `backend/openapi.json`.
2. Move what is under "Unreleased" in [CHANGELOG.md](../../CHANGELOG.md) into a section headed
   `## X.Y.Z — date`, with the migrations and the actions operators must take.
3. Make the source kit with `python scripts/release.py pack --version X.Y.Z` from a clean, committed
   checkout. It writes a ZIP and its SHA-256. Extract it to a new directory and run
   `python scripts/release.py verify` then `python scripts/release.py build`, which builds the images;
   run the installation acceptance on that image. See [deploy/RELEASE.md](../../deploy/RELEASE.md).
4. Tag that commit `vX.Y.Z` and push the tag. The [release workflow](../../.github/workflows/release.yml)
   repeats step 3 on GitHub, publishes the image and opens the GitHub release; started by hand on a
   branch, it rehearses everything except publishing.
5. Update the website's release notes and, when there are migrations, its upgrade page.
