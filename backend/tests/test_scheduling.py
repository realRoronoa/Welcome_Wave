"""Scheduling (docs/specs/scheduling.md): KT sessions on Google Calendar with a Meet link or booked through Calendly, and
their recordings imported from Google Drive. Google is answered by httpx.MockTransport; nothing leaves the machine."""
import io
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters import calendly as cal
from app.adapters import google_calendar as gc
from app.adapters.memory import LocalBlobStorage, MemoryScheduler
from app.config import Settings, get_settings
from app.container import build_container
from app.main import create_app
from app.ports import CalendarEvent
from app.services import scheduling as S

PID, ORG = "proj-demo", "org-demo"
MANAGER, HIRE, RAVI, MEERA = ({"X-Demo-User": u} for u in ("u-priya", "u-vijay", "u-ravi", "u-meera"))
KEY = "whsec-test-key"
NOW = datetime.now(timezone.utc)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CALENDLY_WEBHOOK_SIGNING_KEY", KEY)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENT_RETRIES", "1")
    monkeypatch.setenv("RETRY_SLEEP_S", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.app.state.container.deps.blobs = LocalBlobStorage(str(tmp_path / "blobs"))
        yield c
    get_settings.cache_clear()


@pytest.fixture
def demo(tmp_path):
    c = build_container(Settings(mode="demo", data_dir=str(tmp_path / "data"), agent_retries=1, retry_sleep_s=0))
    c.deps.blobs = LocalBlobStorage(str(tmp_path / "blobs"))
    return c


AGREED = {"consent": True}  # "everyone in this session was told it is recorded and agreed" (services/recordings.py)


def _container(client):
    return client.app.state.container


# ---------------------------------------------------------------- Calendly signatures
def test_calendly_signature_valid():
    body = b'{"event":"invitee.created"}'
    t = int(time.time())
    assert cal.verify_signature(KEY, cal.sign(KEY, body, t), body)
    assert cal.verify_signature(KEY, cal.sign(KEY, body, t), body, now=t + 179)  # inside the tolerance


def test_calendly_signature_bad():
    body = b'{"event":"invitee.created"}'
    good = cal.sign(KEY, body)
    assert not cal.verify_signature(KEY, good, body + b" ")  # the body changed
    assert not cal.verify_signature("another-key", good, body)
    assert not cal.verify_signature(KEY, good.replace("v1=", "v1=0"), body)
    for header in (None, "", "t=abc,v1=00", "v1=deadbeef", f"t={int(time.time())}"):
        assert not cal.verify_signature(KEY, header, body)
    assert not cal.verify_signature("", good, body)  # no key configured: nothing verifies


def test_calendly_signature_stale_timestamp():
    body = b"{}"
    old = int(time.time()) - 181
    assert not cal.verify_signature(KEY, cal.sign(KEY, body, old), body)  # a replay of an old delivery
    assert not cal.verify_signature(KEY, cal.sign(KEY, body, int(time.time()) + 600), body)  # from the future


def test_calendly_links_are_cleaned_and_prefilled():
    assert cal.clean_link("calendly.com/meera/kt?x=1#y") == "https://calendly.com/meera/kt"
    assert cal.clean_link("https://calendly.com/meera/") == "https://calendly.com/meera"
    for bad in ("https://evil.example/calendly.com/meera", "https://calendly.com/", "javascript:alert(1)", ""):
        assert cal.clean_link(bad) is None
    url = cal.booking_link("https://calendly.com/meera", "Priya", "priya@demo.dev", PID, "0123456789ab")
    q = parse_qs(urlsplit(url).query)
    assert q == {"name": ["Priya"], "email": ["priya@demo.dev"], "utm_source": ["welcome-wave"], "utm_campaign": [PID],
                 "utm_content": ["0123456789ab"]}


# ---------------------------------------------------------------- Google: the event payload, OAuth state, tokens
def test_google_event_payload():
    start = datetime(2030, 1, 7, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    body = gc.event_body(CalendarEvent(meeting_id="m-1", title="KT with Meera", description="Agenda", starts_at=start,
                                       ends_at=start + timedelta(minutes=45), attendees=["Meera@Corp.com", "meera@corp.com", "x", "ravi@corp.com"]))
    assert body["summary"] == "KT with Meera" and body["description"] == "Agenda"
    assert body["start"] == {"dateTime": "2030-01-07T10:00:00Z", "timeZone": "UTC"}
    assert body["end"] == {"dateTime": "2030-01-07T10:45:00Z", "timeZone": "UTC"}
    assert body["attendees"] == [{"email": "meera@corp.com"}, {"email": "ravi@corp.com"}]  # deduplicated, no non-addresses
    assert body["conferenceData"] == {"createRequest": {"requestId": "m-1", "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    assert body["extendedProperties"]["private"]["welcome_wave_meeting"] == "m-1"
    assert gc.meet_link({"conferenceData": {"entryPoints": [{"entryPointType": "phone", "uri": "tel:1"},
                                                             {"entryPointType": "video", "uri": "https://meet.google.com/a-b-c"}]}}) \
        == "https://meet.google.com/a-b-c"


def test_google_consent_url_asks_for_offline_calendar_and_drive():
    q = parse_qs(urlsplit(gc.auth_url("cid", "https://ww.example/api/integrations/google/callback", "st")).query)
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and q["state"] == ["st"]
    assert set(q["scope"][0].split()) == {"openid", "email", "https://www.googleapis.com/auth/calendar.events",
                                          "https://www.googleapis.com/auth/drive.readonly"}


def test_oauth_state_round_trip_and_refusals(tmp_path):
    s = Settings(mode="self_hosted", token_encryption_key="k" * 40)
    state = S.make_state(s, "org-1", "u-1")
    assert S.read_state(s, state) == {"org_id": "org-1", "user_id": "u-1"}
    body, sig = state.split(".")
    other = S.make_state(s, "org-2", "u-1").split(".")[0]
    assert S.read_state(s, f"{other}.{sig}") is None  # another organisation's payload under this signature
    assert S.read_state(s, f"{body}.{sig[:-2]}xx") is None
    assert S.read_state(s, "") is None and S.read_state(s, "garbage") is None
    assert S.read_state(s, state, now=time.time() + S.STATE_TTL_S + 1) is None  # expired
    assert S.read_state(Settings(mode="self_hosted", token_encryption_key="j" * 40), state) is None  # another server's key


def test_refresh_tokens_are_encrypted_and_a_changed_key_reads_none(tmp_path):
    s = Settings(mode="self_hosted", data_dir=str(tmp_path))  # no key set: one is generated into DATA_DIR, once
    blob = S.encrypt_token(s, "1//refresh-token")
    assert "refresh-token" not in blob and S.decrypt_token(s, blob) == "1//refresh-token"
    assert (tmp_path / ".token-key").stat().st_mode & 0o777 == 0o600
    assert S.decrypt_token(Settings(mode="self_hosted", token_encryption_key="other"), blob) is None


def test_gcp_needs_a_token_key_once_google_is_set_up():
    gcp = dict(mode="gcp", gcp_project="p", gcs_bucket="b", github_webhook_secret="s", outbox_flush_audience="a", outbox_flush_invoker="i")
    assert not any("TOKEN_ENCRYPTION_KEY" in p for p in Settings(**gcp).production_problems())
    google = Settings(**gcp, google_oauth_client_id="id", google_oauth_client_secret="sec")
    assert any("TOKEN_ENCRYPTION_KEY" in p for p in google.production_problems())
    assert not any("TOKEN_ENCRYPTION_KEY" in p for p in google.model_copy(update={"token_encryption_key": "x"}).production_problems())


def test_unused_secrets_turn_features_off_never_into_keys():
    s = Settings(calendly_webhook_signing_key="unused", google_oauth_client_id="cid", google_oauth_client_secret=" Unused",
                 token_encryption_key="unused")
    assert (s.calendly_webhook_signing_key, s.google_oauth_client_secret, s.token_encryption_key) == ("", "", "")
    assert not s.google_calendar_ready


# ---------------------------------------------------------------- the memory scheduler
def test_memory_scheduler_books_stable_meet_links_cancels_and_finds_recordings():
    sched = MemoryScheduler()
    ev = CalendarEvent(meeting_id="m-1", title="KT", starts_at=NOW, ends_at=NOW + timedelta(minutes=30), attendees=["a@x.dev"])
    a, b = sched.create_event(ev), sched.create_event(ev)
    assert a == b and a.join_url == MemoryScheduler.meet_link("m-1")
    assert urlsplit(a.join_url).path.strip("/").count("-") == 2 and len(urlsplit(a.join_url).path) == 13  # /xxx-xxxx-xxx
    assert sched.create_event(ev.model_copy(update={"meeting_id": "m-2"})).join_url != a.join_url
    assert sched.find_recording(a.event_id) is None  # tests add recordings themselves
    sched.add_recording(a.event_id, b"video-bytes", 90)
    f = sched.find_recording(a.event_id)
    sink = io.BytesIO()
    assert (f.duration_s, sched.download(f, sink), sink.getvalue()) == (90, 11, b"video-bytes")
    with pytest.raises(ValueError):
        sched.download(f, io.BytesIO(), max_bytes=4)
    sched.cancel_event(a.event_id)
    sched.cancel_event(a.event_id)  # twice is fine
    assert sched.cancelled == [a.event_id] and a.event_id not in sched.events
    demo = MemoryScheduler(finds_recordings=True)  # the demo: every booked session "has" a recording
    booked = demo.create_event(ev)
    assert demo.find_recording(booked.event_id).duration_s == 1800


# ---------------------------------------------------------------- Google over a mock transport
class FakeGoogle:
    """Answers the Google endpoints the scheduler calls, and records what it was asked."""

    def __init__(self, attachments=None, video=b"\x00\x00\x00\x18ftypmp42 real video", token_status=200):
        self.calls: list[httpx.Request] = []
        self.attachments = attachments if attachments is not None else []
        self.video, self.token_status = video, token_status
        self.events: dict[str, dict] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        if url.startswith(gc.TOKEN_URL):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "ya29.token", "expires_in": 3600})
        if request.url.host == "www.googleapis.com" and "/calendar/v3/calendars/primary/events" in url:
            if request.method == "POST":
                body = json.loads(request.content)
                ev = {**body, "id": "evt123", "hangoutLink": "https://meet.google.com/abc-defg-hij"}
                self.events["evt123"] = ev
                return httpx.Response(200, json=ev)
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json={"id": "evt123", "attachments": self.attachments})
        if "/drive/v3/files/" in url:
            if request.url.params.get("alt") == "media":
                return httpx.Response(200, content=self.video)
            return httpx.Response(200, json={"id": "drv1", "name": "KT (2030-01-07).mp4", "mimeType": "video/mp4",
                                             "size": str(len(self.video)), "videoMediaMetadata": {"durationMillis": "754000"}})
        return httpx.Response(404, json={"error": {"message": "unexpected " + url}})


def _google(fake, token="1//rt") -> gc.GoogleCalendarScheduler:
    gc._tokens.clear()
    return gc.GoogleCalendarScheduler("cid", "secret", token, transport=httpx.MockTransport(fake))


def test_google_scheduler_inserts_an_event_with_meet_and_cancels_it():
    fake = FakeGoogle()
    sched = _google(fake)
    ev = CalendarEvent(meeting_id="m-1", title="KT", starts_at=NOW, ends_at=NOW + timedelta(minutes=30), attendees=["a@x.dev"])
    booked = sched.create_event(ev)
    assert (booked.event_id, booked.join_url) == ("evt123", "https://meet.google.com/abc-defg-hij")
    insert = next(r for r in fake.calls if r.method == "POST" and "calendar" in str(r.url))
    assert insert.url.params["conferenceDataVersion"] == "1" and insert.url.params["sendUpdates"] == "all"
    assert insert.headers["Authorization"] == "Bearer ya29.token"
    sched.cancel_event("evt123")
    delete = next(r for r in fake.calls if r.method == "DELETE")
    assert delete.url.path.endswith("/events/evt123") and delete.url.params["sendUpdates"] == "all"


def test_google_scheduler_says_when_the_connection_is_revoked():
    with pytest.raises(gc.GoogleAuthError):
        _google(FakeGoogle(token_status=400)).find_recording("evt123")


def _google_session(demo, fake, ends_ago=timedelta(minutes=30), agreed=True):
    sched = _google(fake)
    demo.extras["scheduler"] = sched
    priya = demo.store.get_user("u-priya")
    starts = datetime.now(timezone.utc) - ends_ago - timedelta(minutes=30)
    m = S.book(demo, PID, ORG, priya, S.SessionRequest(title="KT #2: webhooks", starts_at=starts, attendee_ids=["u-ravi"]))
    if agreed:  # someone confirmed that everyone was told and agreed: without it nothing is imported (test_recording_consent.py)
        from app.services import recordings

        m = recordings.confirm_consent(demo, priya, m, "confirm")
    return m


def test_import_hands_the_drive_recording_to_the_meeting_pipeline(demo):
    """The recording Meet attached to the event is streamed from Drive into blob storage, and the meeting takes the same
    path an upload takes: processing, meeting.recording_ready (with the Drive file's own length), then notes."""
    fake = FakeGoogle(attachments=[{"fileId": "doc1", "mimeType": "application/vnd.google-apps.document", "title": "Transcript"},
                                   {"fileId": "drv1", "mimeType": "video/mp4", "title": "Recording"}])
    m = _google_session(demo, fake)
    assert (m.provider, m.calendar_event_id, m.join_url) == ("google", "evt123", "https://meet.google.com/abc-defg-hij")
    out = S.import_recording(demo, ORG, m, "u-priya")
    [event] = [e for t, e in demo.deps.bus.published if e["type"] == "meeting.recording_ready"]
    assert event["meeting_id"] == m.id and event["duration_s"] == 754.0 and event["recording_uri"].endswith(".mp4")
    assert demo.deps.blobs.get(event["recording_uri"]) == fake.video
    media = next(r for r in fake.calls if r.url.params.get("alt") == "media")
    assert media.url.path == "/drive/v3/files/drv1"
    after = demo.store.get_meeting(m.id)
    assert after.recording_uri == event["recording_uri"] and out.status in {"processing", "ready"} and after.status == "ready"
    assert any(a["action"] == "meeting.recording_imported" for a in demo.store.audit_rows)
    with pytest.raises(S.AlreadyRecorded):
        S.import_recording(demo, ORG, after)


def test_import_waits_for_the_recording_and_refuses_what_it_cannot_import(demo):
    m = _google_session(demo, FakeGoogle(attachments=[]))
    with pytest.raises(S.NoRecordingYet):
        S.import_recording(demo, ORG, m)
    assert demo.store.get_meeting(m.id).status == "scheduled"
    manual = demo.store.create_meeting(PID, "Held in Zoom", "u-priya", provider="manual")
    with pytest.raises(S.ImportNotPossible):
        S.import_recording(demo, ORG, manual)


def test_import_refuses_a_recording_over_the_limit(demo):
    demo.settings.max_recording_mb = 0
    m = _google_session(demo, FakeGoogle(attachments=[{"fileId": "drv1", "mimeType": "video/mp4"}]))
    with pytest.raises(ValueError):
        S.import_recording(demo, ORG, m)
    assert demo.store.get_meeting(m.id).status == "scheduled"


def test_a_refused_calendar_event_books_nothing(demo):
    class Down(MemoryScheduler):
        def create_event(self, event):
            raise gc.GoogleError("503: backend error", 503)

    demo.extras["scheduler"] = Down()
    before = len(demo.store.list_meetings(PID))
    with pytest.raises(S.CalendarFailed):
        S.book(demo, PID, ORG, demo.store.get_user("u-priya"), S.SessionRequest(title="KT", starts_at=NOW + timedelta(days=1)))
    assert len(demo.store.list_meetings(PID)) == before


def test_poller_imports_ended_google_sessions_and_expires_the_rest(demo):
    fake = FakeGoogle(attachments=[{"fileId": "drv1", "mimeType": "video/mp4"}])
    google = _google_session(demo, fake)
    fresh = _google_session(demo, FakeGoogle(), ends_ago=timedelta(minutes=2))  # Meet may still be saving it
    old = datetime.now(timezone.utc) - timedelta(days=3)
    lost = demo.store.create_meeting(PID, "Held elsewhere", "u-priya", provider="manual", starts_at=old, ends_at=old + timedelta(hours=1))
    upcoming = demo.store.create_meeting(PID, "Next week", "u-priya", provider="manual", starts_at=NOW + timedelta(days=7),
                                         ends_at=NOW + timedelta(days=7, hours=1))
    demo.extras["scheduler"] = _google(fake)
    out = S.poll_recordings(demo)
    assert out["imported"] == 1 and out["ended"] == 1
    assert demo.store.get_meeting(google.id).status == "ready"
    assert demo.store.get_meeting(lost.id).status == "ended"  # its recording can still be uploaded
    assert demo.store.get_meeting(fresh.id).status == "scheduled" and demo.store.get_meeting(upcoming.id).status == "scheduled"
    assert S.poll_recordings(demo)["imported"] == 0  # nothing twice


def test_scheduler_choice_follows_the_organisations_connection(tmp_path):
    from app.adapters.memory import MemoryStore

    s = Settings(mode="self_hosted", token_encryption_key="k" * 40, google_oauth_client_id="cid", google_oauth_client_secret="sec")

    class C:
        settings, extras, store = s, {}, MemoryStore()

    assert S.scheduler_for(C, "org-1") is None  # not connected: sessions are 'manual'
    C.store.save_calendar_connection("org-1", "google", "kt@corp.com", S.encrypt_token(s, "1//rt"), "", "u-1")
    sched = S.scheduler_for(C, "org-1")
    assert isinstance(sched, gc.GoogleCalendarScheduler) and sched.refresh_token == "1//rt"
    assert S.scheduler_for(C, "org-2") is None
    C.settings = s.model_copy(update={"google_oauth_client_id": ""})  # the server's OAuth client removed
    assert S.scheduler_for(C, "org-1") is None


# ---------------------------------------------------------------- API: schedule, cancel, import, list
def test_schedule_on_the_calendar_and_list(client):
    starts = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    r = client.post("/api/meetings", json={"project_id": PID, "title": "KT #2: the webhook server", "starts_at": starts.isoformat(),
                                           "duration_min": 45, "attendees": ["u-ravi", "u-vijay", "not-a-member"],
                                           "recording_promise": True}, headers=MANAGER)
    assert r.status_code == 200, r.text
    m = r.json()["meeting"]
    assert m["provider"] == "google" and m["join_url"].startswith("https://meet.google.com/")
    assert datetime.fromisoformat(m["ends_at"]) - datetime.fromisoformat(m["starts_at"]) == timedelta(minutes=45)
    assert [a["user_id"] for a in m["attendees"]] == ["u-priya", "u-ravi", "u-vijay"]
    notes = [n for n in _container(client).store.notifications if n["kind"] == "meeting" and m["title"] in n["text"]]
    assert sorted(n["user_id"] for n in notes) == ["u-ravi", "u-vijay"]
    listed = client.get(f"/api/meetings?project_id={PID}", headers=HIRE).json()
    assert next(x for x in listed if x["id"] == m["id"])["join_url"] == m["join_url"]
    assert client.get(f"/api/meetings?project_id={PID}").status_code == 401


def test_schedule_held_elsewhere_and_unscheduled(client):
    other = client.post("/api/meetings", json={"project_id": PID, "title": "Zoom KT", "starts_at": "2030-01-07T10:00:00Z",
                                               "provider": "manual", "join_url": "https://zoom.us/j/1"}, headers=MANAGER).json()["meeting"]
    assert (other["provider"], other["join_url"], other["calendar_event_id"]) == ("manual", "https://zoom.us/j/1", None)
    bad = client.post("/api/meetings", json={"project_id": PID, "title": "x", "provider": "manual", "join_url": "javascript:alert(1)"}, headers=MANAGER)
    assert bad.status_code == 422
    later = client.post("/api/meetings", json={"project_id": PID, "title": "Some time"}, headers=HIRE).json()["meeting"]
    assert later["provider"] == "manual" and later["starts_at"] is None  # no time: held whenever it suits
    assert client.post("/api/meetings", json={"project_id": PID, "title": "x", "provider": "google"}, headers=MANAGER).status_code == 422
    r = client.post(f"/api/meetings/{later['id']}/schedule", json={"starts_at": "2030-02-03T09:00:00Z", "attendees": ["u-ravi"],
                                                                 "recording_promise": True}, headers=HIRE)
    assert r.status_code == 200, r.text
    put = r.json()["meeting"]
    assert put["provider"] == "google" and put["join_url"] and put["starts_at"].startswith("2030-02-03")
    assert client.post(f"/api/meetings/{later['id']}/schedule", json={"starts_at": "2030-02-04T09:00:00Z"}, headers=HIRE).status_code == 409
    assert client.post(f"/api/meetings/{other['id']}/schedule", json={"starts_at": "2030-02-04T09:00:00Z"}, headers=RAVI).status_code == 403


def test_cancel_takes_the_session_off_the_calendar(client):
    m = client.post("/api/meetings", json={"project_id": PID, "title": "KT", "starts_at": "2030-01-07T10:00:00Z", "attendees": ["u-ravi"],
                                           "recording_promise": True},
                    headers=MANAGER).json()["meeting"]
    assert client.post(f"/api/meetings/{m['id']}/cancel", headers=RAVI).status_code == 403
    r = client.post(f"/api/meetings/{m['id']}/cancel", headers=MANAGER).json()
    assert r["meeting"]["status"] == "cancelled" and r["warning"] is None
    assert _container(client).extras["scheduler"].cancelled == [m["calendar_event_id"]]
    assert any(n["user_id"] == "u-ravi" and "cancelled" in n["text"] for n in _container(client).store.notifications)


def test_a_session_that_has_not_started_cannot_be_imported_from_the_demo_calendar(client):
    m = client.post("/api/meetings", json={"project_id": PID, "title": "KT #3", "starts_at": "2030-01-07T10:00:00Z",
                                           "recording_promise": True},
                    headers=MANAGER).json()["meeting"]
    r = client.post(f"/api/meetings/{m['id']}/recording/import", json=AGREED, headers=MANAGER)
    assert r.status_code == 409 and "has not started yet" in r.json()["detail"]  # no stand-in video, no "No speech was detected"
    assert _container(client).store.get_meeting(m["id"]).status == "scheduled"
    assert not [e for _, e in _container(client).deps.bus.published if e["type"] == "meeting.recording_ready"]


def test_import_from_the_demo_calendar_writes_the_notes(client):
    started = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")  # a session that has been held
    m = client.post("/api/meetings", json={"project_id": PID, "title": "KT #4", "starts_at": started,
                                           "recording_promise": True},
                    headers=MANAGER).json()["meeting"]
    assert client.post(f"/api/meetings/{m['id']}/recording/import", headers=HIRE).status_code == 403  # not theirs
    r = client.post(f"/api/meetings/{m['id']}/recording/import", json=AGREED, headers=RAVI)
    assert r.status_code == 202, r.text
    notes = client.get(f"/api/meetings/{m['id']}/notes", headers=MANAGER).json()
    assert notes["meeting"]["status"] == "ready" and notes["notes"] is not None
    again = client.post(f"/api/meetings/{m['id']}/recording/import", headers=MANAGER)
    assert again.status_code == 409


def test_import_explains_what_it_cannot_do(client):
    c = _container(client)
    started = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")  # held: one not started is refused earlier
    m = client.post("/api/meetings", json={"project_id": PID, "title": "KT", "starts_at": started,
                                           "recording_promise": True}, headers=MANAGER).json()["meeting"]
    c.extras["scheduler"] = MemoryScheduler()  # a calendar with no recording on the event yet
    c.extras["scheduler"].events[m["calendar_event_id"]] = None
    r = client.post(f"/api/meetings/{m['id']}/recording/import", json=AGREED, headers=MANAGER)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "no_recording_yet")
    manual = client.post("/api/meetings", json={"project_id": PID, "title": "Zoom", "provider": "manual"}, headers=MANAGER).json()["meeting"]
    r = client.post(f"/api/meetings/{manual['id']}/recording/import", headers=MANAGER)
    assert r.status_code == 409 and "Upload" in r.json()["detail"]


