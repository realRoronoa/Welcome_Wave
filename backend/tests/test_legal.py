"""Legal acceptances (app/legal.py, app/api/legal.py, db/migrations/019_legal.sql; docs/specs/legal-consent.md).

The versions here and in the web app's documents are the same; the operator's details come from settings and are never made
up; each person is asked for the current terms, privacy policy and recording notice, once per version; sign-up and joining
by invitation record the acceptance in the same request and refuse without it; an admin accepts the data-processing
agreement for the organisation and can download the record; everything is audited; erasure keeps the record by id, export
includes it and deleting the organisation removes it; LEGAL_ENFORCE refuses the workspace, and only the workspace."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import legal
from app.config import Settings, get_settings
from app.main import create_app
from app.ports import User
from scripts import check_integrations

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS_TS = ROOT / "frontend" / "src" / "legal" / "documents.ts"
ORG, PID = "org-demo", "proj-demo"
ADMIN, MANAGER, EXPERT, HIRE, EXEC = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-ravi", "u-vijay", "u-exec"))
NEW = {"X-Demo-User": "u-new"}
BOTH = ["terms", "privacy"]
TOKENS = {
    "tok-founder": {"uid": "fb-founder", "email": "founder@acme.dev", "email_verified": True, "name": "Fatima"},
    "tok-hire": {"uid": "fb-hire", "email": "hire@acme.dev", "email_verified": True, "name": "Hari"},
    "tok-other": {"uid": "fb-other", "email": "other@acme.dev", "email_verified": True, "name": "Omar"},
}


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(autouse=True)
def fresh():
    legal._satisfied.forget()
    get_settings.cache_clear()
    yield
    legal._satisfied.forget()
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def prod(client, monkeypatch):
    """The production sign-in path (Firebase tokens, stubbed), data in memory: as tests/test_auth_flow.py."""
    def verify(token, *_a, **_k):
        if token not in TOKENS:
            raise ValueError("bad token")
        return TOKENS[token]

    import firebase_admin.auth

    monkeypatch.setattr(firebase_admin.auth, "verify_id_token", verify)
    monkeypatch.setattr("app.firebase_tokens.verify", verify)
    client.app.state.container.settings.mode = "gcp"
    yield client
    client.app.state.container.settings.mode = "demo"


def _c(client):
    return client.app.state.container


def _newcomer(client, role: str = "expert") -> User:
    """Someone who was a member before the documents existed: an account with no acceptance at all."""
    return _c(client).store.add_user(User(id="u-new", org_id=ORG, email="new@demo.dev", name="Nadia (New)", role=role), PID)


def _audits(client, action: str) -> list[dict]:
    return [a for a in _c(client).store.audit_rows if a["action"] == action]


def _code(r) -> str | None:
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


# ---------------------------------------------------------------- versions: one table, in two places
def test_the_versions_are_the_ones_the_web_app_shows():
    """backend/app/legal.py VERSIONS and frontend/src/legal/documents.ts VERSIONS are the same table. A person is shown one
    and the server records the other, so a difference is a recorded acceptance of a text nobody saw."""
    if not DOCUMENTS_TS.exists():
        pytest.skip("frontend/src/legal/documents.ts is not written yet")
    source = DOCUMENTS_TS.read_text(encoding="utf-8")
    block = re.search(r"export\s+const\s+VERSIONS\b[^=]*=\s*\{(.*?)\}", source, re.DOTALL)
    assert block, "documents.ts exports VERSIONS = { terms, privacy, dpa, recording, subprocessors }"
    shown = dict(re.findall(r"""["']?(\w+)["']?\s*:\s*["'](\d{4}-\d{2}-\d{2})["']""", block.group(1)))
    assert shown == legal.VERSIONS, "change a document's version in BOTH frontend/src/legal/documents.ts and backend/app/legal.py"


def test_every_version_is_a_date_and_every_document_has_a_role():
    assert set(legal.VERSIONS) == {"terms", "privacy", "dpa", "recording", "subprocessors"}
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in legal.VERSIONS.values())
    assert set(legal.REQUIRED) <= set(legal.PERSONAL) and not set(legal.PERSONAL) & set(legal.ORGANISATION)
    assert set(legal.PERSONAL) | set(legal.ORGANISATION) | {"subprocessors"} == set(legal.VERSIONS)
    assert set(legal.TITLES) == set(legal.VERSIONS)


# ---------------------------------------------------------------- the operator's details: settings, never made up
def test_config_shows_no_operator_until_one_is_set(client):
    block = client.get("/api/config").json()["legal"]
    assert block.pop("deployment")["hosting"] == "demo"
    assert block == {"operator": None, "address": None, "contact_email": None, "privacy_email": None, "governing_law": None,
                     "courts": None, "versions": legal.VERSIONS, "enforced": False}


# ---------------------------------------------------------------- how this server is set up: which branch of the text applies
def test_the_demo_says_it_is_a_demo_and_names_no_provider(client):
    d = client.get("/api/config").json()["legal"]["deployment"]
    assert d == {"hosting": "demo", "region": None, "ai": "offline", "ai_location": None, "email": False, "tracing": False,
                 "tracing_region": None, "recording_retention_days": None, "audit_retention_days": 365, "verified_email": True}


