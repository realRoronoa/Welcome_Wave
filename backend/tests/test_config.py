"""Settings rules that keep runs predictable and data where the organisation decided it goes (app/config.py).

* Demo mode reads no env file: a developer's backend/.env (real credentials for other modes) never changes the demo. Only
  the command's own environment does, on purpose.
* LangSmith tracing is opt-in (LANGSMITH_TRACING=true). Off, nothing is sent, whatever other LangChain variables say. On,
  every agent run is traced and its record carries the trace id."""
import langsmith
import pytest
import requests
from fastapi.testclient import TestClient
from langchain_core.tracers.langchain import wait_for_all_tracers

from app.agents.llm import OfflineLLM
from app.config import Settings, get_settings
from app.container import build_container
from app.main import create_app

PID = "proj-demo"
SECRETS = {
    "GEMINI_API_KEY": "AIza-from-dotenv", "GITHUB_APP_ID": "123", "GITHUB_APP_SLUG": "ww-real", "GITHUB_PRIVATE_KEY": "-----BEGIN-----",
    "GITHUB_CLIENT_SECRET": "gh-secret", "GITHUB_TOKEN": "ghp-real", "GOOGLE_OAUTH_CLIENT_ID": "real.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "g-secret", "CALENDLY_WEBHOOK_SIGNING_KEY": "cal-secret", "DEMO_PASSWORD": "from-dotenv",
}


@pytest.fixture
def dotenv(tmp_path, monkeypatch):
    """A working directory whose .env holds real credentials, and a clean environment."""
    for name in [*SECRETS, "MODE", "LANGSMITH_TRACING"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    def write(**extra):
        (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in {**SECRETS, **extra}.items()))

    write()
    return write


def _credentials(s: Settings) -> dict:
    return {k: getattr(s, k.lower()) for k in SECRETS if k != "DEMO_PASSWORD"}


def test_demo_mode_reads_no_env_file(dotenv, monkeypatch):
    s = Settings()
    assert s.is_demo and set(_credentials(s).values()) == {""} and s.demo_password == "welcome-wave"
    dotenv(MODE="demo")  # saying so in the file changes nothing
    assert set(_credentials(Settings()).values()) == {""}
    other = Settings(mode="self_hosted")  # the other modes read it as before
    assert other.gemini_api_key == "AIza-from-dotenv" and other.github_app_id == "123" and other.calendly_webhook_signing_key == "cal-secret"
    dotenv(MODE="self_hosted")
    assert Settings().mode == "self_hosted" and Settings().github_token == "ghp-real"
    monkeypatch.setenv("MODE", "demo")  # the environment picks the mode first, then the file is not read
    assert set(_credentials(Settings()).values()) == {""}
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-on-purpose")  # a key for the demo comes from the command, on purpose
    assert Settings().gemini_api_key == "AIza-on-purpose" and Settings().github_app_id == ""


def test_demo_mode_ignores_the_env_files_rate_limits_and_audit_retention_too(dotenv, monkeypatch):
    from app.api.audit import AuditSettings
    from app.ratelimit import RateLimitSettings

    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)  # the test suite turns the limiter off in its own environment
    dotenv(RATE_LIMIT_ENABLED="false", RATE_LIMIT_CHAT_PER_MINUTE="1", AUDIT_RETENTION_DAYS="31", AUDIT_PURGE_EVERY_S="5")
    limits, audit = RateLimitSettings(), AuditSettings()
    assert (limits.enabled, limits.chat_per_minute) == (True, 20) and (audit.audit_retention_days, audit.audit_purge_every_s) == (365, 86_400)
    dotenv(MODE="self_hosted", RATE_LIMIT_ENABLED="false", RATE_LIMIT_CHAT_PER_MINUTE="1", AUDIT_RETENTION_DAYS="31")  # the other modes read it
    limits, audit = RateLimitSettings(), AuditSettings()
    assert (limits.enabled, limits.chat_per_minute, audit.audit_retention_days) == (False, 1, 31)
    monkeypatch.setenv("MODE", "demo")  # the environment says demo: the file is not read, as for Settings
    assert RateLimitSettings().enabled is True and AuditSettings().audit_retention_days == 365
    monkeypatch.setenv("RATE_LIMIT_CHAT_PER_MINUTE", "7")  # but the command's own environment still counts
    assert RateLimitSettings().chat_per_minute == 7


def test_the_demo_app_ignores_local_credentials(dotenv):
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            cfg = client.get("/api/config").json()
            assert (cfg["mode"], cfg["github_app"], cfg["calendly"], cfg["demo_password"]) == ("demo", False, False, "welcome-wave")
            assert isinstance(client.app.state.container.deps.llm, OfflineLLM)  # the offline model, not Gemini
    finally:
        get_settings.cache_clear()


class _LangSmith(requests.Session):
    """Stands in for api.smith.langchain.com: records what would have been sent."""

    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, str]] = []

    def send(self, request, **kwargs):
        self.sent.append((request.method, request.url))
        r = requests.Response()
        r.status_code, r._content, r.url, r.request = 200, b"{}", request.url, request
        return r


@pytest.fixture
def langsmith_api(monkeypatch):
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING"):
        monkeypatch.delenv(name, raising=False)
    session = _LangSmith()
    langsmith.configure(client=langsmith.Client(api_url="http://langsmith.invalid", api_key="test", session=session,
                                                auto_batch_tracing=False, info={}))
    yield session
    wait_for_all_tracers()
    langsmith.configure(client=None, enabled=None)


def _ask(c):
    c.handle({"type": "chat.question", "project_id": PID, "user_id": "u-vijay", "question": "Where does check_labels report missing labels?"})
    wait_for_all_tracers()
    return c.store.runs[-1]


def test_tracing_is_off_unless_the_organisation_turns_it_on(langsmith_api, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")  # an ambient LangChain variable does not turn it on
    settings = Settings(mode="demo", gemini_api_key="", agent_retries=1, retry_sleep_s=0)
    assert settings.langsmith_tracing is False
    c = build_container(settings)
    run = _ask(c)
    assert run["agent"] == "qa" and run["trace_id"] is None and "trace_id" not in run["detail"]
    assert langsmith_api.sent == []  # nothing left the process


def test_tracing_on_traces_every_agent_run_and_records_its_trace(langsmith_api, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    settings = Settings(mode="demo", gemini_api_key="", agent_retries=1, retry_sleep_s=0)
    assert settings.langsmith_tracing is True
    c = build_container(settings)
    run = _ask(c)
    assert run["agent"] == "qa" and run["trace_id"] and run["detail"]["trace_id"] == run["trace_id"]
    assert any(run["trace_id"] in url for _, url in langsmith_api.sent)  # the trace itself went to LangSmith
    # the seed's runs (ingestion, the first plan, the agents' proposals and tick) were traced too; its demo history is not a run
    assert all(r["trace_id"] for r in c.store.runs if not r["detail"].get("demo_history"))
    client_runs = c.store.list_runs(PID)
    assert client_runs[0]["trace_id"] == run["trace_id"]  # GET /api/runs carries the link
