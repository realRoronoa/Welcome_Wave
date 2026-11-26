"""What Welcome Wave promises about a KT session's recording, beyond taking it in (docs/SECURITY.md 5 and 8):

Consent.   A recording is uploaded or imported only once someone has confirmed that everyone in the session was told it is
           recorded and agreed (CONSENT_STATEMENT). The session keeps who confirmed, when, and the version of the recording
           notice in force (app/legal.py VERSIONS["recording"]; the notice is the public page /recording). The upload and
           Drive import routes take the confirmation with the request; POST /api/meetings/{id}/recording/consent takes it on
           its own, which is what lets the poller bring a Google Calendar session's recording in afterwards. The poller
           never imports a session nobody confirmed: it asks whoever booked it, once. Consent is taken only for a session
           that has started (409 `not_held_yet` before), and it is cleared when a manager removes the recording: the
           confirmation was about that recording, so adding one again needs someone to confirm again.
           Welcome Wave cannot see who was in the call. The confirmation is that person's statement, kept and audited
           (recording.consent_confirmed); it is not proof that consent was given.

Promise.   The other kind of consent, taken BEFORE the call: booking a Google Meet session needs the booker to tick PROMISE_STATEMENT
           ("I will tell everyone on this call that it is recorded, and I will stop the recording if anyone objects."). The
           session keeps who, when and the notice version, as `kind: promised_at_booking`, audited as
           recording.consent_promised. It lets the poller and the manual import bring the Meet recording in with nobody asked
           afterwards. It is a promise by one person, not a record that everyone agreed, and every screen says so: "Promised at
           booking by <name>", against "Confirmed after the call by <name>". A confirmation after the call replaces a promise
           (it says more); removing the recording clears either. Sessions that will not be recorded in Meet are asked nothing.

Notice.    Calendar invitations for a session carry `invitation_notice`: that it will be recorded, what to do if you do
           not agree, and the link to the recording notice.

Removal.   Anyone on the project can ask for a session's recording to be removed. The request goes to the project's
           managers. A manager removes the recording with DELETE /api/meetings/{id}/recording (typing the title, as before)
           or declines with a note; whoever asked is told either way. A request removes nothing by itself.

Retention. Google Cloud: the bucket deletes raw recordings 30 days after they were stored (infra/terraform/data.tf).
           Self-hosted: RECORDING_RETENTION_DAYS (0, the default, keeps them) removes each raw video file that many days
           after it was stored, once a day, and files under recordings/ that no session names (an upload never completed).
           Notes, transcripts and KT links stay; each removal is audited (recording.retention_removed).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app import legal
from app.adapters import audit_store, recording_store
from app.config import dotenv_unless_demo
from app.ports import TRANSCRIPT_SUFFIX, Meeting, User

log = logging.getLogger("recordings")

CONSENT_REQUIRED = "consent_required"
CONSENT_STATEMENT = "Everyone in this session was told it is recorded and agreed to it."
PROMISE_STATEMENT = "I will tell everyone on this call that it is recorded, and I will stop the recording if anyone objects."
PROMISE_REQUIRED = "recording_promise_required"
PROMISE_MESSAGE = "Tick the box to say you will tell everyone the call is recorded and stop the recording if anyone objects."
CONSENT_MESSAGE = ("Confirm that everyone in this session was told it is recorded and agreed to it. "
                   "A recording is not added without that.")
NOT_HELD_YET = "not_held_yet"
NOT_HELD_MESSAGE = "This session has not been held yet. Confirm after it, when you know who took part and that they agreed."
NOT_HELD_IMPORT = "This session has not started yet. Its recording can be imported after the call has been held."
NOTICE_PATH = "/recording"  # the public recording notice (frontend/src/legal)
REASON_MAX = 1000


class RecordingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return init_settings, env_settings, dotenv_unless_demo(dotenv_settings), file_secret_settings  # demo reads no .env (config.py)

    recording_retention_days: int = 0  # self-hosted: raw recordings are removed this many days after they were stored; 0 keeps them
    recording_retention_every_s: int = 86_400  # self-hosted: how often the removal runs (0 = never)


@lru_cache
def recording_settings() -> RecordingSettings:
    return RecordingSettings()


class ConsentRequired(Exception):
    """Nobody has confirmed this session's recording consent, and this request did not either."""


class PromiseRequired(Exception):
    """A Google Meet session was booked without the booker's promise (PROMISE_STATEMENT)."""


class NothingRecorded(Exception):
    """The session has no recording, notes or transcript to remove."""


