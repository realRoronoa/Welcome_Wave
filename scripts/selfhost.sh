#!/usr/bin/env bash
# Welcome Wave, self-hosted on one machine: Postgres in Docker, the API (with its background workers) and the web app.
#
#   ./scripts/selfhost.sh setup     first time: backend/selfhost.env with fresh passwords, Postgres, migrations
#   ./scripts/selfhost.sh start     Postgres (if stopped) + migrations, then the API on :8080 and the web app on :5173
#   ./scripts/selfhost.sh api       the API only (foreground)
#   ./scripts/selfhost.sh migrate   apply database migrations only
#   ./scripts/selfhost.sh stop      stop Postgres (the data stays in the Docker volume ww-db-data)
#   ./scripts/selfhost.sh check     test each integration against its real service (Gemini, Firebase, GitHub, Google OAuth client,
#                                   SMTP login, Calendly key, LangSmith): PASS or FAIL per line, no secret printed; then call each
#                                   Gemini model once (after adding or changing a key)
#   ./scripts/selfhost.sh backup    dump the database to backups/welcomewave-<time>.sql.gz
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
ENVF="$BACKEND/selfhost.env"
DB_CONTAINER="${WW_DB_CONTAINER:-ww-db}"
DB_PORT="${WW_DB_PORT:-5433}"
DB_VOLUME="${WW_DB_VOLUME:-ww-db-data}"
PY="$BACKEND/.venv/bin/python"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(24))'; }

load_env() {
  [ -f "$ENVF" ] || die "backend/selfhost.env is missing: run ./scripts/selfhost.sh setup first"
  set -a; . "$ENVF"; set +a
  [ "${DB_OWNER_PASSWORD:-change-me}" != "change-me" ] || die "set DB_OWNER_PASSWORD in backend/selfhost.env"
}

make_env() {
  if [ -f "$ENVF" ]; then say "Keeping backend/selfhost.env"; return; fi
  local owner app
  owner="$(secret)"; app="$(secret)"
  sed -e "s|^DB_OWNER_PASSWORD=change-me|DB_OWNER_PASSWORD=$owner|" \
      -e "s|^APP_DB_PASSWORD=change-me|APP_DB_PASSWORD=$app|" \
      -e "s|ww_app:change-me@127.0.0.1:5433|ww_app:$app@127.0.0.1:$DB_PORT|" \
      "$BACKEND/selfhost.env.example" > "$ENVF"
  chmod 600 "$ENVF"
  say "Wrote backend/selfhost.env (fresh database passwords). Add your Firebase project and Gemini key there."
}

db_up() {
  command -v docker >/dev/null || die "Docker is needed for the database"
  if ! docker container inspect "$DB_CONTAINER" >/dev/null 2>&1; then
    say "Creating Postgres ($DB_CONTAINER, 127.0.0.1:$DB_PORT, volume $DB_VOLUME)"
    docker run -d --name "$DB_CONTAINER" --restart unless-stopped \
      -e POSTGRES_USER=ww_owner -e POSTGRES_PASSWORD="$DB_OWNER_PASSWORD" -e POSTGRES_DB=welcomewave \
      -p "127.0.0.1:$DB_PORT:5432" -v "$DB_VOLUME:/var/lib/postgresql/data" pgvector/pgvector:pg16 >/dev/null
  elif [ "$(docker container inspect -f '{{.State.Running}}' "$DB_CONTAINER")" != "true" ]; then
    docker start "$DB_CONTAINER" >/dev/null
  fi
  for _ in $(seq 1 60); do
    docker exec "$DB_CONTAINER" pg_isready -U ww_owner -d welcomewave >/dev/null 2>&1 && break
    sleep 1
  done
  docker exec "$DB_CONTAINER" pg_isready -U ww_owner -d welcomewave >/dev/null 2>&1 || die "Postgres did not start"
  # keep the owner's password in step with selfhost.env (inside the container the socket is trusted)
  docker exec "$DB_CONTAINER" psql -q -U ww_owner -d welcomewave -c "ALTER ROLE ww_owner PASSWORD '$DB_OWNER_PASSWORD'" >/dev/null
}

migrate() {
  say "Applying database migrations"
  (cd "$BACKEND" && DATABASE_URL_OWNER="postgresql://ww_owner:$DB_OWNER_PASSWORD@127.0.0.1:$DB_PORT/welcomewave" \
     APP_DB_PASSWORD="$APP_DB_PASSWORD" "$PY" -m app.migrate)
}

api() {
  cd "$BACKEND" && exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080 --proxy-headers
}

case "${1:-}" in
  setup)   make_env; load_env; db_up; migrate; say "Ready. Fill in backend/selfhost.env, then: ./scripts/selfhost.sh start" ;;
  migrate) load_env; db_up; migrate ;;
  api)     load_env; db_up; migrate; api ;;
  start)
    load_env; db_up; migrate
    (cd "$BACKEND" && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080 --proxy-headers) &
    API_PID=$!
    trap 'kill $API_PID 2>/dev/null' EXIT INT TERM
    say "API on http://127.0.0.1:8080 · web app on http://localhost:5173"
    cd "$ROOT/frontend" && npx vite --port 5173 ;;
  check)
    load_env; cd "$BACKEND"
    rc=0
    "$PY" -m scripts.check_integrations || rc=1   # reads the settings from the environment; prints no secret
    if [ -n "${GEMINI_API_KEY:-}" ]; then
      echo; say "Gemini models"; "$PY" -m scripts.check_models || rc=1
    fi
    exit $rc ;;
  stop)    docker stop "$DB_CONTAINER" >/dev/null && say "Postgres stopped; data kept in $DB_VOLUME" ;;
  backup)
    load_env; db_up; mkdir -p "$ROOT/backups"
    out="$ROOT/backups/welcomewave-$(date +%Y%m%d-%H%M%S).sql.gz"
    docker exec "$DB_CONTAINER" pg_dump -U ww_owner -d welcomewave | gzip > "$out" && say "Saved $out" ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