def test_kt_planner_books_on_the_calendar_with_the_presenter_invited(client):
    plan = client.get(f"/api/kt-plan?project_id={PID}", headers=MANAGER).json()
    s = plan["sessions"][0]
    r = client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": s["key"], "starts_at": "2030-01-08T11:00:00Z",
                                                   "duration_min": 30}, headers=MANAGER)
    assert r.status_code == 200, r.text
    m = r.json()["meeting"]
    assert m["provider"] == "google" and m["kt_key"] == s["key"] and m["join_url"]
    assert {a["user_id"] for a in m["attendees"]} == {"u-priya", s["presenter"]["user_id"]}
    [n] = [n for n in _container(client).store.notifications if n["kind"] == "kt_request"]
    assert n["text"].endswith("The invitation is in your calendar; join from KT sessions.")


# ---------------------------------------------------------------- API: the Calendly webhook
def _calendly(client, kind, uri="https://api.calendly.com/scheduled_events/EV1", key=None, invitee="priya@demo.dev",
              host="meera@demo.dev", project=PID, source="welcome-wave", signing_key=KEY):
    payload = {"event": f"invitee.{kind}", "payload": {
        "email": invitee, "name": "Priya", "rescheduled": False,
        "tracking": {"utm_source": source, "utm_campaign": project, "utm_content": key},
        "scheduled_event": {"uri": uri, "name": "KT session (30 min)", "start_time": "2030-01-09T09:00:00.000000Z",
                            "end_time": "2030-01-09T09:30:00.000000Z",
                            "location": {"type": "google_conference", "join_url": "https://meet.google.com/cal-endl-yyy"},
                            "event_memberships": [{"user_email": host, "user_name": "Meera"}], "event_guests": []}}}
    body = json.dumps(payload).encode()
    return client.post("/webhooks/calendly", content=body, headers={"Calendly-Webhook-Signature": cal.sign(signing_key, body),
                                                                     "Content-Type": "application/json"})


