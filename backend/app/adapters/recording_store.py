"""Recording consent, removal requests and the retention loop's list, for the in-memory store and for Postgres.

Functions that take the store rather than store methods (as adapters/audit_store.py), so these features were added without
rewriting the adapters other work is changing. The rules live in app/services/recordings.py; this file only reads and writes.

  consent            four columns on the session (020_recording_consent.sql, 021_recording_promise.sql), read back as
                     Meeting.recording_consent. Two kinds: a confirmation after the call, and the booker's promise at booking. The
                     first confirmation is kept; a confirmation replaces a promise.
  removal requests   recording_removal_requests: one open request per person and session. Memory keeps them on the store
                     (`recording_removal_rows`), where organisation export and deletion find them (lifecycle_memory.py).
  stored recordings  across organisations, for the self-hosted retention loop: Postgres through stored_recordings(), a
                     SECURITY DEFINER function that returns ids, the file's storage path and the status only.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from app.ports import RecordingConsent

OPEN, REMOVED, DECLINED = "open", "removed", "declined"
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_postgres(store) -> bool:
    from sqlalchemy.engine import Engine

    return isinstance(getattr(store, "engine", None), Engine)


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _inner(store):
    """The MemoryStore itself: demo mode wraps it in app.live.LiveStore."""
    return store.__dict__.get("_store", store)


def _memory_rows(store) -> dict[str, dict]:
    return _inner(store).__dict__.setdefault("recording_removal_rows", {})


def _name(store, user_id) -> str | None:
    u = store.get_user(user_id) if user_id else None
    return u.name if u else None


# ---------------------------------------------------------------- consent
CONFIRMED, PROMISED = "confirmed", "promised_at_booking"


def _record_consent(store, meeting_id: str, user_id: str, notice_version: str, kind: str) -> bool:
    """Write the session's consent, if it has none. A promise made at booking is replaced by a later confirmation (which says
    more), never the other way round; a confirmation already there is kept."""
    replaces = ("(recording_consent_at IS NULL OR recording_consent_kind = 'promised_at_booking')" if kind == CONFIRMED
                else "recording_consent_at IS NULL")
    if is_postgres(store):
        if not _is_uuid(meeting_id):
            return False
        return bool(store.one(f"""UPDATE meetings SET recording_consent_by = :u, recording_consent_at = now(), recording_notice_version = :v,
                                  recording_consent_kind = :k
                                  WHERE id = CAST(:i AS uuid) AND {replaces} RETURNING id""",
                              i=meeting_id, u=user_id, v=notice_version, k=kind))
    with _lock:
        m = store.get_meeting(meeting_id)
        if m is None or (m.recording_consent is not None and not (kind == CONFIRMED and m.recording_consent.kind == PROMISED)):
            return False
        store.update_meeting(meeting_id, recording_consent=RecordingConsent(by=user_id, by_name=_name(store, user_id), at=_now(),
                                                                            notice_version=notice_version, kind=kind))
        return True


def confirm_consent(store, meeting_id: str, user_id: str, notice_version: str) -> bool:
    """Record who confirmed, after the call, that everyone was told and agreed. True when this call recorded it; False when
    the session has a confirmation already (the first is kept) or does not exist. It replaces a promise made at booking."""
    return _record_consent(store, meeting_id, user_id, notice_version, CONFIRMED)


def promise_consent(store, meeting_id: str, user_id: str, notice_version: str) -> bool:
    """Record the booker's promise, made when the session was booked, to tell everyone it is recorded and to stop the
    recording if anyone objects. True when recorded; False when the session already carries consent of either kind."""
    return _record_consent(store, meeting_id, user_id, notice_version, PROMISED)


def clear_consent(store, meeting_id: str) -> bool:
    """Forget the session's recording consent (a confirmation or a promise), because the recording it vouched for was
    removed: adding a recording again needs someone to confirm again. True when there was one to forget (the audit log keeps
    who confirmed or promised, and when)."""
    if is_postgres(store):
        if not _is_uuid(meeting_id):
            return False
        return bool(store.one("""UPDATE meetings SET recording_consent_by = NULL, recording_consent_at = NULL, recording_notice_version = NULL,
                                 recording_consent_kind = NULL
                                 WHERE id = CAST(:i AS uuid) AND recording_consent_at IS NOT NULL RETURNING id""", i=meeting_id))
    with _lock:
        m = store.get_meeting(meeting_id)
        if m is None or m.recording_consent is None:
            return False
        store.update_meeting(meeting_id, recording_consent=None)
        return True


# ---------------------------------------------------------------- removal requests
_PG_COLS = """r.id, r.project_id, r.meeting_id, r.requested_by, r.reason, r.status, r.decided_by, r.decided_at, r.note, r.created_at,
              (SELECT u.name FROM users u WHERE u.id = r.requested_by) AS requested_by_name,
              (SELECT u.name FROM users u WHERE u.id = r.decided_by) AS decided_by_name"""


def _shape(r: dict) -> dict:
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()}


def _memory_shape(store, r: dict) -> dict:
    return {**{k: v for k, v in r.items() if k != "org_id"}, "requested_by_name": _name(store, r.get("requested_by")),
            "decided_by_name": _name(store, r.get("decided_by"))}


def create_removal_request(store, org_id: str, project_id: str, meeting_id: str, user_id: str, reason: str) -> tuple[dict, bool]:
    """The person's open request for this session, and whether this call made it (asking twice keeps the first)."""
    if is_postgres(store):
        made = store.one("""INSERT INTO recording_removal_requests (org_id, project_id, meeting_id, requested_by, reason)
                            VALUES (:o, :p, :m, :u, :r) ON CONFLICT DO NOTHING RETURNING id""",
                         o=org_id, p=project_id, m=meeting_id, u=user_id, r=reason)
        row = store.one(f"""SELECT {_PG_COLS} FROM recording_removal_requests r
                            WHERE r.meeting_id = :m AND r.requested_by = :u AND r.status = 'open'""", m=meeting_id, u=user_id)
        return _shape(row), bool(made)
    with _lock:
        rows = _memory_rows(store)
        mine = next((r for r in rows.values() if r["meeting_id"] == meeting_id and r["requested_by"] == user_id and r["status"] == OPEN), None)
        if mine is not None:
            return _memory_shape(store, mine), False
        row = {"id": str(uuid.uuid4()), "org_id": org_id, "project_id": project_id, "meeting_id": meeting_id, "requested_by": user_id,
               "reason": reason, "status": OPEN, "decided_by": None, "decided_at": None, "note": "", "created_at": _now()}
        rows[row["id"]] = row
        return _memory_shape(store, row), True


