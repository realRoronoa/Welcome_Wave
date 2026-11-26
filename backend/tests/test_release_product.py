"""Release round, product completeness (G2): the journeys that had no way through in the app.

E2E-3  an onboarding started without a program (Settings' older "Start onboarding") is adopted by Enrol, not refused for good
E2E-7  a hire's GitHub username can be set or corrected after the invitation (by a manager, or by the hire)
PRIV-9 a KT call's recording, transcript, notes, KT links and indexed windows can be removed
PRIV-8 one person's personal data can be erased; deleting the organisation revokes its Google grant
PRIV-4 the hire's visibility page says what managers and admins can read about their questions
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.memory import LocalBlobStorage, MemoryStore
from app.config import Settings, get_settings
from app.lifecycle import orgdata
from app.main import create_app
from app.services import scheduling as S

PID, ORG = "proj-demo", "org-demo"
ADMIN, MANAGER, HIRE, RAVI, EXEC = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-vijay", "u-ravi", "u-exec"))


@pytest.fixture
def client(tmp_path):
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.app.state.container.deps.blobs = LocalBlobStorage(str(tmp_path / "blobs"))
        yield c
    get_settings.cache_clear()


def _store(c):
    return c.app.state.container.store


def _audits(c, action):
    return c.get(f"/api/audit?action={action}", headers=ADMIN).json()["items"]


# ---------------------------------------------------------------- E2E-3
def _joined_hire(c, email="nora@example.com", name="Nora"):
    r = c.post(f"/api/projects/{PID}/invitations", json={"email": email, "role": "new_hire", "name": name}, headers=MANAGER)
    assert r.status_code == 201, r.text
    inv = next(i for i in c.get("/api/demo/people", headers=MANAGER).json()["invited"] if i["email"] == email)
    joined = c.post("/api/demo/invitations/accept", json={"project_id": PID, "invitation_id": inv["invitation_id"]}, headers=MANAGER)
    assert joined.status_code == 200, joined.text
    return joined.json()


def _template(c):
    return next(t for t in c.get("/api/programs/templates", headers=MANAGER).json() if t["active"])


def _plan_approval(c, ob_id):
    return next(a for a in c.get(f"/api/approvals?project_id={PID}", headers=MANAGER).json()
                if a["kind"] == "task_plan" and a["status"] == "pending" and any(p["onboarding_id"] == ob_id for p in a["payload"]["plans"]))


def test_an_onboarding_started_without_a_program_is_adopted_by_enrol(client):
    nora = _joined_hire(client)
    legacy = client.post(f"/api/projects/{PID}/onboardings", json={"new_hire_id": nora["id"], "start_date": "2026-11-02"}, headers=MANAGER)
    assert legacy.status_code == 202, legacy.text
    ob = legacy.json()["id"]
    detail = client.get(f"/api/projects/{PID}", headers=MANAGER).json()
    assert next(o for o in detail["onboardings"] if o["id"] == ob)["has_program"] is False
    assert next(o for o in detail["onboardings"] if o["new_hire_id"] == "u-vijay")["has_program"] is True
    assert client.get(f"/api/onboardings/{ob}/program", headers=MANAGER).status_code == 404  # the lifecycle cannot see it yet

    body = {"project_id": PID, "email": nora["email"], "name": "Nora", "start_date": "2026-11-09", "template_id": _template(client)["id"]}
    # its first plan waits in Approvals: the enrolment's plan would be dropped as a duplicate of the same run, so it waits
    r = client.post("/api/onboardings/enrol", json=body, headers=MANAGER)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_in_progress"

    plan = _plan_approval(client, ob)
    decided = client.post(f"/api/approvals/{plan['id']}/decide",
                          json={"approve": [t["id"] for t in plan["payload"]["plans"][0]["tasks"]]}, headers=MANAGER)
    assert decided.status_code == 200, decided.text
    r = client.post("/api/onboardings/enrol", json=body, headers=MANAGER)  # refused for good before the fix
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["onboarding"]["id"] == ob and out["adopted"] is True and "keeps its start date" in out["next"]
    assert client.get(f"/api/onboardings/{ob}/program", headers=MANAGER).status_code == 200
    # the plan approved before adoption is not the program's plan: the new one waits, and nothing claims it is in place
    assert out["plan"]["status"] == "awaiting_approval", out["plan"]
    assert _plan_approval(client, ob)["payload"]["plans"][0].get("enrolment")  # the 30/60/90 plan, drafted again
    assert next(o for o in client.get(f"/api/projects/{PID}", headers=MANAGER).json()["onboardings"] if o["id"] == ob)["has_program"]
    # a hire who already has a program is still refused
    again = client.post("/api/onboardings/enrol", json=body, headers=MANAGER)
    assert again.status_code == 409 and "already has an active onboarding" in again.json()["detail"]


# ---------------------------------------------------------------- E2E-7
def test_a_manager_sets_and_corrects_a_hires_github_username(client):
    store = _store(client)
    url = f"/api/projects/{PID}/members/u-arjun"
    assert client.patch(url, json={"github_login": "arjun-codes"}, headers=RAVI).status_code == 403
    assert client.patch(url, json={"github_login": "bad login!"}, headers=MANAGER).status_code == 422
    r = client.patch(url, json={"github_login": "arjun-codes"}, headers=MANAGER)
    assert r.status_code == 200 and r.json()["github_login"] == "arjun-codes"
    assert [u.id for u in store.users_by_github(ORG, ["ARJUN-CODES"])] == ["u-arjun"]  # what pull requests are matched by
    assert any("arjun-codes" in n["text"] for n in store.notifications_for("u-arjun"))
    taken = client.patch(url, json={"github_login": "Vijay"}, headers=MANAGER)  # Vijay's, in another case
    assert taken.status_code == 409 and taken.json()["detail"]["code"] == "github_taken"
    assert client.patch(url, json={"github_login": ""}, headers=MANAGER).json()["github_login"] is None  # cleared
    assert client.patch(f"/api/projects/{PID}/members/no-such-user", json={"github_login": "x"}, headers=MANAGER).status_code == 404
    entries = _audits(client, "member.github_changed")
    assert entries and all(e["target"] == "u-arjun" for e in entries)


def test_a_hire_sets_their_own_github_username(client):
    r = client.patch("/api/me", json={"github_login": "vijay-codes"}, headers=HIRE)
    assert r.status_code == 200 and r.json()["github_login"] == "vijay-codes" and r.json()["projects"]
    assert client.get("/api/me", headers=HIRE).json()["github_login"] == "vijay-codes"
    assert client.patch("/api/me", json={"github_login": "vijay-codes"}, headers=EXEC).status_code == 403  # read-only role


def test_an_admin_can_invite_an_executive(client):
    r = client.post(f"/api/projects/{PID}/invitations", json={"email": "vp@example.com", "role": "executive"}, headers=ADMIN)
    assert r.status_code == 201 and r.json()["invited"]["role"] == "executive"
    assert client.post(f"/api/projects/{PID}/invitations", json={"email": "vp2@example.com", "role": "executive"},
                       headers=MANAGER).status_code == 403


# ---------------------------------------------------------------- PRIV-9
def test_a_manager_removes_a_kt_recording_and_everything_it_produced(client):
    c = client.app.state.container
    store, index = c.store, c.index
    uri = c.deps.blobs.put("gs://local/recordings/proj-demo/mtg-1/call.mp4", b"\x00\x00\x00\x18ftypmp42video", "video/mp4")
    store.update_meeting("mtg-1", recording_uri=uri)
    path = c.deps.blobs.path(uri)
    title = store.get_meeting("mtg-1").title
    windows = [cid for cid, ch in index.chunks.items() if ch.source_type == "meeting"]
    assert windows and store.meeting_notes("mtg-1") and [l for l in store.kt_links(PID) if l.meeting_id == "mtg-1"]
    stale = []
    mark, store.mark_answers_stale = store.mark_answers_stale, lambda ids: stale.append(sorted(ids)) or mark(ids)

    url = "/api/meetings/mtg-1/recording"
    assert client.request("DELETE", url, json={"confirm": title}, headers=RAVI).status_code == 403
    wrong = client.request("DELETE", url, json={"confirm": "not the title"}, headers=MANAGER)
    assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "confirm"
    assert store.meeting_notes("mtg-1")  # nothing changed

    r = client.request("DELETE", url, json={"confirm": title}, headers=MANAGER)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["meeting"]["status"] == "ended" and out["meeting"]["recording_uri"] is None and out["meeting"]["summary"] is None
    assert out["removed"] == {"recording": True, "notes": True, "transcript_windows": len(windows)}
    assert not os.path.exists(path)  # the video file is gone
    assert store.meeting_notes("mtg-1") is None and not [l for l in store.kt_links(PID) if l.meeting_id == "mtg-1"]
    assert not [ch for ch in index.chunks.values() if ch.source_type == "meeting"]
    notes = client.get("/api/meetings/mtg-1/notes", headers=MANAGER)
    assert notes.status_code == 404 or (not notes.json()["notes"] and not notes.json()["transcript"]), notes.text
    assert stale == [sorted(windows)]  # answers that cited the call's windows are marked out of date
    [entry] = _audits(client, "meeting.recording_deleted")
    assert entry["target"] == "mtg-1"
    # asking again changes nothing and still answers
    assert client.request("DELETE", url, json={"confirm": title}, headers=MANAGER).status_code == 200


def test_a_recording_being_processed_cannot_be_removed(client):
    store = _store(client)
    store.update_meeting("mtg-1", status="processing")
    r = client.request("DELETE", "/api/meetings/mtg-1/recording", json={"confirm": store.get_meeting("mtg-1").title}, headers=MANAGER)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "processing"
    assert store.meeting_notes("mtg-1")


# ---------------------------------------------------------------- PRIV-8
def test_an_admin_erases_one_persons_personal_data(client):
    store = _store(client)
    client.post("/api/chat", json={"project_id": PID, "question": "Where does check_labels report missing labels?"}, headers=HIRE)
    assert [m for m in store.chats.values() if m["user_id"] == "u-vijay"]
    url = "/api/org/people/erase"
    assert client.post(url, json={"email": "vijay@demo.dev", "confirm": "vijay@demo.dev"}, headers=MANAGER).status_code == 403
    r = client.post(url, json={"email": "vijay@demo.dev", "confirm": "someone@else.dev"}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "confirm"
    assert client.post(url, json={"email": "nobody@demo.dev", "confirm": "nobody@demo.dev"}, headers=ADMIN).status_code == 404
    me = client.post(url, json={"email": "admin@demo.dev", "confirm": "admin@demo.dev"}, headers=ADMIN)
    assert me.status_code == 409 and me.json()["detail"]["code"] == "self"

    r = client.post(url, json={"email": "Vijay@demo.dev", "confirm": "vijay@demo.dev"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["erased"] == "u-vijay" and out["removed"]["chat_messages"] >= 1 and out["kept"]
    u = store.get_user("u-vijay")
    assert u.name == "Erased person" and u.email == "erased-u-vijay@erased.invalid" and u.github_login is None
    assert u.status == "deactivated"
    assert not [m for m in store.chats.values() if m["user_id"] == "u-vijay"]
    assert not [r for r in store.expert_requests.values() if r.get("asked_by") == "u-vijay"]
    assert not store.notifications_for("u-vijay")
    assert client.get("/api/me", headers=HIRE).status_code in {401, 403}  # the login is unbound
    assert store.get_onboarding("ob-vijay") is not None  # the organisation's records keep their ids
    [entry] = _audits(client, "member.erased")
    assert "vijay@" not in str(entry).lower()  # counts only


def test_deleting_the_organisation_revokes_its_google_grant(tmp_path):
    settings = Settings(mode="self_hosted", data_dir=str(tmp_path), token_encryption_key="k" * 44)
    store = MemoryStore()
    store.save_calendar_connection(ORG, "google", "kt@example.com", S.encrypt_token(settings, "1//refresh"), ["calendar"], "u-admin")
    revoked = []

    def google(request: httpx.Request) -> httpx.Response:
        revoked.append(request.content.decode())
        return httpx.Response(200)

    lifecycle = SimpleNamespace(org_blob_uris=lambda org: [], org_thread_ids=lambda org: [], delete_org=lambda org: {"users": 3})
    c = SimpleNamespace(settings=settings, store=store, lifecycle=lifecycle, checkpointer=None, deps=SimpleNamespace(blobs=None),
                        extras={"google_transport": httpx.MockTransport(google)})
    counts = orgdata.delete(c, ORG)
    assert counts["google_grant_revoked"] == 1 and revoked == ["token=1%2F%2Frefresh"]

    def down(request):
        return httpx.Response(500)

    c.extras["google_transport"] = httpx.MockTransport(down)
    assert orgdata.delete(c, ORG)["google_grant_revoke_failed"] == 1  # logged, never stops the deletion
    c.settings = Settings(mode="demo", data_dir=str(tmp_path))
    assert "google_grant_revoked" not in orgdata.delete(c, ORG)  # the demo's calendar is built in: nothing to revoke


# ---------------------------------------------------------------- PRIV-4
def test_the_hire_is_told_what_managers_and_admins_read_about_their_questions(client):
    client.post("/api/ask/route", json={"project_id": PID, "question": "Where does check_labels report missing labels?"}, headers=HIRE)
    body = client.get("/api/onboardings/ob-vijay/visibility", headers=HIRE).json()
    q, runs, export = body["data"]["questions"], body["data"]["assistant_runs"], body["data"]["export"]
    assert q["visible_to"] == ["hire"]  # the counts themselves reach nobody else
    assert export["visible_to"] == ["hire", "admin"] and "in full" in export["how"] and export["value"]["conversations"] >= 1
    assert runs["visible_to"] == ["hire", "manager"] and runs["value"] and all("summary" in r for r in runs["value"])
    assert "missing labels" not in str(runs["value"])  # never the question's words
    managers_see = client.get(f"/api/runs?project_id={PID}", headers=MANAGER).json()
    assert any(r.get("agent") == "guide" for r in managers_see)  # what the field describes is what managers can read
    assert not any("yours alone" in p and "assistant" in p for p in body["private"])


# ---------------------------------------------------------------- the same store methods on Postgres (row-level security on)
try:
    from tests import test_postgres as pg
except Exception:  # noqa: BLE001 - the Postgres driver is optional for the in-memory tests above
    pg = None
if pg is not None:
    db, engine, store = pg.db, pg.engine, pg.store  # the scratch database and its fixtures (tests/test_postgres.py)
needs_pg = pytest.mark.skipif(pg is None or not pg.ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")


@needs_pg
def test_postgres_github_erasure_and_recording_removal(store, engine):  # noqa: F811 - pytest fixtures from tests/test_postgres.py
    from app.adapters.postgres import PgIndex, current_org
    from app.agents.core.schemas import KTLink, MeetingResult, QAResult, Utterance
    from app.services.chunking import chunk_transcript
    from app.services.embeddings import HashEmbedder

    admin, project = pg._org(store, "g2")
    pid, org = project.id, admin.org_id
    store.create_invitation(org, pid, "hire@g2.example.com", "new_hire", "Nia", None, admin.id)
    current_org.set(None)
    hire = store.accept_invitations("uid-g2-hire", "hire@g2.example.com", "Nia", org)
    current_org.set(org)

    # E2E-7: set, find by it (any case), clear; another organisation's user is not found
    assert store.set_github_login(org, hire.id, "nia-dev").github_login == "nia-dev"
    assert [u.id for u in store.users_by_github(org, ["NIA-DEV"])] == [hire.id]
    assert store.set_github_login(org, hire.id, None).github_login is None
    assert store.set_github_login(org, "not-a-uuid", "x") is None

    # PRIV-9: the call's rows, links and index windows go; the call stays, as held
    m = store.create_meeting(pid, "KT #1", admin.id)
    store.update_meeting(m.id, recording_uri="gs://local/recordings/x.mp4")
    words = [Utterance(start_s=0, end_s=5, speaker="Ravi", text="validate.py checks the labels")]
    store.save_meeting_result(MeetingResult(meeting_id=m.id, project_id=pid, title=m.title, summary="s", decisions=[], action_items=[],
                                            terms=[], utterances=words, screen_moments=[], kt_links=[]))
    store.add_kt_links(pid, [KTLink(meeting_id=m.id, file_path="engine/validate.py", start_s=0, end_s=5, confidence=0.9, method="path")])
    index = PgIndex(engine, HashEmbedder(768))
    index.upsert(chunk_transcript(pid, m.id, m.title, words))
    gone = index.delete_meeting(pid, m.id)
    assert len(gone) == 1 and index.delete_meeting(pid, m.id) == []
    store.erase_meeting_content(m.id)
    after = store.get_meeting(m.id)
    assert after.status == "ended" and after.recording_uri is None and after.summary is None
    assert store.meeting_notes(m.id) is None and not [k for k in store.kt_links(pid) if k.meeting_id == m.id]

    # PRIV-8: anyone of the organisation by email, anonymised; their chat and notifications deleted; ids kept
    store.save_chat(pid, hire.id, "where are labels checked?", QAResult(status="answered", answer="in validate.py", citations=[],
                                                                         confidence=0.9))
    store.notify(hire.id, "account", "hello", "")
    assert store.find_person(org, "HIRE@g2.example.com").id == hire.id
    removed = store.erase_person(org, hire.id)
    assert removed["chat_messages"] == 1 and removed["notifications"] >= 1
    u = store.get_user(hire.id)
    assert u.name == "Erased person" and u.email == f"erased-{hire.id}@erased.invalid" and u.status == "deactivated"
    assert store.find_person(org, "hire@g2.example.com") is None and store.notifications_for(hire.id) == []
    assert not store.is_member(pid, hire.id)
    current_org.set(None)
    assert store.user_by_firebase("uid-g2-hire") is None  # the login is unbound