def test_calendly_booking_becomes_a_session_once_and_its_cancel_cancels_it(client):
    s = client.get(f"/api/kt-plan?project_id={PID}", headers=MANAGER).json()["sessions"][0]
    r = _calendly(client, "created", key=s["key"])
    assert r.status_code == 200 and "created" in r.json(), r.text
    m = _container(client).store.get_meeting(r.json()["created"])
    assert (m.provider, m.title, m.kt_key, m.join_url, m.created_by) == ("calendly", s["title"], s["key"], "https://meet.google.com/cal-endl-yyy", "u-priya")
    assert m.starts_at == datetime(2030, 1, 9, 9, 0, tzinfo=timezone.utc) and {a.email for a in m.attendees} == {"priya@demo.dev", "meera@demo.dev"}
    plan = client.get(f"/api/kt-plan?project_id={PID}", headers=MANAGER).json()
    assert next(x for x in plan["sessions"] if x["key"] == s["key"])["booked"]["meeting_id"] == m.id  # the planner sees it booked
    assert _calendly(client, "created", key=s["key"]).json() == {"duplicate": m.id}  # a redelivery
    assert _calendly(client, "canceled", key=s["key"]).json() == {"cancelled": m.id}
    assert _container(client).store.get_meeting(m.id).status == "cancelled"
    cancelled = client.post(f"/api/meetings/{m.id}/cancel", headers=MANAGER)
    assert cancelled.status_code == 409


