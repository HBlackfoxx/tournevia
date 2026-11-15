FROM node:26-bookworm-slim@sha256:5df79989ed685e09f0a98019db4cebfb22fb9df1a53c0161664a011f7ff6b0f1 AS web
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
COPY scripts/frontend_notices.mjs /build/scripts/frontend_notices.mjs
COPY backend/tests/fixtures/paris-ten.json /build/backend/tests/fixtures/paris-ten.json
RUN npm run build

FROM tournevia-vroom:1.14.0 AS solver

# The psycopg-binary wheel bundles native libraries without their licence texts. Keep Debian's
# copyright file for each of the same libraries; scripts/runtime_notices.py fails the build when
# the wheel bundles one that has none here. Debian's versions differ from the bundled ones.
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS bundled-notices
WORKDIR /work
RUN apt-get update     && apt-get download libpq5 libssl3 libldap-2.5-0 libsasl2-2 libkrb5-3 libkeyutils1 libcom-err2 libcrypt1 libpcre3 libselinux1     && mkdir /notices     && for deb in *.deb; do name="${deb%%_*}"; mkdir "$name" && dpkg-deb -x "$deb" "$name"        && cp "$name/usr/share/doc/$name/copyright" "/notices/$name.copyright" || exit 1; done

FROM python:3.14-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56 AS runtime
ARG VCS_REF=development
LABEL org.opencontainers.image.title="Tournevia Community"       org.opencontainers.image.source="https://github.com/HBlackfoxx/tournevia"       org.opencontainers.image.revision=$VCS_REF org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip check
# Runtime code only; tests, benchmarks and development requirements are added in engine-test.
COPY backend/alembic.ini ./
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/tests/fixtures/paris-ten.json ./tests/fixtures/paris-ten.json
COPY examples/depot.json /app/examples/depot.json
COPY --from=solver /usr/local/bin/vroom /usr/local/bin/vroom
COPY --from=solver /usr/share/doc/vroom/ /usr/share/doc/vroom/
COPY --from=web /build/web/dist /app/web/dist
COPY scripts/runtime_notices.py /tmp/runtime_notices.py
COPY LICENSE NOTICE.md /app/notices/
COPY --from=bundled-notices /notices/ /app/notices/psycopg-binary-libraries/
RUN python /tmp/runtime_notices.py /app/notices && rm /tmp/runtime_notices.py
RUN useradd --uid 10001 --create-home app
USER 10001
CMD ["python", "-m", "app.main"]

FROM runtime AS engine-test
USER root
COPY backend/requirements-dev.txt backend/pytest.ini ./
COPY backend/tests ./tests
COPY backend/benchmarks ./benchmarks
RUN pip install --no-cache-dir -r requirements-dev.txt
USER 10001
# This image exists to run the real-engine suite; without the flag it would skip everything.
ENV RUN_ENGINE_TESTS=1
CMD ["python", "-m", "pytest", "-q", "-m", "engines", "--basetemp", "/tmp/vrp-tests"]

FROM runtime AS app
