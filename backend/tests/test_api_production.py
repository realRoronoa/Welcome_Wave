"""HTTP API: the production paths that the demo flow does not exercise (people, permissions, uploads, limits)."""
import io

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.ports import Project, User

PID = "proj-demo"
MANAGER = {"X-Demo-User": "u-priya"}
HIRE = {"X-Demo-User": "u-vijay"}
SENIOR = {"X-Demo-User": "u-ravi"}


@pytest.fixture(scope="module")
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="module")
def store(client):
    return client.app.state.container.store


def test_public_config_has_no_secrets(client):
    cfg = client.get("/api/config").json()
    assert cfg["mode"] == "demo" and cfg["firebase"] is None
    assert "secret" not in str(cfg).lower()


def test_me_lists_my_projects(client):
    me = client.get("/api/me", headers=HIRE).json()
    assert me["role"] == "new_hire" and [p["id"] for p in me["projects"]] == [PID]


def test_non_members_cannot_see_a_project(client, store):
    store.add_project(Project(id="proj-secret", org_id="org-demo", name="Secret", description=""))
    assert client.get("/api/projects/proj-secret", headers=HIRE).status_code == 404
    assert client.get("/api/coverage?project_id=proj-secret", headers=HIRE).status_code == 404
    store.add_user(User(id="u-other", org_id="org-other", email="o@x.dev", name="Other", role="admin"))
    assert client.get(f"/api/projects/{PID}", headers={"X-Demo-User": "u-other"}).status_code == 404  # admin of another org


def test_manager_creates_project_and_invites(client):
    p = client.post("/api/projects", json={"name": "Payments"}, headers=MANAGER).json()
    assert client.post("/api/projects", json={"name": "Nope"}, headers=HIRE).status_code == 403
    r = client.post(f"/api/projects/{p['id']}/invitations", json={"email": "New@Corp.dev", "role": "new_hire"}, headers=MANAGER)
    assert r.status_code == 201 and r.json()["invited"]["email"] == "new@corp.dev"
    assert client.post(f"/api/projects/{p['id']}/invitations", json={"email": "x@corp.dev", "role": "admin"}, headers=MANAGER).status_code == 403
    # inviting someone already in the organisation adds them straight away
    r = client.post(f"/api/projects/{p['id']}/invitations", json={"email": "vijay@demo.dev", "role": "new_hire"}, headers=MANAGER)
    assert r.json()["added"]["id"] == "u-vijay"
    detail = client.get(f"/api/projects/{p['id']}", headers=MANAGER).json()
    assert {m["id"] for m in detail["members"]} == {"u-priya", "u-vijay"} and len(detail["invitations"]) == 1
    inv = detail["invitations"][0]["id"]
    assert client.delete(f"/api/projects/{p['id']}/invitations/{inv}", headers=MANAGER).status_code == 204
    assert "invitations" not in client.get(f"/api/projects/{p['id']}", headers=HIRE).json()


def test_document_upload_is_checked(client):
    bad = client.post(f"/api/projects/{PID}/documents", files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")}, headers=MANAGER)
    assert bad.status_code == 415
    sneaky = client.post(f"/api/projects/{PID}/documents", files={"file": ("../../etc/passwd.md", io.BytesIO(b"# hi"), "text/markdown")},
                         headers=MANAGER)
    assert sneaky.status_code == 202  # stored under a generated folder; the name cannot escape it
    assert client.post(f"/api/projects/{PID}/documents", files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")}, headers=HIRE).status_code == 403
    docs = client.get(f"/api/projects/{PID}", headers=MANAGER).json()["documents"]
    assert docs[-1]["status"] == "ready"  # demo mode indexes inline


def test_chat_feedback_and_rate_limit(client):
    ans = client.post("/api/chat", json={"project_id": PID, "question": "Where is validate_resource?"}, headers=SENIOR).json()
    assert client.post(f"/api/chat/{ans['message_id']}/feedback", json={"value": 1}, headers=SENIOR).status_code == 204
    assert client.post(f"/api/chat/{ans['message_id']}/feedback", json={"value": 1}, headers=HIRE).status_code == 404
    codes = [client.post("/api/chat", json={"project_id": PID, "question": "What is a violation?"}, headers=SENIOR).status_code
             for _ in range(15)]
    assert 429 in codes


def test_expert_answers_only_their_own_questions(client, store):
    rid = store.create_expert_request(PID, "Why no retries?", "u-ravi", "u-vijay", "m-x")
    store.add_user(User(id="u-meera2", org_id="org-demo", email="m2@demo.dev", name="Meera", role="expert"), PID)
    assert client.post(f"/api/experts/requests/{rid}/answer", json={"answer": "x"}, headers={"X-Demo-User": "u-meera2"}).status_code == 403
    assert client.post(f"/api/experts/requests/{rid}/answer", json={"answer": "Idempotency."}, headers=SENIOR).status_code == 200
    assert client.post(f"/api/experts/requests/{rid}/answer", json={"answer": "again"}, headers=SENIOR).status_code == 409


def test_notifications_can_be_marked_read(client):
    rows = client.get("/api/notifications", headers=MANAGER).json()
    assert rows and any(not n["read_at"] for n in rows)
    assert client.post("/api/notifications/read", json={"ids": None}, headers=MANAGER).json()["marked"] >= 1
    assert all(n["read_at"] for n in client.get("/api/notifications", headers=MANAGER).json())


def test_security_headers_and_request_id(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY" and r.headers["x-request-id"]
    assert client.get("/health/ready").json() == {"ok": True}


def test_demo_mode_cannot_run_in_prod(monkeypatch):
    from app.config import Settings
    from app.container import build_container

    with pytest.raises(RuntimeError, match="MODE=demo is not allowed"):
        build_container(Settings(mode="demo", env="prod"))
    with pytest.raises(RuntimeError, match="GITHUB_WEBHOOK_SECRET"):
        build_container(Settings(mode="gcp", env="prod", gcp_project="p", gcs_bucket="b"))
