"""Who sees a hire's tasks and pull request reviews, and how a hire's questions are counted (demo mode, the seeded project).

docs/PERMISSIONS.md: a hire's data is the hire's, their mentor's and the project's managers'. In the seed Ravi mentors Vijay
and Tom, Meera mentors Arjun and Sara, and Vijay's plan waits for Priya's approval."""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

PID = "proj-demo"
PRIYA, VIJAY, RAVI, MEERA, ELENA, ARJUN = ({"X-Demo-User": u} for u in ("u-priya", "u-vijay", "u-ravi", "u-meera", "u-exec", "u-arjun"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_RETRIES", "1")
    monkeypatch.setenv("RETRY_SLEEP_S", "0")
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _tasks(c, who):
    r = c.get(f"/api/tasks?project_id={PID}", headers=who)
    assert r.status_code == 200, r.text
    return r.json()


def _assignees(rows):
    return {t["assignee"] for t in rows}


def _approve_plan(c):
    plan = next(a for a in c.get(f"/api/approvals?project_id={PID}", headers=PRIYA).json() if a["kind"] == "task_plan")
    ids = [t["id"] for t in plan["payload"]["plans"][0]["tasks"]]
    assert c.post(f"/api/approvals/{plan['id']}/decide", json={"approve": ids}, headers=PRIYA).status_code == 200
    return ids


def test_a_plan_awaiting_approval_is_the_managers_alone(client):
    proposed = {t["id"] for t in _tasks(client, PRIYA) if t["status"] == "proposed"}
    assert proposed  # Vijay's seeded plan
    for who in (VIJAY, RAVI, MEERA, ELENA):  # the hire, his mentor, another expert, an executive
        assert not proposed & {t["id"] for t in _tasks(client, who)}
    ids = _approve_plan(client)
    assert set(ids) <= {t["id"] for t in _tasks(client, VIJAY)} and set(ids) <= {t["id"] for t in _tasks(client, RAVI)}


def test_experts_see_only_their_mentees_tasks(client):
    _approve_plan(client)
    assert _assignees(_tasks(client, RAVI)) == {"Vijay (New hire)", "Tom (New hire)"}
    assert _assignees(_tasks(client, MEERA)) == {"Arjun (New hire)", "Sara (New hire)"}
    assert _assignees(_tasks(client, PRIYA)) >= {"Vijay (New hire)", "Tom (New hire)", "Arjun (New hire)", "Sara (New hire)"}
    assert _assignees(_tasks(client, ARJUN)) == {"Arjun (New hire)"} and _tasks(client, ELENA) == []
    assert not any(t["review_pending"] for t in _tasks(client, RAVI))  # what waits in Approvals is the managers' business


def test_a_review_is_read_by_the_hire_their_mentor_and_managers(client):
    _approve_plan(client)
    code = next(t for t in _tasks(client, VIJAY) if t["kind"] == "code")
    review_id = client.post("/api/demo/pull-request", json={"task_id": code["id"]}, headers=VIJAY).json()["review_id"]
    assert review_id
    for who in (VIJAY, RAVI, PRIYA, {"X-Demo-User": "u-admin"}):
        assert client.get(f"/api/reviews/{review_id}", headers=who).status_code == 200
    for who in (MEERA, ARJUN, ELENA):  # another hire's mentor, another hire, an executive: as if it did not exist
        assert client.get(f"/api/reviews/{review_id}", headers=who).status_code == 404
    assert {t["review_id"] for t in _tasks(client, RAVI) if t["id"] == code["id"]} == {review_id}
    assert not any(t["id"] == code["id"] for t in _tasks(client, MEERA))


def test_an_expert_answer_takes_a_question_off_the_unanswered_count(client):
    """A question the assistant handed to a person counted as unanswered even after they answered it. The counts are the hire's
    own now: the dashboard carries no per-hire activity."""
    def mine():
        return client.get("/api/onboardings/ob-vijay/visibility", headers=VIJAY).json()["data"]["questions"]["value"]

    before = mine()
    asked = client.post("/api/chat", json={"project_id": PID, "question": "How is the mutate module benchmarked?"}, headers=VIJAY).json()
    assert asked["status"] == "not_enough_information" and asked["routed_to"] == "u-ravi"
    waiting = mine()
    assert (waiting["asked"], waiting["unanswered"]) == (before["asked"] + 1, before["unanswered"] + 1)
    [req] = [r for r in client.get("/api/experts/requests", headers=RAVI).json() if r["message_id"] == asked["message_id"]]
    assert client.post(f"/api/experts/requests/{req['id']}/answer", json={"answer": "With pytest-benchmark."}, headers=RAVI).status_code == 200
    after = mine()
    assert (after["asked"], after["unanswered"]) == (waiting["asked"], before["unanswered"])
    assert after["answered_by_expert"] >= 1
    # no manager-facing surface carries them: not the dashboard's rows, and the visibility page says only the hire sees the counts
    rows = client.get(f"/api/dashboard?project_id={PID}", headers=PRIYA).json()["people"]
    row = next(p for p in rows if p["onboarding_id"] == "ob-vijay")
    assert not {"questions_asked", "questions_unanswered", "tasks_done", "tasks_total", "progress", "at_risk"} & set(row)
    field = client.get("/api/onboardings/ob-vijay/visibility", headers=VIJAY).json()["data"]["questions"]
    assert field["visible_to"] == ["hire"]


def test_the_hire_is_told_how_their_questions_are_counted(client):
    private = client.get("/api/onboardings/ob-vijay/visibility", headers=VIJAY).json()["private"]
    assert any("never your name, never your words" in line for line in private)