def test_calendly_reschedule_moves_the_session(client):
    s = client.get(f"/api/kt-plan?project_id={PID}", headers=MANAGER).json()["sessions"][0]
    first = _calendly(client, "created", key=s["key"]).json()["created"]
    second = _calendly(client, "created", uri="https://api.calendly.com/scheduled_events/EV2", key=s["key"]).json()["created"]
    store = _container(client).store
    assert store.get_meeting(first).status == "cancelled" and store.get_meeting(second).kt_key == s["key"]


def test_calendly_webhook_refuses_and_ignores(client):
    assert _calendly(client, "created", signing_key="wrong").status_code == 401
    assert _calendly(client, "created", source="elsewhere").json() == {"ignored": "invitee.created"}  # not booked from Welcome Wave
    assert _calendly(client, "created", project="proj-unknown").json() == {"ignored": "unknown project"}
    outsider = _calendly(client, "created", invitee="someone@else.dev", host="host@else.dev")
    assert outsider.json() == {"ignored": "nobody in this project"}
    body = b'{"event":"routing_form_submission.created","payload":{}}'
    assert client.post("/webhooks/calendly", content=body, headers={"Calendly-Webhook-Signature": cal.sign(KEY, body)}).json() == \
        {"ignored": "routing_form_submission.created"}


