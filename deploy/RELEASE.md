# Releases and source kits

A Tournevia release is a **source installation kit** made from a tagged revision,
which an operator builds and runs locally, and an application image built from that
kit. [SECURITY.md](../SECURITY.md) lists
what is supported. Before operational use, an operator's real workload still needs
the acceptance described in the [roadmap](../docs/07-deferred-scope.md#before-the-first-supported-release).
No registry account or subscription is required to build or run a kit. Internet
access is required to obtain dependencies, container images and routing data.
Docker Desktop terms are separate from this project; Linux Docker Engine is another
installation option.

## What a version tag publishes

Pushing a tag such as `v0.8.0` to GitHub runs
[the release workflow](../.github/workflows/release.yml). It:

1. checks that the tag names the application's version (`web/package.json` and
   `backend/openapi.json`) and that [CHANGELOG.md](../CHANGELOG.md) has a section for it;
2. makes the kit from that commit, extracts it, and runs `release.py verify` and
   `release.py build` there, as an operator would;
3. runs the HTTPS installation acceptance (`scripts/test_https.py`) on the built image;
4. pushes the image to the GitHub container registry as
   `ghcr.io/OWNER/tournevia-app:VERSION`, where OWNER is the repository's owner in
   lower case;
5. opens a GitHub release holding the kit, its SHA-256 file and the two bills of
   materials, with the changelog section and the image's digest as its notes.

A version with a suffix, such as `0.9.0-rc.1`, is marked as a pre-release. There is no
`latest` tag: name a version or, better, the digest on the release's page. Started by
hand from the Actions tab, the workflow rehearses every step except the two that
publish. It was written for 0.8.0 and had not yet run on GitHub when that version was
tagged; a release page that names no image has none.

To use a published image instead of building one, pull it, then give its name to a
launcher from the kit or a checkout of the same version. The launchers only use an
image that is already on the machine:

```sh
docker pull ghcr.io/OWNER/tournevia-app:VERSION
python scripts/start_local.py --image ghcr.io/OWNER/tournevia-app:VERSION
```