def test_deployment_facts_follow_the_settings(monkeypatch):
    from app.api.audit import audit_settings
    from app.services.recordings import recording_settings

    gcp = legal.deployment(Settings(mode="gcp", _env_file=None, gcp_region="europe-west1", vertex_location="europe-west4"))
    assert (gcp["hosting"], gcp["region"], gcp["ai"], gcp["ai_location"]) == ("google_cloud", "europe-west1", "vertex_ai", "europe-west4")
    assert gcp["recording_retention_days"] is None  # the bucket's own rule, which this process cannot see
    # a Gemini API key wins over Vertex AI in either mode (agents/core/llm.py client): the page must not say "Vertex AI" then
    keyed = legal.deployment(Settings(mode="gcp", _env_file=None, gemini_api_key="k"))
    assert (keyed["ai"], keyed["ai_location"]) == ("gemini_api", None)

    bare = legal.deployment(Settings(mode="self_hosted", _env_file=None))
    assert (bare["hosting"], bare["region"], bare["ai"], bare["email"], bare["tracing"]) == ("single_server", None, "none", False, False)
    assert bare["recording_retention_days"] == 0 and bare["audit_retention_days"] == 365

    monkeypatch.setenv("RECORDING_RETENTION_DAYS", "45")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "7")  # below the floor: 30 days are kept whatever this says
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://eu.api.smith.langchain.com")
    recording_settings.cache_clear()
    audit_settings.cache_clear()
    try:
        full = legal.deployment(Settings(mode="self_hosted", _env_file=None, gemini_api_key="k", smtp_host="smtp.wave.test",
                                         smtp_from="hello@wave.test", langsmith_tracing=True, require_verified_email=False))
    finally:
        monkeypatch.delenv("RECORDING_RETENTION_DAYS")
        monkeypatch.delenv("AUDIT_RETENTION_DAYS")
        recording_settings.cache_clear()
        audit_settings.cache_clear()
    assert (full["ai"], full["email"], full["tracing"], full["tracing_region"]) == ("gemini_api", True, True, "eu")
    assert (full["recording_retention_days"], full["audit_retention_days"], full["verified_email"]) == (45, 30, False)


def test_the_check_warns_when_a_setting_adds_a_sub_processor():
    quiet = legal.deployment_report(Settings(mode="gcp", _env_file=None, vertex_location="asia-south1"))
    assert quiet == []
    loud = {(n["env"], n["level"]) for n in legal.deployment_report(Settings(
        mode="gcp", _env_file=None, gemini_api_key="k", smtp_host="smtp.wave.test", smtp_from="hello@wave.test", langsmith_tracing=True))}
    assert {("LANGSMITH_TRACING", "warn"), ("SMTP_HOST", "warn"), ("GEMINI_API_KEY", "warn")} <= loud
    text = " ".join(n["detail"] for n in legal.deployment_report(Settings(mode="self_hosted", _env_file=None, langsmith_tracing=True)))
    assert "30 days" in text and "sub-processor" in text and "RECORDING_RETENTION_DAYS" not in text  # the env is its own column
    assert any(n["env"] == "RECORDING_RETENTION_DAYS" for n in legal.deployment_report(Settings(mode="self_hosted", _env_file=None)))