def removal_requests(store, *, meeting_id: str | None = None, project_id: str | None = None, status: str | None = None) -> list[dict]:
    """A session's requests, or a project's; oldest first."""
    if is_postgres(store):
        if (meeting_id and not _is_uuid(meeting_id)) or (project_id and not _is_uuid(project_id)):
            return []
        rows = store.rows(f"""SELECT {_PG_COLS} FROM recording_removal_requests r
                              WHERE (CAST(:m AS uuid) IS NULL OR r.meeting_id = CAST(:m AS uuid))
                                AND (CAST(:p AS uuid) IS NULL OR r.project_id = CAST(:p AS uuid))
                                AND (CAST(:s AS text) IS NULL OR r.status = CAST(:s AS text))
                              ORDER BY r.created_at, r.id""", m=meeting_id, p=project_id, s=status)
        return [_shape(r) for r in rows]
    with _lock:
        rows = [dict(r) for r in _memory_rows(store).values()
                if (meeting_id is None or r["meeting_id"] == meeting_id) and (project_id is None or r["project_id"] == project_id)
                and (status is None or r["status"] == status)]
    return [_memory_shape(store, r) for r in sorted(rows, key=lambda r: (r["created_at"], r["id"]))]


def get_removal_request(store, request_id: str) -> dict | None:
    if is_postgres(store):
        if not _is_uuid(request_id):
            return None
        r = store.one(f"SELECT {_PG_COLS} FROM recording_removal_requests r WHERE r.id = CAST(:i AS uuid)", i=request_id)
        return _shape(r) if r else None
    with _lock:
        r = _memory_rows(store).get(request_id)
        r = dict(r) if r else None
    return _memory_shape(store, r) if r else None


def close_removal_requests(store, meeting_id: str, status: str, decided_by: str | None, note: str = "",
                           request_id: str | None = None) -> list[dict]:
    """Close the session's open requests (or the one named) as 'removed' or 'declined'. Returns the ones this call closed,
    so two managers deciding at once tell each requester once."""
    if status not in {REMOVED, DECLINED}:
        raise ValueError(status)
    if is_postgres(store):
        if not _is_uuid(meeting_id) or (request_id and not _is_uuid(request_id)):
            return []
        ids = store.rows("""UPDATE recording_removal_requests SET status = :s, decided_by = :d, decided_at = now(), note = :n
                            WHERE meeting_id = CAST(:m AS uuid) AND status = 'open' AND (CAST(:i AS uuid) IS NULL OR id = CAST(:i AS uuid))
                            RETURNING id""", s=status, d=decided_by, n=note, m=meeting_id, i=request_id)
        return [r for r in (get_removal_request(store, str(i["id"])) for i in ids) if r]
    with _lock:
        closed = []
        for r in _memory_rows(store).values():
            if r["meeting_id"] == meeting_id and r["status"] == OPEN and (request_id is None or r["id"] == request_id):
                r.update(status=status, decided_by=decided_by, decided_at=_now(), note=note)
                closed.append(dict(r))
    return [_memory_shape(store, r) for r in closed]


# ---------------------------------------------------------------- the retention loop (self-hosted)
def stored_recordings(store) -> list[dict]:
    """Every session with a stored recording, in every organisation: {"id", "org_id", "project_id", "recording_uri", "status"}."""
    if is_postgres(store):
        from app.adapters.postgres import current_org

        token = current_org.set(None)  # the definer function crosses organisations; no tenant is set for it
        try:
            return [_shape(r) for r in store.rows("SELECT id, org_id, project_id, recording_uri, status FROM stored_recordings()")]
        finally:
            current_org.reset(token)
    inner = _inner(store)
    return [{"id": m.id, "org_id": inner.org_of_project(m.project_id), "project_id": m.project_id, "recording_uri": m.recording_uri,
             "status": m.status} for m in list(inner.meetings.values()) if m.recording_uri]


def clear_recording(store, org_id: str, meeting_id: str, uri: str) -> bool:
    """Forget a session's raw recording file (retention removed it); its notes, transcript and KT links stay. Only when the
    session still names that file and its notes are not being written. Runs as the organisation."""
    if is_postgres(store):
        from app.adapters.postgres import current_org

        token = current_org.set(org_id)
        try:
            return bool(store.one("""UPDATE meetings SET recording_uri = NULL
                                     WHERE id = CAST(:i AS uuid) AND recording_uri = :u AND status <> 'processing' RETURNING id""",
                                  i=meeting_id, u=uri))
        finally:
            current_org.reset(token)
    with _lock:
        m = store.get_meeting(meeting_id)
        if m is None or m.recording_uri != uri or m.status == "processing":
            return False
        store.update_meeting(meeting_id, recording_uri=None)
        return True
