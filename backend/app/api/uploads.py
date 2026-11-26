"""KT call recordings, uploaded without passing through the API process (docs/DEPLOY.md, "KT session recordings").

A recording is often hundreds of megabytes. On Google Cloud a request through Cloud Run is capped at 32 MiB and an instance's
/tmp lives in its memory, so the browser uploads the file straight to Cloud Storage with a signed URL. Self-hosted mode has no
Cloud Storage: the same three calls stream the file to disk on the one machine. The web app uses one flow for both:

  1. POST /api/meetings/{id}/recording/upload     {"filename", "size", "duration_s", "consent"}
       -> {"upload": {"id", "method", "url", "headers", "expires_at", "max_bytes"}}
       `consent: true` is the caller's confirmation that everyone in the session was told it is recorded and agreed
       (app/services/recordings.py). Without it, and with no confirmation on the session yet, the answer is 422
       {"code": "consent_required"} and nothing is uploaded. The confirmation is recorded here, on the session.
  2. <method> <url>, with exactly <headers> and the file itself as the body (no form encoding, no sign-in header).
       Google Cloud: PUT to a V4 signed Cloud Storage URL. Self-hosted: POST /api/meetings/{id}/recording on this API, the
       upload id in X-Upload-Token. Start it before `expires_at`; a long upload that started in time finishes.
  3. POST /api/meetings/{id}/recording/complete   {"upload_id": <upload.id>}
       -> 202 {"meeting"}. The file is checked (it is there, its size and type are the ones signed, it starts like a video
       of that type; the caller and the call are the ones the upload was issued for), then the call takes the path it always
       has: status 'processing', `meeting.recording_ready`, and the Meeting agent writes the notes.

The upload id is signed with the server's token key and names the call, the person, the stored file, its type, size and
length. Nothing about an upload is stored before step 3 (the consent of step 1 is about the session, not the upload); a file
uploaded but never completed is removed with the other raw recordings (the bucket's 30-day rule on Google Cloud;
RECORDING_RETENTION_DAYS self-hosted).

POST /api/meetings/{id}/recording without X-Upload-Token (one multipart request through the API, with the form field
`consent=true`) remains for self-hosted mode, for clients that predate this flow. Google Cloud refuses it: Cloud Run would cut any real recording off at 32 MiB.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.auth import check_project, container, current_user
from app.api.outbox import publish
from app.ports import AI_OFF, User
from app.services import recordings

router = APIRouter()
log = logging.getLogger("uploads")

RECORDING_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}
START_S = 15 * 60  # step 2 must start within this
COMPLETE_S = 12 * 3600  # step 3 is accepted this long after the PUT's deadline (2 GB at 1 Mbit/s takes about 4.5 hours)
MAX_LENGTH_S = 4 * 3600  # the longest recording the Meeting agent takes
WRITE_BUFFER = 1 << 20  # self-hosted: bytes gathered before each write to disk
EBML = b"\x1a\x45\xdf\xa3"  # WebM and Matroska begin with this
ISO_BOXES = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"}  # MP4 and QuickTime begin with one of these boxes


# ---------------------------------------------------------------- rules shared with the Drive import (routes.py)
def may_add_recording(user: User, m) -> None:
    if user.role not in {"manager", "admin", "expert"} and m.created_by != user.id:
        raise HTTPException(403, "Only a manager, an expert or whoever booked this call can add its recording")


def _recordable(c, user: User, meeting_id: str):
    """The call, if this person may add its recording now. Raises the HTTP error to answer otherwise."""
    if c.settings.is_demo:
        raise HTTPException(404, "Not Found")
    m = c.store.get_meeting(meeting_id)
    if not m:
        raise HTTPException(404, "Not found")
    check_project(c, user, m.project_id)
    may_add_recording(user, m)
    if not c.settings.ai_ready:
        raise HTTPException(503, AI_OFF)
    if m.status in {"processing", "ready"}:
        raise HTTPException(409, "This call already has its recording")
    if m.status in {"live", "cancelled"}:
        raise HTTPException(409, "End the call first" if m.status == "live" else "This call was cancelled")
    return m


def consent_or_422(c, user: User, m, confirmed: bool, via: str):
    """The session with its recording consent on record: already there, or confirmed by this request (recorded and audited
    now). Otherwise 422 `consent_required`, before anything is uploaded, imported or stored."""
    try:
        return recordings.require_consent(c, user, m, confirmed, via)
    except recordings.ConsentRequired:
        raise HTTPException(422, detail={"code": recordings.CONSENT_REQUIRED, "message": recordings.CONSENT_MESSAGE}) from None


def recording_ext(filename: str) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in RECORDING_TYPES:
        raise HTTPException(415, "Upload the recording as an MP4, WebM, MOV or MKV video")
    return ext


def _check_length(duration_s: float) -> None:
    if not 1 <= duration_s <= MAX_LENGTH_S:
        raise HTTPException(422, "The recording must be between a second and four hours long")


def _limit(c) -> int:
    return c.settings.max_recording_mb * 1024 * 1024


def _new_uri(c, m, ext: str) -> str:
    return f"gs://{c.settings.gcs_bucket or 'local'}/recordings/{m.id}/{uuid.uuid4().hex}{ext}"


def looks_like_video(head: bytes, ext: str) -> bool:
    """The file's first bytes match its type: an EBML header for WebM and MKV, an ISO media box for MP4 and MOV."""
    if ext in {".webm", ".mkv"}:
        return head[:4] == EBML
    return len(head) >= 8 and head[4:8] in ISO_BOXES


