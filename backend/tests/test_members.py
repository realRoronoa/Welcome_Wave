"""Members administration (app/api/members.py): list, change a role, deactivate and reactivate, in demo mode. The store
methods also run on Postgres in tests/test_postgres.py (test_members_roles_and_deactivation)."""
import pytest
from fastapi.testclient import TestClient

from app.adapters.memory import MemoryStore
from app.config import get_settings
from app.main import create_app
from app.ports import LastAdmin, Project, User

PID, ORG = "proj-demo", "org-demo"
ALEX, PRIYA, VIJAY, RAVI = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-vijay", "u-ravi"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_RETRIES", "1")
    monkeypatch.setenv("RETRY_SLEEP_S", "0")
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _store(c):
    return c.app.state.container.store


def _audit(c, action):
    return [r for r in _store(c).audit_rows if r["action"] == action]


def _told(c, user_id):
    return [n["text"] for n in _store(c).notifications if n["user_id"] == user_id and n["kind"] == "account"]


def test_admins_list_members_with_role_status_and_projects(client):
    r = client.get("/api/org/members", headers=ALEX)
    assert r.status_code == 200
    rows = {m["id"]: m for m in r.json()}
    assert rows["u-ravi"] == {"id": "u-ravi", "name": "Ravi (Senior)", "email": "ravi@demo.dev", "role": "expert", "status": "active",
                              "projects": [{"id": PID, "name": "Mini Policy Engine"}]}
    assert rows["u-noah"]["projects"] == []  # the previous cohort: still in the organisation, on no project
    assert [m["name"] for m in r.json()] == sorted(m["name"] for m in r.json())
    for who in (PRIYA, RAVI, VIJAY, {"X-Demo-User": "u-exec"}):
        assert client.get("/api/org/members", headers=who).status_code == 403


def test_changing_a_role_is_audited_and_the_member_told(client):
    r = client.patch("/api/org/members/u-meera", json={"role": "manager"}, headers=ALEX)
    assert r.status_code == 200 and r.json()["role"] == "manager" and r.json()["status"] == "active"
    assert client.get("/api/me", headers={"X-Demo-User": "u-meera"}).json()["role"] == "manager"
    [entry] = _audit(client, "member.role_changed")
    assert (entry["actor_id"], entry["target"], entry["detail"]) == ("u-admin", "u-meera", {"from": "expert", "to": "manager"})
    assert _told(client, "u-meera") == ["Alex (Admin) changed your role from expert to manager."]
    assert client.patch("/api/org/members/u-meera", json={"role": "manager"}, headers=ALEX).status_code == 200
    assert len(_audit(client, "member.role_changed")) == 1  # nothing changed, nothing recorded
    assert client.patch("/api/org/members/u-meera", json={"role": "owner"}, headers=ALEX).status_code == 422
    assert client.patch("/api/org/members/u-nobody", json={"role": "expert"}, headers=ALEX).status_code == 404


def test_the_organisation_keeps_an_active_admin(client):
    r = client.patch("/api/org/members/u-admin", json={"role": "manager"}, headers=ALEX)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "last_admin"
    assert client.post("/api/org/members/u-admin/deactivate", headers=ALEX).json()["detail"]["code"] == "self"
    assert client.get("/api/me", headers=ALEX).json()["role"] == "admin" and not _audit(client, "member.role_changed")
    assert client.patch("/api/org/members/u-priya", json={"role": "admin"}, headers=ALEX).status_code == 200
    assert client.patch("/api/org/members/u-admin", json={"role": "manager"}, headers=ALEX).status_code == 200  # Priya is one now
    assert client.get("/api/org/members", headers=ALEX).status_code == 403  # and Alex no longer is

    store = MemoryStore()  # the store refuses it too, whoever asks (two admins demoting each other at once)
    store.add_project(Project(id="p1", org_id="o1", name="P"))
    store.add_user(User(id="a", org_id="o1", email="a@x", name="A", role="admin"), "p1")
    store.add_user(User(id="b", org_id="o1", email="b@x", name="B", role="expert"), "p1")
    for change in (lambda: store.set_member_role("o1", "a", "expert"), lambda: store.set_member_status("o1", "a", "deactivated")):
        with pytest.raises(LastAdmin):
            change()
    assert store.get_user("a").role == "admin" and store.get_user("a").status == "active"
    assert store.set_member_role("o2", "a", "expert") is None  # not a member of that organisation
    store.left.add("b")  # someone who left (or was enrolled and has not joined) is not a member
    assert [m["id"] for m in store.org_members("o1")] == ["a"] and store.org_member("o1", "b") is None


