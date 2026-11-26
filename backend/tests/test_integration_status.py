"""Is each integration set up, and does it work (app/services/integration_status.py): GET /api/integrations/status for admins and
`./scripts/selfhost.sh check` (scripts/check_integrations.py). Every real call goes through a mock transport; no secret ever appears
in what is returned or printed."""
from __future__ import annotations

import json
import smtplib

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.services import integration_status as I
from scripts import check_integrations

ADMIN, MANAGER, EXPERT, HIRE, EXEC = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-ravi", "u-vijay", "u-exec"))
SECRETS = {"gemini_api_key": "AQ.secret-gemini-key", "firebase_api_key": "AIza-secret-firebase", "github_token": "github_pat_secret",
           "google_oauth_client_secret": "GOCSPX-secret", "calendly_webhook_signing_key": "cal-secret-key", "smtp_password": "smtp-secret-pw"}
ENV_SECRETS = {"LANGSMITH_API_KEY": "lsv2_secret-langsmith"}


class Services:
    """Answers each service the way it would, and records which were called."""

    def __init__(self, **status):
        self.calls, self.status = [], status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls.append(host)
        code = self.status.get(host, 200)
        if host == "generativelanguage.googleapis.com":
            assert request.headers["x-goog-api-key"] == SECRETS["gemini_api_key"] and "key" not in request.url.params  # a header, not the address
            return httpx.Response(code, json={"models": [{"name": "models/a"}, {"name": "models/b"}]} if code == 200 else {"error": {"message": "API key not valid"}})
        if host == "identitytoolkit.googleapis.com":
            body = json.loads(request.content)
            assert body["identifier"].endswith("@example.invalid")  # an address that cannot exist
            return httpx.Response(code, json={"registered": False} if code == 200 else {"error": {"status": "INVALID_ARGUMENT"}})
        if host == "api.github.com":
            return httpx.Response(code, json={"login": "someone"}, headers={"x-ratelimit-limit": "5000"})
        if host == "api.smith.langchain.com":
            return httpx.Response(code, json=[])
        return httpx.Response(404)


def configured(**extra) -> Settings:
    return Settings(mode="self_hosted", _env_file=None, firebase_project_id="ww-test", firebase_api_key=SECRETS["firebase_api_key"],
                    gemini_api_key=SECRETS["gemini_api_key"], github_token=SECRETS["github_token"],
                    google_oauth_client_id="123.apps.googleusercontent.com", google_oauth_client_secret=SECRETS["google_oauth_client_secret"],
                    calendly_webhook_signing_key=SECRETS["calendly_webhook_signing_key"], smtp_host="smtp.example.com", smtp_user="me@example.com",
                    smtp_password=SECRETS["smtp_password"], public_url="https://ww.example.com", langsmith_tracing=True, **extra)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for k, v in ENV_SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    I.clear_cache()
    yield
    I.TRANSPORT = None
    I.clear_cache()


@pytest.fixture
def services():
    svc = Services()
    I.TRANSPORT = httpx.MockTransport(svc)
    return svc


class FakeSMTP:
    fail = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def ehlo(self): ...
    def starttls(self, context=None): ...

    def login(self, user, password):
        if FakeSMTP.fail:
            raise smtplib.SMTPAuthenticationError(535, b"nope")

    def quit(self): ...


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.fail = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def by_key(items):
    return {i["key"]: i for i in items}


# ---------------------------------------------------------------- the checks
def test_everything_configured_and_working(services, smtp):
    items = by_key(I.check_all(configured()))
    assert list(items) == list(I.KEYS) == ["gemini", "firebase", "github", "google_calendar", "calendly", "smtp", "langsmith"]
    assert [items[k]["ok"] for k in ("gemini", "firebase", "github", "smtp", "langsmith")] == [True] * 5
    assert items["gemini"]["detail"] == "The key is accepted; 2 models listed." and "5000 API calls" in items["github"]["detail"]
    assert "smtp.example.com:587" in items["smtp"]["detail"] and "no message was sent" in items["smtp"]["detail"]
    # presence only, so "not tested" rather than "working": an OAuth client needs a person to sign in, a signing key a real booking
    assert items["google_calendar"]["configured"] and items["google_calendar"]["ok"] is None
    assert "https://ww.example.com/api/integrations/google/callback" in items["google_calendar"]["detail"]
    assert items["calendly"]["configured"] and items["calendly"]["ok"] is None and "/webhooks/calendly" in items["calendly"]["detail"]
    assert set(services.calls) == {"generativelanguage.googleapis.com", "identitytoolkit.googleapis.com", "api.github.com", "api.smith.langchain.com"}


