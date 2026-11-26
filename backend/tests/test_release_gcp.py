"""Release round, Google Cloud: recordings uploaded straight to storage (app/api/uploads.py), Drive imports streamed to storage
with one import per session (app/services/scheduling.py), Cloud Storage signing (app/adapters/gcp.py), trace-aware JSON logs
(app/main.py), and the deployment files (cloudbuild.yaml, infra/k8s/workers.yaml, infra/terraform). Nothing leaves the machine."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.adapters.gcp import WRITE_CHUNK, GcsStorage
from app.adapters.memory import LocalBlobStorage, MemoryScheduler
from app.api import auth, uploads
from app.config import get_settings
from app.main import JsonFormatter, create_app, setup_logging, trace_context, trace_of
from app.ports import RecordingFile
from app.services import scheduling as S

ROOT = Path(__file__).resolve().parents[2]
PID, ORG = "proj-demo", "org-demo"
MANAGER, HIRE, RAVI, MEERA = ({"X-Demo-User": u} for u in ("u-priya", "u-vijay", "u-ravi", "u-meera"))
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4000  # an ISO media file's first box, then anything
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 3000
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"0" * 3000


class ListBus:
    """Keeps what was published, handles nothing: these tests stop where the Meeting agent would start."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic, event):
        self.published.append((topic, event))


class FakeGcs:
    """Cloud Storage as the API sees it, with `browser_put` doing what Cloud Storage enforces for a signed PUT."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.signed: list[tuple] = []
        self.deleted: list[str] = []

    def signed_upload(self, uri, content_type, size, minutes=15):
        self.signed.append((uri, content_type, size, minutes))
        name = uri[5:].split("/", 1)[1]
        return (f"https://storage.googleapis.com/bucket/{name}?X-Goog-Signature=abc",
                {"Content-Type": content_type, "x-goog-content-length-range": f"{size},{size}", "x-goog-if-generation-match": "0"})

    def browser_put(self, url, headers, data):
        uri = next(u for u, *_ in reversed(self.signed) if url.split("?")[0].endswith(u[5:].split("/", 1)[1]))
        _, ctype, size, _ = next(s for s in self.signed if s[0] == uri)
        assert headers["Content-Type"] == ctype and headers["x-goog-content-length-range"] == f"{size},{size}"
        if len(data) != size:
            return 400
        if uri in self.objects:
            return 412  # x-goog-if-generation-match: 0
        self.objects[uri] = (data, headers["Content-Type"])
        return 200

    def stat(self, uri):
        o = self.objects.get(uri)
        return {"size": len(o[0]), "content_type": o[1]} if o else None

    def head(self, uri, n):
        return self.objects[uri][0][:n]

    def get(self, uri):
        return self.objects[uri][0]

    def put(self, uri, data, content_type="application/octet-stream"):
        self.objects[uri] = (data, content_type)
        return uri

    def delete(self, uri):
        self.deleted.append(uri)
        return self.objects.pop(uri, None) is not None


@pytest.fixture
def api(monkeypatch, tmp_path):
    """The real app, signed in by X-Demo-User, but running as self-hosted or Google Cloud (`as_mode`)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()

    def resolve(c, authorization, x_demo_user, allow_deactivated=False):  # sign-in as in the demo, whatever the mode
        user = c.store.get_user(x_demo_user) if x_demo_user else None
        if user is None:
            raise HTTPException(401, "Missing bearer token")
        return user

    monkeypatch.setattr(auth, "_resolve", resolve)
    with TestClient(create_app()) as client:
        c = client.app.state.container
        c.deps.bus = ListBus()
        yield client, c
    get_settings.cache_clear()


def as_mode(c, mode, blobs, tmp_path, **extra):
    c.settings = c.settings.model_copy(update={"mode": mode, "gemini_api_key": "test-key", "data_dir": str(tmp_path / "data"),
                                                "gcs_bucket": "bucket" if mode == "gcp" else "", **extra})
    c.deps.blobs = blobs


