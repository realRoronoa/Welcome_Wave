"""Release round, Google Cloud, on Postgres as ww_app (row-level security on): one Drive import per session across instances,
because the import's lease is a database row. Run with a scratch server:

    TEST_PG_ADMIN_URL=postgresql://ww:ww@localhost:55432/postgres pytest tests/test_release_gcp_postgres.py
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adapters.memory import LocalBlobStorage
from app.adapters.postgres import PgStore
from app.config import Settings
from app.services import scheduling as S
from tests.test_postgres import ADMIN, _org, db, engine  # noqa: F401 - the scratch database and its fixtures
from tests.test_release_gcp import MP4, ChunkyScheduler, ListBus

pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")


def _instance(store, tmp_path, name):
    """One API instance: its own store (connections) and its own disk, the shared database."""
    return SimpleNamespace(settings=Settings(mode="self_hosted", gemini_api_key="k", data_dir=str(tmp_path / name / "data")),
                           deps=SimpleNamespace(blobs=LocalBlobStorage(str(tmp_path / name / "blobs")), bus=ListBus()),
                           store=store, extras={"scheduler": ChunkyScheduler(MP4)})


def test_a_session_being_imported_on_one_instance_is_not_imported_again_on_another(engine, tmp_path):  # noqa: F811
    a_store, b_store = PgStore(engine), PgStore(engine)
    user, project = _org(a_store, "import")
    m = a_store.create_meeting(project.id, "KT on Meet", user.id, provider="google", calendar_event_id="evt-pg-1")
    from app.adapters import recording_store

    assert recording_store.confirm_consent(a_store, m.id, user.id, "2026-11-19")  # nothing is imported without it
    m = a_store.get_meeting(m.id)
    assert m.recording_consent.by == user.id and m.recording_consent.by_name == user.name
    a, b = _instance(a_store, tmp_path, "a"), _instance(b_store, tmp_path, "b")
    lease = f"recording-import:{m.id}"
    assert a_store.claim_event(lease, S.IMPORT_LEASE_S) == "new"  # instance A is streaming it from Drive
    with pytest.raises(S.ImportInProgress):
        S.import_recording(b, user.org_id, m, user.id)
    assert not list((tmp_path / "b" / "blobs").rglob("*.mp4"))  # B downloaded nothing
    a_store.release_event(lease)  # A stopped (or its lease lapsed)
    out = S.import_recording(b, user.org_id, m, user.id)
    assert out.status == "processing" and b.deps.blobs.get(out.recording_uri) == MP4
    assert b_store.event_status(lease) == "none"  # the lease is given back once the call is claimed
    [(_, event)] = b.deps.bus.published
    assert event["type"] == "meeting.recording_ready" and event["recording_uri"] == out.recording_uri
    with pytest.raises(S.AlreadyRecorded):
        S.import_recording(a, user.org_id, a_store.get_meeting(m.id), user.id)
