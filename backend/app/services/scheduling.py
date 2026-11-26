"""Scheduling KT sessions and bringing their recordings back (docs/specs/scheduling.md).

Where a session is held, by provider:
  google   - an event on the organisation's connected Google Calendar, with a Google Meet link. Its recording is imported
             from Google Drive (by hand, or by the poller once the session has ended).
  calendly - booked on a person's Calendly page; the signed webhook creates and cancels the meeting. Recording: upload.
  manual   - held anywhere (any link, or none). Recording: upload.

Which scheduler books a session: demo mode uses the MemoryScheduler (Meet-style links, a recording "found" for every
session); self-hosted and Google Cloud use Google Calendar once the organisation has connected it, else 'manual'.

Every recording, imported or uploaded, ends the same way: the meeting moves to 'processing', `meeting.recording_ready`
is published, and the Meeting agent writes the notes and links each moment to the code on screen.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.ports import Attendee, CalendarEvent, Meeting, RecordingFile, RecordingNotShared, User  # noqa: F401 - RecordingNotShared: the poller below

log = logging.getLogger("scheduling")

GRACE_S = 10 * 60  # Meet takes a few minutes after a call to save its recording to Drive and attach it to the event
WAIT_S = 48 * 3600  # a session with no recording this long after its end is 'ended' (its recording can still be added)
POLL_LIMIT = 20  # sessions looked at per poll
MAX_LENGTH_S = 4 * 3600  # the longest recording the Meeting agent takes (the same limit as an upload)
RECORDING_TYPES = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "video/x-matroska": ".mkv"}


class SchedulingUnavailable(Exception):
    """Google Calendar was asked for, but this organisation has not connected it (or the server has no OAuth client)."""


class CalendarFailed(Exception):
    """The calendar refused the event (or could not be reached). Nothing was booked."""


class NoRecordingYet(Exception):
    """The session's calendar event has no recording attached (yet)."""


class ImportNotPossible(Exception):
    """This session's recording cannot be imported: not a Google Calendar session, Google not connected, or cancelled."""


class AlreadyRecorded(Exception):
    """The session already has its recording (or another import of it is running)."""


class ImportInProgress(AlreadyRecorded):
    """Another request, the poller or another instance is importing this session's recording right now."""


# ---------------------------------------------------------------- secrets: the refresh token and the OAuth state
def _key_material(settings) -> bytes:
    """TOKEN_ENCRYPTION_KEY, or (self-hosted, demo) a key generated once into DATA_DIR, like the blob signing key."""
    if settings.token_encryption_key:
        return settings.token_encryption_key.encode()
    path = os.path.join(settings.data_path, ".token-key")
    if not os.path.exists(path):
        os.makedirs(settings.data_path, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(48))
        except FileExistsError:  # another worker wrote it first
            pass
    with open(path) as f:
        return f.read().strip().encode()


def _fernet(settings):
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"ww-token:" + _key_material(settings)).digest()))


def encrypt_token(settings, token: str) -> str:
    return _fernet(settings).encrypt(token.encode()).decode()


def decrypt_token(settings, blob: str) -> str | None:
    """None when the key changed since the token was stored: the organisation connects Google again."""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet(settings).decrypt(blob.encode()).decode()
    except (InvalidToken, ValueError):
        return None


STATE_TTL_S = 600


def _state_key(settings) -> bytes:
    return hashlib.sha256(b"ww-oauth-state:" + _key_material(settings)).digest()