def _meeting(c, title="KT: the webhook queue", by="u-priya"):
    return c.store.create_meeting(PID, title, by, provider="manual")


def _start(client, m, headers=MANAGER, filename="kt.mp4", size=None, duration_s=754.2):
    # consent: the uploader's confirmation that everyone was told and agreed (refusals without it: test_recording_consent.py)
    body = {"filename": filename, "size": len(MP4) if size is None else size, "duration_s": duration_s, "consent": True}
    return client.post(f"/api/meetings/{m.id}/recording/upload", json=body, headers=headers)


def _ready_events(c):
    return [e for _, e in c.deps.bus.published if e["type"] == "meeting.recording_ready"]


# ---------------------------------------------------------------- Google Cloud: browser -> Cloud Storage, never through Cloud Run
def test_gcp_upload_goes_straight_to_storage_and_complete_starts_processing(api, tmp_path):
    client, c = api
    gcs = FakeGcs()
    as_mode(c, "gcp", gcs, tmp_path, token_encryption_key="k" * 48)
    m = _meeting(c)
    r = _start(client, m)
    assert r.status_code == 200, r.text
    up = r.json()["upload"]
    assert up["method"] == "PUT" and up["url"].startswith("https://storage.googleapis.com/")
    assert up["headers"] == {"Content-Type": "video/mp4", "x-goog-content-length-range": f"{len(MP4)},{len(MP4)}",
                             "x-goog-if-generation-match": "0"}
    assert up["max_bytes"] == c.settings.max_recording_mb * 1024 * 1024 and up["expires_at"].endswith("Z")
    [(uri, ctype, size, minutes)] = gcs.signed
    assert uri.startswith(f"gs://bucket/recordings/{m.id}/") and uri.endswith(".mp4") and (ctype, size, minutes) == ("video/mp4", len(MP4), 15)
    assert not gcs.objects and c.store.get_meeting(m.id).status == "scheduled"  # nothing stored, nothing claimed yet

    assert gcs.browser_put(up["url"], up["headers"], MP4) == 200
    done = client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=MANAGER)
    assert done.status_code == 202, done.text
    assert done.json()["meeting"]["status"] == "processing" and done.json()["meeting"]["recording_uri"] == uri
    [event] = _ready_events(c)
    assert event["meeting_id"] == m.id and event["recording_uri"] == uri and event["duration_s"] == 754.2
    assert any(a["action"] == "meeting.recording_uploaded" and a["target"] == m.id for a in c.store.audit_rows)
    again = client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=MANAGER)
    assert again.status_code == 202 and len(_ready_events(c)) == 1 and uri in gcs.objects  # a repeated call changes nothing


def test_gcp_refuses_the_one_request_upload_through_cloud_run(api, tmp_path):
    client, c = api
    as_mode(c, "gcp", FakeGcs(), tmp_path, token_encryption_key="k" * 48)
    m = _meeting(c)
    r = client.post(f"/api/meetings/{m.id}/recording", files={"file": ("kt.mp4", MP4, "video/mp4")}, data={"duration_s": "60", "consent": "true"},
                    headers=MANAGER)
    assert r.status_code == 410 and r.json()["detail"]["code"] == "direct_upload"
    assert c.store.get_meeting(m.id).status == "scheduled"


def test_gcp_upload_needs_the_shared_token_key(api, tmp_path):
    client, c = api
    as_mode(c, "gcp", FakeGcs(), tmp_path, token_encryption_key="")
    r = _start(client, _meeting(c))
    assert r.status_code == 503 and "TOKEN_ENCRYPTION_KEY" in r.json()["detail"]


