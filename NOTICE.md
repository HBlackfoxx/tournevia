# Notices

**Tournevia Community — Copyright 2026 Mohamed Boukri. Licensed under Apache-2.0.**
[LICENSE](LICENSE) contains the unchanged licence text; the bracketed fields in its
appendix are the licence's own example, not project information. The licence covers
material its copyright holder can license. It grants no rights in material owned by
others, nor in the Tournevia name (Apache-2.0 section 6). Security reports go through
the private channel in [SECURITY.md](SECURITY.md).

Third-party software, data and services keep their own licences and terms, listed
below. Each licence was read from the package's own metadata or licence file on
12 November 2026, unless marked otherwise.

## Data and services

| Source | Used for | Licence and attribution |
|---|---|---|
| OpenStreetMap, through Geofabrik extracts | Routing graphs and every route's geometry | © OpenStreetMap contributors, Open Database License (ODbL) 1.0. The map always credits OpenStreetMap for the routes, even when an operator replaces `MAP_STYLE_URL`. Graphs are built locally and are not part of this repository; publishing a graph brings the ODbL's obligations. Route sheets and CSV exports carry no credit: add one if you publish them. |
| OpenFreeMap Liberty (default `MAP_STYLE_URL`) | Basemap in the browser | The map shows the style's attribution: "OpenFreeMap © OpenMapTiles Data from OpenStreetMap". Keep it visible, in screenshots too. OpenFreeMap's terms apply to the service. |
| Base Adresse Nationale (optional) | Address lookup through the installation's own geocoder or IGN Géoplateforme | Licence Ouverte 2.0 (Etalab), which requires naming the source and the data's date: the interface shows `GEOCODER_DATASET` (set it to the bundle's date), or the IGN attribution for the public service. |

Sources: [S6, S9, S17–S19](docs/08-source-register.md).

## Software in the application image

| Component | Version | Licence |
|---|---|---|
| CPython | 3.14 | PSF-2.0 |
| FastAPI, pydantic, pydantic-core, pydantic-settings, SQLAlchemy, Alembic, Mako, anyio, h11, argon2-cffi, argon2-cffi-bindings, annotated-doc, annotated-types, typing-inspection | as locked in `backend/requirements.lock` | MIT |
| Starlette, uvicorn, click, idna, MarkupSafe, python-dotenv, pycparser | as locked | BSD-3-Clause |
| greenlet | as locked | MIT AND PSF-2.0 |
| typing_extensions | as locked | PSF-2.0 |
| cffi | as locked | MIT-0 |
| tzdata, opentelemetry-api | as locked | Apache-2.0 |
| **psycopg, psycopg-binary** | 3.3.6 | **LGPL-3.0-only**: used unmodified and loaded dynamically; its licence text ships in the image |
| VROOM | 1.14.0 (commit `1fd711bc`) | BSD-2-Clause, © 2016 Julien Coupey; its bundled cxxopts, polylineencoder and RapidJSON are MIT |
| libstdc++, libgcc | Debian bookworm 12.2.0 | GPL-3.0 with the GCC Runtime Library Exception |
| Debian base packages | `python:3.14-slim-bookworm`, pinned by digest | Various; each package's copyright file is in the image |

## Third-party source in this repository

| File | From | Licence |
|---|---|---|
| [deploy/valhalla-graph.lua](deploy/valhalla-graph.lua) | Valhalla's `lua/graph.lua` at commit `0baa5c68`, with three lines changed ([why](deploy/README.md#weight-limits-tagged-maxweightrating)) | MIT, © 2018 Valhalla contributors, © 2015-2017 Mapillary AB, Mapzen; the licence text is at the top of the file |

It is used when a routing graph is built and is not part of the application image.

## Software in the browser application

| Component | Version | Licence |
|---|---|---|
| React, React DOM, scheduler | 19.3.1, 19.3.1, 0.28.0 | MIT |
| MapLibre GL JS | 6.11.0 | BSD-3-Clause |
| **Geist font** (`@fontsource-variable/geist`) | 5.3.0 | **SIL Open Font License 1.1**, © 2024 The Geist Project Authors. The font files ship with the interface; the font may not be sold on its own. |
| MapLibre's dependencies | 24 packages | ISC (8), MIT (11), BSD-2-Clause (2), BSD-3-Clause (2), MIT OR Apache-2.0 (1) |

## Engines and services the operator runs

These images are pulled by their pinned digests from their publishers; they are not
part of this repository or its source kits.

| Component | Pin | Licence |
|---|---|---|
| Valhalla | 3.9.0, [engines.json](deploy/engines.json) | MIT, © Valhalla contributors. Its image also contains GEOS (LGPL-2.1+), libspatialite (MPL-1.1/GPL-2+/LGPL-2.1+) and ZeroMQ (MPL-2.0); prime_server has no licence file in the image (not verified). |
| nginx (HTTPS overlays) | 1.30.5-alpine | BSD-2-Clause |
| PostgreSQL | 16-alpine | Not verified: the image contains no licence file |
| addok (optional geocoder) | 1.0.3 | WTFPL; its addok-france and addok-sqlite-store plugins are MIT. The same image contains Unidecode (GPL-2.0-or-later) and ngram (LGPL-3.0); python-geohash has no licence metadata (not verified). |
| Redis (optional geocoder index) | 7.0.5 | Not verified: the image contains no licence file |

## Where the licence texts are kept

- The interface's footer links `/assets/THIRD_PARTY_NOTICES.txt`, which the build
  collects from every production npm package, with `/assets/dependency-inventory.json`.
- The application image keeps the Python packages' licence files and inventory in
  `/app/notices`, with the Python licence and `debian-packages.tsv`; VROOM's licence and
  its bundled libraries' licences in `/usr/share/doc/vroom`; and each Debian package's
  copyright file in `/usr/share/doc`. See the
  [release guide](deploy/RELEASE.md#dependency-pins-and-original-notices).
- The Linux psycopg-binary wheel bundles native libraries (libpq, OpenSSL, Kerberos,
  OpenLDAP, Cyrus SASL, keyutils, libcom_err, libxcrypt, PCRE and libselinux) without
  their licence texts. The application image keeps Debian's copyright file for each of
  the same libraries in `/app/notices/psycopg-binary-libraries`; Debian's versions
  differ from the bundled ones. The image build fails when the wheel bundles a library
  that has no file there.

## Source of the published image

The release workflow publishes the application image only; the engine images above
are pulled from their own publishers. Reviewed on 15 November 2026 by the maintainer,
which is not legal advice. The image contains, each under its own licence:

| Part | Where its source is |
|---|---|
| Tournevia (Apache-2.0) | This repository at the revision in the image's `org.opencontainers.image.revision` label, and the release's source kit. |
| VROOM (BSD-2-Clause) | Built unmodified from the pinned commit by [Dockerfile.vroom](deploy/Dockerfile.vroom). |
| Python packages, including psycopg (LGPL-3.0-only) | Installed unmodified from PyPI at the versions and hashes in `backend/requirements.lock`; `/app/notices/python-inventory.json` lists them. psycopg is loaded dynamically and can be replaced by another build of the same version. |
| Native libraries inside the psycopg-binary wheel | Built by the psycopg project. The wheel's own bill of materials (`psycopg_binary-*.dist-info/sboms`) names the CentOS 7 packages some of them come from. Some parts are under the LGPL (keyutils) or the GPL family, as their copyright files say. Not verified further. |
| Debian packages of the base image | `python:3.14-slim-bookworm`, pinned by digest in the [Dockerfile](Dockerfile). Several are under the GPL or LGPL. `/app/notices/debian-packages.tsv` lists each package and exact version; Debian publishes the source of every version (`apt-get source PACKAGE=VERSION`, or snapshot.debian.org). |