def make_state(settings, org_id: str, user_id: str, now: float | None = None) -> str:
    """The OAuth `state`: who started the connection and for which organisation, signed, valid for 10 minutes. The callback
    trusts nothing else (it arrives without the app's sign-in header)."""
    body = json.dumps({"o": org_id, "u": user_id, "n": secrets.token_urlsafe(9), "x": int((now or time.time()) + STATE_TTL_S)},
                      separators=(",", ":")).encode()
    b = base64.urlsafe_b64encode(body).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(_state_key(settings), b"google:" + b.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{b}.{sig}"


def read_state(settings, state: str, now: float | None = None) -> dict | None:
    """{"org_id", "user_id"} from a state this server made in the last 10 minutes; None if forged, altered or expired."""
    try:
        b, sig = (state or "").split(".", 1)
        want = base64.urlsafe_b64encode(hmac.new(_state_key(settings), b"google:" + b.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(want, sig):
            return None
        body = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict) or int(body.get("x", 0)) < (now or time.time()):
        return None
    return {"org_id": str(body["o"]), "user_id": str(body["u"])}


def redirect_uri(settings, base_url: str) -> str:
    """Where Google sends the browser back: GOOGLE_OAUTH_REDIRECT_URI, else PUBLIC_URL (or this request's origin) + the
    callback path. Register the same URL on the OAuth client."""
    if settings.google_oauth_redirect_uri:
        return settings.google_oauth_redirect_uri
    return f"{(settings.public_url or base_url).rstrip('/')}/api/integrations/google/callback"


# ---------------------------------------------------------------- which scheduler
def _tenant(org_id: str | None) -> None:
    try:
        from app.adapters.postgres import current_org

        current_org.set(org_id)
    except ImportError:
        pass


def scheduler_for(c, org_id: str):
    """The organisation's Scheduler, or None ('manual': held anywhere, recording uploaded). `c.extras["scheduler"]`
    overrides it (tests); demo mode keeps one MemoryScheduler that finds a recording for every session."""
    injected = c.extras.get("scheduler")
    if injected is not None:
        return injected
    if c.settings.is_demo:
        from app.adapters.memory import MemoryScheduler

        return c.extras.setdefault("scheduler", MemoryScheduler(finds_recordings=True))
    if not c.settings.google_calendar_ready:
        return None
    conn = c.store.calendar_connection(org_id, "google")
    token = decrypt_token(c.settings, conn["refresh_token_enc"]) if conn else None
    if not token:
        if conn:
            log.warning("google connection cannot be decrypted (TOKEN_ENCRYPTION_KEY changed?)", extra={"extra_fields": {"org_id": org_id}})
        return None
    from app.adapters.google_calendar import GoogleCalendarScheduler

    return GoogleCalendarScheduler(c.settings.google_oauth_client_id, c.settings.google_oauth_client_secret, token,
                                   transport=c.extras.get("google_transport"))


def status(c, user: User) -> dict:
    """GET /api/integrations: what is connected, for the Integrations tab and the schedule form."""
    s = c.settings
    conn = None if s.is_demo else c.store.calendar_connection(user.org_id, "google")
    connected = s.is_demo or bool(conn)
    by = c.store.get_user(str(conn["connected_by"])) if conn and conn.get("connected_by") else None
    return {
        "google": {
            "available": s.is_demo or s.google_calendar_ready,  # an OAuth client is set up on the server
            "connected": connected,
            "account": "Demo calendar" if s.is_demo else (conn or {}).get("account_email") or None,
            "connected_at": (conn or {}).get("connected_at"),
            "connected_by": by.name if by else None,
            "can_manage": user.role in {"manager", "admin"} and not s.is_demo,
        },
        "calendly": {
            "webhook": bool(s.calendly_webhook_signing_key),
            "webhook_url": f"{s.public_url.rstrip('/')}/webhooks/calendly" if s.public_url else "/webhooks/calendly",
            "link": c.store.scheduling_link(user.id, "calendly"),
        },
        "default_provider": "google" if connected else "manual",
    }


# ---------------------------------------------------------------- booking
@dataclass
class SessionRequest:
    title: str
    starts_at: datetime | None = None  # None: an unscheduled session ('manual'), held whenever it suits
    duration_min: int = 30
    attendee_ids: list[str] = field(default_factory=list)  # project members to invite (the booker always is)
    provider: str | None = None  # 'google' | 'manual'; None: Google when connected and a time is given, else manual
    join_url: str | None = None  # manual: where it is held
    description: str = ""
    # The booker ticked the promise (recordings.PROMISE_STATEMENT): kept on the session as consent 'promised_at_booking'
    recording_promise: bool = False


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _invitation_note(settings) -> str:
    """What every calendar invitation for a KT session says: how it becomes notes, and the recording notice (that it is
    recorded, what to do if you do not agree, where the notice is: services/recordings.py)."""
    from app.services import recordings

    return ("Record this meeting in Meet: Welcome Wave writes the notes from the recording.\n\n"
            + recordings.invitation_notice(settings))


def will_be_recorded(c, org_id: str, req: SessionRequest) -> bool:
    """True when this request makes a Google Meet session: the one kind Welcome Wave brings a recording in for by itself. Such
    a session needs the booker's promise (require_promise); one held anywhere else is asked nothing."""
    if req.provider is not None:
        return req.provider == "google"
    return bool(req.starts_at) and scheduler_for(c, org_id) is not None


def require_promise(c, org_id: str, req: SessionRequest) -> None:
    """Raise recordings.PromiseRequired when the request makes a Meet session and the booker did not promise."""
    if not req.recording_promise and will_be_recorded(c, org_id, req):
        from app.services import recordings

        raise recordings.PromiseRequired()


def _record_promise(c, user: User, m: Meeting | None, req: SessionRequest) -> Meeting | None:
    """The booker's promise, on the session it was made for: only a session that is on Google Meet carries one."""
    if m is not None and req.recording_promise and m.provider == "google":
        from app.services import recordings

        return recordings.promise_consent(c, user, m)
    return m


def book(c, project_id: str, org_id: str, user: User, req: SessionRequest, kt_key: str | None = None) -> Meeting | None:
    """Create the session and, on Google, its calendar event with a Meet link (Google sends the invitations). The meeting
    row comes first, so a KT planner session booked twice is refused (None) before any invitation goes out. If Google
    refuses the event, the row is removed again and CalendarFailed raised: nothing half-booked is left."""
    sched = scheduler_for(c, org_id)
    starts = _utc(req.starts_at)
    provider = req.provider or ("google" if sched is not None and starts else "manual")
    if provider == "google" and sched is None:
        raise SchedulingUnavailable()
    if provider == "google" and starts is None:
        raise ValueError("A Google Calendar session needs a start time")
    ends = starts + timedelta(minutes=req.duration_min) if starts else None
    members = {u.id: u for u in c.store.members(project_id)}
    people = [members[i] for i in dict.fromkeys([user.id, *req.attendee_ids]) if i in members]
    if user.id not in members:  # an admin who is not on the project still organises it
        people.insert(0, user)
    attendees = [Attendee(email=u.email, name=u.name, user_id=u.id) for u in people]
    m = c.store.create_meeting(project_id, req.title, user.id, kt_key, provider=provider,
                               join_url=req.join_url if provider == "manual" else None, starts_at=starts, ends_at=ends, attendees=attendees)
    if m is None or provider != "google":
        return m
    note = (req.description + "\n\n" if req.description else "") + _invitation_note(c.settings)
    try:
        booking = sched.create_event(CalendarEvent(meeting_id=m.id, title=req.title, description=note, starts_at=starts, ends_at=ends,
                                                   attendees=[a.email for a in attendees]))
    except Exception as e:
        log.warning("calendar event not created", extra={"extra_fields": {"meeting_id": m.id, "error": str(e)[:300]}})
        c.store.discard_meeting(m.id)
        raise CalendarFailed(str(e) or "Google Calendar could not be reached") from e
    c.store.update_meeting(m.id, join_url=booking.join_url, calendar_event_id=booking.event_id)
    return _record_promise(c, user, c.store.get_meeting(m.id), req)  # after the event exists: a refused event leaves no promise


def reschedule(c, org_id: str, user: User, meeting_id: str, req: SessionRequest) -> Meeting:
    """Give an unscheduled session (booked without a time) its time, attendees and place; on Google, its calendar event."""
    m = c.store.get_meeting(meeting_id)
    sched = scheduler_for(c, org_id)
    starts = _utc(req.starts_at)
    provider = req.provider or ("google" if sched is not None else "manual")
    if provider == "google" and sched is None:
        raise SchedulingUnavailable()
    if starts is None:
        raise ValueError("Pick a start time")
    ends = starts + timedelta(minutes=req.duration_min)
    members = {u.id: u for u in c.store.members(m.project_id)}
    known = {a.user_id: a for a in m.attendees if a.user_id}
    ids = list(dict.fromkeys([*(a.user_id for a in m.attendees if a.user_id), user.id, *req.attendee_ids]))
    attendees = [Attendee(email=members[i].email, name=members[i].name, user_id=i) if i in members else known[i] for i in ids
                 if i in members or i in known]
    fields = {"provider": provider, "starts_at": starts, "ends_at": ends, "started_at": starts, "attendees": attendees}
    if provider == "google":
        try:
            booking = sched.create_event(CalendarEvent(meeting_id=m.id, title=m.title, starts_at=starts, ends_at=ends,
                                                       description=_invitation_note(c.settings),
                                                       attendees=[a.email for a in attendees]))
        except Exception as e:
            raise CalendarFailed(str(e) or "Google Calendar could not be reached") from e
        fields.update(join_url=booking.join_url, calendar_event_id=booking.event_id)
    else:
        fields["join_url"] = req.join_url or m.join_url
    c.store.update_meeting(m.id, **fields)
    return _record_promise(c, user, c.store.get_meeting(m.id), req)


def cancel_event(c, org_id: str, m: Meeting) -> str | None:
    """After a session is cancelled here: take it off the calendar too. Returns what the person still has to do, if anything."""
    if m.provider == "calendly":
        return "Cancel it in Calendly too, so the invitation is withdrawn."
    if m.provider != "google" or not m.calendar_event_id:
        return None
    sched = scheduler_for(c, org_id)
    if sched is None:
        return "Google Calendar is not connected any more: remove the event from the calendar by hand."
    try:
        sched.cancel_event(m.calendar_event_id)
    except Exception as e:  # noqa: BLE001 - cancelled here either way
        log.warning("calendar event not cancelled", extra={"extra_fields": {"meeting_id": m.id, "error": str(e)[:300]}})
        return "Google Calendar did not cancel the event: remove it from the calendar by hand."
    return None


# ---------------------------------------------------------------- recordings
IMPORT_LEASE_S = 600  # an import holds this lease, renewed while the file streams; one whose process died is retried after it
LEASE_RENEW_S = 30


class _Renewing:
    """A write-only file that passes each chunk on, and renews the import's lease every LEASE_RENEW_S while the file streams."""

    def __init__(self, sink, renew):
        self.sink, self.renew, self.at = sink, renew, time.monotonic()

    def write(self, chunk):
        n = self.sink.write(chunk)
        if time.monotonic() - self.at >= LEASE_RENEW_S:
            self.at = time.monotonic()
            self.renew()
        return n


def _download_into_storage(c, sched, f: RecordingFile, uri: str, mime: str, renew) -> None:
    """Stream the Drive file into blob storage, holding at most one chunk on the way. Cloud Storage takes it as a resumable
    upload (on Cloud Run /tmp is memory, so nothing is spooled there); files on disk (self-hosted) are written in place. A
    failure (too large, Drive or storage gone) leaves nothing stored."""
    blobs = c.deps.blobs
    limit = c.settings.max_recording_mb * 1024 * 1024
    try:
        if callable(getattr(blobs, "open_writer", None)):
            with blobs.open_writer(uri, mime) as sink:
                sched.download(f, _Renewing(sink, renew), max_bytes=limit)
        elif callable(getattr(blobs, "path", None)):
            path = blobs.path(uri)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "xb") as sink:
                sched.download(f, _Renewing(sink, renew), max_bytes=limit)
        else:  # a store that can do neither: through a temporary file on disk
            with tempfile.SpooledTemporaryFile(max_size=16 << 20) as tmp:
                sched.download(f, _Renewing(tmp, renew), max_bytes=limit)
                tmp.seek(0)
                if callable(getattr(blobs, "put_file", None)):
                    blobs.put_file(uri, tmp, mime)
                else:
                    blobs.put(uri, tmp.read(), mime)
    except BaseException:
        try:
            blobs.delete(uri)
        except Exception:  # noqa: BLE001 - nothing was stored; raw recordings expire anyway
            log.warning("partial recording not removed", extra={"extra_fields": {"uri": uri}})
        raise


def _ext(f: RecordingFile) -> str:
    name = (f.name or "").lower()
    for ext in (".mp4", ".webm", ".mov", ".mkv"):
        if name.endswith(ext):
            return ext
    return RECORDING_TYPES.get((f.mime or "").split(";")[0], ".mp4")


def delete_recording_blobs(c, uri: str) -> None:
    """Delete a stored recording and the Google transcript kept beside it (a transcript that was never stored is no error)."""
    from app.services import transcripts

    try:
        c.deps.blobs.delete(transcripts.blob_uri(uri))
    except Exception as e:  # noqa: BLE001 - the recording itself is what must go; a transcript left behind expires with recordings/
        log.warning("transcript not removed", extra={"extra_fields": {"error": type(e).__name__}})
    c.deps.blobs.delete(uri)


def _fetch_google_transcript(c, sched, m: Meeting, uri: str, length: float) -> str | None:
    """Google's transcript for the session, stored beside the recording; its address, or None when Meet saved none or it
    could not be read (the notes are then written from the recording, as before). Never raises: a transcript is a bonus."""
    from app.services import transcripts

    find, fetch = getattr(sched, "find_transcript", None), getattr(sched, "fetch_transcript", None)
    if not (callable(find) and callable(fetch)):
        return None
    try:
        file = find(m.calendar_event_id)
        if file is None:
            return None
        text = fetch(file, transcripts.MAX_BYTES)
        if not transcripts.parse(text, length):  # no speech in it: not worth using
            return None
        tid = transcripts.blob_uri(uri)
        c.deps.blobs.put(tid, text.encode("utf-8"), "text/plain")
        return tid
    except Exception as e:  # noqa: BLE001 - tolerant by design
        log.warning("google transcript not used", extra={"extra_fields": {"meeting_id": m.id, "error": type(e).__name__}})
        return None


def start_notes(c, m: Meeting, uri: str, length: float, transcript_uri: str | None = None) -> bool:
    """Hand a stored recording to the Meeting agent: claim the session (status 'processing', its recording_uri) and publish
    `meeting.recording_ready`. False when another upload or import claimed it first (nothing changed). The claim and the
    event are two writes: if the event cannot be recorded (the database went away between them), the claim is undone (the
    session's previous status and recording come back) and the error raised, so a session is never left 'processing' with
    no event to process it, which no retry could recover (a claim takes only scheduled, ended or failed sessions). Uploads
    and imports both go through here. `transcript_uri`: Google's own transcript, stored beside the recording; the Note-taker
    writes the notes from it instead of transcribing the video (services/transcripts.py)."""
    from app.api.outbox import publish

    if not c.store.claim_meeting_import(m.id, uri, length):
        return False
    try:
        publish(c, "meetings", {"type": "meeting.recording_ready", "project_id": m.project_id, "meeting_id": m.id,
                                "recording_uri": uri, "duration_s": length, "dedupe": f"rec:{m.id}:{uuid.uuid4().hex[:8]}",
                                **({"transcript_uri": transcript_uri} if transcript_uri else {})})
    except Exception:
        try:
            c.store.update_meeting(m.id, status=m.status, recording_uri=m.recording_uri, duration_s=m.duration_s)
            delete_recording_blobs(c, uri)  # nothing names this copy any more
        except Exception:  # noqa: BLE001 - the original error is the one to report
            log.warning("a recording claim could not be undone", extra={"extra_fields": {"meeting_id": m.id}}, exc_info=True)
        raise
    if m.recording_uri and m.recording_uri != uri:  # a new recording for a session whose notes failed: the old file is
        try:                                       # named by nothing now, so organisation deletion could never find it
            delete_recording_blobs(c, m.recording_uri)
        except Exception as e:  # noqa: BLE001 - the new recording is in; the old one expires with the recordings/ rule
            log.warning("replaced recording not removed", extra={"extra_fields": {"meeting_id": m.id, "error": type(e).__name__}})
    return True


def import_recording(c, org_id: str, m: Meeting, actor_id: str | None = None) -> Meeting:
    """Find the session's recording on its calendar event, stream it into blob storage and hand it to the Meeting agent
    (status 'processing', `meeting.recording_ready`), exactly as an upload does. Raises NoRecordingYet, ImportNotPossible,
    AlreadyRecorded (ImportInProgress while another import of it runs), recordings.ConsentRequired (nobody has confirmed the
    session's recording consent: nothing is asked of Google), or the scheduler's own errors (GoogleError, ValueError when it
    is too large).

    One import per session at a time across every process and instance: the lease is a row in the database (the same claim
    events use), renewed while the file streams, so a retried button press or the poller on another instance does not start
    a second download, and an import whose instance died is retried once the lease lapses."""
    if m.status in {"processing", "ready"}:
        raise AlreadyRecorded()
    if m.status in {"cancelled", "live"}:
        raise ImportNotPossible("This session was cancelled" if m.status == "cancelled" else "This call is still live")
    if m.provider != "google" or not m.calendar_event_id:
        raise ImportNotPossible("Only sessions on Google Calendar can be imported. Upload the recording instead.")
    sched = scheduler_for(c, org_id)
    if sched is None:
        raise ImportNotPossible("Google Calendar is not connected. Connect it in Settings, or upload the recording.")
    if m.recording_consent is None:  # the route records it from the request first; the poller asks whoever booked it
        from app.services import recordings

        raise recordings.ConsentRequired()
    lease = f"recording-import:{m.id}"
    if c.store.claim_event(lease, IMPORT_LEASE_S) != "new":
        raise ImportInProgress()
    try:
        f = sched.find_recording(m.calendar_event_id)  # raises RecordingNotShared when it is attached but this account cannot open it
        if f is None:
            if getattr(sched, "finds_recordings", False) and m.starts_at and _utc(m.starts_at) > datetime.now(timezone.utc):
                # the demo calendar: a session that has not been held has no recording, and says so plainly
                raise ImportNotPossible("This session has not started yet. Its recording can be imported after the call has been held.")
            raise NoRecordingYet()
        length = f.duration_s or ((m.ends_at - m.starts_at).total_seconds() if m.ends_at and m.starts_at else 0)
        length = round(min(max(length, 1.0), MAX_LENGTH_S), 3)
        uri = f"gs://{c.settings.gcs_bucket or 'local'}/recordings/{m.id}/{uuid.uuid4().hex}{_ext(f)}"
        mime = f.mime if (f.mime or "").startswith("video/") else "video/mp4"
        _download_into_storage(c, sched, f, uri, mime, renew=lambda: c.store.touch_event(lease))
        transcript_uri = _fetch_google_transcript(c, sched, m, uri, length)
        if not start_notes(c, m, uri, length, transcript_uri):  # an upload got there first: this copy is not needed
            delete_recording_blobs(c, uri)
            raise AlreadyRecorded()
        c.store.audit(org_id, actor_id, "meeting.recording_imported", m.id,
                      {"source": "google_drive", "duration_s": round(length), "google_transcript": bool(transcript_uri)})
        return c.store.get_meeting(m.id)
    finally:
        c.store.release_event(lease)


def poll_recordings(c, limit: int = POLL_LIMIT) -> dict:
    """One round of the recording poller (self-hosted: a loop in app/selfhost.py every RECORDING_POLL_S; Google Cloud: Cloud
    Scheduler calls POST /internal/recordings/poll). For each scheduled session that has ended:

      * a Google session whose recording consent is on record, of either kind (a promise made at booking, or a confirmation
        after the call), has its recording imported once Meet has attached it and Drive has finished writing it; Google's own
        transcript comes with it when Meet saved one. A session that is not ready yet is looked at again next round.
      * a Google session with neither kind is not fetched at all: whoever booked it is asked once to confirm.
      * whoever booked a session is told once when its recording came in, and once when it cannot (the recording is not
        shared with the connected Google account, Google needs connecting again, or nothing came within WAIT_S).
      * a session with no recording WAIT_S after its end becomes 'ended' (a recording can still be added by hand).

    Imports never double: one import per session at a time (a lease row) and the claim takes only a session that has no
    recording. Returns counts."""
    from app.services import recordings

    out = {"imported": 0, "waiting": 0, "ended": 0, "failed": 0, "needs_consent": 0, "stuck": 0}
    _tenant(None)
    rows = c.store.meetings_awaiting_recording(GRACE_S, WAIT_S, limit)
    now = datetime.now(timezone.utc)
    for r in rows:
        org = str(r["org_id"])
        _tenant(org)
        try:
            m = c.store.get_meeting(str(r["id"]))
            if not m or m.status != "scheduled":
                continue
            done = False
            stuck: str | None = None
            if m.provider == "google" and c.settings.ai_ready and m.recording_consent is None:
                out["needs_consent"] += 1
                try:
                    recordings.ask_for_consent(c, m)
                except Exception:  # noqa: BLE001 - asked again next round
                    log.warning("consent request not sent", extra={"extra_fields": {"meeting_id": m.id}}, exc_info=True)
            elif m.provider == "google" and c.settings.ai_ready:
                try:
                    import_recording(c, org, m)
                    done = True
                    out["imported"] += 1
                    recordings.tell_imported(c, m)
                except (NoRecordingYet, ImportNotPossible, AlreadyRecorded):
                    pass
                except RecordingNotShared:
                    stuck = "not_shared"
                except Exception as e:  # noqa: BLE001 - Google or storage briefly unavailable: the next poll tries again
                    log.warning("recording import failed", extra={"extra_fields": {"meeting_id": m.id, "error": str(e)[:300]}})
                    out["failed"] += 1
                    if type(e).__name__ == "GoogleAuthError":
                        stuck = "no_access"
            if done:
                continue
            ended = r["ends_at"]
            if isinstance(ended, str):
                ended = datetime.fromisoformat(ended)
            if ended and now - _utc(ended) > timedelta(seconds=WAIT_S) and c.store.expire_meeting(m.id):
                out["ended"] += 1
                if m.provider == "google" and m.recording_consent is not None and stuck is None:
                    stuck = "none_found"
            else:
                out["waiting"] += 1
            if stuck and recordings.tell_stuck(c, m, stuck):
                out["stuck"] += 1
        finally:
            _tenant(None)
    return out


# ---------------------------------------------------------------- Calendly
def handle_calendly(c, b) -> dict:
    """A verified Calendly delivery (adapters/calendly.Booking). The project comes from the link's utm_campaign; the
    booking counts only if its invitee is a member of that project (the host alone is not enough: every booking on a
    member's page has a member host), so a tagged link used by an outsider with their own email books nothing. The
    audit entry names the host and records the invitee as an unverified booker. invitee.created makes the session (once
    per scheduled event); invitee.canceled cancels it."""
    org = c.store.org_of_project(b.project_id)
    if not org:
        return {"ignored": "unknown project"}
    _tenant(org)
    emails = [e for e in dict.fromkeys([b.invitee.get("email", ""), *(h["email"] for h in b.hosts), *b.guests]) if e]
    people = {e: c.store.user_by_email(org, e) for e in emails}
    members = {e: u for e, u in people.items() if u and c.store.is_member(b.project_id, u.id)}
    if not members:
        return {"ignored": "nobody in this project"}
    # The booking must be made BY a member: the host is always one (it is their Calendly page), so a host alone would let
    # anyone with a tagged link book into the project. The invitee's email is typed into Calendly's form and not verified,
    # so it decides only whether the booking counts and who may manage the session; the audit names the host, whose
    # calendar Calendly's signed delivery vouches for, and records the typed booker as such.
    booker = members.get(b.invitee.get("email", ""))
    if booker is None:
        return {"ignored": "the invitee is not in this project"}
    host = next((members[h["email"]] for h in b.hosts if h.get("email") in members), None)

    if b.kind == "canceled":
        m = c.store.meeting_by_event("calendly", b.event_uri)
        if not m or not c.store.cancel_meeting(m.id):
            return {"ignored": "nothing to cancel"}
        c.store.audit(org, None, "meeting.cancelled", m.id, {"via": "calendly", "rescheduled": b.rescheduled})
        c.store.close_notifications(f"/meetings?call={m.id}")
        return {"cancelled": m.id}

    creator = booker
    title = b.name
    if b.kt_key:
        from app.services.kt_planner import planned_title

        title = planned_title(c.store, c.index, b.project_id, org, b.kt_key) or title
    names = {b.invitee.get("email", ""): b.invitee.get("name", ""), **{h["email"]: h["name"] for h in b.hosts}}
    attendees = [Attendee(email=e, name=(people[e].name if people.get(e) else names.get(e, "")), user_id=people[e].id if people.get(e) else None)
                 for e in emails]

    def create(key):
        return c.store.create_meeting(b.project_id, title, creator.id, key, provider="calendly", join_url=b.join_url,
                                      calendar_event_id=b.event_uri, starts_at=b.starts_at, ends_at=b.ends_at, attendees=attendees)

    m = create(b.kt_key)
    if m is None:
        existing = c.store.meeting_by_event("calendly", b.event_uri)
        if existing:
            return {"duplicate": existing.id}  # a redelivery
        # The session's key is held by another scheduled booking: a Calendly reschedule whose cancellation has not arrived
        # yet. The newest booking wins; anything else holding it (a Google session) keeps it, and this one books no key.
        holder = next((x for x in c.store.list_meetings(b.project_id)
                       if x.kt_key == b.kt_key and x.status == "scheduled" and x.provider == "calendly"), None)
        if holder and c.store.cancel_meeting(holder.id):
            m = create(b.kt_key)
        m = m or create(None)
        if m is None:
            return {"ignored": "could not record the booking"}
    c.store.audit(org, host.id if host else None, "meeting.created", m.id,
                  {"via": "calendly", "kt_key": m.kt_key, "booked_as": booker.id, "booker_verified": False})
    return {"created": m.id}