def test_complete_checks_the_file_and_removes_what_fails(api, tmp_path):
    client, c = api
    gcs = FakeGcs()
    as_mode(c, "gcp", gcs, tmp_path, token_encryption_key="k" * 48)

    m = _meeting(c, "Never arrived")
    up = _start(client, m).json()["upload"]
    r = client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=MANAGER)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "upload_missing"

    m = _meeting(c, "Not a video")
    up = _start(client, m, size=len(PDF)).json()["upload"]
    assert gcs.browser_put(up["url"], up["headers"], PDF) == 200
    r = client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=MANAGER)
    assert r.status_code == 415 and not gcs.objects and c.store.get_meeting(m.id).status == "scheduled"

    m = _meeting(c, "WebM named .mp4")
    up = _start(client, m, size=len(WEBM)).json()["upload"]
    gcs.browser_put(up["url"], up["headers"], WEBM)
    assert client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=MANAGER).status_code == 415

    m = _meeting(c, "Wrong type stored")
    up = _start(client, m).json()["upload"]
    uri = gcs.signed[-1][0]
    gcs.objects[uri] = (MP4, "text/html")  # stored some other way than the signed URL
    assert client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=MANAGER).status_code == 415
    assert uri not in gcs.objects

    m = _meeting(c, "Wrong size stored")
    up = _start(client, m).json()["upload"]
    gcs.objects[gcs.signed[-1][0]] = (MP4 + b"extra", "video/mp4")
    assert client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=MANAGER).status_code == 400
    assert not _ready_events(c)


def test_complete_is_for_the_person_and_the_call_the_upload_was_issued_to(api, tmp_path):
    client, c = api
    gcs = FakeGcs()
    as_mode(c, "gcp", gcs, tmp_path, token_encryption_key="k" * 48)
    m, other = _meeting(c), _meeting(c, "Another call")
    up = _start(client, m).json()["upload"]
    gcs.browser_put(up["url"], up["headers"], MP4)
    for meeting, who in ((m, RAVI), (other, MANAGER)):  # another person; another call
        r = client.post(f"/api/meetings/{meeting.id}/recording/complete", json={"upload_id": up["id"]}, headers=who)
        assert r.status_code == 403
    body, sig = up["id"].split(".")
    forged = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + sig
    assert client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": forged}, headers=MANAGER).status_code == 403
    assert gcs.objects and c.store.get_meeting(m.id).status == "scheduled"  # nobody else's call removed the file
    assert client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=MANAGER).status_code == 202


def test_an_upload_id_expires(api, tmp_path):
    _client, c = api
    as_mode(c, "self_hosted", LocalBlobStorage(str(tmp_path / "blobs")), tmp_path)
    t0 = time.time()
    uid = uploads.make_upload_id(c.settings, "m1", "u1", "gs://local/recordings/m1/a.mp4", "video/mp4", 10, 60.0, int(t0) + 900)
    assert uploads.read_upload_id(c.settings, uid, now=t0)["k"] == "gs://local/recordings/m1/a.mp4"
    assert uploads.read_upload_id(c.settings, uid, now=t0 + 901) is None  # step 2 must start in time
    assert uploads.read_upload_id(c.settings, uid, now=t0 + 901, grace_s=uploads.COMPLETE_S) is not None  # step 3 may come later
    assert uploads.read_upload_id(c.settings, uid, now=t0 + 901 + uploads.COMPLETE_S) is None
    assert uploads.read_upload_id(c.settings, "not-an-id", now=t0) is None


def test_who_may_start_an_upload_and_what(api, tmp_path):
    client, c = api
    as_mode(c, "gcp", FakeGcs(), tmp_path, token_encryption_key="k" * 48)
    m = _meeting(c)
    assert _start(client, m, headers=HIRE).status_code == 403  # a new hire who did not book the call
    assert _start(client, m, filename="kt.avi").status_code == 415
    assert _start(client, m, size=c.settings.max_recording_mb * 1024 * 1024 + 1).status_code == 413
    assert _start(client, m, duration_s=0.2).status_code == 422
    c.store.claim_meeting_import(m.id, "gs://bucket/recordings/x.mp4", 60.0)
    assert _start(client, m).status_code == 409  # it has its recording
    c.settings = c.settings.model_copy(update={"mode": "demo"})
    assert _start(client, _meeting(c)).status_code == 404  # the demo has no uploads


