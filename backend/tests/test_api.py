"""HTTP API in demo mode: seeded project, offline LLM, in-memory stores."""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

PID = "proj-demo"
MANAGER = {"X-Demo-User": "u-priya"}
HIRE = {"X-Demo-User": "u-vijay"}


@pytest.fixture(scope="module")
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


def test_health_and_me(client):
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/api/me", headers=HIRE).json()["role"] == "new_hire"


def test_code_view_shows_who_explained_each_file(client):
    files = {f["path"]: f["kt_clips"] for f in client.get(f"/api/code/files?project_id={PID}", headers=HIRE).json()}
    assert files["engine/validate.py"] >= 2 and files["webhooks/server.py"] == 0
    ex = client.get(f"/api/code/explanations?project_id={PID}&path=engine/validate.py", headers=HIRE).json()
    assert {c["symbol"] for c in ex["clips"]} >= {"validate_resource", "check_labels"}
    assert ex["clips"][0]["meeting_title"].startswith("KT #1")


def test_coverage_reports_unexplained_module(client):
    cov = client.get(f"/api/coverage?project_id={PID}", headers=MANAGER).json()
    assert "webhooks" in cov["gaps"] and "engine" not in cov["gaps"]


def test_chat_answers_with_citations_offline(client):
    r = client.post("/api/chat", json={"project_id": PID, "question": "Where does check_labels report missing labels?"}, headers=HIRE)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "answered" and body["citations"]


def test_onboarding_plan_then_approval_flow(client):
    # Vijay is already onboarding (seed): a second active onboarding for the same person is refused
    r = client.post(f"/api/projects/{PID}/onboardings", json={"new_hire_id": "u-vijay", "start_date": "2026-10-15"}, headers=MANAGER)
    assert r.status_code == 409
    pending = client.get(f"/api/approvals?project_id={PID}", headers=MANAGER).json()
    plan = next(a for a in pending if a["kind"] == "task_plan")  # the seed also has a KT call review
    tasks = plan["payload"]["plans"][0]["tasks"]
    assert any(t["origin"] == "coverage_gap" and "webhooks" in t["title"] for t in tasks)
    assert any(t["origin"] == "first_pr" for t in tasks)
    r = client.post(f"/api/approvals/{plan['id']}/decide", json={"approve": [t["id"] for t in tasks]}, headers=MANAGER)
    assert r.json()["approval"]["status"] == "approved"
    again = client.post(f"/api/approvals/{plan['id']}/decide", json={"approve": [t["id"] for t in tasks]}, headers=MANAGER)
    assert again.status_code == 409  # a second click never resumes the run twice
    mine = client.get(f"/api/tasks?project_id={PID}", headers=HIRE).json()
    assert len(mine) >= len(tasks)


def test_roles_are_enforced(client):
    assert client.get(f"/api/dashboard?project_id={PID}", headers=HIRE).status_code == 403
    dash = client.get(f"/api/dashboard?project_id={PID}", headers=MANAGER).json()
    assert dash["people"][0]["name"].startswith("Vijay") and "webhooks" in dash["kt_gaps"]


def test_github_webhook_requires_signature(client):
    body = json.dumps({"repository": {"full_name": "demo/mini-policy-engine"}}).encode()
    assert client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "ping"}).status_code == 401
    sig = "sha256=" + hmac.new(b"dev-secret", body, hashlib.sha256).hexdigest()
    r = client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sig})
    assert r.status_code == 202 and r.json() == {"events": 0}