def test_config_shows_the_details_that_are_set(monkeypatch):
    for k, v in {"LEGAL_OPERATOR_NAME": "  Example Wave   Private Limited ", "LEGAL_OPERATOR_ADDRESS": "1 Test Street,\n Testville",
                 "LEGAL_CONTACT_EMAIL": "legal@wave.test", "LEGAL_PRIVACY_EMAIL": "privacy@wave.test",
                 "LEGAL_GOVERNING_LAW": "the laws of Testland", "LEGAL_COURTS": "the courts of Testville"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        block = c.get("/api/config").json()["legal"]
    assert block["operator"] == "Example Wave Private Limited" and block["address"] == "1 Test Street, Testville"  # one clean line each
    assert (block["contact_email"], block["privacy_email"]) == ("legal@wave.test", "privacy@wave.test")
    assert (block["governing_law"], block["courts"]) == ("the laws of Testland", "the courts of Testville")


@pytest.mark.parametrize("value", ["[COMPANY]", "<your company>", "{{operator}}", "TODO", "tbd", "unused", "changeme", "change-me", "XXXX",
                                   "Your Company Name", "   ", "n/a",
                                   # what the documentation's own examples and common template leftovers look like
                                   "…", "...", "the laws of …", "the courts of ...", "-", "?", "REPLACE_ME", "placeholder", "Company Name",
                                   "COMPANY NAME HERE", "{{ operator }} Ltd", "Insert name", "fill in"])
def test_a_placeholder_is_never_shown_as_the_operator(value):
    s = Settings(mode="demo", _env_file=None, legal_operator_name=value, legal_courts=value)
    assert legal.details(s)["operator"] is None and legal.details(s)["courts"] is None


@pytest.mark.parametrize("value", ["mailto:x@y.co", "x@y", "a b@c.co", "…@wave.test"])
def test_a_contact_address_that_is_a_link_or_a_placeholder_is_not_shown(value):
    assert legal.details(Settings(mode="demo", _env_file=None, legal_privacy_email=value))["privacy_email"] is None


def test_a_real_looking_value_with_a_dot_or_the_word_example_is_kept():
    s = Settings(mode="demo", _env_file=None, legal_operator_name="Example Wave Private Ltd.", legal_governing_law="the laws of India")
    assert legal.details(s)["operator"] == "Example Wave Private Ltd." and legal.details(s)["governing_law"] == "the laws of India"


def test_a_contact_address_that_is_not_an_email_is_not_shown():
    s = Settings(mode="demo", _env_file=None, legal_contact_email="call us", legal_privacy_email="privacy@wave.test")
    assert legal.details(s)["contact_email"] is None and legal.details(s)["privacy_email"] == "privacy@wave.test"


def test_no_legal_setting_is_needed_to_start():
    """The founders' running release keeps starting: an empty LEGAL_* setting is never a production problem."""
    assert Settings(mode="demo", _env_file=None).production_problems() == []
    bare = Settings(mode="self_hosted", env="prod", _env_file=None, firebase_project_id="p", firebase_api_key="k",
                    database_url="postgresql+psycopg://u:p@db/x", public_url="https://ww.example.com")
    assert not [p for p in bare.production_problems() if "LEGAL" in p.upper()]
    assert bare.legal_enforce is False


def test_the_check_script_lists_the_missing_details_and_never_fails_over_them(monkeypatch, capsys):
    from app.services import integration_status as I

    monkeypatch.setattr(I, "check_all", lambda *a, **k: [])
    monkeypatch.setattr(check_integrations, "get_settings",
                        lambda: Settings(mode="demo", _env_file=None, legal_operator_name="Example Wave Private Limited",
                                         legal_contact_email="not an email", legal_courts="[COURTS]"))
    assert check_integrations.main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(re.match(r"\s+set\s+LEGAL_OPERATOR_NAME\b", l) for l in lines)
    for env in ("LEGAL_OPERATOR_ADDRESS", "LEGAL_CONTACT_EMAIL", "LEGAL_PRIVACY_EMAIL", "LEGAL_GOVERNING_LAW", "LEGAL_COURTS"):
        assert any(re.match(rf"\s+not set\s+{env}\b", l) for l in lines), env
    out = "\n".join(lines)
    assert "5 not set" in out and "is not an email address" in out and "is a placeholder" in out
    assert "not an email" not in out.replace("is not an email address", "") and "[COURTS]" not in out  # a rejected value is not echoed


# ---------------------------------------------------------------- where an acceptance came from: the network, not the address
@pytest.mark.parametrize("address,prefix", [("203.0.113.77", "203.0.113.0/24"), ("2001:db8:1234:5678::9", "2001:db8:1234::/48"),
                                            ("::ffff:198.51.100.9", "198.51.100.0/24"), ("testclient", None), ("", None), (None, None)])
def test_only_the_network_of_an_address_is_kept(address, prefix):
    assert legal.ip_prefix(address) == prefix


def test_the_user_agent_is_one_short_printable_line():
    assert legal.user_agent("Mozilla/5.0\r\n  (X11)\x00 " + "a" * 400) == ("Mozilla/5.0 (X11) " + "a" * 400)[:200]
    assert legal.user_agent("") is None and legal.user_agent(None) is None


def test_an_acceptance_stores_the_network_and_the_browser_and_nothing_more():
    with TestClient(create_app(), client=("203.0.113.77", 51000)) as c:
        _newcomer(c)
        r = c.post("/api/legal/accept", json={"documents": BOTH}, headers={**NEW, "User-Agent": "Mozilla/5.0 (Test)"})
        assert r.status_code == 200
        rows = _c(c).store.legal_acceptances(ORG, user_id="u-new")
    assert {(r["document"], r["ip_prefix"], r["user_agent"], r["method"]) for r in rows} == {
        ("terms", "203.0.113.0/24", "Mozilla/5.0 (Test)", "prompt"), ("privacy", "203.0.113.0/24", "Mozilla/5.0 (Test)", "prompt")}
    assert set(rows[0]) == {"id", "org_id", "user_id", "document", "version", "method", "accepted_at", "ip_prefix", "user_agent"}
    assert "203.0.113.77" not in str(rows)  # no name, no email, no full address in the record


# ---------------------------------------------------------------- each person: asked once per version
def test_the_demo_is_not_blocked_everyone_seeded_has_accepted(client):
    for who in (ADMIN, MANAGER, EXPERT, HIRE, EXEC):
        s = client.get("/api/legal/status", headers=who).json()
        assert s["needs"] == [] and {a["document"] for a in s["accepted"]} == set(legal.PERSONAL), who
    assert client.get("/api/org/legal", headers=ADMIN).json()["dpa"]["accepted"] is True
    everyone = {u.id for u in _c(client).store.users.values() if u.org_id == ORG}
    assert {a["actor_id"] for a in _audits(client, "legal.accepted")} == everyone and len(_audits(client, "legal.dpa_accepted")) == 1


def test_someone_who_never_accepted_can_sign_in_and_is_told_what_to_accept(client):
    _newcomer(client)
    assert client.get("/api/me", headers=NEW).status_code == 200  # signing in is never refused over this
    s = client.get("/api/legal/status", headers=NEW).json()
    assert s == {"needs": ["terms", "privacy", "recording"], "accepted": [], "versions": legal.VERSIONS, "required": BOTH, "enforced": False}
    assert client.get(f"/api/projects/{PID}", headers=NEW).status_code == 200  # not enforced by default: the web app asks


def test_accepting_records_the_current_versions_once_and_audits_it(client):
    _newcomer(client)
    r = client.post("/api/legal/accept", json={"documents": BOTH}, headers=NEW)
    assert r.status_code == 200 and r.json()["needs"] == ["recording"] and r.json()["recorded"] == BOTH
    assert [(a["document"], a["version"]) for a in r.json()["accepted"]] in ([("privacy", legal.VERSIONS["privacy"]), ("terms", legal.VERSIONS["terms"])],
                                                                               [("terms", legal.VERSIONS["terms"]), ("privacy", legal.VERSIONS["privacy"])])
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z", a["at"]) for a in r.json()["accepted"])
    [entry] = [a for a in _audits(client, "legal.accepted") if a["actor_id"] == "u-new"]
    assert entry["target"] == "u-new" and entry["detail"] == {"documents": {d: legal.VERSIONS[d] for d in BOTH}, "method": "prompt"}
    # again: nothing new is recorded or audited, and the answer is the same status
    again = client.post("/api/legal/accept", json={"documents": ["privacy", "terms", "terms"]}, headers=NEW)
    assert again.status_code == 200 and again.json()["recorded"] == [] and again.json()["needs"] == ["recording"]
    assert len(_c(client).store.legal_acceptances(ORG, user_id="u-new")) == 2
    assert len([a for a in _audits(client, "legal.accepted") if a["actor_id"] == "u-new"]) == 1
    # the recording notice is its own acknowledgement
    assert legal.has_acknowledged(_c(client), _c(client).store.get_user("u-new")) is False
    assert client.post("/api/legal/accept", json={"documents": ["recording"]}, headers=NEW).json()["needs"] == []
    assert legal.has_acknowledged(_c(client), _c(client).store.get_user("u-new")) is True


def test_one_persons_acceptance_is_theirs_alone(client):
    _newcomer(client)
    client.post("/api/legal/accept", json={"documents": BOTH}, headers=NEW)
    other = _c(client).store.add_user(User(id="u-other", org_id=ORG, email="other@demo.dev", name="Other", role="expert"), PID)
    assert client.get("/api/legal/status", headers={"X-Demo-User": other.id}).json()["needs"] == list(legal.PERSONAL)
    assert client.get("/api/legal/status").status_code == 401 and client.post("/api/legal/accept", json={"documents": BOTH}).status_code == 401


def test_what_cannot_be_accepted_here_is_refused_plainly(client):
    _newcomer(client)
    r = client.post("/api/legal/accept", json={"documents": ["dpa"]}, headers=NEW)
    assert r.status_code == 422 and _code(r) == "dpa_is_organisation"
    r = client.post("/api/legal/accept", json={"documents": ["terms", "cookies"]}, headers=NEW)
    assert r.status_code == 422 and _code(r) == "unknown_document" and "cookies" in r.json()["detail"]["message"]
    assert client.post("/api/legal/accept", json={"documents": ["subprocessors"]}, headers=NEW).status_code == 422  # nobody accepts a list
    assert client.post("/api/legal/accept", json={"documents": []}, headers=NEW).status_code == 422
    assert client.post("/api/legal/accept", json={}, headers=NEW).status_code == 422
    assert _c(client).store.legal_acceptances(ORG, user_id="u-new") == []  # nothing was recorded by any of them


def test_a_version_the_person_did_not_see_is_not_accepted_for_them(client):
    _newcomer(client)
    r = client.post("/api/legal/accept", json={"documents": BOTH, "versions": {"terms": "2020-01-01", "privacy": legal.VERSIONS["privacy"]}}, headers=NEW)
    assert r.status_code == 409 and _code(r) == "version_changed" and r.json()["detail"]["versions"] == {"terms": legal.VERSIONS["terms"]}
    assert _c(client).store.legal_acceptances(ORG, user_id="u-new") == []
    ok = client.post("/api/legal/accept", json={"documents": BOTH, "versions": {d: legal.VERSIONS[d] for d in BOTH}}, headers=NEW)
    assert ok.status_code == 200 and ok.json()["recorded"] == BOTH


def test_a_new_version_asks_everyone_again_and_keeps_the_old_record(client, monkeypatch):
    assert client.get("/api/legal/status", headers=HIRE).json()["needs"] == []
    old = legal.VERSIONS["terms"]
    monkeypatch.setitem(legal.VERSIONS, "terms", "2027-03-01")
    s = client.get("/api/legal/status", headers=HIRE).json()
    assert s["needs"] == ["terms"] and s["versions"]["terms"] == "2027-03-01"
    assert client.get("/api/config").json()["legal"]["versions"]["terms"] == "2027-03-01"
    after = client.post("/api/legal/accept", json={"documents": ["terms"]}, headers=HIRE).json()
    assert after["needs"] == [] and after["recorded"] == ["terms"]
    assert {(a["document"], a["version"]) for a in after["accepted"]} >= {("terms", old), ("terms", "2027-03-01")}  # both stand


def test_an_executive_is_read_only_and_can_still_accept_for_themselves(client, monkeypatch):
    monkeypatch.setitem(legal.VERSIONS, "privacy", "2027-03-01")
    assert client.get("/api/legal/status", headers=EXEC).json()["needs"] == ["privacy"]
    assert client.post("/api/legal/accept", json={"documents": ["privacy"]}, headers=EXEC).json()["needs"] == []
    assert client.post("/api/projects", json={"name": "No"}, headers=EXEC).status_code == 403  # still read-only for everything else


# ---------------------------------------------------------------- sign-up and joining: accepted in the same request
SIGNUP = {"name": "Fatima", "org_name": "Acme", "project_name": "Payments"}


def test_sign_up_without_acceptance_creates_nothing(prod):
    for body in (SIGNUP, {**SIGNUP, "accept": []}, {**SIGNUP, "accept": ["terms"]}, {**SIGNUP, "accept": ["privacy", "recording"]}):
        r = prod.post("/api/signup", json=body, headers=bearer("tok-founder"))
        assert r.status_code == 422 and _code(r) == "legal_required", body
        assert set(r.json()["detail"]["needs"]) <= set(BOTH) and r.json()["detail"]["versions"] == legal.VERSIONS
    assert prod.post("/api/signup", json={**SIGNUP, "accept": [*BOTH, "cookies"]}, headers=bearer("tok-founder")).status_code == 422
    r = prod.get("/api/me", headers=bearer("tok-founder"))
    assert r.status_code == 403 and _code(r) == "no_account"  # no organisation, no account
    assert "Acme" not in _c(prod).store.orgs.values()


def test_sign_up_records_the_acceptance_with_the_account(prod):
    r = prod.post("/api/signup", json={**SIGNUP, "accept": BOTH}, headers={**bearer("tok-founder"), "User-Agent": "Mozilla/5.0 (Signup)"})
    assert r.status_code == 201
    me = r.json()
    rows = _c(prod).store.legal_acceptances(me["org_id"], user_id=me["id"])
    assert {(x["document"], x["version"], x["method"], x["user_agent"]) for x in rows} == {
        (d, legal.VERSIONS[d], "signup", "Mozilla/5.0 (Signup)") for d in BOTH}
    [entry] = [a for a in _audits(prod, "legal.accepted") if a["actor_id"] == me["id"]]
    assert entry["org_id"] == me["org_id"] and entry["detail"]["method"] == "signup"
    s = prod.get("/api/legal/status", headers=bearer("tok-founder")).json()
    assert s["needs"] == ["recording"]  # the notice is acknowledged in the app, or with the sign-up when the page shows it
    assert prod.get("/api/org/legal", headers=bearer("tok-founder")).json()["dpa"]["accepted"] is False  # the DPA is a separate act


def test_sign_up_can_carry_the_recording_notice_and_the_dpa(prod):
    r = prod.post("/api/signup", json={**SIGNUP, "documents": [*BOTH, "recording", "dpa"]}, headers=bearer("tok-founder"))  # `documents` is read too
    assert r.status_code == 201
    me = r.json()
    assert prod.get("/api/legal/status", headers=bearer("tok-founder")).json()["needs"] == []
    dpa = prod.get("/api/org/legal", headers=bearer("tok-founder")).json()["dpa"]
    assert dpa["accepted"] is True and dpa["accepted_by"] == {"id": me["id"], "name": "Fatima", "email": "founder@acme.dev"}
    assert [a["detail"] for a in _audits(prod, "legal.dpa_accepted") if a["org_id"] == me["org_id"]] == [
        {"version": legal.VERSIONS["dpa"], "method": "signup"}]


def test_joining_by_invitation_needs_and_records_the_acceptance(prod):
    founder = prod.post("/api/signup", json={**SIGNUP, "accept": BOTH}, headers=bearer("tok-founder")).json()
    pid = founder["projects"][0]["id"]
    assert prod.post(f"/api/projects/{pid}/invitations", json={"email": "hire@acme.dev", "role": "new_hire"},
                     headers=bearer("tok-founder")).status_code == 201
    for body in ({"org_id": founder["org_id"]}, {"org_id": founder["org_id"], "accept": ["terms"]}):
        r = prod.post("/api/invitations/accept", json=body, headers=bearer("tok-hire"))
        assert r.status_code == 422 and _code(r) == "legal_required"
    r = prod.post("/api/invitations/accept", json={"org_id": founder["org_id"], "accept": ["dpa", *BOTH]}, headers=bearer("tok-hire"))
    assert r.status_code == 422 and _code(r) == "dpa_is_organisation"  # a new member cannot bind the organisation
    r = prod.get("/api/me", headers=bearer("tok-hire"))
    assert r.status_code == 403 and _code(r) == "invitation_pending"  # still not joined
    hire = prod.post("/api/invitations/accept", json={"org_id": founder["org_id"], "accept": [*BOTH, "recording"]}, headers=bearer("tok-hire")).json()
    rows = _c(prod).store.legal_acceptances(founder["org_id"], user_id=hire["id"])
    assert {(x["document"], x["method"]) for x in rows} == {(d, "invitation") for d in legal.PERSONAL}
    assert prod.get("/api/legal/status", headers=bearer("tok-hire")).json()["needs"] == []
    assert [a["detail"]["method"] for a in _audits(prod, "legal.accepted") if a["actor_id"] == hire["id"]] == ["invitation"]


def test_a_join_is_not_undone_when_its_acceptance_cannot_be_recorded(prod):
    """The account exists by the time the acceptance is written. Should that write fail, the person is in, and is asked at
    their next sign-in: never an account that cannot be reached, never a silent acceptance."""
    def away(*_a, **_k):
        raise RuntimeError("database away")

    store = _c(prod).store
    with pytest.MonkeyPatch.context() as broken:
        broken.setattr(type(getattr(store, "_store", store)), "record_legal_acceptance", away)
        r = prod.post("/api/signup", json={**SIGNUP, "accept": BOTH}, headers=bearer("tok-founder"))
    assert r.status_code == 201 and r.json()["role"] == "admin"
    assert prod.get("/api/legal/status", headers=bearer("tok-founder")).json()["needs"] == list(legal.PERSONAL)


def test_a_demo_invitee_is_asked_like_anyone_else(client):
    priya = MANAGER
    assert client.post(f"/api/projects/{PID}/invitations", json={"email": "zed@demo.dev", "role": "new_hire", "name": "Zed"}, headers=priya).status_code == 201
    assert client.post(f"/api/projects/{PID}/invitations", json={"email": "yan@demo.dev", "role": "new_hire", "name": "Yan"}, headers=priya).status_code == 201
    invited = {i["email"]: i for i in client.get("/api/demo/people").json()["invited"]}
    zed = client.post("/api/demo/invitations/accept", json={"project_id": PID, "invitation_id": invited["zed@demo.dev"]["invitation_id"]}).json()
    assert client.get("/api/legal/status", headers={"X-Demo-User": zed["id"]}).json()["needs"] == list(legal.PERSONAL)
    yan = client.post("/api/demo/invitations/accept", json={"project_id": PID, "invitation_id": invited["yan@demo.dev"]["invitation_id"],
                                                             "accept": [*BOTH, "recording"]}).json()
    assert client.get("/api/legal/status", headers={"X-Demo-User": yan["id"]}).json()["needs"] == []
    assert _c(client).store.legal_acceptances(ORG, user_id=yan["id"])[0]["method"] == "invitation"


# ---------------------------------------------------------------- the organisation's data-processing agreement
def test_only_an_admin_reads_or_accepts_the_dpa(client):
    for who in (MANAGER, EXPERT, HIRE, EXEC):
        assert client.get("/api/org/legal", headers=who).status_code == 403
        assert client.post("/api/org/legal/dpa", json={}, headers=who).status_code == 403
        assert client.get("/api/org/legal/dpa/record", headers=who).status_code == 403
    assert client.get("/api/org/legal").status_code == 401 and client.post("/api/org/legal/dpa").status_code == 401


def test_an_admin_accepts_the_dpa_once_for_the_organisation(prod):
    founder = prod.post("/api/signup", json={**SIGNUP, "accept": BOTH}, headers=bearer("tok-founder")).json()
    h = bearer("tok-founder")
    before = prod.get("/api/org/legal", headers=h).json()
    assert before["dpa"] == {"version": legal.VERSIONS["dpa"], "accepted": False, "accepted_at": None, "accepted_by": None, "previous": []}
    assert before["versions"] == legal.VERSIONS and before["operator"]["operator"] is None
    r = prod.get("/api/org/legal/dpa/record", headers=h)
    assert r.status_code == 404 and _code(r) == "dpa_not_accepted"
    stale = prod.post("/api/org/legal/dpa", json={"version": "2020-01-01"}, headers=h)
    assert stale.status_code == 409 and _code(stale) == "version_changed"
    done = prod.post("/api/org/legal/dpa", headers=h)  # no body is needed
    assert done.status_code == 200 and done.json()["recorded"] is True
    dpa = done.json()["dpa"]
    assert dpa["accepted"] is True and dpa["accepted_by"] == {"id": founder["id"], "name": "Fatima", "email": "founder@acme.dev"}
    assert [(a["actor_id"], a["target"], a["detail"]) for a in _audits(prod, "legal.dpa_accepted") if a["org_id"] == founder["org_id"]] == [
        (founder["id"], founder["org_id"], {"version": legal.VERSIONS["dpa"], "method": "organisation"})]
    # a second admin clicks: the first acceptance stands, nothing new is recorded or audited
    pid = founder["projects"][0]["id"]
    prod.post(f"/api/projects/{pid}/invitations", json={"email": "other@acme.dev", "role": "admin"}, headers=h)
    prod.post("/api/invitations/accept", json={"org_id": founder["org_id"], "accept": BOTH}, headers=bearer("tok-other"))
    again = prod.post("/api/org/legal/dpa", json={"version": legal.VERSIONS["dpa"]}, headers=bearer("tok-other"))
    assert again.status_code == 200 and again.json()["recorded"] is False and again.json()["dpa"]["accepted_by"]["id"] == founder["id"]
    assert again.json()["dpa"]["accepted_at"] == dpa["accepted_at"]
    assert len([a for a in _audits(prod, "legal.dpa_accepted") if a["org_id"] == founder["org_id"]]) == 1
    assert len(_c(prod).store.legal_acceptances(founder["org_id"], document="dpa")) == 1


def test_the_dpa_record_is_a_text_file_naming_version_time_and_person(client, monkeypatch):
    monkeypatch.setattr(_c(client).settings, "legal_operator_name", "Example Wave Private Limited")
    r = client.get("/api/org/legal/dpa/record", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"] == f'attachment; filename="welcome-wave-dpa-acceptance-{legal.VERSIONS["dpa"]}.txt"'
    text, status = r.text, client.get("/api/org/legal", headers=ADMIN).json()["dpa"]
    for expected in ("Data Processing Agreement: record of acceptance", "Mini Policy Engine", f"Version:           {legal.VERSIONS['dpa']}",
                     status["accepted_at"], "Alex (Admin) <admin@demo.dev>", "u-admin", "Example Wave Private Limited",
                     "not the text of the agreement"):
        assert expected in text, expected
    assert client.get("/api/org/legal/dpa/record?version=2020-01-01", headers=ADMIN).status_code == 404


def test_the_record_never_invents_an_operator(client):
    text = client.get("/api/org/legal/dpa/record", headers=ADMIN).text
    assert f"Operator:          {legal.NEUTRAL_OPERATOR}" in text and "[" not in text and "{{" not in text


def test_the_dpa_record_cannot_be_given_extra_lines_through_a_name(prod):
    """The record states one fact per line and is kept as evidence: a name must not be able to add a line to it."""
    body = {"name": "Fatima\nAccepted by:       Someone Else <ceo@acme.dev>", "org_name": "Acme\r\nVersion:           1999-01-01",
            "project_name": "Payments\x00", "accept": ["terms", "privacy", "dpa"]}
    r = prod.post("/api/signup", json=body, headers=bearer("tok-founder"))
    assert r.status_code == 201, r.text
    assert "\n" not in r.json()["name"] and r.json()["name"].startswith("Fatima Accepted by:")  # stored as one line
    lines = prod.get("/api/org/legal/dpa/record", headers=bearer("tok-founder")).text.splitlines()
    assert len([x for x in lines if x.startswith("Accepted by:")]) == 1 and len([x for x in lines if x.startswith("Version:")]) == 1
    # a name that reached the store another way (an identity token, an older account) is collapsed when the record is written
    c = _c(prod)
    me = c.store.get_user(r.json()["id"])
    c.store.users[me.id] = me.model_copy(update={"name": "Fatima\nVersion:           1999-01-01"})
    lines = prod.get("/api/org/legal/dpa/record", headers=bearer("tok-founder")).text.splitlines()
    assert len([x for x in lines if x.startswith("Version:")]) == 1
    assert prod.post("/api/signup", json={**body, "name": "\n\t "}, headers=bearer("tok-other")).status_code == 422


def test_a_new_dpa_version_needs_accepting_again_and_the_earlier_one_stays_on_record(client, monkeypatch):
    first = client.get("/api/org/legal", headers=ADMIN).json()["dpa"]
    old = legal.VERSIONS["dpa"]
    monkeypatch.setitem(legal.VERSIONS, "dpa", "2027-03-01")
    now = client.get("/api/org/legal", headers=ADMIN).json()["dpa"]
    assert now["version"] == "2027-03-01" and now["accepted"] is False and now["accepted_by"] is None
    assert now["previous"] == [{"version": old, "accepted_at": first["accepted_at"], "accepted_by": first["accepted_by"]}]
    assert old in client.get("/api/org/legal/dpa/record", headers=ADMIN).text  # the newest accepted is still downloadable
    after = client.post("/api/org/legal/dpa", json={}, headers=ADMIN).json()
    assert after["recorded"] is True and after["dpa"]["accepted"] is True and [p["version"] for p in after["dpa"]["previous"]] == [old]
    assert "2027-03-01" in client.get("/api/org/legal/dpa/record", headers=ADMIN).text
    assert old in client.get(f"/api/org/legal/dpa/record?version={old}", headers=ADMIN).text


def test_setup_asks_an_admin_to_accept_the_dpa_and_is_done_only_when_it_is(client, monkeypatch):
    """Nothing else prompts an admin about the agreement: Setup's first step does, and Home's focus with it. The step follows
    the acceptance itself: an admin cannot mark it done by hand, and a new version opens it again."""
    step = lambda: next(s for s in client.get("/api/setup", headers=ADMIN).json()["steps"] if s["key"] == "dpa")  # noqa: E731
    assert client.get("/api/setup", headers=ADMIN).json()["steps"][0]["key"] == "dpa"
    assert step()["status"] == "done" and step()["how"] == "Checked from your data."  # the demo organisation accepted it
    monkeypatch.setitem(legal.VERSIONS, "dpa", "2027-03-01")
    assert step()["status"] == "todo"
    focus = client.get("/api/home", headers=ADMIN).json()["focus"]
    assert any(f["key"] == "setup:dpa" and "Data Processing Agreement" in f["text"] for f in focus)
    r = client.patch("/api/setup", json={"step": "dpa", "action": "complete"}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "checked_from_data" and step()["status"] == "todo"
    assert client.patch("/api/setup", json={"step": "dpa", "action": "skip"}, headers=ADMIN).status_code == 200  # a signed copy elsewhere
    assert step()["status"] == "skipped"
    assert client.post("/api/org/legal/dpa", json={}, headers=ADMIN).status_code == 200
    assert step()["status"] == "done"


# ---------------------------------------------------------------- erasure, export, deletion
def test_erasing_a_person_keeps_their_acceptance_under_the_erased_identity(client):
    store = _c(client).store
    before = store.legal_acceptances(ORG, user_id="u-ravi")
    assert len(before) == 3
    r = client.post("/api/org/people/erase", json={"email": "ravi@demo.dev", "confirm": "ravi@demo.dev"}, headers=ADMIN)
    assert r.status_code == 200 and any("accepted the terms" in kept for kept in r.json()["kept"])
    assert store.legal_acceptances(ORG, user_id="u-ravi") == before  # every row, unchanged
    assert "ravi@demo.dev" not in str(before) and "Ravi (Senior)" not in str(before)  # it never held his name or email: only the id
    assert store.get_user("u-ravi").name == "Erased person"


def test_the_dpa_record_of_an_erased_admin_says_so_and_keeps_version_and_time(client):
    store = _c(client).store
    store.add_user(User(id="u-admin2", org_id=ORG, email="second@demo.dev", name="Second Admin", role="admin"), PID)
    store.firebase["fb-second"] = "u-admin2"  # a member with a login, so the organisation keeps an admin
    at = client.get("/api/org/legal", headers=ADMIN).json()["dpa"]["accepted_at"]
    r = client.post("/api/org/people/erase", json={"email": "admin@demo.dev", "confirm": "admin@demo.dev"}, headers={"X-Demo-User": "u-admin2"})
    assert r.status_code == 200, r.text
    dpa = client.get("/api/org/legal", headers={"X-Demo-User": "u-admin2"}).json()["dpa"]
    assert dpa["accepted"] is True and dpa["accepted_at"] == at and dpa["accepted_by"]["name"] == "Erased person"
    text = client.get("/api/org/legal/dpa/record", headers={"X-Demo-User": "u-admin2"}).text
    assert "Erased person" in text and "admin@demo.dev" not in text and "Alex" not in text and at in text and "u-admin" in text


def test_the_export_holds_the_acceptances_and_deleting_the_organisation_removes_them(client):
    data = client.get("/api/org/export", headers=ADMIN).json()
    rows = data["legal_acceptances"]
    assert {r["document"] for r in rows} == {"terms", "privacy", "recording", "dpa"} and all(r["org_id"] == ORG for r in rows)
    assert len([r for r in rows if r["document"] == "dpa"]) == 1
    store = _c(client).store
    store.record_legal_acceptance("org-other", "u-elsewhere", "terms", legal.VERSIONS["terms"], "signup")
    r = client.request("DELETE", "/api/org", json={"confirm": "Mini Policy Engine"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["deleted"]["legal_acceptances"] == len(rows)
    assert store.legal_acceptances(ORG) == [] and len(store.legal_acceptances("org-other")) == 1  # another organisation's stay


# ---------------------------------------------------------------- LEGAL_ENFORCE
@pytest.fixture
def enforced(client, monkeypatch):
    monkeypatch.setattr(_c(client).settings, "legal_enforce", True)
    return client


def test_enforced_the_workspace_is_refused_until_the_terms_are_accepted(enforced):
    c = enforced
    _newcomer(c, role="admin")
    assert c.get("/api/config").json()["legal"]["enforced"] is True
    for method, path in (("GET", f"/api/projects/{PID}"), ("GET", "/api/projects"), ("GET", f"/api/tasks?project_id={PID}"),
                         ("POST", "/api/chat"), ("GET", "/api/org/members"), ("GET", "/api/audit")):
        r = c.request(method, path, headers=NEW, json={"project_id": PID, "question": "Where?"} if method == "POST" else None)
        assert r.status_code == 403 and _code(r) == "legal_required", (method, path, r.status_code)
        assert r.json()["detail"]["needs"] == BOTH and r.json()["detail"]["versions"] == legal.VERSIONS
    # what stays open: who they are, the documents, their notifications, and (admins) export, deletion and the DPA
    assert c.get("/api/me", headers=NEW).status_code == 200
    assert c.get("/api/legal/status", headers=NEW).json()["enforced"] is True
    assert c.get("/api/notifications", headers=NEW).status_code == 200
    assert c.get("/api/org/export", headers=NEW).status_code == 200
    assert c.get("/api/org/legal", headers=NEW).status_code == 200
    assert c.request("DELETE", "/api/org", json={"confirm": "not the name"}, headers=NEW).status_code == 422  # reached, and refused on its own terms
    # one of the two is not enough; both are
    assert c.post("/api/legal/accept", json={"documents": ["terms"]}, headers=NEW).status_code == 200
    r = c.get("/api/projects", headers=NEW)
    assert r.status_code == 403 and r.json()["detail"]["needs"] == ["privacy"]
    assert c.post("/api/legal/accept", json={"documents": ["privacy"]}, headers=NEW).status_code == 200
    assert c.get("/api/projects", headers=NEW).status_code == 200  # the recording notice is not part of the gate
    assert c.get("/api/legal/status", headers=NEW).json()["needs"] == ["recording"]


def test_enforced_people_who_accepted_notice_nothing_and_a_new_version_asks_again(enforced, monkeypatch):
    c = enforced
    assert c.get(f"/api/projects/{PID}", headers=HIRE).status_code == 200  # everyone in the demo has accepted
    monkeypatch.setitem(legal.VERSIONS, "terms", "2027-03-01")
    legal._satisfied.forget()  # a new version ships with a new process
    r = c.get(f"/api/projects/{PID}", headers=HIRE)
    assert r.status_code == 403 and r.json()["detail"]["needs"] == ["terms"]
    assert c.post("/api/legal/accept", json={"documents": ["terms"]}, headers=HIRE).status_code == 200
    assert c.get(f"/api/projects/{PID}", headers=HIRE).status_code == 200


def test_enforced_a_person_can_still_leave(enforced):
    _newcomer(enforced)
    assert enforced.post("/api/me/leave", headers=NEW).status_code == 204


def test_enforcement_is_off_unless_asked_for():
    assert Settings(mode="demo", _env_file=None).legal_enforce is False
    assert Settings(mode="demo", _env_file=None, legal_enforce="true").legal_enforce is True
    assert Settings(mode="demo", _env_file=None, legal_enforce="  ").legal_enforce is False  # LEGAL_ENFORCE= in a template: off
