#!/usr/bin/env bash
# Welcome Wave, self-hosted on one machine: Postgres, the API (with its background workers) and the web app.
#
# For people to use (a team server). The same images Google Cloud runs, kept running by Docker (restarted if they stop and
# after a reboot). docs/ADMIN_GUIDE.md, "Self-hosted release", has the HTTPS reverse proxy and the settings to check.
#   ./scripts/selfhost.sh setup     first time: backend/selfhost.env with fresh database passwords, and Postgres
#   ./scripts/selfhost.sh release   build both images, apply migrations, run the API and the web app; the web app listens on
#                                   127.0.0.1:8000 for your HTTPS reverse proxy. Run it again to update.
#   ./scripts/selfhost.sh status    what is running, and whether the API answers through the web app
#   ./scripts/selfhost.sh logs      follow the API's log (logs web, logs db: the others)
#   ./scripts/selfhost.sh backup    database, files, keys and settings in one owner-only file: backups/welcomewave-<time>.tar
#   ./scripts/selfhost.sh restore <file> [--replace]
#                                   put a backup back: into an empty database, or with --replace over the current data (which
#                                   is backed up first)
#   ./scripts/selfhost.sh stop      stop the web app, the API and Postgres (the data stays)
#   ./scripts/selfhost.sh check     test each integration against its real service (Gemini, Firebase, GitHub, Google OAuth client,
#                                   SMTP login, Calendly key, LangSmith): PASS or FAIL per line, no secret printed; then call each
#                                   Gemini model once
#
# For development on this machine only (the API from backend/.venv, the Vite dev server; both on localhost):
#   ./scripts/selfhost.sh start     Postgres + migrations, then the API on 127.0.0.1:8080 and the dev server on localhost:5173
#   ./scripts/selfhost.sh api       the API only (foreground)
#   ./scripts/selfhost.sh migrate   apply database migrations only
#
# Settings live in backend/selfhost.env (WW_ENV_FILE names another file). Names and ports, in the environment or in that file:
#   WW_WEB_PORT (8000) and WW_WEB_BIND (127.0.0.1) where the web app listens (0.0.0.0: every address of this machine, for a
#   reverse proxy on another one), WW_WEB_HOSTS other host names it answers to besides PUBLIC_URL's and localhost (comma-
#   separated; any other gets 421), WW_BACKUP_DIR (backups/), WW_DB_PORT (5433),
#   WW_DB_CONTAINER (ww-db), WW_DB_VOLUME (ww-db-data), WW_API_CONTAINER (ww-api), WW_WEB_CONTAINER (ww-web),
#   WW_NETWORK (ww-net), WW_IMAGE_TAG (selfhost).
set -euo pipefail
umask 077 # everything this script and the API it starts write (settings, backups, documents, recordings, keys): owner only

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
ENVF="${WW_ENV_FILE:-$BACKEND/selfhost.env}"
PY="$BACKEND/.venv/bin/python"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(24))'; }
urlenc() { printf '%s' "$1" | python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read(), safe=""))'; }
sql_literal() { local q="'"; printf "'%s'" "${1//$q/$q$q}"; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

names() {
  DB_CONTAINER="${WW_DB_CONTAINER:-ww-db}"
  DB_PORT="${WW_DB_PORT:-5433}"
  DB_VOLUME="${WW_DB_VOLUME:-ww-db-data}"
  # A tag, not a digest: Postgres runs for years on this machine and its minor releases carry security fixes.
  DB_IMAGE="${WW_DB_IMAGE:-pgvector/pgvector:pg16}"
  NET="${WW_NETWORK:-ww-net}"
  API_CONTAINER="${WW_API_CONTAINER:-ww-api}"
  WEB_CONTAINER="${WW_WEB_CONTAINER:-ww-web}"
  API_IMAGE="ww-api:${WW_IMAGE_TAG:-selfhost}"
  WEB_IMAGE="ww-web:${WW_IMAGE_TAG:-selfhost}"
  WEB_BIND="${WW_WEB_BIND:-127.0.0.1}"
  WEB_PORT="${WW_WEB_PORT:-8000}"
  BACKUPS="${WW_BACKUP_DIR:-$ROOT/backups}"
}
names

load_env() {
  [ -f "$ENVF" ] || die "$ENVF is missing: run ./scripts/selfhost.sh setup first"
  set -a; . "$ENVF"; set +a
  [ "${DB_OWNER_PASSWORD:-change-me}" != "change-me" ] || die "set DB_OWNER_PASSWORD in $ENVF"
  names # the file may set WW_* names too
}

# the variable names the settings file sets (the release passes these, by name, to the API container)
env_names() { sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$ENVF" | sort -u; }

make_env() {
  if [ -f "$ENVF" ]; then say "Keeping $ENVF"; return; fi
  local owner app
  owner="$(secret)"; app="$(secret)"
  sed -e "s|^DB_OWNER_PASSWORD=change-me|DB_OWNER_PASSWORD=$owner|" \
      -e "s|^APP_DB_PASSWORD=change-me|APP_DB_PASSWORD=$app|" \
      -e "s|ww_app:change-me@127.0.0.1:5433|ww_app:$app@127.0.0.1:$DB_PORT|" \
      "$BACKEND/selfhost.env.example" > "$ENVF" # created owner-only (umask 077)
  chmod 600 "$ENVF"
  say "Wrote $ENVF (fresh database passwords). Add your Firebase project and Gemini key there."
}

need_docker() { command -v docker >/dev/null || die "Docker is needed"; docker info >/dev/null 2>&1 || die "Docker is not running"; }
exists() { docker container inspect "$1" >/dev/null 2>&1; }
running() { [ "$(docker container inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }
ensure_network() { docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null; }
db_on_network() { # a Postgres container made before the release existed joins the release's network (it keeps its port)
  ensure_network
  docker container inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$DB_CONTAINER" \
    | tr ' ' '\n' | grep -qx "$NET" || docker network connect "$NET" "$DB_CONTAINER"
}
db_sql() { docker exec -i "$DB_CONTAINER" psql -X -q -v ON_ERROR_STOP=1 -U ww_owner "$@"; } # SQL on stdin, never in argv

db_up() {
  need_docker
  ensure_network
  if ! exists "$DB_CONTAINER"; then
    say "Creating Postgres ($DB_CONTAINER, 127.0.0.1:$DB_PORT, volume $DB_VOLUME)"
    POSTGRES_PASSWORD="$DB_OWNER_PASSWORD" docker run -d --name "$DB_CONTAINER" --restart unless-stopped --network "$NET" \
      --log-opt max-size=10m --log-opt max-file=5 \
      -e POSTGRES_USER=ww_owner -e POSTGRES_PASSWORD -e POSTGRES_DB=welcomewave \
      -p "127.0.0.1:$DB_PORT:5432" -v "$DB_VOLUME:/var/lib/postgresql/data" "$DB_IMAGE" >/dev/null
  elif ! running "$DB_CONTAINER"; then
    docker start "$DB_CONTAINER" >/dev/null
  fi
  # over TCP: a new container's first start runs a temporary server on its socket only, then restarts it; that one must
  # not be mistaken for the real server
  local up=""
  for _ in $(seq 1 90); do
    if docker exec "$DB_CONTAINER" pg_isready -h 127.0.0.1 -U ww_owner -d welcomewave >/dev/null 2>&1; then up=1; break; fi
    sleep 1
  done
  [ -n "$up" ] || die "Postgres did not start (docker logs $DB_CONTAINER)"
  # keep the owner's password in step with the settings file (inside the container the socket is trusted); sent on stdin,
  # so it never shows in the process list
  echo "ALTER ROLE ww_owner PASSWORD $(sql_literal "$DB_OWNER_PASSWORD");" | db_sql -d welcomewave >/dev/null
}

# ---------------------------------------------------------------- development (backend/.venv)
ensure_venv() {
  [ -x "$PY" ] && return
  local py=""
  for c in python3.12 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then py="$c"; break; fi
  done
  [ -n "$py" ] || die "development commands need Python 3.12 (3.11 works); ./scripts/selfhost.sh release needs only Docker"
  say "Creating backend/.venv with $("$py" --version) and the exact versions the images use"
  "$py" -m venv "$BACKEND/.venv"
  "$BACKEND/.venv/bin/pip" install -q --no-deps -r "$BACKEND/requirements.lock"
  "$BACKEND/.venv/bin/pip" check
}

migrate() {
  say "Applying database migrations"
  (cd "$BACKEND" && DATABASE_URL_OWNER="postgresql://ww_owner:$(urlenc "$DB_OWNER_PASSWORD")@127.0.0.1:$DB_PORT/welcomewave" \
     APP_DB_PASSWORD="$APP_DB_PASSWORD" "$PY" -m app.migrate)
}

api() {
  cd "$BACKEND" && exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080 --proxy-headers
}

# ---------------------------------------------------------------- release (Docker)
release_checks() {
  [ -n "${PUBLIC_URL:-}" ] || die "set PUBLIC_URL in $ENVF to the address people open, such as https://onboarding.example.com: \
invitation links, pre-boarding links, the Google sign-in return address and webhook addresses are built from it"
  case "$PUBLIC_URL" in # the API checks the same at start (ENV=prod): https, or plain http on this machine only
    http://localhost* | http://127.0.0.1* | http://\[::1\]* | https://localhost* | https://127.0.0.1*)
      warn "PUBLIC_URL is $PUBLIC_URL: invitation links open only on this machine. Set it to the HTTPS address people use." ;;
    https://*) ;;
    *) die "PUBLIC_URL must be the https:// address people open (it is $PUBLIC_URL): sign-in tokens must not cross the network \
unencrypted. Put the HTTPS reverse proxy in front (docs/ADMIN_GUIDE.md, \"Self-hosted release\")" ;;
  esac
  local port
  port="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).port or 80)' "$PUBLIC_URL" 2>/dev/null)" \
    || die "PUBLIC_URL ($PUBLIC_URL) has an invalid port"
  case "$PUBLIC_URL" in http://localhost* | http://127.0.0.1* | http://\[::1\]*)
    [ "$port" = "$WEB_PORT" ] || warn "PUBLIC_URL is $PUBLIC_URL, but the web app listens on port $WEB_PORT: links built from it \
(invitations, sign-in) would not open the app. On this machine, set PUBLIC_URL=http://localhost:$WEB_PORT." ;;
  esac
  WEB_HOSTS_RE="$(web_hosts_re)" || exit 1
  [ -z "${FIREBASE_AUTH_EMULATOR_HOST:-}" ] || die "FIREBASE_AUTH_EMULATOR_HOST is for development only: remove it from $ENVF"
  [ -n "${FIREBASE_PROJECT_ID:-}" ] && [ -n "${FIREBASE_API_KEY:-}" ] \
    || die "set FIREBASE_PROJECT_ID and FIREBASE_API_KEY in $ENVF (docs/INTEGRATIONS.md, Firebase)"
}

web_hosts_re() { # the host names the web app answers to, as the regular expression nginx checks (frontend/nginx.conf.template)
  python3 - "$PUBLIC_URL" "$WEB_BIND" "${WW_WEB_HOSTS:-}" <<'PY'
import re, sys, urllib.parse
public, bind, extra = sys.argv[1:4]
names = ["localhost", "127.0.0.1", "[::1]"]
host = urllib.parse.urlsplit(public).hostname or ""
names.append(f"[{host}]" if ":" in host else host)
if bind not in ("", "0.0.0.0", "::"):
    names.append(bind)
names += [n.strip().lower() for n in re.split(r"[,\s]+", extra) if n.strip()]
for n in names:
    if not re.fullmatch(r"[a-z0-9.-]+|\[[0-9a-f:.]+\]", n):
        sys.exit(f"error: {n!r} is not a host name (PUBLIC_URL, WW_WEB_BIND or WW_WEB_HOSTS)")
seen = list(dict.fromkeys(names))
print("|".join(n.replace(".", r"\.").replace("[", r"\[").replace("]", r"\]") for n in seen))
PY
}

host_data_dir() { # DATA_DIR as the API resolves it: relative to backend/ unless absolute
  local d="${DATA_DIR:-.data}"
  case "$d" in /*) printf '%s' "$d" ;; *) printf '%s' "$BACKEND/$d" ;; esac
}

data_owner() { # the API container runs as the owner of the data folder (never root), so files keep one owner in both modes
  local u g
  if [ "$(uname)" = Darwin ]; then u="$(stat -f %u "$1")"; g="$(stat -f %g "$1")"; else u="$(stat -c %u "$1")"; g="$(stat -c %g "$1")"; fi
  if [ "$u" = 0 ]; then
    chown -R 10001:10001 "$1" || die "$1 belongs to root: give it to the account that runs Welcome Wave"
    u=10001; g=10001
  fi
  printf '%s:%s' "$u" "$g"
}

container_db_url() { # DATABASE_URL as the API container sees Postgres: the database container by name on the network
  local u="$DATABASE_URL"
  u="${u/@127.0.0.1:$DB_PORT\//@$DB_CONTAINER:5432/}"
  u="${u/@localhost:$DB_PORT\//@$DB_CONTAINER:5432/}"
  case "$u" in *@127.0.0.1[:/]* | *@localhost[:/]*)
    die "DATABASE_URL points at this machine's own address, which the API container cannot reach: use the $DB_CONTAINER database (127.0.0.1:$DB_PORT) or a host name" ;;
  esac
  printf '%s' "$u"
}

build_images() {
  say "Building $API_IMAGE (backend/Dockerfile) and $WEB_IMAGE (frontend/Dockerfile)"
  docker build -t "$API_IMAGE" "$BACKEND"
  docker build -t "$WEB_IMAGE" "$ROOT/frontend"
}

migrate_image() {
  say "Applying database migrations"
  DATABASE_URL_OWNER="postgresql://ww_owner:$(urlenc "$DB_OWNER_PASSWORD")@$DB_CONTAINER:5432/welcomewave" \
    docker run --rm --network "$NET" -e DATABASE_URL_OWNER -e APP_DB_PASSWORD "$API_IMAGE" python -m app.migrate
}

replace_container() { # stop (gracefully: in-flight requests and jobs get 30 s) and remove
  if exists "$1"; then docker stop -t 30 "$1" >/dev/null 2>&1 || true; docker rm "$1" >/dev/null; fi
}

run_api() {
  local data user db_url trusted args=() n
  data="$(host_data_dir)"; mkdir -p "$data"
  user="$(data_owner "$data")"
  db_url="$(container_db_url)"
  # the folder must really be shared with the container (Docker Desktop shares only some folders; an unshared one would be an
  # empty folder inside Docker, and the API would fail to start, or lose what it writes)
  : > "$data/.ww-mount-check"
  docker run --rm --user "$user" -v "$data:/data" --entrypoint sh "$API_IMAGE" \
    -c 'test -f /data/.ww-mount-check && rm /data/.ww-mount-check' >/dev/null 2>&1 \
    || { rm -f "$data/.ww-mount-check"; die "Docker cannot share the data folder $data with the API container, or the API \
cannot write it. Docker Desktop: Settings > Resources > File sharing, add the folder (or set DATA_DIR to one already shared)."; }
  # Only the web container talks to the API, from this network: uvicorn takes the client's address from X-Forwarded-For when
  # the request comes from here, and the rightmost address not on this network is the one the reverse proxy saw.
  trusted="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}},{{end}}' "$NET")"; trusted="${trusted%,}"
  # every setting in the file, passed by name (the values never appear in a command line); the database passwords stay out:
  # the API connects as ww_app through DATABASE_URL
  while IFS= read -r n; do
    case "$n" in DB_OWNER_PASSWORD | APP_DB_PASSWORD | DATABASE_URL | DATA_DIR | ENV | PORT | WW_*) ;; *) args+=(-e "$n") ;; esac
  done < <(env_names)
  replace_container "$API_CONTAINER"
  DATABASE_URL="$db_url" DATA_DIR=/data ENV=prod WW_TRUSTED_PROXIES="$trusted" \
  docker run -d --name "$API_CONTAINER" --restart unless-stopped --network "$NET" --user "$user" \
    --log-opt max-size=10m --log-opt max-file=5 \
    --health-cmd 'python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8080/health\", timeout=5)"' \
    --health-interval 30s --health-timeout 10s --health-retries 3 \
    -e DATABASE_URL -e DATA_DIR -e ENV -e WW_TRUSTED_PROXIES -e PORT=8080 ${args[@]+"${args[@]}"} \
    -v "$data:/data" "$API_IMAGE" \
    sh -c 'umask 077; exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1 --proxy-headers \
             --forwarded-allow-ips="$WW_TRUSTED_PROXIES" --timeout-graceful-shutdown 20' >/dev/null
}

run_web() {
  replace_container "$WEB_CONTAINER"
  docker run -d --name "$WEB_CONTAINER" --restart unless-stopped --network "$NET" \
    --log-opt max-size=10m --log-opt max-file=5 \
    -e PORT=8080 -e API_URL="http://$API_CONTAINER:8080" -e WEB_HOSTS="$WEB_HOSTS_RE" \
    -p "$WEB_BIND:$WEB_PORT:8080" "$WEB_IMAGE" >/dev/null
}

web_url() { case "$WEB_BIND" in 0.0.0.0 | "") printf 'http://127.0.0.1:%s' "$WEB_PORT" ;; *) printf 'http://%s:%s' "$WEB_BIND" "$WEB_PORT" ;; esac; }

wait_ready() { # the API answers, through the web app, with its database
  local url; url="$(web_url)/health/ready"
  for _ in $(seq 1 90); do
    curl -fsS -o /dev/null "$url" 2>/dev/null && return 0
    if exists "$API_CONTAINER" && [ "$(docker container inspect -f '{{.RestartCount}}' "$API_CONTAINER")" -gt 2 ]; then break; fi
    sleep 1
  done
  docker logs --tail 40 "$API_CONTAINER" >&2 || true
  die "the API did not become ready at $url (its last log lines are above)"
}

stop_release() { # remembered, so a restore can start what was running
  STOPPED=""
  for c in "$WEB_CONTAINER" "$API_CONTAINER"; do
    if running "$c"; then docker stop -t 30 "$c" >/dev/null; STOPPED="$c $STOPPED"; fi
  done
}

# ---------------------------------------------------------------- backup and restore
tar_data() { # GNU tar exits 1 when a file changed while it was read (the API was writing it): the archive is still good
  local rc=0
  tar -C "$1" -czf "$2" . || rc=$?
  [ "$rc" -eq 0 ] || { [ "$rc" -eq 1 ] && tar --version 2>/dev/null | grep -q GNU; } || die "could not archive $1"
}

backup() {
  load_env; db_up
  mkdir -p "$BACKUPS"; chmod 700 "$BACKUPS"
  local stamp out data level last
  stamp="$(date +%Y%m%d-%H%M%S)"
  out="$BACKUPS/welcomewave-$stamp.tar"
  WORK="$(mktemp -d "$BACKUPS/.partial-$stamp.XXXXXX")"
  trap 'rm -rf "$WORK"' EXIT
  # 1. the database, as one consistent snapshot (roles are not in it: a restore creates them)
  docker exec "$DB_CONTAINER" pg_dump -U ww_owner -d welcomewave | gzip > "$WORK/database.sql.gz"
  last="$(gzip -dc "$WORK/database.sql.gz" | tail -n 12)" # pg_dump's last words (newer versions add an \unrestrict line)
  case "$last" in *"PostgreSQL database dump complete"*) ;; *) die "the database dump is incomplete" ;; esac
  level="$(echo "SELECT max(version) FROM schema_migrations;" | db_sql -tA -d welcomewave 2>/dev/null)" || level=none
  # 2. files and keys: documents, recordings, .token-key (it decrypts the stored Google tokens) and .signing-key
  data="$(host_data_dir)"
  if [ -d "$data" ]; then tar_data "$data" "$WORK/files.tar.gz"; else mkdir "$WORK/empty"; tar_data "$WORK/empty" "$WORK/files.tar.gz"; fi
  # 3. the settings: database passwords, TOKEN_ENCRYPTION_KEY or BLOB_SIGNING_KEY when they are set, and the integrations
  cp "$ENVF" "$WORK/selfhost.env"
  printf 'welcome-wave backup 1\ncreated %s\nmigrations %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" > "$WORK/MANIFEST"
  (cd "$WORK" && sha256 MANIFEST database.sql.gz files.tar.gz selfhost.env > SHA256SUMS)
  tar -C "$WORK" -cf "$out.partial" SHA256SUMS MANIFEST database.sql.gz files.tar.gz selfhost.env
  mv "$out.partial" "$out"
  rm -rf "$WORK"; trap - EXIT
  say "Saved $out"
  echo "It holds every organisation's data and the keys and passwords in $ENVF: keep copies off this machine, as safely."
}

env_value() { # a value from a settings file, read as text (a backup's file is not run)
  sed -nE "s/^[[:space:]]*(export[[:space:]]+)?$1=//p" "$2" | tail -n 1 | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

restore() {
  local file="${1:-}" mode="${2:-}" data stamp tables owners bad c
  [ -n "$file" ] || die "usage: ./scripts/selfhost.sh restore <backup .tar> [--replace]"
  [ -f "$file" ] || die "no such file: $file"
  case "$mode" in "" | --replace) ;; *) die "unknown option $mode (only --replace)" ;; esac
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT
  tar -C "$WORK" -xf "$file" SHA256SUMS MANIFEST database.sql.gz files.tar.gz selfhost.env 2>/dev/null \
    || die "$file is not a Welcome Wave backup (made by ./scripts/selfhost.sh backup)"
  (cd "$WORK" && sha256 -c SHA256SUMS >/dev/null) || die "$file is damaged: its checksums do not match"
  say "Restoring $(sed -n 's/^created //p' "$WORK/MANIFEST") (migrations up to $(sed -n 's/^migrations //p' "$WORK/MANIFEST"))"
  need_docker

  if [ ! -f "$ENVF" ]; then
    cp "$WORK/selfhost.env" "$ENVF"; chmod 600 "$ENVF"
    say "Restored the settings to $ENVF"
  fi
  load_env
  for c in TOKEN_ENCRYPTION_KEY BLOB_SIGNING_KEY; do
    [ "$(env_value "$c" "$WORK/selfhost.env")" = "$(env_value "$c" "$ENVF")" ] \
      || warn "$c in $ENVF differs from the backup's: $( [ "$c" = TOKEN_ENCRYPTION_KEY ] && echo "stored Google Calendar connections will not decrypt (connect again, or use the backup's value)" || echo "links to recordings made before stop working (harmless)")"
  done

  db_up
  tables="$(echo "SELECT count(*) FROM pg_tables WHERE schemaname = 'public';" | db_sql -tA -d welcomewave)"
  [ "$tables" = 0 ] || [ "$mode" = --replace ] \
    || die "the database already holds data ($tables tables). To overwrite it: restore $file --replace (the current data is backed up first)"
  stop_release # the API stays stopped while its database and files are replaced
  trap 'rm -rf "$WORK"; echo "The restore did not finish, and the app is stopped. Fix the cause and run the restore again \
(with --replace, the data it was replacing was saved in $BACKUPS first)." >&2' EXIT
  if [ "$tables" != 0 ]; then
    say "Backing up the current data first"
    WW_ENV_FILE="$ENVF" bash "$0" backup
    echo "DROP DATABASE welcomewave WITH (FORCE); CREATE DATABASE welcomewave OWNER ww_owner;" | db_sql -d postgres
  fi

  # pg_dump leaves roles out, yet the dump gives the cross-organisation functions to ww_definer and grants the data to ww_app:
  # without these two roles those lines would fail, and the functions would stay with the restoring superuser
  db_sql -d welcomewave >/dev/null <<'SQL'
SET client_min_messages TO warning;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ww_definer') THEN CREATE ROLE ww_definer NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ww_app') THEN
    CREATE ROLE ww_app LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END $$;
GRANT ww_definer TO CURRENT_USER;
SQL
  say "Loading the database"
  gzip -dc "$WORK/database.sql.gz" | db_sql --single-transaction -d welcomewave >/dev/null

  data="$(host_data_dir)"
  stamp="$(date +%Y%m%d-%H%M%S)"
  if [ -d "$data" ] && [ -n "$(ls -A "$data")" ]; then
    mv "$data" "$data.before-restore-$stamp"
    say "Moved the current files to $data.before-restore-$stamp"
  fi
  mkdir -p "$data"
  tar -C "$data" -xzf "$WORK/files.tar.gz"

  # a backup from an older release is brought up to date; the migrator also sets ww_app's password and checks the roles
  if docker image inspect "$API_IMAGE" >/dev/null 2>&1; then db_on_network; migrate_image
  elif [ -x "$PY" ]; then migrate
  else echo "Migrations run with the next ./scripts/selfhost.sh release (or start)."
  fi

  owners="$(echo "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  WHERE n.nspname = 'public' AND p.prosecdef AND pg_get_userbyid(p.proowner) = 'ww_definer';" | db_sql -tA -d welcomewave)"
  bad="$(echo "SELECT string_agg(p.oid::regprocedure::text, ', ') FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE n.nspname = 'public' AND p.prosecdef AND pg_get_userbyid(p.proowner) <> 'ww_definer';" | db_sql -tA -d welcomewave)"
  [ -z "$bad" ] || die "these SECURITY DEFINER functions are not owned by ww_definer: $bad"
  for c in $STOPPED; do docker start "$c" >/dev/null; done
  case "$STOPPED" in *"$WEB_CONTAINER"*) wait_ready ;; esac
  rm -rf "$WORK"; trap - EXIT
  say "Restored: $(echo "SELECT count(*) FROM organizations;" | db_sql -tA -d welcomewave) organisation(s), $(echo "SELECT count(*) FROM users;" | db_sql -tA -d welcomewave) people, $owners cross-organisation functions owned by ww_definer"
  [ -n "$STOPPED" ] && echo "Started again: $STOPPED"
  return 0
}

# ---------------------------------------------------------------- commands
main() {
case "${1:-}" in
  setup)
    make_env; load_env; db_up
    say "Ready. Fill in $ENVF (Firebase and Gemini at least: docs/INTEGRATIONS.md), then:"
    echo "  ./scripts/selfhost.sh release   for people to use (docs/ADMIN_GUIDE.md, \"Self-hosted release\")"
    echo "  ./scripts/selfhost.sh start     to develop on this machine" ;;
  release)
    load_env; release_checks; need_docker
    build_images
    db_up; db_on_network
    migrate_image
    say "Starting the API ($API_CONTAINER) and the web app ($WEB_CONTAINER)"
    run_api
    run_web
    wait_ready
    say "Welcome Wave is running: $(web_url) (PUBLIC_URL $PUBLIC_URL)"
    echo "Docker restarts both if they stop, and after a reboot. Next, if not done yet (docs/ADMIN_GUIDE.md, \"Self-hosted release\"):"
    echo "  - an HTTPS reverse proxy for $PUBLIC_URL that forwards to $(web_url)"
    echo "  - Firebase > Authentication > Settings > Authorized domains: add the host name of PUBLIC_URL"
    echo "  - ./scripts/selfhost.sh backup on a schedule, with copies kept off this machine" ;;
  status)
    [ -f "$ENVF" ] && load_env
    need_docker
    docker ps -a --filter "name=^${DB_CONTAINER}\$" --filter "name=^${API_CONTAINER}\$" --filter "name=^${WEB_CONTAINER}\$" \
      --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    if exists "$WEB_CONTAINER"; then
      if curl -fsS -o /dev/null "$(web_url)/health/ready" 2>/dev/null; then echo "ready: the API answers through $(web_url), with its database"
      else echo "not ready: $(web_url)/health/ready does not answer (./scripts/selfhost.sh logs)"; exit 1; fi
    fi ;;
  logs)
    need_docker
    case "${2:-api}" in api) c="$API_CONTAINER" ;; web) c="$WEB_CONTAINER" ;; db) c="$DB_CONTAINER" ;; *) die "logs api|web|db" ;; esac
    exec docker logs -f --tail 200 "$c" ;;
  migrate) load_env; ensure_venv; db_up; migrate ;;
  api)     load_env; ensure_venv; db_up; migrate; api ;;
  start)
    load_env; ensure_venv; db_up; migrate
    (cd "$BACKEND" && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080 --proxy-headers) &
    API_PID=$!
    trap 'kill $API_PID 2>/dev/null' EXIT INT TERM
    say "Development: the API on http://127.0.0.1:8080, the Vite dev server on http://localhost:5173 (this machine only)."
    echo "For people to use, run ./scripts/selfhost.sh release instead: never expose the dev server."
    cd "$ROOT/frontend" && npx vite --port 5173 ;;
  check)
    load_env
    if exists "$API_CONTAINER" && running "$API_CONTAINER"; then # the release: test with the settings the API runs with
      rc=0
      docker exec "$API_CONTAINER" python -m scripts.check_integrations || rc=1
      if [ -n "${GEMINI_API_KEY:-}" ]; then echo; say "Gemini models"; docker exec "$API_CONTAINER" python -m scripts.check_models || rc=1; fi
      exit $rc
    fi
    ensure_venv; cd "$BACKEND"
    rc=0
    "$PY" -m scripts.check_integrations || rc=1   # reads the settings from the environment; prints no secret
    if [ -n "${GEMINI_API_KEY:-}" ]; then
      echo; say "Gemini models"; "$PY" -m scripts.check_models || rc=1
    fi
    exit $rc ;;
  stop)
    need_docker
    for c in "$WEB_CONTAINER" "$API_CONTAINER" "$DB_CONTAINER"; do
      if running "$c"; then docker stop -t 30 "$c" >/dev/null; echo "stopped $c"; fi
    done
    say "Stopped; the data stays (database volume $DB_VOLUME, files in the data folder). Start again with release or start." ;;
  backup)  backup ;;
  restore) restore "${2:-}" "${3:-}" ;;
  *) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 2 ;;
esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
