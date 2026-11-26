# One image for the Cloud Run API, the Cloud Run migration job and the three GKE workers (only the command differs):
#   API      (default)  uvicorn app.main:app
#   migrate             python -m app.migrate
#   workers             python -m workers.worker   (WORKER_QUEUE=meetings|ingestion|reviews)
FROM python:3.12-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./
RUN python -m venv /venv && /venv/bin/pip install -r requirements.lock

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/venv/bin:$PATH
WORKDIR /app
COPY --from=deps /venv /venv
COPY app ./app
COPY workers ./workers
COPY db ./db
COPY eval ./eval
COPY scripts ./scripts
COPY sample_repo ./sample_repo
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin ww
USER 10001

# Cloud Run sets $PORT and scales instances (not processes): one worker per container, ~40 requests on its threadpool.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 20"]