def test_a_late_upload_after_an_import_is_removed(api, tmp_path):
    client, c = api
    gcs = FakeGcs()
    as_mode(c, "gcp", gcs, tmp_path, token_encryption_key="k" * 48)
    m = _meeting(c)
    up = _start(client, m).json()["upload"]
    gcs.browser_put(up["url"], up["headers"], MP4)
    assert c.store.claim_meeting_import(m.id, "gs://bucket/recordings/imported.mp4", 60.0)  # Drive got there first
    r = client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=MANAGER)
    assert r.status_code == 409 and not gcs.objects and c.store.get_meeting(m.id).recording_uri.endswith("imported.mp4")


# ---------------------------------------------------------------- self-hosted: the same three calls, the file through the API
def test_self_hosted_upload_streams_to_disk_with_the_same_calls(api, tmp_path):
    client, c = api
    blobs = LocalBlobStorage(str(tmp_path / "blobs"))
    as_mode(c, "self_hosted", blobs, tmp_path)
    m = _meeting(c, by="u-vijay")  # whoever booked the call may add its recording
    up = _start(client, m, headers=HIRE, filename="C:\\calls\\KT.WEBM", size=len(WEBM)).json()["upload"]
    assert up["method"] == "POST" and up["url"] == f"/api/meetings/{m.id}/recording"
    assert up["headers"]["Content-Type"] == "video/webm" and up["headers"]["X-Upload-Token"] == up["id"]
    r = client.post(up["url"], content=WEBM, headers=up["headers"])  # no sign-in header: the upload id is the credential
    assert r.status_code == 204, r.text
    again = client.post(up["url"], content=WEBM, headers=up["headers"])
    assert again.status_code == 409  # the stored file is never replaced
    done = client.post(f"/api/meetings/{m.id}/recording/complete", json={"upload_id": up["id"]}, headers=HIRE)
    assert done.status_code == 202, done.text
    uri = done.json()["meeting"]["recording_uri"]
    assert uri.endswith(".webm") and blobs.get(uri) == WEBM and _ready_events(c)[0]["recording_uri"] == uri


def test_self_hosted_upload_refuses_a_wrong_file_and_keeps_nothing(api, tmp_path):
    client, c = api
    blobs = LocalBlobStorage(str(tmp_path / "blobs"))
    as_mode(c, "self_hosted", blobs, tmp_path)
    m = _meeting(c)
    up = _start(client, m).json()["upload"]
    stored = Path(blobs.path(uploads.read_upload_id(c.settings, up["id"])["k"]))
    assert client.post(up["url"], content=MP4[:-1], headers=up["headers"]).status_code == 400  # not the size it was started with
    assert client.post(up["url"], content=MP4, headers={**up["headers"], "Content-Type": "video/quicktime"}).status_code == 415
    assert client.post(up["url"], content=MP4, headers={**up["headers"], "X-Upload-Token": "forged.token"}).status_code == 403
    other = _meeting(c, "Another call")
    assert client.post(f"/api/meetings/{other.id}/recording", content=MP4, headers=up["headers"]).status_code == 403
    assert not stored.exists()


def test_self_hosted_keeps_the_one_request_upload(api, tmp_path):
    client, c = api
    blobs = LocalBlobStorage(str(tmp_path / "blobs"))
    as_mode(c, "self_hosted", blobs, tmp_path)
    m = _meeting(c)
    r = client.post(f"/api/meetings/{m.id}/recording", files={"file": ("kt.mov", MP4, "video/quicktime")}, data={"duration_s": "90.5", "consent": "true"},
                    headers=MANAGER)
    assert r.status_code == 202, r.text
    meeting = r.json()["meeting"]
    assert meeting["status"] == "processing" and meeting["recording_uri"].endswith(".mov") and blobs.get(meeting["recording_uri"]) == MP4
    assert _ready_events(c)[0]["duration_s"] == 90.5
    m2 = _meeting(c, "Bad")
    r = client.post(f"/api/meetings/{m2.id}/recording", files={"file": ("kt.mp4", b"not a video at all", "video/mp4")},
                    data={"duration_s": "60", "consent": "true"}, headers=MANAGER)
    assert r.status_code == 415 and not list((tmp_path / "blobs").rglob(f"*{m2.id}*/*"))
    assert client.post(f"/api/meetings/{m2.id}/recording", files={"file": ("kt.mp4", MP4, "video/mp4")}, data={"duration_s": "60", "consent": "true"},
                       headers=HIRE).status_code == 403
    assert client.post(f"/api/meetings/{m2.id}/recording", files={"file": ("kt.mp4", MP4, "video/mp4")}, data={"duration_s": "60", "consent": "true"}
                       ).status_code == 401  # no sign-in


