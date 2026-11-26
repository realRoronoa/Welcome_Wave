"""KT call review: the transcript is the managers', and they book follow-up KT sessions from it in Approvals.
Demo mode: the seeded project, whose recorded call 'KT #1' gives the KT planner reasons for a session with Ravi."""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

PID = "proj-demo"
ADMIN, MANAGER, HIRE, RAVI = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-vijay", "u-ravi"))


@pytest.fixture
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _review(c):
    [a] = [a for a in c.get(f"/api/approvals?project_id={PID}", headers=MANAGER).json() if a["kind"] == "kt_followup"]
    return a


def test_only_managers_read_the_transcript(client):
    for who in (MANAGER, ADMIN):
        body = client.get("/api/meetings/mtg-1/notes", headers=who).json()
        assert body["transcript"] == "full" and len(body["notes"]["utterances"]) > 3
    for who in (HIRE, RAVI):
        body = client.get("/api/meetings/mtg-1/notes", headers=who).json()
        assert body["transcript"] == "managers_only" and body["notes"]["utterances"] == []
        # the rest of the notes stay in the onboarding flow
        assert body["notes"]["summary"] and body["notes"]["action_items"] and body["notes"]["kt_links"]


def test_a_recorded_call_asks_the_managers_for_a_review(client):
    a = _review(client)
    assert a["payload"] == {"meeting_id": "mtg-1", "title": "KT #1: How validation works"} and a["status"] == "pending"
    notes = [n for n in client.app.state.container.store.notifications if n["user_id"] == "u-priya" and n["link"].startswith(f"/approvals/{a['id']}")]
    assert len(notes) == 1 and "transcript" in notes[0]["text"]


def test_followups_are_the_planner_sessions_the_call_gave_reasons_for(client):
    sessions = client.get("/api/meetings/mtg-1/followups", headers=MANAGER).json()["sessions"]
    assert len(sessions) == 1
    [s] = sessions
    assert s["presenter"]["user_id"] == "u-ravi" and s["booked"] is None
    kinds = {r["kind"] for i in s["items"] for r in i["reasons"] if r["ref"].get("meeting_id") == "mtg-1"}
    assert kinds == {"action_item", "named_not_shown"}
    for who in (HIRE, RAVI):
        assert client.get("/api/meetings/mtg-1/followups", headers=who).status_code == 403


def test_approving_the_review_books_the_chosen_followups(client):
    a = _review(client)
    [s] = client.get("/api/meetings/mtg-1/followups", headers=MANAGER).json()["sessions"]
    r = client.post(f"/api/approvals/{a['id']}/decide", json={"approve": [s["key"]]}, headers=MANAGER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approval"]["status"] == "approved" and [b["key"] for b in body["result"]["booked"]] == [s["key"]]
    m = client.app.state.container.store.get_meeting(body["result"]["booked"][0]["meeting_id"])
    assert m.status == "scheduled" and m.kt_key == s["key"] and m.created_by == "u-priya"
    # Ravi was sent the agenda; the review is no longer waiting, and its notice is closed
    store = client.app.state.container.store
    assert any(n["user_id"] == "u-ravi" and n["kind"] == "kt_request" for n in store.notifications)
    assert all(x["kind"] != "kt_followup" for x in client.get(f"/api/approvals?project_id={PID}", headers=MANAGER).json())
    assert all(n["read_at"] for n in store.notifications if n["link"].startswith(f"/approvals/{a['id']}"))
    [after] = client.get("/api/meetings/mtg-1/followups", headers=MANAGER).json()["sessions"]
    assert after["booked"]["meeting_id"] == m.id
    # deciding twice is refused
    assert client.post(f"/api/approvals/{a['id']}/decide", json={"approve": []}, headers=MANAGER).status_code == 409


def test_a_review_can_be_approved_without_booking_and_bad_keys_are_refused(client):
    a = _review(client)
    assert client.post(f"/api/approvals/{a['id']}/decide", json={"approve": ["not-a-key"]}, headers=MANAGER).status_code == 422
    r = client.post(f"/api/approvals/{a['id']}/decide", json={"approve": []}, headers=MANAGER)
    assert r.status_code == 200 and r.json()["result"] == {"booked": [], "skipped": []}
    assert r.json()["approval"]["status"] == "approved"


def test_a_session_booked_meanwhile_counts_as_booked_and_an_unknown_one_is_skipped(client):
    a = _review(client)
    [s] = client.get("/api/meetings/mtg-1/followups", headers=MANAGER).json()["sessions"]
    assert client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": s["key"]}, headers=RAVI).status_code == 200
    r = client.post(f"/api/approvals/{a['id']}/decide", json={"approve": [s["key"], "0123456789ab"]}, headers=MANAGER)
    assert r.status_code == 200, r.text
    assert [b["key"] for b in r.json()["result"]["booked"]] == [s["key"]] and r.json()["result"]["skipped"] == ["0123456789ab"]


def test_only_managers_decide_a_review(client):
    a = _review(client)
    assert client.post(f"/api/approvals/{a['id']}/decide", json={"approve": []}, headers=RAVI).status_code == 403
    assert client.post(f"/api/approvals/{a['id']}/decide", json={"approve": []}, headers=HIRE).status_code == 403