# ---------------------------------------------------------------- the notice
def notice_version() -> str:
    return legal.VERSIONS["recording"]


def notice_url(settings) -> str:
    """The recording notice's address: absolute when PUBLIC_URL is set (an invitation is read outside the app)."""
    base = (settings.public_url or "").rstrip("/")
    return f"{base}{NOTICE_PATH}" if base else ""


def invitation_notice(settings) -> str:
    """The lines a calendar invitation for a KT session carries."""
    url = notice_url(settings)
    return ("This session will be recorded. Welcome Wave turns the recording into notes for the project team. "
            "If you do not agree to being recorded, tell the organiser before it starts.\n"
            + (f"Recording notice: {url}" if url else f"Recording notice: open {NOTICE_PATH} on your Welcome Wave site."))


# ---------------------------------------------------------------- consent
def not_held_yet(m: Meeting, now: datetime | None = None) -> bool:
    """The session starts in the future. "Everyone in this session was told and agreed" cannot be true of it yet: its
    attendees can still change, and a confirmation on record would let the poller import the recording with nobody asked."""
    if m.starts_at is None:
        return False
    starts = m.starts_at if m.starts_at.tzinfo else m.starts_at.replace(tzinfo=timezone.utc)
    return starts > (now or datetime.now(timezone.utc))


def confirm_consent(c, user: User, m: Meeting, via: str) -> Meeting:
    """Record that `user` confirms CONSENT_STATEMENT for this session (the first confirmation is kept), and audit it.
    `via`: 'upload', 'import' or 'confirm'."""
    version = notice_version()
    was = m.recording_consent.kind if m.recording_consent else None
    if recording_store.confirm_consent(c.store, m.id, user.id, version):
        detail = {"notice_version": version, "via": via}
        if was == recording_store.PROMISED:
            detail["replaced"] = was  # the promise made at booking; the audit log keeps who made it
        c.store.audit(user.org_id, user.id, "recording.consent_confirmed", m.id, detail)
        try:
            c.store.close_notifications(_consent_link(m))
        except Exception:  # noqa: BLE001 - the confirmation is recorded; a notice left open is cosmetic
            log.warning("consent notice not closed", exc_info=True)
    return c.store.get_meeting(m.id)


def require_consent(c, user: User, m: Meeting, confirmed: bool, via: str) -> Meeting:
    """The session with its consent on record (a confirmation or a promise made at booking), or ConsentRequired. Called before
    a recording is stored or claimed. A confirmation sent with the request replaces a promise."""
    if m.recording_consent is not None and not (confirmed and m.recording_consent.kind == recording_store.PROMISED):
        return m
    if not confirmed:
        raise ConsentRequired()
    return confirm_consent(c, user, m, via)


def promise_consent(c, user: User, m: Meeting) -> Meeting:
    """Record the booker's promise at booking (PROMISE_STATEMENT) and audit it. A session that carries consent already keeps it."""
    version = notice_version()
    if recording_store.promise_consent(c.store, m.id, user.id, version):
        c.store.audit(user.org_id, user.id, "recording.consent_promised", m.id, {"notice_version": version, "via": "booking"})
    return c.store.get_meeting(m.id)


def _consent_link(m: Meeting) -> str:
    return f"/meetings?call={m.id}&project={m.project_id}&consent=1"


def ask_for_consent(c, m: Meeting) -> bool:
    """The poller found an ended Google Calendar session nobody confirmed: ask whoever booked it, once. True when asked now."""
    if not m.created_by:
        return False
    link = _consent_link(m)
    if c.store.notification_recipients(link, "recording_consent"):
        return False
    c.store.notify(m.created_by, "recording_consent",
                   f"'{m.title}' has ended. Confirm that everyone agreed to its recording, and Welcome Wave brings it in from Google Drive.",
                   link)
    return True


# ---------------------------------------------------------------- what the booker is told about the automatic import
def _session_link(m: Meeting, extra: str = "") -> str:
    return f"/meetings?call={m.id}&project={m.project_id}{extra}"


STUCK_REASONS = {
    "not_shared": ("Google has the recording but did not let Welcome Wave open it. Share the recording with the Google account "
                   "your organisation connected, or upload it here."),
    "none_found": ("No recording came from Google within two days. Check that the host turned recording on in Meet, and that your "
                   "organisation connected Google, or upload the recording here."),
    "no_access": ("Welcome Wave can no longer reach Google. An admin needs to connect Google again in Settings, Integrations; "
                  "the recording is then brought in on the next round."),
}