def test_a_deactivated_member_is_refused_keeps_their_data_and_gets_no_new_work(client):
    asked = client.post("/api/chat", json={"project_id": PID, "question": "How is the mutate module benchmarked?"}, headers=VIJAY).json()
    assert asked["routed_to"] == "u-ravi"
    [request] = [r for r in client.get("/api/experts/requests", headers=RAVI).json() if r["message_id"] == asked["message_id"]]

    r = client.post("/api/org/members/u-ravi/deactivate", headers=ALEX)
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "deactivated" and out["role"] == "expert" and out["projects"] == [{"id": PID, "name": "Mini Policy Engine"}]
    waiting = [q["id"] for q in out["needs_reassignment"]["expert_requests"]]
    assert request["id"] in waiting and len(waiting) == 2  # his question and the seeded one with the assistant's draft
    assert "question" not in out["needs_reassignment"]["expert_requests"][0]  # what the hire asked stays with its recipients
    assert {(m["onboarding_id"], m["hire"]["name"]) for m in out["needs_reassignment"]["mentoring"]} == \
        {("ob-vijay", "Vijay (New hire)"), ("ob-tom", "Tom (New hire)")}
    [entry] = _audit(client, "member.deactivated")
    assert entry["target"] == "u-ravi" and entry["detail"] == {"open_expert_requests": 2, "mentoring": 2}
    assert _told(client, "u-ravi") == ["Alex (Admin) deactivated your account. Your work in Welcome Wave is kept."]

    for path in ("/api/me", f"/api/kt-plan?project_id={PID}", "/api/experts/requests"):  # every signed-in route
        refused = client.get(path, headers=RAVI)
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "deactivated"
    login = client.post("/api/demo/login", json={"email": "ravi@demo.dev", "password": "welcome-wave"})
    assert login.status_code == 403 and login.json()["detail"]["code"] == "deactivated"
    assert "u-ravi" not in {m["id"] for m in client.get("/api/demo/people").json()["members"]}
    again = client.post("/api/org/members/u-ravi/deactivate", headers=ALEX)
    assert again.status_code == 200 and len(_audit(client, "member.deactivated")) == 1  # already so: nothing recorded

    store = _store(client)  # their data stays: the request, the membership, the mentoring
    assert store.get_expert_request(request["id"])["expert_id"] == "u-ravi" and store.is_member(PID, "u-ravi")
    assert client.app.state.container.lifecycle.get_program("ob-vijay").mentor_id == "u-ravi"
    rerouted = client.post("/api/chat", json={"project_id": PID, "question": "How is the policy loader profiled?"}, headers=VIJAY).json()
    assert rerouted["status"] == "not_enough_information" and rerouted["routed_to"] not in {None, "u-ravi"}
    tiered = client.post("/api/ask/route", json={"project_id": PID, "question": "Who reviews engine changes?", "ask_person": True},
                         headers=VIJAY).json()
    assert tiered.get("tier") != 3 and "Ravi" not in " ".join(tiered.get("why", []))  # his mentor tier is skipped, not him asked
    plan = client.get(f"/api/kt-plan?project_id={PID}", headers=PRIYA).json()
    assert "u-ravi" not in {s["presenter"]["user_id"] for s in plan["sessions"] if s["presenter"]}

    back = client.post("/api/org/members/u-ravi/reactivate", headers=ALEX)
    assert back.status_code == 200 and back.json()["status"] == "active" and "needs_reassignment" not in back.json()
    assert client.get("/api/me", headers=RAVI).status_code == 200
    assert [e["target"] for e in _audit(client, "member.reactivated")] == ["u-ravi"]
    assert _told(client, "u-ravi")[-1] == "Alex (Admin) reactivated your account. You can sign in again."
    assert client.post("/api/org/members/u-ravi/reactivate", headers=ALEX).status_code == 200
    assert len(_audit(client, "member.reactivated")) == 1