def test_calendly_webhook_is_off_without_a_signing_key(client):
    _container(client).settings.calendly_webhook_signing_key = ""
    assert _calendly(client, "created").status_code == 404


# ---------------------------------------------------------------- API: integrations
def test_integrations_status_and_calendly_links(client):
    st = client.get("/api/integrations", headers=MEERA).json()
    assert st["google"]["connected"] and st["google"]["account"] == "Demo calendar" and not st["google"]["can_manage"]
    assert st["calendly"] == {"webhook": True, "webhook_url": "/webhooks/calendly", "link": None}  # (Ravi's link is in the seed, Meera's is not)
    assert client.put("/api/integrations/calendly/link", json={"url": "https://evil.example/x"}, headers=MEERA).status_code == 422
    r = client.put("/api/integrations/calendly/link", json={"url": "calendly.com/meera-demo/kt?utm=1"}, headers=MEERA)
    assert r.json() == {"link": "https://calendly.com/meera-demo/kt"}
    assert client.get("/api/integrations", headers=MEERA).json()["calendly"]["link"] == "https://calendly.com/meera-demo/kt"
    links = client.get(f"/api/scheduling/links?project_id={PID}", headers=MANAGER).json()
    assert links["links"] == {"u-ravi": "https://calendly.com/ravi-demo/kt-session", "u-meera": "https://calendly.com/meera-demo/kt"}
    assert links["prefill"] == {"name": "Priya (Manager)", "email": "priya@demo.dev", "utm_source": "welcome-wave", "utm_campaign": PID}
    assert client.delete("/api/integrations/calendly/link", headers=MEERA).status_code == 204
    assert client.get(f"/api/scheduling/links?project_id={PID}", headers=MANAGER).json()["links"] == {"u-ravi": "https://calendly.com/ravi-demo/kt-session"}
    assert client.get("/api/integrations/google/start", headers=MANAGER).status_code == 404  # the demo's calendar is built in
    cfg = client.get("/api/config").json()
    assert cfg["google_calendar"] is True and cfg["calendly"] is True and "livekit" not in cfg and "recording" not in cfg


