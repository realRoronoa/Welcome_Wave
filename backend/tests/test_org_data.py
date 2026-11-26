"""The organisation's data leaves completely: DELETE /api/org removes the rows AND the stored files, the LangGraph
checkpoints of its paused runs, the outbox events of its projects and, in the demo, the calendar connection and the Calendly
links; GET /api/org/export stays JSON and carries no credential (app/lifecycle/orgdata.py)."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.lifecycle import orgdata
from app.main import create_app

PID, ORG, NAME = "proj-demo", "org-demo", "Mini Policy Engine"
ADMIN, MANAGER = ({"X-Demo-User": u} for u in ("u-admin", "u-priya"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _c(client):
    return client.app.state.container


def _blob_exists(c, uri) -> bool:
    return os.path.exists(c.deps.blobs.path(uri))


def _stored_things(client):
    """A document upload, a call recording, a connected calendar and a second Calendly link: what lives outside the rows."""
    c = _c(client)
    r = client.post(f"/api/projects/{PID}/documents", files={"file": ("guide.md", b"# Guide\nThe runbook.", "text/markdown")},
                    data={"title": "Guide"}, headers=MANAGER)
    assert r.status_code == 202, r.text
    doc_uri = next(d["uri"] for d in c.store.document_rows.values() if d["title"] == "Guide")
    rec_uri = c.deps.blobs.put("gs://local/recordings/mtg-1/abc.mp4", b"\x00 video", "video/mp4")
    c.store.update_meeting("mtg-1", recording_uri=rec_uri)
    c.store.save_calendar_connection(ORG, "google", "kt@example.com", "SECRET-REFRESH-TOKEN", "calendar", "u-admin")
    c.store.set_scheduling_link(ORG, "u-meera", "calendly", "https://calendly.com/meera-demo/kt")
    return doc_uri, rec_uri


def test_the_export_carries_no_credential_and_includes_the_demo_stores(client):
    _stored_things(client)
    r = client.get("/api/org/export", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert data["calendar_connections"] == [{"org_id": ORG, "provider": "google", "account_email": "kt@example.com", "scopes": "calendar",
                                             "connected_by": "u-admin", "connected_at": data["calendar_connections"][0]["connected_at"]}]
    assert {l["user_id"] for l in data["scheduling_links"]} == {"u-ravi", "u-meera"}
    assert "SECRET-REFRESH-TOKEN" not in r.text and "refresh_token" not in r.text and "token_hash" not in r.text


def test_deleting_the_organisation_removes_files_checkpoints_outbox_and_the_demo_stores(client):
    c = _c(client)
    doc_uri, rec_uri = _stored_things(client)
    assert _blob_exists(c, doc_uri) and _blob_exists(c, rec_uri)
    threads = c.lifecycle.org_thread_ids(ORG)
    assert threads and any(c.checkpointer.get_tuple({"configurable": {"thread_id": t}}) for t in threads)  # runs paused at an approval
    assert any(r["event"].get("project_id") == PID for r in c.store.outbox.values())
    assert c.store.calendar_conns and c.store.sched_links

    r = client.request("DELETE", "/api/org", json={"confirm": NAME}, headers=ADMIN)
    assert r.status_code == 200, r.text
    gone = r.json()["deleted"]
    assert gone["stored_files"] == 2 and gone["checkpoint_threads"] == len(threads) and gone["outbox"] >= 1
    assert gone["calendar_connections"] == 1 and gone["scheduling_links"] == 2 and "stored_files_failed" not in gone

    assert not _blob_exists(c, doc_uri) and not _blob_exists(c, rec_uri)
    assert not any(c.checkpointer.get_tuple({"configurable": {"thread_id": t}}) for t in threads)
    assert not any(r["event"].get("project_id") == PID for r in c.store.outbox.values())
    assert not c.store.calendar_conns and not c.store.sched_links and not c.store.runs
    assert not c.store.projects and not c.lifecycle.programs(ORG)


def test_one_file_that_will_not_delete_does_not_stop_the_rest(client, monkeypatch):
    c = _c(client)
    doc_uri, rec_uri = _stored_things(client)
    real = c.deps.blobs.delete

    def flaky(uri):
        if uri == doc_uri:
            raise OSError("storage unavailable")
        return real(uri)

    monkeypatch.setattr(c.deps.blobs, "delete", flaky)
    gone = client.request("DELETE", "/api/org", json={"confirm": NAME}, headers=ADMIN).json()["deleted"]
    assert gone["stored_files"] == 1 and gone["stored_files_failed"] == 1  # reported, so an operator can finish it
    assert not _blob_exists(c, rec_uri) and not c.store.projects


def test_the_files_are_found_before_the_rows_go(client):
    c = _c(client)
    doc_uri, rec_uri = _stored_things(client)
    assert {doc_uri, rec_uri} <= set(c.lifecycle.org_blob_uris(ORG))
    orgdata.delete(c, ORG)
    assert c.lifecycle.org_blob_uris(ORG) == [] and c.lifecycle.org_thread_ids(ORG) == []


def test_the_local_storage_deletes_a_file_and_the_folders_it_leaves_empty(tmp_path):
    from app.adapters.memory import LocalBlobStorage

    blobs = LocalBlobStorage(str(tmp_path))
    uri = blobs.put("gs://local/docs/p1/abc/guide.md", b"x")
    other = blobs.put("gs://local/docs/p1/def/other.md", b"y")
    assert blobs.delete(uri) is True and blobs.delete(uri) is False  # already gone: not an error
    assert not (tmp_path / "local/docs/p1/abc").exists() and (tmp_path / "local/docs/p1/def/other.md").exists()
    assert blobs.get(other) == b"y"
    with pytest.raises(ValueError):
        blobs.delete("gs://local/../../etc/passwd")  # never outside the storage root