def tell_imported(c, m: Meeting) -> bool:
    """One notice to whoever booked the session: its recording is in and the notes are being written. Told once."""
    if not m.created_by:
        return False
    link = _session_link(m, "&recording=imported")
    try:
        if c.store.notification_recipients(link, "recording_imported"):
            return False
        c.store.close_notifications(_session_link(m, "&recording=stuck"))  # a problem told earlier is solved
        c.store.notify(m.created_by, "recording_imported",
                       f"The recording of '{m.title}' came in from Google Drive. The notes are being written.", link)
        return True
    except Exception:  # noqa: BLE001 - the import is done; a notice not sent is cosmetic
        log.warning("import notice not sent", exc_info=True)
        return False


def tell_stuck(c, m: Meeting, reason: str) -> bool:
    """One notice to whoever booked the session: its recording cannot be brought in, and what to do. Told once per session;
    the notice closes itself when the recording comes in."""
    if not m.created_by or reason not in STUCK_REASONS:
        return False
    link = _session_link(m, "&recording=stuck")
    try:
        if c.store.notification_recipients(link, "recording_stuck"):
            return False
        c.store.notify(m.created_by, "recording_stuck", f"The recording of '{m.title}' is not in yet. {STUCK_REASONS[reason]}", link)
        return True
    except Exception:  # noqa: BLE001 - asked again next round is not needed; the session page still says it is waiting
        log.warning("stuck notice not sent", exc_info=True)
        return False


# ---------------------------------------------------------------- removal requests
def _removal_link(m: Meeting, request_id: str) -> str:
    return f"/meetings?call={m.id}&project={m.project_id}&removal={request_id}"


def has_recorded_content(c, m: Meeting) -> bool:
    return bool(m.recording_uri) or m.status in {"processing", "ready", "failed"} or c.store.meeting_notes(m.id) is not None


def request_removal(c, user: User, m: Meeting, reason: str) -> tuple[dict, bool]:
    """`user` asks for this session's recording to be removed. The project's managers are told; nothing is removed. Asking
    again while the first request is open returns it unchanged. The reason is the person's own words: it is shown to the
    managers and never reaches a model or the audit log."""
    if not has_recorded_content(c, m):
        raise NothingRecorded()
    row, created = recording_store.create_removal_request(c.store, user.org_id, m.project_id, m.id, user.id, reason.strip()[:REASON_MAX])
    if created:
        managers = [p for p in c.store.managers(m.project_id) if p.id != user.id]
        c.store.audit(user.org_id, user.id, "recording.removal_requested", m.id, {"request_id": row["id"], "managers_told": len(managers)})
        for p in managers:
            try:
                c.store.notify(p.id, "recording_removal", f"{user.name} asked for the recording of '{m.title}' to be removed.",
                               _removal_link(m, row["id"]))
            except Exception:  # noqa: BLE001 - the request is stored and shows on the session
                log.warning("removal request notice failed", exc_info=True)
    return row, created


def _tell_requester(c, m: Meeting, row: dict, text: str) -> None:
    c.store.close_notifications(f"/meetings?call={m.id}&project={m.project_id}&removal={row['id']}")
    if row.get("requested_by") and row["requested_by"] != row.get("decided_by"):
        try:
            c.store.notify(row["requested_by"], "recording_removal", text, f"/meetings?call={m.id}&project={m.project_id}")
        except Exception:  # noqa: BLE001
            log.warning("removal outcome notice failed", exc_info=True)


def decline_removal(c, user: User, m: Meeting, request_id: str, note: str) -> dict | None:
    """A manager declines one open request, with a note for whoever asked. None when it is not open any more."""
    closed = recording_store.close_removal_requests(c.store, m.id, recording_store.DECLINED, user.id, note.strip()[:REASON_MAX], request_id)
    if not closed:
        return None
    row = closed[0]
    c.store.audit(user.org_id, user.id, "recording.removal_declined", m.id, {"request_id": row["id"]})
    _tell_requester(c, m, row, f"{user.name} declined your request to remove the recording of '{m.title}'."
                    + (f" Their note: {row['note'][:300]}" if row.get("note") else ""))
    return row


def recording_removed(c, user: User, m: Meeting) -> int:
    """After a manager removed the session's recording: its open requests are done, and whoever asked is told."""
    closed = recording_store.close_removal_requests(c.store, m.id, recording_store.REMOVED, user.id)
    for row in closed:
        _tell_requester(c, m, row, f"The recording of '{m.title}' was removed, as you asked.")
    return len(closed)