def test_video_sniffing():
    assert uploads.looks_like_video(MP4, ".mp4") and uploads.looks_like_video(MP4, ".mov")
    assert uploads.looks_like_video(b"\x00\x00\x00\x08wide\x00\x00", ".mov")
    assert uploads.looks_like_video(WEBM, ".webm") and uploads.looks_like_video(WEBM, ".mkv")
    assert not uploads.looks_like_video(WEBM, ".mp4") and not uploads.looks_like_video(MP4, ".webm")
    assert not uploads.looks_like_video(b"<html>", ".mp4") and not uploads.looks_like_video(b"", ".mp4")


# ---------------------------------------------------------------- Cloud Storage signing and streaming (offline)
def _offline_gcs(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage
    from google.oauth2 import service_account

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    sa = service_account.Credentials.from_service_account_info({"type": "service_account", "client_email": "ww-api@p.iam.gserviceaccount.com",
                                                                "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token",
                                                                "project_id": "p"})
    gcs = GcsStorage.__new__(GcsStorage)
    gcs.client, gcs.bucket = storage.Client(project="p", credentials=AnonymousCredentials()), "bucket"
    monkeypatch.setattr(gcs, "_signing", lambda: {"credentials": sa})
    return gcs


def test_signed_upload_binds_type_size_and_create_only(monkeypatch):
    gcs = _offline_gcs(monkeypatch)
    url, headers = gcs.signed_upload("gs://bucket/recordings/m1/abc.mp4", "video/mp4", 123456, minutes=15)
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    assert parts.netloc == "storage.googleapis.com" and parts.path == "/bucket/recordings/m1/abc.mp4"
    assert q["X-Goog-Algorithm"] == ["GOOG4-RSA-SHA256"] and q["X-Goog-Expires"] == ["900"]
    assert set(q["X-Goog-SignedHeaders"][0].split(";")) == {"content-type", "host", "x-goog-content-length-range", "x-goog-if-generation-match"}
    assert headers == {"Content-Type": "video/mp4", "x-goog-content-length-range": "123456,123456", "x-goog-if-generation-match": "0"}


def test_drive_import_streams_into_cloud_storage_in_chunks(monkeypatch):
    gcs = _offline_gcs(monkeypatch)
    opened = {}

    class Writer:
        def __init__(self):
            self.chunks, self.exc = [], None

        def write(self, b):
            self.chunks.append(bytes(b))
            return len(b)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.exc = exc_type
            return False

    class Blob:
        def open(self, mode, **kw):
            opened.update(kw, mode=mode)
            opened["writer"] = Writer()
            return opened["writer"]

    monkeypatch.setattr(gcs, "_blob", lambda uri: Blob())
    with gcs.open_writer("gs://bucket/recordings/m1/a.mp4", "video/mp4") as w:
        w.write(b"abc")
    assert opened["mode"] == "wb" and opened["content_type"] == "video/mp4" and opened["if_generation_match"] == 0
    assert opened["chunk_size"] == WRITE_CHUNK and WRITE_CHUNK % (256 * 1024) == 0 and WRITE_CHUNK <= 16 << 20
    with pytest.raises(ValueError), gcs.open_writer("gs://bucket/recordings/m1/b.mp4", "video/mp4"):
        raise ValueError("too large")
    assert opened["writer"].exc is ValueError  # the library cancels the resumable upload: nothing is stored


# ---------------------------------------------------------------- Drive import: no spool, one at a time, nothing left behind
class StreamingStore(FakeGcs):
    """Cloud Storage with a streaming writer, as GcsStorage.open_writer."""

    def __init__(self):
        super().__init__()
        self.writes: list[int] = []

    def open_writer(self, uri, content_type):
        store = self

        class W:
            def __init__(self):
                self.buf = bytearray()

            def write(self, b):
                store.writes.append(len(b))
                self.buf += b
                return len(b)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                if exc_type is None:
                    store.objects[uri] = (bytes(self.buf), content_type)
                return False

        return W()


class ChunkyScheduler(MemoryScheduler):
    """A calendar whose recording arrives in many chunks, as Drive sends it."""

    def __init__(self, data: bytes, chunk: int = 1000):
        super().__init__()
        self.data, self.chunk = data, chunk

    def find_recording(self, event_id):
        return RecordingFile(file_id="drv", name="Recording.mp4", mime="video/mp4", duration_s=600.0, size=len(self.data))

    def download(self, file, sink, max_bytes=None):
        for i in range(0, len(self.data), self.chunk):
            if max_bytes is not None and i + self.chunk > max_bytes:
                raise ValueError("The recording is larger than the upload limit")
            sink.write(self.data[i:i + self.chunk])
        return len(self.data)


def _google_meeting(c):
    from app.adapters import recording_store

    m = c.store.create_meeting(PID, "KT on Meet", "u-priya", provider="google", calendar_event_id="evt-1")
    recording_store.confirm_consent(c.store, m.id, "u-priya", "2026-11-19")  # nothing is imported without it
    return c.store.get_meeting(m.id)


def test_import_streams_without_a_spool_and_renews_its_lease(api, tmp_path, monkeypatch):
    _client, c = api
    store = StreamingStore()
    as_mode(c, "gcp", store, tmp_path, token_encryption_key="k" * 48)
    data = MP4 * 5
    c.extras["scheduler"] = ChunkyScheduler(data)
    monkeypatch.setattr(S.tempfile, "SpooledTemporaryFile", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spooled")))
    monkeypatch.setattr(S, "LEASE_RENEW_S", 0)
    touched = []
    real_touch = c.store.touch_event
    monkeypatch.setattr(c.store, "touch_event", lambda key: (touched.append(key), real_touch(key)))
    m = _google_meeting(c)
    out = S.import_recording(c, ORG, m, "u-priya")
    assert out.status == "processing" and store.objects[out.recording_uri][0] == data
    assert len(store.writes) == len(range(0, len(data), 1000)) and max(store.writes) == 1000  # chunk by chunk
    assert touched and set(touched) == {f"recording-import:{m.id}"}
    assert c.store.event_status(f"recording-import:{m.id}") == "none"  # the lease is given back


def test_one_import_per_session_across_instances(api, tmp_path):
    client, c = api
    as_mode(c, "gcp", StreamingStore(), tmp_path, token_encryption_key="k" * 48)
    c.extras["scheduler"] = ChunkyScheduler(MP4)
    m = _google_meeting(c)
    assert c.store.claim_event(f"recording-import:{m.id}", S.IMPORT_LEASE_S) == "new"  # another instance is importing it
    with pytest.raises(S.ImportInProgress):
        S.import_recording(c, ORG, m)
    r = client.post(f"/api/meetings/{m.id}/recording/import", headers=MANAGER)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "import_in_progress"
    assert S.poll_recordings(c)["imported"] == 0
    c.store.release_event(f"recording-import:{m.id}")
    assert client.post(f"/api/meetings/{m.id}/recording/import", headers=MANAGER).status_code == 202


def test_a_failed_or_beaten_import_leaves_nothing_stored(api, tmp_path, monkeypatch):
    _client, c = api
    store = StreamingStore()
    as_mode(c, "gcp", store, tmp_path, token_encryption_key="k" * 48, max_recording_mb=0)
    c.extras["scheduler"] = ChunkyScheduler(MP4)
    m = _google_meeting(c)
    with pytest.raises(ValueError):
        S.import_recording(c, ORG, m)
    assert not store.objects and c.store.get_meeting(m.id).status == "scheduled"
    assert c.store.event_status(f"recording-import:{m.id}") == "none"

    c.settings = c.settings.model_copy(update={"max_recording_mb": 2048})
    monkeypatch.setattr(c.store, "claim_meeting_import", lambda *a: False)  # an upload got there first
    with pytest.raises(S.AlreadyRecorded):
        S.import_recording(c, ORG, m)
    assert not store.objects and store.deleted


def test_import_on_disk_writes_in_place(api, tmp_path, monkeypatch):
    _client, c = api
    blobs = LocalBlobStorage(str(tmp_path / "blobs"))
    as_mode(c, "self_hosted", blobs, tmp_path)
    c.extras["scheduler"] = ChunkyScheduler(MP4)
    monkeypatch.setattr(S.tempfile, "SpooledTemporaryFile", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spooled")))
    out = S.import_recording(c, ORG, _google_meeting(c))
    assert blobs.get(out.recording_uri) == MP4


# ---------------------------------------------------------------- logs: JSON throughout, grouped by trace
def test_trace_headers_are_read():
    assert trace_of({"x-cloud-trace-context": "0af7651916cd43dd8448eb211c80319c/12345;o=1"}) == (
        "0af7651916cd43dd8448eb211c80319c", f"{12345:016x}")
    assert trace_of({"traceparent": "00-0AF7651916CD43DD8448EB211C80319C-b7ad6b7169203331-01"}) == (
        "0af7651916cd43dd8448eb211c80319c", "b7ad6b7169203331")
    assert trace_of({"x-cloud-trace-context": "nonsense"}) is None and trace_of({}) is None


def test_log_lines_carry_the_trace_and_uvicorn_logs_json(monkeypatch):
    fmt = JsonFormatter(project="welcome-wave-dev")
    record = logging.LogRecord("api", logging.INFO, "", 0, "request", None, None)
    token = trace_context.set(("0af7651916cd43dd8448eb211c80319c", "b7ad6b7169203331"))
    try:
        line = fmt.format(record)
    finally:
        trace_context.reset(token)
    assert '"logging.googleapis.com/trace": "projects/welcome-wave-dev/traces/0af7651916cd43dd8448eb211c80319c"' in line
    assert '"logging.googleapis.com/spanId": "b7ad6b7169203331"' in line
    assert "logging.googleapis.com/trace" not in JsonFormatter(project="").format(record)

    setup_logging()
    access, server = logging.getLogger("uvicorn.access"), logging.getLogger("uvicorn")
    assert not access.handlers and access.propagate is False  # the app's own request line is the only one per request
    assert not server.handlers and server.propagate is True  # uvicorn's start-up and error lines go out as JSON


def test_requests_log_their_trace(api):
    client, _c = api
    lines: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record):  # formatted while the request runs, as the real handler does
            lines.append(JsonFormatter(project="welcome-wave-dev").format(record))

    keep, logger = Keep(), logging.getLogger("api")
    logger.addHandler(keep)
    try:
        client.get("/health", headers={"X-Cloud-Trace-Context": "0af7651916cd43dd8448eb211c80319c/1;o=1"})
    finally:
        logger.removeHandler(keep)
    [line] = [x for x in lines if '"message": "request"' in x]
    assert '"logging.googleapis.com/trace": "projects/welcome-wave-dev/traces/0af7651916cd43dd8448eb211c80319c"' in line
    assert trace_context.get() is None  # reset after the request


# ---------------------------------------------------------------- deployment files
def _cloudbuild():
    return yaml.safe_load((ROOT / "cloudbuild.yaml").read_text())


def _step(build, sid):
    return next(s for s in build["steps"] if s["id"] == sid)["args"][-1]


def test_cloud_run_settings_keep_streams_alive_and_ingress_behind_the_load_balancer():
    build = _cloudbuild()
    api, web = _step(build, "deploy-api"), _step(build, "deploy-web")
    assert "--session-affinity" in api and "--timeout 3600" in api and "--concurrency 80" in api
    assert "--timeout 3600" in web and re.search(r"--concurrency \d{3}", web) and "--max-instances 10" in web
    for script in (api, web):
        assert "--ingress ${_INGRESS}" in script
    assert build["substitutions"]["_INGRESS"] == "all"
    smoke = _step(build, "smoke")
    assert "_PUBLIC_URL" in smoke and "internal-and-cloud-load-balancing" in smoke


def test_email_reaches_the_api_and_the_workers_from_one_configuration():
    build = _cloudbuild()
    api, workers = _step(build, "deploy-api"), _step(build, "deploy-workers")
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_FROM"):
        assert f"{name}=${{_{name}}}" in api and f"__{name}__" in workers
    assert "SMTP_PASSWORD=ww-smtp-password:latest" in api and "__PUBLIC_URL__" in workers
    docs = list(yaml.safe_load_all((ROOT / "infra/k8s/workers.yaml").read_text()))
    config = next(d for d in docs if d and d["kind"] == "ConfigMap")["data"]
    assert {"SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_FROM", "PUBLIC_URL"} <= set(config)
    assert "SMTP_PASSWORD=ww-smtp-password" in config["GSM_SECRETS"]
    tf = (ROOT / "infra/terraform/secrets.tf").read_text()
    assert '"smtp-password"' in tf and re.search(r'"smtp-password"\s*=\s*\["api", "worker"\]', tf)
    assert "smtp-password" in (ROOT / "infra/scripts/set-secrets.sh").read_text()


def test_workers_use_a_current_proxy_and_leave_replicas_to_keda():
    docs = [d for d in yaml.safe_load_all((ROOT / "infra/k8s/workers.yaml").read_text()) if d]
    deployments = [d for d in docs if d["kind"] == "Deployment"]
    assert {d["metadata"]["name"] for d in deployments} == {"meeting-worker", "ingestion-worker", "review-worker"}
    for d in deployments:
        assert "replicas" not in d["spec"]  # KEDA owns the count; a deploy must not reset it
        [proxy] = d["spec"]["template"]["spec"]["initContainers"]
        assert re.fullmatch(r"gcr\.io/cloud-sql-connectors/cloud-sql-proxy:2\.28\.0@sha256:[0-9a-f]{64}", proxy["image"])
        [worker] = d["spec"]["template"]["spec"]["containers"]
        assert worker["readinessProbe"]["httpGet"]["path"] == "/healthz"
    deploy = _step(_cloudbuild(), "deploy-workers")
    assert "for d in review-worker meeting-worker ingestion-worker; do" in deploy and "rollout status deploy/$$d" in deploy


def test_terraform_sets_bucket_cors_alerts_log_exclusions_and_lb_routes():
    tf = {p.name: p.read_text() for p in (ROOT / "infra/terraform").glob("*.tf")}
    data, obs, lb = tf["data.tf"], tf["observability.tf"], tf["lb.tf"]
    assert re.search(r'dynamic "cors"', data) and '"PUT"' in data and "x-goog-content-length-range" in data
    assert "x-goog-if-generation-match" in data
    assert "oldest_unacked_message_age" in obs and "kubernetes.io/container/restart_count" in obs
    assert "cloud_scheduler_job" in obs and "google_logging_project_exclusion" in obs
    for path in ("/welcome/", "/api/preboarding/", "/api/blobs/", "/api/integrations/google/callback"):
        assert path in obs
    assert 'paths   = ["/api/*", "/webhooks/*", "/health/ready"]' in lb and "ww-api" in lb
    assert '"300"' in data  # dev max_connections covers the worst-case pools