def test_google_connect_callback_stores_an_encrypted_token(client, tmp_path):
    c = _container(client)
    c.settings = c.settings.model_copy(update={"mode": "self_hosted", "google_oauth_client_id": "cid", "google_oauth_client_secret": "sec",
                                               "token_encryption_key": "k" * 40, "public_url": "https://ww.example"})
    seen = []

    def google(request):
        seen.append(request)
        id_token = "e30." + __import__("base64").urlsafe_b64encode(json.dumps({"email": "kt@corp.com"}).encode()).decode().rstrip("=") + ".sig"
        return httpx.Response(200, json={"refresh_token": "1//refresh", "access_token": "ya29", "id_token": id_token,
                                         "scope": " ".join(gc.SCOPES)})

    c.extras["google_transport"] = httpx.MockTransport(google)

    def callback(query: str, state: str):  # in the browser that started the connection: it holds the state's cookie
        from app.api.integrations import STATE_COOKIE, _state_mark

        client.cookies.set(STATE_COOKIE, _state_mark(state))
        return client.get(f"/api/integrations/google/callback?{query}&state={state}", follow_redirects=False)

    state = S.make_state(c.settings, ORG, "u-priya")
    r = callback("code=4/abc", state)
    assert r.status_code == 303 and r.headers["location"] == "/app#/settings?tab=integrations&google=connected"
    form = parse_qs(seen[0].content.decode())
    assert form["redirect_uri"] == ["https://ww.example/api/integrations/google/callback"] and form["code"] == ["4/abc"]
    conn = c.store.calendar_connection(ORG, "google")
    assert conn["account_email"] == "kt@corp.com" and "refresh" not in conn["refresh_token_enc"]
    assert S.decrypt_token(c.settings, conn["refresh_token_enc"]) == "1//refresh"

    expired = S.make_state(c.settings, ORG, "u-priya", now=time.time() - 3600)
    assert callback("code=x", expired).headers["location"].endswith("google=expired")
    hire = S.make_state(c.settings, ORG, "u-vijay")  # a new hire cannot connect the organisation's calendar
    assert callback("code=x", hire).headers["location"].endswith("google=denied")
    assert callback("error=access_denied", state).headers["location"].endswith("google=expired")  # each state works once
    denied = callback("error=access_denied", S.make_state(c.settings, ORG, "u-priya"))
    assert denied.headers["location"].endswith("google=denied")