# ---------------------------------------------------------------- retention (self-hosted)
def _on_disk(blobs) -> bool:
    return callable(getattr(blobs, "path", None)) and not callable(getattr(blobs, "signed_upload", None))


def purge_recordings(c, days: int | None = None, now: float | None = None) -> dict:
    """Remove raw recordings stored more than `days` ago (RECORDING_RETENTION_DAYS; 0 removes nothing) from this machine's
    disk. A session keeps its notes, transcript and KT links and loses only its video file; a session whose notes are still
    being written is left for the next round. Files under recordings/ that no session names (an upload that was never
    completed) go too. A file's age is the time since it was written here. Returns counts."""
    days = recording_settings().recording_retention_days if days is None else days
    out = {"removed": 0, "abandoned": 0, "kept": 0, "failed": 0}
    blobs = c.deps.blobs
    if days <= 0 or not _on_disk(blobs):
        return out
    cutoff = (now if now is not None else time.time()) - days * 86_400
    rows = recording_store.stored_recordings(c.store)
    named: set[str] = set()
    for r in rows:
        uri = r["recording_uri"]
        try:
            path = blobs.path(uri)
            named.add(os.path.realpath(path))
            stored = os.path.getmtime(path)
        except (OSError, ValueError):  # no such file (already gone, or the disk is away): nothing to remove, nothing changed
            continue
        if stored >= cutoff or r["status"] == "processing":
            out["kept"] += 1
            continue
        try:
            # The session forgets the file and the removal is audited BEFORE the file goes. If the database write fails,
            # nothing changed and the next round tries again; if the delete fails, the file is named by no session and is
            # past the cutoff, so `_remove_abandoned` takes it (this round or the next).
            if not recording_store.clear_recording(c.store, str(r["org_id"]), str(r["id"]), uri):
                out["kept"] += 1  # the session changed meanwhile (a new recording, or its notes are being written)
                continue
            named.discard(os.path.realpath(path))
            audit_store.write_as(c.store, str(r["org_id"]), None, "recording.retention_removed", str(r["id"]),
                                 {"retention_days": days, "stored_at": datetime.fromtimestamp(stored, timezone.utc).isoformat()})
            try:
                blobs.delete(uri + TRANSCRIPT_SUFFIX)  # Google's transcript, kept beside the recording
            except Exception:  # noqa: BLE001 - named by no session now: the abandoned-files pass below takes it
                pass
            blobs.delete(uri)
            out["removed"] += 1
        except Exception as e:  # noqa: BLE001 - tried again next round
            out["failed"] += 1
            log.warning("recording not removed by retention", extra={"extra_fields": {"meeting_id": str(r["id"]), "error": type(e).__name__}})
    out["abandoned"] = _remove_abandoned(c, blobs, named, cutoff)
    log.info("recording retention ran", extra={"extra_fields": {**out, "retention_days": days}})
    return out


def _remove_abandoned(c, blobs, named: set[str], cutoff: float) -> int:
    """Files under recordings/ older than the cutoff that no session names."""
    try:
        root = os.path.dirname(blobs.path(f"gs://{c.settings.gcs_bucket or 'local'}/recordings/x"))
    except ValueError:
        return 0
    gone = 0
    for folder, _dirs, files in os.walk(root, topdown=False):
        for name in files:
            path = os.path.join(folder, name)
            try:
                if os.path.realpath(path) in named or os.path.getmtime(path) >= cutoff:
                    continue
                os.remove(path)
                gone += 1
            except OSError:
                continue
        if folder != root:
            try:
                os.rmdir(folder)  # only when it is empty now
            except OSError:
                pass
    return gone


class RetentionLoop:
    """Self-hosted mode: `purge_recordings` once a day (RECORDING_RETENTION_EVERY_S), first two minutes after start."""

    def __init__(self, c, every_s: int):
        self.c, self.every_s = c, every_s
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="ww-recording-retention", daemon=True)

    def start(self) -> "RetentionLoop":
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        wait = min(120, self.every_s)
        while not self._stop.wait(wait):
            try:
                purge_recordings(self.c)
            except Exception:  # noqa: BLE001 - tried again next time
                log.warning("recording retention failed", exc_info=True)
            wait = self.every_s


def on_start(c) -> list:
    """Called by app/selfhost.py start(). Returns what must be stopped at shutdown: the loop, when retention is on."""
    s = recording_settings()
    if c.settings.is_self_hosted and s.recording_retention_days > 0 and s.recording_retention_every_s > 0:
        return [RetentionLoop(c, s.recording_retention_every_s).start()]
    return []
