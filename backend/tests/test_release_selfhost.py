"""Release round, self-hosted release and supply chain: the web server's configuration (frontend/nginx.conf.template), the
self-hosted script's pure parts (scripts/selfhost.sh, sourced: its commands need Docker, which CI's docker job runs), what
scripts/package.sh refuses to share, the images' pins, the locked versions, and the CI gates. Nothing here starts a container."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "frontend/nginx.conf.template").read_text()
SELFHOST = ROOT / "scripts/selfhost.sh"


def sh(code: str, **env: str) -> subprocess.CompletedProcess:
    """Run bash with scripts/selfhost.sh sourced (its commands run only when executed, not when sourced)."""
    full = {k: v for k, v in os.environ.items() if not k.startswith("WW_")} | {"WW_ENV_FILE": "/nonexistent"} | env
    return subprocess.run(["bash", "-c", f'source "{SELFHOST}"; {code}'], capture_output=True, text=True, env=full, timeout=30)


# ---------------------------------------------------------------- web server (nginx.conf.template)
def csp() -> dict[str, list[str]]:
    parts = " ".join(re.findall(r'set \$csp "(?:\$csp )?([^"]*)";', TEMPLATE))
    out = {}
    for d in filter(None, (p.strip() for p in parts.split(";"))):
        name, *values = d.split()
        out[name] = values
    return out


def test_csp_allows_only_what_the_app_uses():
    policy = csp()
    assert policy["default-src"] == ["'self'"]
    assert "https://storage.googleapis.com" in policy["connect-src"]  # the signed direct upload of a recording on Google Cloud
    for domain in ("agora.io", "sd-rtn.com", "rtnsvc.com", "rtesvc.com"):
        assert f"https://*.{domain}:*" in policy["connect-src"]
        assert f"wss://*.{domain}:*" in policy["connect-src"]  # Agora selects non-default WebSocket ports
    assert not {"'unsafe-inline'", "'unsafe-eval'"} & set(policy["script-src"])
    assert "'wasm-unsafe-eval'" in policy["script-src"]  # Agora audio compiles WebAssembly without allowing JS eval
    assert policy["worker-src"] == ["'self'", "blob:"]  # Agora audio uses same-origin blob workers
    assert policy["frame-ancestors"] == ["'none'"] and policy["object-src"] == ["'none'"] and policy["base-uri"] == ["'none'"]


def test_template_uses_only_variables_the_image_or_the_release_sets():
    # The image's entrypoint substitutes only variables that are set, so one nobody sets would reach nginx as `${NAME}`.
    upper = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", TEMPLATE))
    assert upper == {"PORT", "API_URL", "NGINX_LOCAL_RESOLVERS", "WEB_HOSTS"}
    docker = (ROOT / "frontend/Dockerfile").read_text()
    assert re.search(r"ENV PORT=8080 NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 WEB_HOSTS=\"\.\*\"", docker)
    assert "-e API_URL=" in SELFHOST.read_text() and "-e WEB_HOSTS=" in SELFHOST.read_text()
    assert "API_URL" in (ROOT / "docker-compose.yml").read_text()


def test_host_check_comes_before_any_location():
    server = TEMPLATE[TEMPLATE.index("server {"):]
    assert server.index("if ($ww_host_ok = 0) { return 421; }") < server.index("location ")
    assert '"~^(${WEB_HOSTS})$" 1;' in TEMPLATE


@pytest.mark.parametrize("uri, logged", [
    ("/welcome/tok123", "/welcome/[redacted]"),
    ("/api/preboarding/tok123/acknowledge", "/api/preboarding/[redacted]/acknowledge"),
    ("/api/blobs/tok123", "/api/blobs/[redacted]"),
    ("/api/integrations/google/callback?code=c&state=s", "/api/integrations/google/callback?[redacted]"),
    ("/github/callback?code=c&state=s&installation_id=1", "/github/callback?[redacted]"),
    ("/api/projects/p1", "/api/projects/p1"),
])
def test_access_log_redacts_credentials_in_addresses(uri, logged):
    block = TEMPLATE[TEMPLATE.index("map $request_uri $ww_safe_uri {"):]
    block = block[:block.index("}\n")]
    for pattern, out in re.findall(r'^\s*~(\S+)\s+"([^"]+)";', block, re.M):
        m = re.match(pattern.replace("(?<", "(?P<"), uri)
        if m:
            assert re.sub(r"\$\{(\w+)\}", lambda g: m.group(g.group(1)), out) == logged
            return
    assert uri == logged


def test_compressed_cached_and_streamed_as_documented():
    assert "gzip_static on;" in TEMPLATE and "gzip on;" in TEMPLATE and "gzip_proxied any;" in TEMPLATE
    assert re.search(r"location = /api/live \{[^}]*proxy_buffering off;", TEMPLATE)
    assert re.search(r"recording\$ \{[^}]*proxy_request_buffering off;[^}]*client_max_body_size 2100m;", TEMPLATE)
    assert re.search(r"recording/import\$ \{[^}]*proxy_read_timeout 600s;", TEMPLATE)
    assert re.search(r'location = /index.html \{[^}]*Cache-Control "no-cache" always;', TEMPLATE)


# ---------------------------------------------------------------- scripts/selfhost.sh
def test_selfhost_script_parses_and_writes_owner_only():
    assert subprocess.run(["bash", "-n", str(SELFHOST)]).returncode == 0
    text = SELFHOST.read_text()
    assert re.search(r"^umask 077", text, re.M)
    assert "ALTER ROLE ww_owner PASSWORD $(sql_literal" in text and "| db_sql" in text  # the password goes on stdin
    # development only: the API and the Vite dev server stay on this machine
    start = text[text.index("  start)"):text.index("  check)")]
    assert "--host 127.0.0.1" in start and "npx vite --port 5173" in start and "--host 0" not in start


@pytest.mark.parametrize("public, extra, bind, ok, refused", [
    ("https://onboarding.example.com", "", "127.0.0.1",
     ["onboarding.example.com", "localhost", "127.0.0.1", "[::1]"], ["evil.example", "onboarding.example.com.evil.example",
                                                                     "onboardingxexample.com"]),
    ("https://wave.example.com:8443", "wave.lan, Intranet-Host", "10.0.0.5",
     ["wave.example.com", "wave.lan", "intranet-host", "10.0.0.5"], ["wave", "10.0.0.50"]),
])
def test_web_answers_to_public_url_and_named_hosts_only(public, extra, bind, ok, refused):
    r = sh("web_hosts_re", PUBLIC_URL=public, WW_WEB_HOSTS=extra, WW_WEB_BIND=bind)
    assert r.returncode == 0, r.stderr
    allowed = re.compile(f"^({r.stdout.strip()})$")  # as nginx's map applies it to $host (lower case, no port)
    assert all(allowed.match(h) for h in ok)
    assert not any(allowed.match(h) for h in refused)


def test_a_host_list_that_is_not_host_names_is_refused():
    r = sh("web_hosts_re", PUBLIC_URL="https://ok.example.com", WW_WEB_HOSTS="a.example|.*")
    assert r.returncode != 0 and "not a host name" in r.stderr


@pytest.mark.parametrize("public, fails, says", [
    ("http://onboarding.example.com", True, "must be the https:// address"),
    ("", True, "set PUBLIC_URL"),
    ("https://onboarding.example.com", False, ""),
    ("http://localhost:8000", False, "open only on this machine"),
    ("http://localhost:5173", False, "listens on port 8000"),
])
def test_release_checks_public_url(public, fails, says):
    r = sh("release_checks && echo passed", PUBLIC_URL=public, FIREBASE_PROJECT_ID="p", FIREBASE_API_KEY="k")
    assert ("passed" not in r.stdout) == fails
    assert says in r.stderr


def test_release_refuses_the_auth_emulator():
    r = sh("release_checks && echo passed", PUBLIC_URL="https://a.example.com", FIREBASE_PROJECT_ID="p",
           FIREBASE_API_KEY="k", FIREBASE_AUTH_EMULATOR_HOST="127.0.0.1:9099")
    assert "passed" not in r.stdout and "development only" in r.stderr


def test_the_api_container_reaches_the_database_container_by_name():
    r = sh("container_db_url", DATABASE_URL="postgresql+psycopg://ww_app:x@127.0.0.1:5433/welcomewave", WW_DB_CONTAINER="db1")
    assert r.stdout == "postgresql+psycopg://ww_app:x@db1:5432/welcomewave"
    r = sh("container_db_url", DATABASE_URL="postgresql+psycopg://ww_app:x@db.internal:5432/welcomewave")
    assert r.stdout == "postgresql+psycopg://ww_app:x@db.internal:5432/welcomewave"
    r = sh("container_db_url", DATABASE_URL="postgresql+psycopg://ww_app:x@127.0.0.1:6543/welcomewave")
    assert r.returncode != 0 and "cannot reach" in r.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="runs the bash release script")
def test_backup_settings_are_read_as_text_never_run(tmp_path):
    f = tmp_path / "selfhost.env"
    f.write_text('A=1\nTOKEN_ENCRYPTION_KEY="abc def"   # note\nexport BLOB_SIGNING_KEY=\'q1\'\nX=$(touch pwned)\n')
    r = sh(f'echo "[$(env_value TOKEN_ENCRYPTION_KEY {f})][$(env_value BLOB_SIGNING_KEY {f})][$(env_value NONE {f})]"')
    assert r.stdout.strip() == "[abc def][q1][]"
    assert not (Path.cwd() / "pwned").exists()


def test_restore_keeps_the_definer_functions_owner():
    text = SELFHOST.read_text()
    restore = text[text.index("restore() {"):]
    # the roles exist before the dump loads, the load stops at the first error, and a function left to another owner fails it
    assert restore.index("CREATE ROLE ww_definer NOLOGIN") < restore.index('database.sql.gz" | db_sql --single-transaction')
    assert "ON_ERROR_STOP=1" in text
    assert "pg_get_userbyid(p.proowner) <> 'ww_definer'" in restore and "are not owned by ww_definer" in restore
    assert "--replace" in restore and 'bash "$0" backup' in restore  # data being replaced is backed up first


# ---------------------------------------------------------------- scripts/package.sh
@pytest.mark.parametrize("path, left_out", [
    ("backend/selfhost.env", True), ("backend/.env", True), ("backend/.env.example", False), ("key.pem", True),
    ("backups/welcomewave-1.tar", True), ("dump.sql.gz", True), ("x.dump", True), ("notes/dump.sql", True),
    ("backend/db/migrations/001_init.sql", False), ("infra/terraform/terraform.tfstate", True),
    ("infra/terraform/terraform.tfstate.backup", True), ("infra/terraform/prod.tfvars", True),
    ("infra/terraform/prod.tfvars.example", False), ("infra/terraform/.terraform/x", True),
    ("backend/.data/.token-key", True), ("backend/.data.before-restore-20261118/doc.pdf", True),
    ("frontend/src/App.tsx", False),
])
def test_package_never_shares_secrets_or_data(path, left_out):
    text = (ROOT / "scripts/package.sh").read_text()
    fn = text[text.index("excluded() {"):text.index("\n}\n", text.index("excluded() {")) + 3]
    r = subprocess.run(["bash", "-c", f'{fn}\nexcluded "$1"', "_", path], capture_output=True, text=True)
    assert (r.returncode == 0) == left_out


# ---------------------------------------------------------------- images, locked versions, CI
def test_images_are_pinned_and_run_unprivileged():
    api, web = (ROOT / "backend/Dockerfile").read_text(), (ROOT / "frontend/Dockerfile").read_text()
    for text in (api, web):
        froms = re.findall(r"^FROM (\S+)", text, re.M)
        assert froms and all("@sha256:" in f for f in froms), froms
    assert re.search(r"^FROM nginx:1\.30-alpine@", web, re.M) and "USER nginx" in web
    assert "USER 10001" in api and "pip uninstall" in api and "--no-deps -r requirements.lock" in api
    assert "chown 10001:10001 /app/.blobs /app/.data /data" in api  # demo uploads under docker compose, and the release's data


def lock() -> dict[str, str]:
    pins = {}
    for line in (ROOT / "backend/requirements.lock").read_text().splitlines():
        if line and not line.startswith("#"):
            name, _, version = line.partition("==")
            assert version, f"not an exact pin: {line}"
            pins[name.lower()] = version
    return pins


def vtuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else x for x in re.findall(r"\d+|[a-z]+", v))


@pytest.mark.parametrize("name, least", [
    ("pypdf", "6.21.4"), ("certifi", "2026.10.18"), ("pyasn1", "0.6.5"), ("dnspython", "2.9.2"),
    ("opentelemetry-api", "1.45.2"), ("opentelemetry-sdk", "1.45.2"),
])
def test_locked_versions_carry_the_security_fixes(name, least):
    assert vtuple(lock()[name]) >= vtuple(least)


def test_lock_is_complete_without_test_tool_leftovers():
    pins = lock()
    assert "msgpack" in pins  # CacheControl (firebase-admin) needs it; --no-deps would otherwise leave it out
    assert not {"locust", "gevent", "flask", "greenlet", "zope.interface", "zope.event", "websocket-client"} & set(pins)
    dev = (ROOT / "backend/requirements-dev.txt").read_text()
    assert all("==" in line for line in dev.splitlines() if line and not line.startswith(("#", "-r")))


def test_npm_packages_come_from_the_public_registry():
    resolved = re.findall(r'"resolved": "([^"]+)"', (ROOT / "frontend/package-lock.json").read_text())
    assert resolved and all(u.startswith("https://registry.npmjs.org/") for u in resolved)
    assert "registry=https://registry.npmjs.org/" in (ROOT / "frontend/.npmrc").read_text()


def test_ci_fails_on_vulnerabilities_and_lint():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = {s.get("name", ""): s for job in ci["jobs"].values() for s in job["steps"]}
    pip_audit = next(s for n, s in steps.items() if n.startswith("Python packages in the API image"))
    npm_audit = next(s for n, s in steps.items() if n.startswith("npm packages shipped to browsers"))
    for step in (pip_audit, npm_audit):
        assert not step.get("continue-on-error")
    assert "pip-audit==" in pip_audit["run"] and "--audit-level" not in npm_audit["run"]  # judged per advisory in the script
    assert "ruff check" in steps["Lint"]["run"]
    uses = [s["uses"] for job in ci["jobs"].values() for s in job["steps"] if "uses" in s]
    assert uses and all(re.search(r"@[0-9a-f]{40}$", u) for u in uses)  # actions pinned to a commit
    assert (ROOT / ".github/dependabot.yml").exists()
