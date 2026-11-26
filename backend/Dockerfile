# The API image (the web app's is frontend/Dockerfile). One image for every Python process, wherever it runs: Cloud Run
# (the API and the migration job), Kubernetes (infra/k8s: the API, the migration Job, the three workers and the scheduled
# calls), the self-hosted release (scripts/selfhost.sh release) and docker compose (the demo). Only the command differs:
#   API      (default)  uvicorn app.main:app on $PORT (8080)
#   migrate             python -m app.migrate
#   workers             python -m workers.worker   (WORKER_QUEUE=meetings|ingestion|reviews; health on :8081/healthz)
#   build    docker build -t welcome-wave-api backend    (labels: --build-arg VERSION=v1.2.3 --build-arg REVISION=$(git rev-parse HEAD))
# Two stages: the locked dependencies are installed into a venv in the first, and only that venv and the code reach the
# image people run, as user 10001 (never root), with no pip, no compiler and no build cache. It runs with a read-only root
# filesystem: it writes only to /tmp, /data and, in the demo, /app/.blobs and /app/.data.
# The base image is pinned by digest, so a rebuild gets exactly what was tested; .github/dependabot.yml proposes updates.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
WORKDIR /app
COPY requirements.lock ./
# Exactly the locked versions: --no-deps resolves nothing at build time, and pip check fails the build if the lock misses one.
# The venv gets no pip of its own.
RUN python -m venv --without-pip /venv \
 && pip --python /venv/bin/python install --no-deps -r requirements.lock \
 && pip --python /venv/bin/python check

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/venv/bin:$PATH
WORKDIR /app
# nothing is installed at run time: the image keeps no pip to patch
RUN PIP_ROOT_USER_ACTION=ignore python -m pip uninstall -q -y pip
COPY --from=deps /venv /venv
COPY app ./app
COPY workers ./workers
COPY db ./db
COPY eval ./eval
COPY scripts ./scripts
COPY sample_repo ./sample_repo
# The user the API runs as, and the folders it writes to: demo mode's files (.blobs) and keys (.data) under /app, and /data,
# the data folder of a self-hosted release (which mounts the operator's DATA_DIR there).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin ww \
 && mkdir -p /app/.blobs /app/.data /data \
 && chown 10001:10001 /app/.blobs /app/.data /data
USER 10001
EXPOSE 8080

# Docker (compose, the self-hosted release) marks the container unhealthy when the API stops answering. Kubernetes and Cloud
# Run ignore this and use their own probes (infra/k8s/base/api.yaml). The workers and the migration run on Kubernetes or
# as one-off containers, where it does not apply.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8080'), timeout=4)"]

# Cloud Run sets $PORT and scales instances (not processes): one worker per container, ~40 requests on its threadpool.
# X-Forwarded-For is trusted only from FORWARDED_ALLOW_IPS (uvicorn's default: 127.0.0.1), never from anyone: with '*' the
# leftmost entry, which the client writes, became the address the rate limits charge. On Cloud Run the limits read the
# entries Google's front ends appended instead (RATE_LIMIT_TRUSTED_HOPS, set by cloudbuild.yaml; app/ratelimit.py).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --proxy-headers --timeout-graceful-shutdown 20"]

# Last, so a new version or commit changes only this layer
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Welcome Wave API" \
      org.opencontainers.image.description="Welcome Wave's API, agents, workers and database migrations" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