def test_nothing_is_called_for_what_is_not_set_up(services):
    items = I.check_all(Settings(mode="self_hosted", _env_file=None), live=True)
    assert not services.calls and all(not i["configured"] and i["ok"] is None for i in items if i["key"] != "langsmith")
    assert all(set(i) == {"key", "name", "configured", "ok", "detail"} and i["detail"] for i in items)


def test_a_refused_key_is_a_failure_with_a_plain_reason_and_no_secret(services, smtp):
    services.status = {"generativelanguage.googleapis.com": 400, "identitytoolkit.googleapis.com": 400, "api.github.com": 401,
                       "api.smith.langchain.com": 403}
    smtp.fail = True
    items = by_key(I.check_all(configured()))
    assert all(items[k]["configured"] and items[k]["ok"] is False for k in ("gemini", "firebase", "github", "smtp", "langsmith"))
    assert "HTTP 400" in items["gemini"]["detail"] and "aistudio.google.com" in items["gemini"]["detail"]
    assert "FIREBASE_API_KEY" in items["firebase"]["detail"] and "HTTP 401" in items["github"]["detail"]
    assert "user name or password" in items["smtp"]["detail"] and "EU region" in items["langsmith"]["detail"]
    everything = json.dumps(items)
    assert not [v for v in [*SECRETS.values(), *ENV_SECRETS.values()] if v in everything]


