"""Recording consent, the notice on invitations, removal requests, self-hosted retention (PRIV-9) and the copy at the Gemini
Files API (PRIV-5): app/services/recordings.py, app/api/recordings.py, app/api/uploads.py, app/services/scheduling.py,
app/agents/core/llm.py. Nothing leaves the machine. Postgres: tests/test_recording_consent_postgres.py."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import legal
from app.adapters import recording_store
from app.adapters.memory import LocalBlobStorage, MemoryScheduler
from app.agents.core.llm import GeminiLLM, LLMError, MediaRef
from app.services import recordings
from app.services import scheduling as S
from tests.test_release_gcp import MP4, FakeGcs, _meeting, _ready_events, api, as_mode  # noqa: F401 - the app in any mode
from tests.test_scheduling import FakeGoogle, _container, _google_session, client, demo  # noqa: F401 - the demo app and container

PID, ORG = "proj-demo", "org-demo"
ADMIN, MANAGER, HIRE, RAVI, MEERA = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-vijay", "u-ravi", "u-meera"))
VERSION = legal.VERSIONS["recording"]


def _audit(c, action, target=None):
    """Audit entries of one action (for one target: the demo's own history has a consent entry for KT #1)."""
    return [a for a in c.store.audit_rows if a["action"] == action and (target is None or a["target"] == target)]


def _held(client, title="KT #4"):  # noqa: F811
    """A Google Calendar session of the demo that has been held: the demo calendar has a recording for it."""
    started = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return client.post("/api/meetings", json={"project_id": PID, "title": title, "starts_at": started,
                                              "recording_promise": True}, headers=MANAGER).json()["meeting"]


# ---------------------------------------------------------------- consent: import
def test_a_drive_import_is_refused_until_someone_confirms_consent(client):  # noqa: F811
    c = _container(client)
    m = _held(client)
    # A meeting booked by an older client can lack the booking promise. The import must still refuse it.
    c.store.update_meeting(m["id"], recording_consent=None)
    for body in ({}, {"consent": False}):
        r = client.post(f"/api/meetings/{m['id']}/recording/import", json=body, headers=RAVI)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "consent_required", r.text
        assert "told it is recorded and agreed" in r.json()["detail"]["message"]
    assert c.store.get_meeting(m["id"]).status == "scheduled" and not _audit(c, "recording.consent_confirmed", m["id"])
    assert not [e for _, e in c.deps.bus.published if e["type"] == "meeting.recording_ready" and e["meeting_id"] == m["id"]]

    r = client.post(f"/api/meetings/{m['id']}/recording/import", json={"consent": True}, headers=RAVI)
    assert r.status_code == 202, r.text
    consent = r.json()["meeting"]["recording_consent"]
    assert consent["by"] == "u-ravi" and consent["by_name"] == "Ravi (Senior)" and consent["notice_version"] == VERSION
    assert datetime.fromisoformat(consent["at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    [entry] = _audit(c, "recording.consent_confirmed", m["id"])
    assert (entry["actor_id"], entry["target"], entry["detail"]) == ("u-ravi", m["id"], {"notice_version": VERSION, "via": "import"})
    # the session shows it to everyone who can see the session
    listed = next(x for x in client.get(f"/api/meetings?project_id={PID}", headers=HIRE).json() if x["id"] == m["id"])
    assert listed["recording_consent"]["by_name"] == "Ravi (Senior)"


def test_the_import_service_never_fetches_a_session_nobody_confirmed(demo):  # noqa: F811
    fake = FakeGoogle(attachments=[{"fileId": "drv1", "mimeType": "video/mp4"}])
    m = _google_session(demo, fake, agreed=False)
    before = len(fake.calls)
    with pytest.raises(recordings.ConsentRequired):
        S.import_recording(demo, ORG, m, "u-priya")
    assert len(fake.calls) == before  # Google was not asked for anything
    assert demo.store.get_meeting(m.id).status == "scheduled"


def test_an_import_that_cannot_happen_says_why_before_asking_for_consent(client):  # noqa: F811
    manual = client.post("/api/meetings", json={"project_id": PID, "title": "Zoom", "provider": "manual"}, headers=MANAGER).json()["meeting"]
    r = client.post(f"/api/meetings/{manual['id']}/recording/import", headers=MANAGER)
    assert r.status_code == 409 and "Upload" in r.json()["detail"]
    assert _container(client).store.get_meeting(manual["id"]).recording_consent is None


# ---------------------------------------------------------------- consent: its own route
def test_consent_is_confirmed_once_by_someone_who_may_add_the_recording(client):  # noqa: F811
    c = _container(client)
    m = _held(client)
    url = f"/api/meetings/{m['id']}/recording/consent"
    assert client.post(url, json={"confirm": True}).status_code == 401
    assert client.post(url, json={"confirm": True}, headers=HIRE).status_code == 403  # not a manager, an expert or the booker
    r = client.post(url, json={}, headers=MANAGER)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "consent_required"
    r = client.post(url, json={"confirm": True}, headers=MANAGER)
    assert r.status_code == 200 and r.json()["meeting"]["recording_consent"]["by"] == "u-priya"
    assert r.json()["statement"] == recordings.CONSENT_STATEMENT
    first = r.json()["meeting"]["recording_consent"]
    again = client.post(url, json={"confirm": True}, headers=RAVI)  # a second confirmation changes nothing
    assert again.status_code == 200 and again.json()["meeting"]["recording_consent"] == first
    assert [a["detail"]["via"] for a in _audit(c, "recording.consent_confirmed", m["id"])] == ["confirm"]
    assert client.post(f"/api/meetings/{m['id']}/recording/import", json={}, headers=RAVI).status_code == 202  # consent is on the session
    assert client.post("/api/meetings/nope/recording/consent", json={"confirm": True}, headers=MANAGER).status_code == 404


def test_a_cancelled_session_takes_no_consent(client):  # noqa: F811
    m = client.post("/api/meetings", json={"project_id": PID, "title": "Off", "starts_at": "2030-01-07T10:00:00Z",
                                             "recording_promise": True}, headers=MANAGER).json()["meeting"]
    assert client.post(f"/api/meetings/{m['id']}/cancel", headers=MANAGER).status_code == 200
    assert client.post(f"/api/meetings/{m['id']}/recording/consent", json={"confirm": True}, headers=MANAGER).status_code == 409


def test_consent_is_not_taken_for_a_session_that_has_not_been_held(client):  # noqa: F811
    """'Everyone in this session was told it is recorded and agreed' cannot be true of a session a week away, and once on
    record the poller would import the recording with nobody asked. Neither route takes it."""
    c = _container(client)
    later = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = client.post("/api/meetings", json={"project_id": PID, "title": "Next week", "starts_at": later,
                                             "recording_promise": True}, headers=MANAGER).json()["meeting"]
    r = client.post(f"/api/meetings/{m['id']}/recording/consent", json={"confirm": True}, headers=MANAGER)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_held_yet" and "not been held yet" in r.json()["detail"]["message"]
    r = client.post(f"/api/meetings/{m['id']}/recording/import", json={"consent": True}, headers=RAVI)
    assert r.status_code == 409 and "has not started yet" in r.json()["detail"]
    consent = c.store.get_meeting(m["id"]).recording_consent
    assert consent is not None and consent.kind == "promised_at_booking"
    assert not _audit(c, "recording.consent_confirmed", m["id"])


def test_removing_a_recording_clears_its_consent_so_adding_it_again_needs_a_new_confirmation(client):  # noqa: F811
    """Someone says they did not agree; a manager removes the recording. The confirmation was about that recording."""
    c = _container(client)
    m = _held(client)
    assert client.post(f"/api/meetings/{m['id']}/recording/import", json={"consent": True}, headers=RAVI).status_code == 202
    c.store.update_meeting(m["id"], status="ready")
    assert client.post(f"/api/meetings/{m['id']}/recording/removal-requests", json={"reason": "I never agreed."}, headers=HIRE).status_code == 201
    r = client.request("DELETE", f"/api/meetings/{m['id']}/recording", json={"confirm": m["title"]}, headers=MANAGER)
    assert r.status_code == 200 and r.json()["removal_requests_closed"] == 1 and r.json()["meeting"]["recording_consent"] is None
    assert _audit(c, "meeting.recording_deleted", m["id"])[0]["detail"]["consent_cleared"] is True
    assert len(_audit(c, "recording.consent_confirmed", m["id"])) == 1  # the log keeps who confirmed the first time
    again = client.post(f"/api/meetings/{m['id']}/recording/import", json={}, headers=RAVI)
    assert again.status_code == 422 and again.json()["detail"]["code"] == "consent_required"
    assert client.post(f"/api/meetings/{m['id']}/recording/import", json={"consent": True}, headers=MANAGER).status_code == 202
    assert c.store.get_meeting(m["id"]).recording_consent.by == "u-priya"
    assert sorted(a["actor_id"] for a in _audit(c, "recording.consent_confirmed", m["id"])) == ["u-priya", "u-ravi"]


def test_erasing_a_person_blanks_the_reason_they_wrote_and_says_what_stays(client):  # noqa: F811
    c = _container(client)
    r = client.post("/api/meetings/mtg-1/recording/removal-requests", json={"reason": "I read out my home address, 12 Rose Lane."}, headers=HIRE)
    assert r.status_code == 201
    hire = c.store.get_user("u-vijay")
    r = client.post("/api/org/people/erase", json={"email": hire.email, "confirm": hire.email}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["removed"]["recording_removal_reasons"] == 1, r.text
    mine = [x for x in recording_store.removal_requests(c.store, meeting_id="mtg-1") if x["requested_by"] == "u-vijay"]
    assert [(x["reason"], x["status"]) for x in mine] == [("", "open")]  # the row stays, the words do not
    assert "Rose Lane" not in client.get("/api/org/export", headers=ADMIN).text
    kept = " ".join(r.json()["kept"])
    for phrase in ("transcripts they speak in", "verified from a question", "without the reason they wrote", "network and browser"):
        assert phrase in kept, phrase


# ---------------------------------------------------------------- consent: the poller
def test_the_poller_asks_the_booker_once_and_imports_only_after_consent(demo):  # noqa: F811
    fake = FakeGoogle(attachments=[{"fileId": "drv1", "mimeType": "video/mp4"}])
    m = _google_session(demo, fake, agreed=False)
    demo.extras["scheduler"] = S.scheduler_for(demo, ORG)
    before = len(fake.calls)
    out = S.poll_recordings(demo)
    assert out["imported"] == 0 and out["needs_consent"] == 1 and len(fake.calls) == before  # nothing fetched from Google
    asked = [n for n in demo.store.notifications if n["kind"] == "recording_consent"]
    assert [(n["user_id"], n["link"]) for n in asked] == [("u-priya", f"/meetings?call={m.id}&project={PID}&consent=1")]
    assert "Confirm that everyone agreed" in asked[0]["text"]
    S.poll_recordings(demo)
    assert len([n for n in demo.store.notifications if n["kind"] == "recording_consent"]) == 1  # asked once

    recordings.confirm_consent(demo, demo.store.get_user("u-priya"), demo.store.get_meeting(m.id), "confirm")
    assert all(n["read_at"] for n in demo.store.notifications if n["kind"] == "recording_consent")  # the request is answered
    assert S.poll_recordings(demo)["imported"] == 1 and demo.store.get_meeting(m.id).status == "ready"


# ---------------------------------------------------------------- consent: uploads
def test_an_upload_does_not_start_without_consent(api, tmp_path):  # noqa: F811
    web, c = api
    gcs = FakeGcs()
    as_mode(c, "gcp", gcs, tmp_path, token_encryption_key="k" * 48)
    m = _meeting(c)
    body = {"filename": "kt.mp4", "size": len(MP4), "duration_s": 60}
    r = web.post(f"/api/meetings/{m.id}/recording/upload", json=body, headers=MANAGER)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "consent_required"
    assert not gcs.signed and c.store.get_meeting(m.id).recording_consent is None  # no upload link was made
    # a refusal for any other reason records no consent either
    assert web.post(f"/api/meetings/{m.id}/recording/upload", json={**body, "filename": "kt.pdf", "consent": True}, headers=MANAGER).status_code == 415
    assert c.store.get_meeting(m.id).recording_consent is None

    r = web.post(f"/api/meetings/{m.id}/recording/upload", json={**body, "consent": True}, headers=MANAGER)
    assert r.status_code == 200, r.text
    consent = c.store.get_meeting(m.id).recording_consent
    assert (consent.by, consent.notice_version) == ("u-priya", VERSION)
    assert [a["detail"] for a in _audit(c, "recording.consent_confirmed", m.id)] == [{"notice_version": VERSION, "via": "upload"}]
    # with consent on the session, a second upload (the first was abandoned) needs no new confirmation
    assert web.post(f"/api/meetings/{m.id}/recording/upload", json=body, headers=RAVI).status_code == 200
    assert c.store.get_meeting(m.id).recording_consent.by == "u-priya"


def test_the_one_request_upload_needs_consent_too_and_stores_nothing_without_it(api, tmp_path):  # noqa: F811
    web, c = api
    as_mode(c, "self_hosted", LocalBlobStorage(str(tmp_path / "blobs")), tmp_path)
    m = _meeting(c)
    r = web.post(f"/api/meetings/{m.id}/recording", files={"file": ("kt.mp4", MP4, "video/mp4")}, data={"duration_s": "60"}, headers=MANAGER)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "consent_required"
    assert not list((tmp_path / "blobs").rglob("*.mp4")) and c.store.get_meeting(m.id).status == "scheduled"
    r = web.post(f"/api/meetings/{m.id}/recording", files={"file": ("kt.mp4", MP4, "video/mp4")},
                    data={"duration_s": "60", "consent": "true"}, headers=MANAGER)
    assert r.status_code == 202 and r.json()["meeting"]["recording_consent"]["by"] == "u-priya"


def test_a_file_for_a_session_without_consent_is_not_kept(api, tmp_path):  # noqa: F811
    """An upload id issued before consent was asked for (an older client, a deploy in between) completes to nothing."""
    from app.api import uploads

    web, c = api
    gcs = FakeGcs()
    as_mode(c, "gcp", gcs, tmp_path, token_encryption_key="k" * 48)
    m = _meeting(c)
    uri = f"gs://bucket/recordings/{m.id}/old.mp4"
    gcs.put(uri, MP4, "video/mp4")
    old = uploads.make_upload_id(c.settings, m.id, "u-priya", uri, "video/mp4", len(MP4), 60, int(time.time()) + 600)
    r = web.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": old}, headers=MANAGER)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "consent_required"
    assert uri not in gcs.objects and not _ready_events(c) and c.store.get_meeting(m.id).status == "scheduled"


# ---------------------------------------------------------------- the notice on invitations
def test_calendar_invitations_carry_the_recording_notice_and_its_link(demo):  # noqa: F811
    sched = MemoryScheduler()
    seen = []
    book = sched.create_event
    sched.create_event = lambda event: (seen.append(event), book(event))[1]
    demo.extras["scheduler"] = sched
    priya = demo.store.get_user("u-priya")
    demo.settings = demo.settings.model_copy(update={"public_url": "https://ww.example.com/"})
    S.book(demo, PID, ORG, priya, S.SessionRequest(title="KT: queues", starts_at=datetime.now(timezone.utc) + timedelta(days=1)))
    text = seen[0].description
    assert "This session will be recorded." in text and "If you do not agree to being recorded, tell the organiser" in text
    assert "Recording notice: https://ww.example.com/recording" in text

    later = demo.store.create_meeting(PID, "KT: later", "u-priya", provider="manual")
    S.reschedule(demo, ORG, priya, later.id, S.SessionRequest(title="KT: later", starts_at=datetime.now(timezone.utc) + timedelta(days=2), provider="google"))
    assert "This session will be recorded." in seen[1].description and "https://ww.example.com/recording" in seen[1].description

    demo.settings = demo.settings.model_copy(update={"public_url": ""})  # no public address: the path, never a made-up host
    assert recordings.invitation_notice(demo.settings).endswith("Recording notice: open /recording on your Welcome Wave site.")
    assert recordings.notice_version() == VERSION


# ---------------------------------------------------------------- removal requests
def test_a_removal_request_goes_to_the_managers_and_a_manager_removes_or_declines(client):  # noqa: F811
    c = _container(client)
    title = c.store.get_meeting("mtg-1").title
    url = "/api/meetings/mtg-1/recording/removal-requests"
    assert client.post(url, json={}).status_code == 401
    r = client.post(url, json={"reason": "I read out my home address at 2:10."}, headers=HIRE)
    assert r.status_code == 201 and r.json()["created"] is True, r.text
    req = r.json()["request"]
    assert (req["status"], req["requested_by"], req["requested_by_name"], req["meeting_id"]) == ("open", "u-vijay", "Vijay (New hire)", "mtg-1")
    # nothing was removed: a person decides
    assert c.store.get_meeting("mtg-1").status == "ready" and c.store.meeting_notes("mtg-1") is not None
    told = [n for n in c.store.notifications if n["kind"] == "recording_removal"]
    assert {n["user_id"] for n in told} == {"u-priya", "u-admin"} and all(n["link"].endswith(f"&removal={req['id']}") for n in told)
    assert "Vijay (New hire) asked for the recording of" in told[0]["text"] and "home address" not in told[0]["text"]
    [entry] = _audit(c, "recording.removal_requested")
    assert entry["actor_id"] == "u-vijay" and entry["target"] == "mtg-1" and "home address" not in str(entry["detail"])  # the reason is not audited
    # asking again keeps the first request and tells nobody twice
    again = client.post(url, json={"reason": "again"}, headers=HIRE)
    assert again.status_code == 201 and again.json()["created"] is False and again.json()["request"]["id"] == req["id"]
    assert len([n for n in c.store.notifications if n["kind"] == "recording_removal"]) == len(told)

    # who sees what: managers the project's requests, everyone else their own
    listing = f"/api/recording-removal-requests?project_id={PID}"
    assert [x["id"] for x in client.get(listing + "&status=open", headers=MANAGER).json()["requests"]] == [req["id"]]
    assert [x["id"] for x in client.get(listing + "&status=open", headers=HIRE).json()["requests"]] == [req["id"]]
    assert client.get(listing + "&status=open", headers=RAVI).json()["requests"] == []
    assert any(x["status"] == "declined" for x in client.get(listing, headers=MANAGER).json()["requests"])  # the demo's answered one

    # declining: a manager, once, with a note for whoever asked
    decline = f"{url}/{req['id']}/decline"
    assert client.post(decline, json={"note": "x"}, headers=RAVI).status_code == 403
    assert client.post(f"{url}/nope/decline", json={}, headers=MANAGER).status_code == 404
    r = client.post(decline, json={"note": "The address was cut from the transcript."}, headers=MANAGER)
    assert r.status_code == 200 and (r.json()["request"]["status"], r.json()["request"]["decided_by"]) == ("declined", "u-priya")
    assert client.post(decline, json={}, headers=MANAGER).status_code == 409
    mine = [n for n in c.store.notifications if n["user_id"] == "u-vijay" and n["kind"] == "recording_removal"]
    assert len(mine) == 1 and "declined your request" in mine[0]["text"] and "cut from the transcript" in mine[0]["text"]
    assert all(n["read_at"] for n in c.store.notifications if n["link"].endswith(f"&removal={req['id']}"))  # the managers' notices close
    assert _audit(c, "recording.removal_declined")[0]["detail"] == {"request_id": req["id"]}
    assert c.store.meeting_notes("mtg-1") is not None

    # removing the recording (the existing removal) answers every open request and tells whoever asked
    second = client.post(url, json={"reason": "Still want it gone."}, headers=HIRE).json()
    third = client.post(url, json={}, headers=MEERA).json()
    assert second["created"] and third["created"]
    r = client.request("DELETE", "/api/meetings/mtg-1/recording", json={"confirm": title}, headers=MANAGER)
    assert r.status_code == 200 and r.json()["removal_requests_closed"] == 2
    assert c.store.meeting_notes("mtg-1") is None
    assert not recording_store.removal_requests(c.store, meeting_id="mtg-1", status="open")
    assert {x["status"] for x in recording_store.removal_requests(c.store, meeting_id="mtg-1") if x["id"] in {second["request"]["id"], third["request"]["id"]}} == {"removed"}
    for uid in ("u-vijay", "u-meera"):
        assert any("was removed, as you asked" in n["text"] for n in c.store.notifications if n["user_id"] == uid)
    assert _audit(c, "meeting.recording_deleted")[0]["detail"]["removal_requests"] == 2
    # nothing recorded any more: nothing to ask for
    r = client.post(url, json={}, headers=HIRE)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nothing_recorded"


def test_removal_requests_stay_inside_the_project_and_bound_the_reason(client):  # noqa: F811
    assert client.post("/api/meetings/nope/recording/removal-requests", json={}, headers=HIRE).status_code == 404
    assert client.post("/api/meetings/mtg-1/recording/removal-requests", json={"reason": "x" * 1001}, headers=HIRE).status_code == 422
    assert client.get("/api/recording-removal-requests?project_id=other", headers=HIRE).status_code in {403, 404}
    c = _container(client)
    planned = client.post("/api/meetings", json={"project_id": PID, "title": "Next", "provider": "manual"}, headers=MANAGER).json()["meeting"]
    r = client.post(f"/api/meetings/{planned['id']}/recording/removal-requests", json={}, headers=HIRE)
    assert r.status_code == 409 and not recording_store.removal_requests(c.store, meeting_id=planned["id"])


def test_the_demo_shows_consent_and_an_answered_request_and_the_export_carries_both(client):  # noqa: F811
    c = _container(client)
    consent = c.store.get_meeting("mtg-1").recording_consent
    assert (consent.by, consent.notice_version) == ("u-ravi", VERSION)
    [seeded] = recording_store.removal_requests(c.store, meeting_id="mtg-1")
    assert (seeded["status"], seeded["requested_by"], seeded["decided_by"]) == ("declined", "u-meera", "u-priya")
    data = client.get("/api/org/export", headers=ADMIN).json()
    assert [r["id"] for r in data["recording_removal_requests"]] == [seeded["id"]]
    assert next(m for m in data["meetings"] if m["id"] == "mtg-1")["recording_consent"]["by"] == "u-ravi"
    from app.lifecycle import orgdata

    assert orgdata.delete(c, ORG)["recording_removal_requests"] == 1  # deleting the organisation deletes them
    assert not recording_store.removal_requests(c.store, project_id=PID)


# ---------------------------------------------------------------- retention (PRIV-9)
def _aged(blobs, uri, days, now):
    blobs.put(uri, MP4, "video/mp4")
    at = now - days * 86_400
    os.utime(blobs.path(uri), (at, at))
    return uri


def _self_hosted(demo, tmp_path):  # noqa: F811
    blobs = LocalBlobStorage(str(tmp_path / "blobs"))
    demo.deps.blobs = blobs
    demo.settings = demo.settings.model_copy(update={"mode": "self_hosted", "gcs_bucket": ""})
    return blobs


def test_retention_removes_old_raw_recordings_and_keeps_the_notes(demo, tmp_path):  # noqa: F811
    blobs, now = _self_hosted(demo, tmp_path), time.time()
    old = _aged(blobs, "gs://local/recordings/mtg-1/old.mp4", 40, now)
    demo.store.update_meeting("mtg-1", recording_uri=old)
    recent = demo.store.create_meeting(PID, "Last week", "u-priya")
    demo.store.claim_meeting_import(recent.id, _aged(blobs, f"gs://local/recordings/{recent.id}/new.mp4", 5, now), 60)
    demo.store.update_meeting(recent.id, status="ready")
    busy = demo.store.create_meeting(PID, "Being written", "u-priya")
    demo.store.claim_meeting_import(busy.id, _aged(blobs, f"gs://local/recordings/{busy.id}/busy.mp4", 90, now), 60)  # 'processing'
    abandoned = _aged(blobs, "gs://local/recordings/never-completed/x.mp4", 45, now)
    fresh_upload = _aged(blobs, "gs://local/recordings/in-flight/y.mp4", 0, now)
    document = _aged(blobs, "gs://local/documents/guide.md", 400, now)  # not a recording: never touched

    assert recordings.purge_recordings(demo, days=0, now=now) == {"removed": 0, "abandoned": 0, "kept": 0, "failed": 0}  # 0 keeps everything
    assert os.path.exists(blobs.path(old)) and os.path.exists(blobs.path(abandoned))

    out = recordings.purge_recordings(demo, days=30, now=now)
    assert out == {"removed": 1, "abandoned": 1, "kept": 2, "failed": 0}
    m = demo.store.get_meeting("mtg-1")
    assert m.recording_uri is None and m.status == "ready" and not os.path.exists(blobs.path(old))
    notes = demo.store.meeting_notes("mtg-1")
    assert notes is not None and notes.utterances and demo.store.kt_links(PID)  # notes, transcript and KT links stay
    assert demo.store.get_meeting(recent.id).recording_uri and os.path.exists(blobs.path(demo.store.get_meeting(recent.id).recording_uri))
    assert os.path.exists(blobs.path(demo.store.get_meeting(busy.id).recording_uri))  # its notes are being written: next round
    assert not os.path.exists(blobs.path(abandoned)) and os.path.exists(blobs.path(fresh_upload)) and os.path.exists(blobs.path(document))
    [entry] = _audit(demo, "recording.retention_removed")
    assert entry["actor_id"] is None and entry["target"] == "mtg-1" and entry["detail"]["retention_days"] == 30
    assert recordings.purge_recordings(demo, days=30, now=now)["removed"] == 0  # nothing twice


def test_a_retention_removal_whose_database_write_failed_is_finished_and_audited_later(demo, tmp_path, monkeypatch):  # noqa: F811
    """The session forgets the file and the removal is audited before the file goes: a failed write changes nothing and the
    next round finishes the job, so no session is left naming a file that is gone with no audit entry."""
    blobs, now = _self_hosted(demo, tmp_path), time.time()
    old = _aged(blobs, "gs://local/recordings/mtg-1/old.mp4", 40, now)
    demo.store.update_meeting("mtg-1", recording_uri=old)
    real = recording_store.clear_recording

    def blink(*_a, **_k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(recording_store, "clear_recording", blink)
    assert recordings.purge_recordings(demo, days=30, now=now)["failed"] == 1
    assert os.path.exists(blobs.path(old)) and demo.store.get_meeting("mtg-1").recording_uri == old  # nothing changed
    monkeypatch.setattr(recording_store, "clear_recording", real)
    assert recordings.purge_recordings(demo, days=30, now=now)["removed"] == 1
    audited = [a for a in demo.store.audit_rows if a["action"] == "recording.retention_removed" and a["target"] == "mtg-1"]
    assert not os.path.exists(blobs.path(old)) and demo.store.get_meeting("mtg-1").recording_uri is None and len(audited) == 1
    # a file whose delete failed is named by no session any more: the same round removes it as abandoned
    again = _aged(blobs, "gs://local/recordings/mtg-1/again.mp4", 40, now)
    demo.store.update_meeting("mtg-1", recording_uri=again)
    real_delete = blobs.delete
    monkeypatch.setattr(blobs, "delete", lambda _uri: (_ for _ in ()).throw(OSError("disk busy")))
    out = recordings.purge_recordings(demo, days=30, now=now)
    monkeypatch.setattr(blobs, "delete", real_delete)
    assert out["failed"] == 1 and out["abandoned"] == 1 and not os.path.exists(blobs.path(again))
    assert demo.store.get_meeting("mtg-1").recording_uri is None


def test_retention_never_runs_against_cloud_storage_and_survives_a_missing_file(demo, tmp_path):  # noqa: F811
    now = time.time()
    demo.deps.blobs = FakeGcs()  # Google Cloud: the bucket's own 30-day rule does this
    demo.store.update_meeting("mtg-1", recording_uri="gs://bucket/recordings/mtg-1/a.mp4")
    assert recordings.purge_recordings(demo, days=1, now=now)["removed"] == 0
    assert demo.store.get_meeting("mtg-1").recording_uri

    _self_hosted(demo, tmp_path)  # the file is not there (the disk is away): the session keeps naming it
    demo.store.update_meeting("mtg-1", recording_uri="gs://local/recordings/mtg-1/gone.mp4")
    assert recordings.purge_recordings(demo, days=1, now=now) == {"removed": 0, "abandoned": 0, "kept": 0, "failed": 0}
    assert demo.store.get_meeting("mtg-1").recording_uri == "gs://local/recordings/mtg-1/gone.mp4"


def test_the_retention_loop_starts_only_self_hosted_with_days_set(demo, tmp_path, monkeypatch):  # noqa: F811
    recordings.recording_settings.cache_clear()
    try:
        assert recordings.recording_settings().recording_retention_days == 0  # the default keeps every recording
        assert recordings.on_start(demo) == []
        _self_hosted(demo, tmp_path)
        assert recordings.on_start(demo) == []  # self-hosted, but nobody asked for retention
        monkeypatch.setenv("RECORDING_RETENTION_DAYS", "30")
        recordings.recording_settings.cache_clear()
        demo.settings = demo.settings.model_copy(update={"mode": "demo"})
        assert recordings.on_start(demo) == []  # the demo never removes anything
        demo.settings = demo.settings.model_copy(update={"mode": "self_hosted"})
        [loop] = recordings.on_start(demo)
        loop.stop()
        assert isinstance(loop, recordings.RetentionLoop) and loop.every_s == 86_400
    finally:
        recordings.recording_settings.cache_clear()


# ---------------------------------------------------------------- the copy at the Gemini Files API (PRIV-5)
class FakeFiles:
    """The Files API as GeminiLLM uses it: upload, get (while it is processing), delete."""

    def __init__(self, states=("ACTIVE",)):
        self.states, self.uploaded, self.deleted, self.polls = list(states), [], [], 0

    def _file(self, name):
        state = self.states[min(self.polls, len(self.states) - 1)]
        return SimpleNamespace(name=name, uri=f"https://generativelanguage.googleapis.com/v1beta/{name}", state=SimpleNamespace(name=state))

    def upload(self, file, config):
        self.uploaded.append(file)
        return self._file(f"files/f{len(self.uploaded)}")

    def get(self, name):
        self.polls += 1
        return self._file(name)

    def delete(self, name):
        self.deleted.append(name)


def _gemini(tmp_path, files) -> GeminiLLM:
    llm = GeminiLLM.__new__(GeminiLLM)  # no SDK client, no key: only the Files API calls are exercised
    llm.settings = SimpleNamespace(llm_video_timeout_s=30)
    llm.video_client = SimpleNamespace(files=files)
    llm.local_files = LocalBlobStorage(str(tmp_path / "blobs"))
    llm.local_files.put("gs://local/recordings/m1/a.mp4", MP4, "video/mp4")
    llm._uploads, llm._upload_lock = {}, threading.Lock()
    return llm


def test_the_copy_at_gemini_is_uploaded_once_and_deleted_when_released(tmp_path):
    files = FakeFiles()
    llm = _gemini(tmp_path, files)
    ref = MediaRef(uri="gs://local/recordings/m1/a.mp4")
    first, second = llm._media_uri(ref), llm._media_uri(ref)
    assert first == second == ("https://generativelanguage.googleapis.com/v1beta/files/f1", "video/mp4") and len(files.uploaded) == 1
    assert llm.release_media(ref.uri) is True and files.deleted == ["files/f1"]
    assert llm.release_media(ref.uri) is False and files.deleted == ["files/f1"]  # nothing left to delete
    llm._media_uri(ref)  # a retried run uploads it again
    assert len(files.uploaded) == 2 and llm.release_media(ref.uri) and files.deleted == ["files/f1", "files/f2"]


def test_a_copy_gemini_could_not_read_is_deleted_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    files = FakeFiles(states=("PROCESSING", "FAILED"))
    llm = _gemini(tmp_path, files)
    with pytest.raises(LLMError):
        llm._media_uri(MediaRef(uri="gs://local/recordings/m1/a.mp4"))
    assert files.deleted == ["files/f1"] and llm._uploads == {}


def test_a_delete_google_refuses_does_not_fail_the_notes_and_vertex_has_nothing_to_delete(tmp_path):
    files = FakeFiles()
    files.delete = lambda name: (_ for _ in ()).throw(RuntimeError("503"))
    llm = _gemini(tmp_path, files)
    llm._media_uri(MediaRef(uri="gs://local/recordings/m1/a.mp4"))
    assert llm.release_media("gs://local/recordings/m1/a.mp4") is False  # logged; Google deletes it itself within 48 hours
    llm.local_files = None  # Vertex AI reads the recording from Cloud Storage in place: no copy exists
    assert llm.release_media("gs://bucket/recordings/m1/a.mp4") is False


def test_the_note_taker_releases_the_recording_when_the_analysis_finishes_and_when_it_fails(demo):  # noqa: F811
    from app.agents.specialists.note_taker.agent import process_recording

    released = []
    llm = demo.deps.llm
    uri = "gs://local/recordings/mtg-1/a.mp4"
    llm.release_media = lambda u: released.append(u) or True  # the adapter's hook, as GeminiLLM has it
    process_recording(demo.deps, PID, "mtg-1", uri, 20)
    assert released == [uri]

    structured = llm.structured

    def failing(prompt, schema, **kw):
        if kw.get("media"):
            raise LLMError("the model stopped answering")
        return structured(prompt, schema, **kw)

    llm.structured = failing
    with pytest.raises(LLMError):
        process_recording(demo.deps, PID, "mtg-1", "gs://local/recordings/mtg-1/b.mp4", 20)
    assert released == [uri, "gs://local/recordings/mtg-1/b.mp4"]
