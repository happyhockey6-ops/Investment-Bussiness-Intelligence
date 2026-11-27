FROM postgres:16.16-bookworm@sha256:cd0e9518bda5f2ea667ff84d22795d089720c883f288e215d97ce57437e8ef37 AS recovery
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/trusty
COPY deploy/recovery.py .
RUN install -d -o 10001 -g 10001 /var/lib/trusty/raw
ARG TRUSTY_CODE_COMMIT
ENV TRUSTY_CODE_COMMIT=${TRUSTY_CODE_COMMIT}
USER 10001:10001

FROM python:3.13.18-slim-bookworm@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3 AS app
WORKDIR /opt/trusty
COPY README.md pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home trusty && install -d -o trusty -g trusty /var/lib/trusty/raw
COPY alembic.ini .
COPY alembic ./alembic
USER trusty
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).close()"]
CMD ["python", "-m", "ibi.api"]