def test_an_unreachable_service_reports_the_error_type_not_the_address(monkeypatch):
    def down(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")  # the address carries the Firebase key

    I.TRANSPORT = httpx.MockTransport(down)
    items = by_key(I.check_all(configured(), live=True))
    assert items["firebase"]["ok"] is False and "ConnectError" in items["firebase"]["detail"]
    assert SECRETS["firebase_api_key"] not in json.dumps(items)

    def slow(request):
        raise httpx.ReadTimeout("slow")

    I.TRANSPORT = httpx.MockTransport(slow)
    assert "did not answer in time" in by_key(I.check_all(configured()))["gemini"]["detail"]


def test_one_check_that_blows_up_does_not_hide_the_others(services, monkeypatch):
    monkeypatch.setattr(I, "github", lambda s, live: 1 / 0)
    items = by_key(I.check_all(configured()))
    assert items["github"]["ok"] is False and "ZeroDivisionError" in items["github"]["detail"] and items["gemini"]["ok"] is True


def test_misconfigurations_are_named():
    half = by_key(I.check_all(Settings(mode="self_hosted", _env_file=None, google_oauth_client_id="x.apps.googleusercontent.com", smtp_host="h"),
                              live=False))
    assert half["google_calendar"]["ok"] is False and "GOOGLE_OAUTH_CLIENT_SECRET is missing" in half["google_calendar"]["detail"]
    assert half["smtp"]["configured"] is False and "SMTP_FROM" in half["smtp"]["detail"]
    wrong = by_key(I.check_all(Settings(mode="self_hosted", _env_file=None, google_oauth_client_id="client-123", google_oauth_client_secret="s"),
                               live=False))
    assert wrong["google_calendar"]["ok"] is False and "googleusercontent.com" in wrong["google_calendar"]["detail"]
    off = I.langsmith(Settings(mode="self_hosted", _env_file=None, langsmith_tracing=False), True)
    assert off["configured"] is False and "unused" in off["detail"]  # a key is set (the fixture) but tracing is off


def test_tracing_on_without_a_key_fails(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    item = I.langsmith(Settings(mode="self_hosted", _env_file=None, langsmith_tracing=True), True)
    assert item["configured"] is False and item["ok"] is False and "no LANGSMITH_API_KEY" in item["detail"]


# ---------------------------------------------------------------- GET /api/integrations/status
@pytest.fixture
def client(monkeypatch):
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def test_only_admins_read_the_status(client):
    assert client.get("/api/integrations/status").status_code == 401
    for who in (MANAGER, EXPERT, HIRE, EXEC):
        assert client.get("/api/integrations/status", headers=who).status_code == 403
    r = client.get("/api/integrations/status", headers=ADMIN)
    assert r.status_code == 200 and set(r.json()) == {"items", "checked_at"}


def test_the_demo_reports_nothing_set_up_and_calls_nobody(client, services):
    items = client.get("/api/integrations/status", headers=ADMIN).json()["items"]
    assert [i["key"] for i in items] == list(I.KEYS) and not services.calls
    assert not any(i["configured"] for i in items) and all(i["ok"] is None for i in items)
    assert "offline model" in by_key(items)["gemini"]["detail"] and "built-in calendar" in by_key(items)["google_calendar"]["detail"]


def test_the_status_of_a_configured_server_has_no_secret_and_is_cached_for_five_minutes(client, services, smtp, monkeypatch):
    c = client.app.state.container
    c.settings = configured().model_copy(update={"mode": "demo"})  # a demo server whose command line carried the keys
    first = client.get("/api/integrations/status", headers=ADMIN)
    assert first.status_code == 200
    items = by_key(first.json()["items"])
    assert items["gemini"]["ok"] is True and items["github"]["ok"] is True and items["smtp"]["ok"] is True
    assert not [v for v in [*SECRETS.values(), *ENV_SECRETS.values()] if v in first.text]
    made = len(services.calls)
    assert made == 3  # Gemini, GitHub, LangSmith (a demo server has no Firebase: its own sign-in page)
    again = client.get("/api/integrations/status", headers=ADMIN)
    assert len(services.calls) == made and again.json()["checked_at"] == first.json()["checked_at"]  # the cache answered
    client.get("/api/integrations/status", params={"refresh": "true"}, headers=ADMIN)
    assert len(services.calls) == made * 2  # a refresh calls them again
    now = I.time.time()
    monkeypatch.setattr(I.time, "time", lambda: now + I.CACHE_S + 1)  # five minutes later it re-checks by itself
    client.get("/api/integrations/status", headers=ADMIN)
    assert len(services.calls) == made * 3


def test_google_calendar_says_whether_the_organisation_connected_an_account(client, monkeypatch):
    s = configured()
    assert "No organisation account is connected" in I.google_calendar(s, False)["detail"]
    connected = I.google_calendar(s, True)
    assert "An organisation account is connected" in connected["detail"] and connected["ok"] is None
    seen = []
    real = I.check_all
    monkeypatch.setattr(I, "check_all", lambda settings, live=True, google_connected=None: seen.append(google_connected) or real(settings, live, google_connected))
    c = client.app.state.container
    c.settings = s.model_copy(update={"mode": "demo"})
    client.get("/api/integrations/status", headers=ADMIN, params={"refresh": "true"})
    c.store.save_calendar_connection("org-demo", "google", "kt@example.com", "ENC", "calendar", "u-admin")
    r = client.get("/api/integrations/status", headers=ADMIN, params={"refresh": "true"})
    assert seen == [False, True] and "kt@example.com" not in r.text  # the route tells it, and the address is never shown


# ---------------------------------------------------------------- ./scripts/selfhost.sh check
def test_the_check_script_prints_pass_or_fail_per_integration_and_no_secret(services, smtp, monkeypatch, capsys):
    monkeypatch.setattr(check_integrations, "get_settings", lambda: configured())
    services.status = {"api.github.com": 401}
    assert check_integrations.main() == 1  # one configured integration failed
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert any(l.startswith("  PASS  Gemini") for l in lines) and any(l.startswith("  FAIL  GitHub") for l in lines)
    assert any(l.startswith("  set   Google Calendar") for l in lines) and any(l.startswith("  PASS  Email (SMTP)") for l in lines)
    assert "1 failed: GitHub" in out
    assert not [v for v in [*SECRETS.values(), *ENV_SECRETS.values()] if v in out]


def test_the_check_script_succeeds_when_nothing_configured_fails(services, smtp, monkeypatch, capsys):
    monkeypatch.setattr(check_integrations, "get_settings", lambda: configured())
    assert check_integrations.main() == 0 and "Nothing configured is failing." in capsys.readouterr().out
    monkeypatch.setattr(check_integrations, "get_settings", lambda: Settings(mode="self_hosted", _env_file=None))
    assert check_integrations.main() == 0 and "skip" in capsys.readouterr().out
