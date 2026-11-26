"""Recording consent, removal requests and the retention list on Postgres as ww_app (row-level security on;
db/migrations/020_recording_consent.sql, app/adapters/recording_store.py). Run with a scratch server:

    TEST_PG_ADMIN_URL=postgresql://ww:ww@localhost:55432/postgres pytest tests/test_recording_consent_postgres.py
"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from app.adapters import recording_store
from app.adapters.lifecycle_pg import PgLifecycleStore
from app.adapters.memory import LocalBlobStorage
from app.adapters.postgres import current_org
from app.config import Settings
from app.services import recordings
from tests.test_postgres import ADMIN, _org, db, engine, store  # noqa: F401 - the scratch database and its fixtures

pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def test_consent_is_kept_once_and_read_back_with_the_session(store):  # noqa: F811
    user, project = _org(store, "rc")
    m = store.create_meeting(project.id, "KT: queues", user.id)
    assert m.recording_consent is None
    assert recording_store.confirm_consent(store, m.id, user.id, "2026-11-19") is True
    assert recording_store.confirm_consent(store, m.id, user.id, "2027-01-01") is False  # the first confirmation is the one kept
    assert recording_store.confirm_consent(store, "not-a-uuid", user.id, "2026-11-19") is False
    got = store.get_meeting(m.id).recording_consent
    assert (got.by, got.by_name, got.notice_version) == (user.id, user.name, "2026-11-19") and got.at.tzinfo is not None
    assert next(x for x in store.list_meetings(project.id) if x.id == m.id).recording_consent == got


def test_consent_is_cleared_with_the_recording_and_can_be_confirmed_again(store):  # noqa: F811
    user, project = _org(store, "rcc")
    m = store.create_meeting(project.id, "KT: retries", user.id)
    assert recording_store.clear_consent(store, m.id) is False and recording_store.clear_consent(store, "not-a-uuid") is False
    assert recording_store.confirm_consent(store, m.id, user.id, "2026-11-19") is True
    assert recording_store.clear_consent(store, m.id) is True and store.get_meeting(m.id).recording_consent is None
    assert recording_store.clear_consent(store, m.id) is False
    assert recording_store.confirm_consent(store, m.id, user.id, "2027-01-01") is True  # a new confirmation is taken
    assert store.get_meeting(m.id).recording_consent.notice_version == "2027-01-01"


def test_erasing_a_person_blanks_the_reasons_they_wrote_and_keeps_the_rows(engine, store):  # noqa: F811
    admin, project = _org(store, "rer")
    hire = PgLifecycleStore(engine).ensure_user(admin.org_id, f"rer-hire-{os.urandom(3).hex()}@example.com", "Hema", None)
    m = store.create_meeting(project.id, "KT: billing", admin.id)
    mine, _ = recording_store.create_removal_request(store, admin.org_id, project.id, m.id, hire.id, "I read out my home address.")
    theirs, _ = recording_store.create_removal_request(store, admin.org_id, project.id, m.id, admin.id, "Wrong screen shared.")
    assert store.erase_person(admin.org_id, hire.id)["recording_removal_reasons"] == 1
    rows = {r["id"]: r for r in recording_store.removal_requests(store, meeting_id=m.id)}
    assert (rows[mine["id"]]["reason"], rows[mine["id"]]["status"], rows[mine["id"]]["requested_by"]) == ("", "open", hire.id)
    assert rows[theirs["id"]]["reason"] == "Wrong screen shared."  # nobody else's words are touched


def test_removal_requests_are_one_open_per_person_and_never_cross_organisations(store):  # noqa: F811
    a_user, a_project = _org(store, "ra")
    a_meeting = store.create_meeting(a_project.id, "KT A", a_user.id)
    row, created = recording_store.create_removal_request(store, a_user.org_id, a_project.id, a_meeting.id, a_user.id, "My address is read out.")
    assert created and (row["status"], row["requested_by"], row["requested_by_name"], row["reason"]) == ("open", a_user.id, a_user.name, "My address is read out.")
    again, created = recording_store.create_removal_request(store, a_user.org_id, a_project.id, a_meeting.id, a_user.id, "again")
    assert not created and again["id"] == row["id"] and again["reason"] == "My address is read out."
    assert [r["id"] for r in recording_store.removal_requests(store, project_id=a_project.id, status="open")] == [row["id"]]
    assert recording_store.get_removal_request(store, row["id"])["meeting_id"] == a_meeting.id

    b_user, b_project = _org(store, "rb")  # another organisation sees none of it, and cannot write into it
    assert recording_store.removal_requests(store, project_id=a_project.id) == []
    assert recording_store.get_removal_request(store, row["id"]) is None
    assert recording_store.close_removal_requests(store, a_meeting.id, recording_store.DECLINED, b_user.id, "no") == []
    with pytest.raises(Exception):  # noqa: B017, PT011 - row-level security refuses a row for another organisation
        recording_store.create_removal_request(store, a_user.org_id, a_project.id, a_meeting.id, b_user.id, "")

    current_org.set(a_user.org_id)
    [closed] = recording_store.close_removal_requests(store, a_meeting.id, recording_store.DECLINED, a_user.id, "It stays.", row["id"])
    assert (closed["status"], closed["decided_by"], closed["decided_by_name"], closed["note"]) == ("declined", a_user.id, a_user.name, "It stays.")
    assert closed["decided_at"] is not None
    assert recording_store.close_removal_requests(store, a_meeting.id, recording_store.REMOVED, a_user.id) == []  # nothing open
    second, created = recording_store.create_removal_request(store, a_user.org_id, a_project.id, a_meeting.id, a_user.id, "")
    assert created and second["id"] != row["id"]  # a decided request does not block a new one
    assert [r["status"] for r in recording_store.close_removal_requests(store, a_meeting.id, recording_store.REMOVED, a_user.id)] == ["removed"]


def test_export_and_deletion_cover_the_requests_and_the_consent(engine, store):  # noqa: F811
    user, project = _org(store, "rx")
    m = store.create_meeting(project.id, "KT X", user.id)
    recording_store.confirm_consent(store, m.id, user.id, "2026-11-19")
    row, _ = recording_store.create_removal_request(store, user.org_id, project.id, m.id, user.id, "please")
    lc = PgLifecycleStore(engine)
    data = lc.export_org(user.org_id)
    assert [r["id"] for r in data["recording_removal_requests"]] == [row["id"]]
    exported = next(x for x in data["meetings"] if x["id"] == m.id)
    assert exported["recording_consent_by"] == user.id and exported["recording_notice_version"] == "2026-11-19"
    assert lc.delete_org(user.org_id)["recording_removal_requests"] == 1
    assert recording_store.removal_requests(store, project_id=project.id) == []


def test_retention_finds_recordings_in_every_organisation_and_clears_only_the_file(engine, store, tmp_path):  # noqa: F811
    blobs, now = LocalBlobStorage(str(tmp_path / "blobs")), time.time()

    def recorded(label, days, status="ready"):
        user, project = _org(store, label)
        m = store.create_meeting(project.id, f"KT {label}", user.id)
        uri = blobs.put(f"gs://local/recordings/{m.id}/a.mp4", MP4, "video/mp4")
        os.utime(blobs.path(uri), (now - days * 86_400,) * 2)
        assert store.claim_meeting_import(m.id, uri, 60)
        if status != "processing":
            store.update_meeting(m.id, status=status, summary="The notes.")
        return user, m, uri

    a_user, a_meeting, a_uri = recorded("r1", 40)
    b_user, b_meeting, b_uri = recorded("r2", 3)
    c_user, c_meeting, c_uri = recorded("r3", 90, status="processing")
    current_org.set(None)
    listed = {r["id"]: r for r in recording_store.stored_recordings(store)}
    assert {a_meeting.id, b_meeting.id, c_meeting.id} <= set(listed)
    assert listed[a_meeting.id] == {"id": a_meeting.id, "org_id": a_user.org_id, "project_id": a_meeting.project_id,
                                    "recording_uri": a_uri, "status": "ready"}

    c = SimpleNamespace(store=store, deps=SimpleNamespace(blobs=blobs), settings=Settings(mode="self_hosted", gemini_api_key="k"))
    out = recordings.purge_recordings(c, days=30, now=now)
    assert out["removed"] == 1 and out["failed"] == 0 and out["kept"] >= 2
    assert not os.path.exists(blobs.path(a_uri)) and os.path.exists(blobs.path(b_uri)) and os.path.exists(blobs.path(c_uri))
    current_org.set(a_user.org_id)
    after = store.get_meeting(a_meeting.id)
    assert after.recording_uri is None and after.status == "ready" and after.summary == "The notes."  # only the file went
    entry = store.one("SELECT actor_id, detail FROM audit_log WHERE action = 'recording.retention_removed' AND target = :t", t=a_meeting.id)
    assert entry["actor_id"] is None and entry["detail"]["retention_days"] == 30
    current_org.set(b_user.org_id)
    assert store.get_meeting(b_meeting.id).recording_uri == b_uri
    assert store.one("SELECT 1 AS x FROM audit_log WHERE action = 'recording.retention_removed'") is None  # nothing of B's, nothing of A's shown to B
    current_org.set(c_user.org_id)
    assert store.get_meeting(c_meeting.id).recording_uri == c_uri  # its notes are being written
    # clear_recording acts only on the file the session still names
    assert recording_store.clear_recording(store, b_user.org_id, b_meeting.id, "gs://local/recordings/other.mp4") is False
    assert recording_store.clear_recording(store, c_user.org_id, c_meeting.id, c_uri) is False