The image is not signed. The routing graph is never part of it; see
[install from the extracted kit](#install-from-the-extracted-kit).

## Make the kit

From a clean, committed checkout with Python and Git installed:

```sh
python scripts/release.py pack --version 0.8.0-rc.1
```

The command creates a ZIP and SHA-256 sidecar under `deploy/runtime/releases`.
It uses Git HEAD, refuses tracked local/private paths, and never includes ignored
workspace settings, passwords, databases, backups, graph/PBF files or local planning notes.
The ZIP includes the application, tests, fixtures, migrations, build/deployment
scripts, schemas, documentation and a `release.json` source checksum manifest.
The same Git revision and version produce the same archive bytes. This manifest
detects accidental source changes; it is not a publisher signature or a trust
root.

### Sign the kit

Sign the ZIP (and, after a build, its SBOMs) with the release signer's SSH key
(OpenSSH 8.1 or later):

```sh
python scripts/release.py sign --key ~/.ssh/tournevia-release deploy/runtime/releases/tournevia-0.8.0-rc.1-REVISION.zip
```

This writes `FILE.sig` next to each file. It never creates keys and never
overwrites an existing signature. Publish the signer's public key as an
`allowed_signers` line through a channel independent of the download. No release
signing key has been published yet. Recipients verify before extracting:

```sh
ssh-keygen -Y verify -f allowed_signers -I release@example.org -n tournevia-release \
  -s tournevia-0.8.0-rc.1-REVISION.zip.sig < tournevia-0.8.0-rc.1-REVISION.zip
```

These scripts build images locally and never publish them. The image pushed by the
release workflow carries no signature; refer to it by its digest.

## Install from the extracted kit

The kit also includes `scripts/start_server.py` for operator-configured HTTPS
on a Linux VPS; see the [server launcher](README.md#https-server-launcher).
Windows uses Docker Desktop Linux containers and `python`; macOS uses Docker
Desktop and `python3`, with AMD64 emulation potentially needed on Apple Silicon;
Linux uses Docker Engine/Compose and `python3`. A complete macOS installation is
not yet validated. No native desktop installer or public npx package is supplied.

Prerequisites: Python 3.11+ on the host, Docker with Linux containers, Compose
2.24.4+ and enough disk/RAM for the regional routing graph. The current build target
is Linux AMD64, including Docker Desktop on Windows. Do not assume a weak VPS can
run graph preparation or the tested 250-delivery workload; see measured routing
resource settings in [the deployment guide](README.md).

Extract the ZIP to a new directory and run there:

```sh
python scripts/release.py verify
python scripts/release.py build
```

The build verifies the included source checksums, builds the pinned VROOM source
and application, generates dependency notices, records the resulting image ID in
`deploy/runtime/release-image.json`, and prints its exact local image
tag and a `start_local.py --image ...` command. It also writes two CycloneDX
software bills of materials to `deploy/runtime/sbom`: `TAG.image.cdx.json` lists the
Debian and Python packages in the image (the digest-pinned Syft scanner reads a
saved image archive, with no network or Docker daemon access), and
`TAG.web.cdx.json` lists the frontend's production npm packages with their lockfile
integrity hashes, selected exactly like the bundled notices. Use that printed command to create
a new workspace. For a different local port or persistent directory, append
`--port 8010 --directory /your/private/workspace` (use an appropriate Windows path
on Windows). Existing directories preserve their previous image selection.

Open the private setup shortcut printed by the launcher, then create the company,
its main depot and the first administrator; the setup guide that follows can add
fictional example data to try a calculation. No account or secret from development
is bundled.
The compiled frontend is served by the application image; Node.js is not needed
on the host. Installing and starting with the host launcher uses only Python's standard
library; the backup taken by `--upgrade-image` needs the backend runtime dependencies.

The routing graph is deliberately separate. `python scripts/prepare_routing.py`
downloads the Île-de-France extract and builds its graph with the pinned image, and
`start_local.py --routing-config region/valhalla.json` runs the router inside the
workspace and labels plans with the extract's download date; see
[first route on your computer](README.md#first-route-on-your-computer). Basemap
coverage is not proof of routing coverage. Shared access uses the HTTPS overlay and
an operator-trusted hostname/certificate; do not expose local mode publicly.

## Dependency pins and original notices

- Node, Python and Debian build bases and PostgreSQL are digest-pinned. The HTTPS
  proxy and Valhalla already use digests. VROOM's source commit is verified during
  its build; its three bundled header dependencies' license files are retained.
- `web/package-lock.json` records exact npm versions and integrity hashes.
  `backend/requirements.lock` records the exact Python runtime version closure
  alongside direct requirements. These are tested version pins, not a claim of
  fully hermetic or bit-identical image builds: apt repositories and wheel bytes
  are not snapshotted here.
- Every frontend production package's original notice is collected during the
  build into `/assets/THIRD_PARTY_NOTICES.txt`, linked in the application footer.
  `/assets/dependency-inventory.json` lists package versions and lockfile integrity.
  Packages removed by tree shaking may still be included conservatively.
- The application image keeps Python distribution notices and versions in
  `/app/notices`, the Python interpreter license there, VROOM and embedded-library
  notices under `/usr/share/doc/vroom`, and original Debian package copyright files
  under `/usr/share/doc`. Its Debian package inventory is
  `/app/notices/debian-packages.tsv`. Build tools are not included in the final image.
- The psycopg-binary wheel bundles native libraries (libpq, OpenSSL, Kerberos,
  OpenLDAP, Cyrus SASL and others) without their licence texts. The image keeps
  Debian's copyright file for each of the same libraries in
  `/app/notices/psycopg-binary-libraries` (Debian's versions differ from the bundled
  ones), and the build fails when the wheel bundles a library that has no file there.
- [NOTICE.md](../NOTICE.md#source-of-the-published-image) says where the source of
  each part of the published image is. That inventory is the maintainer's own review,
  not legal advice; whoever redistributes the image, or one built from it, carries
  the same notice and source obligations.
- OSM/Geofabrik graph data and OpenFreeMap basemap data have their own attribution
  and data terms; they are not relicensed under the application's Apache-2.0.
  See [NOTICE.md](../NOTICE.md) and the [source register](../docs/08-source-register.md).

## Installation acceptance

After building the kit, use the printed image tag with the existing disposable
installation tests. The following host-side acceptance tools additionally need
`backend/requirements-dev.txt`, and the HTTPS test needs OpenSSL:

```sh
python scripts/test_https.py --image YOUR_BUILT_IMAGE --company
python scripts/test_https.py --image YOUR_BUILT_IMAGE --recovery
```

The company check starts an empty installation, provisions a named account, loads
fictional sources through the API, runs the real road worker, edits and marks
plans READY, and verifies guarded CSV export. The recovery check uses a separate
sample installation and verifies isolated restore and interrupted-job recovery.
Both own random temporary stacks; neither resets an existing user workspace.
Record actual results for the exact image, not merely a successful build.

## Upgrade and backout

Never delete volumes or re-run first-use provisioning to upgrade an installation.
Take and verify a backup first, record the current image ID and workspace files,
then stop its API/worker before changing the image used by its setup/API/worker
services. Keep all three on the same image. Run the existing migration setup and
health/account/planning checks before allowing users back in.

For launcher-created workspaces, image selection is recorded in both
`workspace.json` and `image.json` inside the private workspace directory; changing
the launcher's `--image` alone does not upgrade an existing directory. Rerun the
launcher with `--upgrade-image NEW_IMAGE`: it backs up the workspace, stops its
API/worker, records the new and previous image in both files and starts setup
first. Otherwise follow
[database roles, upgrades and recovery](README.md#database-roles-upgrades-and-recovery).
If an upgrade fails, preserve that database for diagnosis. Restore the verified
pre-upgrade backup into a new database and start the recorded previous image
against that copy after checks. Do not assume schema downgrades or an older image
against a migrated database are safe.