# ---------------------------------------------------------------- the signed upload id
def _key(settings) -> bytes:
    from app.services.scheduling import _key_material  # TOKEN_ENCRYPTION_KEY, or (self-hosted) the key kept in DATA_DIR

    return hashlib.sha256(b"ww-recording-upload:" + _key_material(settings)).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_upload_id(settings, meeting_id: str, user_id: str, uri: str, content_type: str, size: int, duration_s: float,
                   expires: int) -> str:
    body = _b64(json.dumps({"m": meeting_id, "u": user_id, "k": uri, "t": content_type, "n": int(size),
                            "d": round(float(duration_s), 3), "x": int(expires)}, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(_key(settings), body.encode(), hashlib.sha256).digest())}"


def read_upload_id(settings, upload_id: str, now: float | None = None, grace_s: int = 0) -> dict | None:
    """The upload an id names, if this server signed it and it is no more than `grace_s` past its deadline; else None."""
    try:
        body, sig = (upload_id or "").split(".", 1)
        want = _b64(hmac.new(_key(settings), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(want, sig):
            return None
        t = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        t = {"m": str(t["m"]), "u": str(t["u"]), "k": str(t["k"]), "t": str(t["t"]), "n": int(t["n"]), "d": float(t["d"]),
             "x": int(t["x"])}
    except (ValueError, TypeError, KeyError):
        return None
    if t["x"] + grace_s < (now if now is not None else time.time()):
        return None
    return t


def _direct(blobs) -> bool:
    """Cloud Storage: the browser uploads straight to the bucket with a signed URL."""
    return callable(getattr(blobs, "signed_upload", None))


def _on_disk(blobs) -> bool:
    """Files on disk (self-hosted): the API receives the upload and writes it in place."""
    return callable(getattr(blobs, "path", None))


# ---------------------------------------------------------------- 1. start
class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    size: int = Field(gt=0)
    duration_s: float
    consent: bool = False  # "everyone in this session was told it is recorded and agreed" (services/recordings.py)


@router.post("/meetings/{meeting_id}/recording/upload")
async def start_upload(meeting_id: str, body: UploadIn, user: User = Depends(current_user), c=Depends(container)):
    """Where and how to upload this call's recording (see the module docstring). No file is stored yet; the caller's
    confirmation of the recording consent is, once every other check has passed."""
    m = await run_in_threadpool(_recordable, c, user, meeting_id)
    ext = recording_ext(body.filename)
    _check_length(body.duration_s)
    if body.size > _limit(c):
        raise HTTPException(413, f"The recording is larger than the {c.settings.max_recording_mb} MB limit")
    blobs = c.deps.blobs
    if not (_direct(blobs) or _on_disk(blobs)):
        raise HTTPException(501, "This server's file storage cannot take an upload")
    if c.settings.mode == "gcp" and not c.settings.token_encryption_key:
        # the upload id must verify on whichever instance step 3 lands on: they share no disk to keep a generated key on
        raise HTTPException(503, "Uploads are not set up: TOKEN_ENCRYPTION_KEY is missing on the server")
    m = await run_in_threadpool(consent_or_422, c, user, m, body.consent, "upload")
    content_type, uri = RECORDING_TYPES[ext], _new_uri(c, m, ext)
    expires = int(time.time()) + START_S
    upload_id = await run_in_threadpool(make_upload_id, c.settings, m.id, user.id, uri, content_type, body.size, body.duration_s, expires)
    if _direct(blobs):
        method = "PUT"
        url, headers = await run_in_threadpool(blobs.signed_upload, uri, content_type, body.size, START_S // 60)
    else:  # the upload id is the credential for the file, sent as a header so it never appears in a URL or a log line
        method = "POST"  # the one route whose body may be a recording's size (app/ratelimit.py body_limit, nginx.conf.template)
        url, headers = f"/api/meetings/{m.id}/recording", {"Content-Type": content_type, "X-Upload-Token": upload_id}
    log.info("recording upload started", extra={"extra_fields": {"meeting_id": m.id, "size": body.size, "direct": _direct(blobs)}})
    return {"upload": {"id": upload_id, "method": method, "url": url, "headers": headers,
                       "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat().replace("+00:00", "Z"),
                       "max_bytes": _limit(c)}}


# ---------------------------------------------------------------- 2. the file (self-hosted; Google Cloud: the signed URL)
@router.post("/meetings/{meeting_id}/recording", status_code=202)
async def post_recording(meeting_id: str, request: Request, x_upload_token: str | None = Header(default=None),
                         authorization: str | None = Header(default=None), x_demo_user: str | None = Header(default=None),
                         c=Depends(container)):
    """Self-hosted. With X-Upload-Token: the file of an upload started in step 1, as the raw body (204). Without it: the
    older one-request upload, multipart with `file` and `duration_s`, for clients that predate the three steps (202)."""
    if x_upload_token is not None:
        if c.settings.is_demo:
            raise HTTPException(404, "Not Found")
        return await _receive(meeting_id, request, x_upload_token, c)
    user = await current_user(request, authorization, x_demo_user)  # here, not as a dependency: the token path has no sign-in
    if c.settings.is_demo:
        raise HTTPException(404, "Not Found")
    return await _one_request_upload(meeting_id, request, user, c)


async def _receive(meeting_id: str, request: Request, upload_id: str, c) -> Response:
    """The file streamed to disk as it arrives (never whole in memory). The upload id is the credential, as a signed URL is
    on Google Cloud, where this route takes no file."""
    blobs = c.deps.blobs
    if _direct(blobs) or not _on_disk(blobs):
        raise HTTPException(404, "Not Found")
    t = await run_in_threadpool(read_upload_id, c.settings, upload_id)
    if not t or t["m"] != meeting_id:
        raise HTTPException(403, "This upload has expired or is not for this call. Start the upload again.")
    if (request.headers.get("content-type") or "").split(";")[0].strip().lower() != t["t"]:
        raise HTTPException(415, f"Send the file with Content-Type: {t['t']}")
    declared = request.headers.get("content-length")
    if declared is not None and declared.strip() != str(t["n"]):
        raise HTTPException(400, "The file is not the size the upload was started with")
    path = blobs.path(t["k"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        f = open(path, "xb")  # noqa: SIM115 - closed below; "x": an upload never replaces a stored file
    except FileExistsError:
        raise HTTPException(409, "This file has been uploaded already") from None
    written, buf = 0, bytearray()
    try:
        async for chunk in request.stream():
            written += len(chunk)
            if written > t["n"]:
                raise HTTPException(400, "The file is larger than the upload was started with")
            buf += chunk
            if len(buf) >= WRITE_BUFFER:
                await run_in_threadpool(f.write, bytes(buf))
                buf.clear()
        if buf:
            await run_in_threadpool(f.write, bytes(buf))
        if written != t["n"]:
            raise HTTPException(400, "The upload stopped before the whole file arrived. Try again.")
    except BaseException:  # a refusal, a dropped connection, a cancelled request: no partial file is left behind
        f.close()
        blobs.delete(t["k"])  # not awaited: a cancelled request could not wait for it
        raise
    f.close()
    return Response(status_code=204)


# ---------------------------------------------------------------- 3. complete
class CompleteIn(BaseModel):
    upload_id: str = Field(min_length=10, max_length=4000)


@router.post("/meetings/{meeting_id}/recording/complete", status_code=202)
async def complete_upload(meeting_id: str, body: CompleteIn, user: User = Depends(current_user), c=Depends(container)):
    """The file of step 2 has arrived: check it and hand it to the Meeting agent, exactly as any recording."""
    if c.settings.is_demo:
        raise HTTPException(404, "Not Found")
    t = await run_in_threadpool(read_upload_id, c.settings, body.upload_id, None, COMPLETE_S)
    if not t or t["m"] != meeting_id or t["u"] != user.id:
        raise HTTPException(403, "This upload has expired, or was not started by you for this call. Upload the recording again.")

    def done_already():
        """The call's recording is this upload's file: completed before (a repeated call, or two at once)."""
        m = c.store.get_meeting(meeting_id)
        if m and m.recording_uri == t["k"]:
            check_project(c, user, m.project_id)
            return m
        return None

    def finish():
        if (m := done_already()) is not None:
            return m
        try:
            m = _recordable(c, user, meeting_id)
        except HTTPException as e:
            if e.status_code == 409:  # the call got its recording from elsewhere, or was cancelled: this file is not needed
                if (done := done_already()) is not None:
                    return done
                c.deps.blobs.delete(t["k"])
            raise
        return accept(c, user, m, t["k"], t["t"], t["d"], size=t["n"])

    return {"meeting": await run_in_threadpool(finish)}


def _stat(blobs, uri: str) -> dict | None:
    if callable(getattr(blobs, "stat", None)):
        return blobs.stat(uri)
    try:
        return {"size": os.path.getsize(blobs.path(uri)), "content_type": ""}  # on disk: the PUT checked the type
    except FileNotFoundError:
        return None


def _head(blobs, uri: str, n: int = 16) -> bytes:
    if callable(getattr(blobs, "head", None)):
        return blobs.head(uri, n)
    with open(blobs.path(uri), "rb") as f:
        return f.read(n)


def accept(c, user: User, m, uri: str, content_type: str, duration_s: float, size: int | None = None):
    """Check a stored recording, then start its processing: claim the call (status 'processing'), publish
    `meeting.recording_ready`, audit. A file that fails a check, or arrives after the call got its recording elsewhere, is
    deleted. Returns the call."""
    blobs = c.deps.blobs
    found = _stat(blobs, uri)
    if found is None:
        raise HTTPException(409, detail={"code": "upload_missing", "message": "The file has not arrived. Upload it again."})
    ext = next((e for e, t in RECORDING_TYPES.items() if t == content_type), "")

    def refuse(status: int, message):
        blobs.delete(uri)
        raise HTTPException(status, message)

    if m.recording_consent is None:  # an upload started before consent was asked for: the file is not kept
        refuse(422, {"code": recordings.CONSENT_REQUIRED, "message": recordings.CONSENT_MESSAGE})
    if found["size"] <= 0 or found["size"] > _limit(c):
        refuse(413, f"The recording is larger than the {c.settings.max_recording_mb} MB limit" if found["size"] else "The file is empty")
    if size is not None and found["size"] != size:
        refuse(400, "The file that arrived is not the size the upload was started with. Upload it again.")
    if found.get("content_type") and found["content_type"].split(";")[0].strip().lower() != content_type:
        refuse(415, "Upload the recording as an MP4, WebM, MOV or MKV video")
    if not looks_like_video(_head(blobs, uri), ext):
        refuse(415, "This file is not an MP4, WebM, MOV or MKV video")
    if not c.store.claim_meeting_import(m.id, uri, round(duration_s, 3)):
        now = c.store.get_meeting(m.id)
        if now and now.recording_uri == uri:  # the same upload, completed by a call that ran at the same time
            return now
        refuse(409, "This call already has its recording")  # an import (or another upload) got there first
    publish(c, "meetings", {"type": "meeting.recording_ready", "project_id": m.project_id, "meeting_id": m.id,
                            "recording_uri": uri, "duration_s": round(duration_s, 3), "dedupe": f"rec:{m.id}:{uuid.uuid4().hex[:8]}"})
    c.store.audit(user.org_id, user.id, "meeting.recording_uploaded", m.id, {"duration_s": round(duration_s), "bytes": found["size"]})
    log.info("recording upload completed", extra={"extra_fields": {"meeting_id": m.id, "size": found["size"]}})
    return c.store.get_meeting(m.id)


# ---------------------------------------------------------------- the one-request upload (self-hosted only)
async def _one_request_upload(meeting_id: str, request: Request, user: User, c) -> dict:
    """The recording in one multipart request through the API (`file`, `duration_s`, `consent`). New clients use the three steps, which
    work in every mode; Google Cloud refuses this one (Cloud Run cuts a request off at 32 MiB). The browser reads the video's
    length (`duration_s`); the analysis is cut into segments by it."""
    if _direct(c.deps.blobs):
        raise HTTPException(410, detail={"code": "direct_upload", "message": "Upload the recording to storage: "
                                         "POST /api/meetings/{id}/recording/upload, then …/recording/complete."})
    m = await run_in_threadpool(_recordable, c, user, meeting_id)  # who and which call first, then the body is read
    limit = _limit(c)
    if int(request.headers.get("content-length") or 0) > limit + 64 * 1024:
        raise HTTPException(413, f"The recording is larger than the {c.settings.max_recording_mb} MB limit")
    if not (request.headers.get("content-type") or "").lower().startswith("multipart/form-data"):
        raise HTTPException(415, "Start an upload with POST /api/meetings/{id}/recording/upload")
    form = await request.form(max_files=1, max_fields=4)  # the file spools to a temporary file on disk, not to memory
    try:
        file, raw_length = form.get("file"), form.get("duration_s")
        if not isinstance(file, StarletteUploadFile) or not isinstance(raw_length, str):
            raise HTTPException(422, "Send the recording as `file` and its length in seconds as `duration_s`")
        confirmed = str(form.get("consent") or "").strip().lower() in {"true", "1", "yes", "on"}
        try:
            duration_s = float(raw_length)
        except ValueError:
            raise HTTPException(422, "`duration_s` must be a number of seconds") from None
        _check_length(duration_s)
        ext = recording_ext(file.filename or "")
        if (file.size or 0) > limit:
            raise HTTPException(413, f"The recording is larger than the {c.settings.max_recording_mb} MB limit")

        def store_it():
            nonlocal m
            m = consent_or_422(c, user, m, confirmed, "upload")  # before the file is stored
            uri = _new_uri(c, m, ext)
            file.file.seek(0)
            put_file = getattr(c.deps.blobs, "put_file", None)
            if put_file:
                put_file(uri, file.file, RECORDING_TYPES[ext])
            else:
                c.deps.blobs.put(uri, file.file.read(), RECORDING_TYPES[ext])
            return accept(c, user, m, uri, RECORDING_TYPES[ext], duration_s)

        return {"meeting": await run_in_threadpool(store_it)}
    finally:
        await form.close()


# Recording consent on its own and removal requests: their own module, served with these routes.
from app.api import recordings as _recordings  # noqa: E402

router.include_router(_recordings.router)
