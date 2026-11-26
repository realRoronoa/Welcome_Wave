"""Google Calendar (with Google Meet) and Google Drive, over plain HTTPS with httpx: the Scheduler port for an organisation
that connected its Google account (Settings -> Integrations).

Booking a KT session inserts a Calendar event with a Meet conference (events.insert, conferenceDataVersion=1,
sendUpdates=all), so every attendee gets the invitation from Google. When someone records the meeting in Meet, Meet
saves the video to the organiser's Drive and attaches it to the event (`attachments`, with the transcript, a Google Doc,
beside it). The recording is found there (events.get), read with Drive files.get, and streamed into blob storage; from then
on it is the same `meeting.recording_ready` pipeline an uploaded recording takes. When Meet also saved a transcript, it is
exported as plain text (Drive files.export, inside the same drive.readonly scope) and the notes are written from it.

OAuth: one connection per organisation, scopes calendar.events + drive.readonly (+ openid email, to show which account is
connected). Only the refresh token is stored, encrypted (services/scheduling.py); access tokens live in memory.
"""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from app.ports import CalendarBooking, CalendarEvent, RecordingFile, RecordingNotShared, TranscriptFile

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CALENDAR = "https://www.googleapis.com/calendar/v3"
DRIVE = "https://www.googleapis.com/drive/v3"
SCOPES = ("openid", "email", "https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/drive.readonly")
TIMEOUT = httpx.Timeout(20.0, read=60.0)


class GoogleError(RuntimeError):
    """Google refused or failed a request. `status` is its HTTP status (0: not reached)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class GoogleAuthError(GoogleError):
    """The organisation's Google connection no longer works (revoked, expired, wrong client): connect it again."""


# ---------------------------------------------------------------- OAuth
def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Google's consent page. offline + consent: Google returns a refresh token every time, also on a reconnect."""
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
    })


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str, transport: httpx.BaseTransport | None = None) -> dict:
    """The authorisation code for tokens: {"refresh_token", "email", "scopes"}. The ID token comes straight from Google's
    token endpoint over TLS, so its claims are read without checking its signature (OpenID Connect Core 3.1.3.7)."""
    import jwt

    with httpx.Client(transport=transport, timeout=TIMEOUT) as http:
        r = http.post(TOKEN_URL, data={"code": code, "client_id": client_id, "client_secret": client_secret,
                                       "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if r.status_code != 200:
        raise GoogleAuthError(f"Google did not accept the sign-in ({_reason(r)})", r.status_code)
    body = r.json()
    if not body.get("refresh_token"):
        raise GoogleAuthError("Google returned no refresh token; remove Welcome Wave's access in your Google account and connect again")
    granted = set((body.get("scope") or "").split())
    missing = [s for s in SCOPES[2:] if s not in granted]
    if missing:
        raise GoogleAuthError("Allow access to Calendar events and Drive files on Google's consent page, then connect again")
    email = ""
    if body.get("id_token"):
        try:
            email = jwt.decode(body["id_token"], options={"verify_signature": False}).get("email", "")
        except jwt.PyJWTError:
            email = ""
    return {"refresh_token": body["refresh_token"], "email": email, "scopes": " ".join(sorted(granted))}


def revoke(token: str, transport: httpx.BaseTransport | None = None) -> bool:
    """Best effort: tell Google to drop the grant. Disconnecting works whether or not Google answers."""
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT) as http:
            return http.post(REVOKE_URL, data={"token": token}).status_code == 200
    except httpx.HTTPError:
        return False


# Access tokens by refresh token digest: one token refresh an hour per organisation, not one per request.
_tokens: dict[str, tuple[str, float]] = {}
_tokens_lock = threading.Lock()


# ---------------------------------------------------------------- the event a session becomes
def event_body(event: CalendarEvent) -> dict:
    """The events.insert payload for a KT session: title, agenda, times (UTC), attendees and a Meet conference.
    requestId is the meeting id, so a retried insert never makes a second conference."""
    return {
        "summary": event.title,
        "description": event.description,
        "start": {"dateTime": _iso(event.starts_at), "timeZone": "UTC"},
        "end": {"dateTime": _iso(event.ends_at), "timeZone": "UTC"},
        "attendees": [{"email": e} for e in dict.fromkeys(a.strip().lower() for a in event.attendees if a and "@" in a)],
        "conferenceData": {"createRequest": {"requestId": event.meeting_id, "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
        "extendedProperties": {"private": {"welcome_wave_meeting": event.meeting_id}},
        "guestsCanModify": False,
        "reminders": {"useDefault": True},
    }


def meet_link(event: dict) -> str | None:
    """The Meet link of an event: hangoutLink, else the conference's video entry point."""
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    for ep in (event.get("conferenceData") or {}).get("entryPoints") or []:
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]
    return None


def recording_attachment(event: dict) -> dict | None:
    """The video Meet attached to the event (the transcript, a Google Doc, sits beside it and is not the recording)."""
    for a in event.get("attachments") or []:
        if (a.get("mimeType") or "").startswith("video/") and a.get("fileId"):
            return a
    return None


DOC_MIME = "application/vnd.google-apps.document"
READY_AFTER_S = 120  # a recording file touched more recently than this may still be being written


def transcript_attachment(event: dict) -> dict | None:
    """The transcript Meet attached: a Google Doc whose title says "Transcript". The doc of Gemini's own meeting notes
    ("Notes by Gemini") is also a Doc and is not the transcript. Meet titles it in the organiser's language, so a transcript
    titled in another language is not found: the notes are then written from the recording, as before."""
    for a in event.get("attachments") or []:
        title = (a.get("title") or "").casefold()
        if a.get("mimeType") == DOC_MIME and a.get("fileId") and "transcript" in title and "gemini" not in title:
            return a
    return None


def _modified_recently(f: dict) -> bool:
    raw = f.get("modifiedTime")
    if not raw:
        return False
    try:
        at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - (at if at.tzinfo else at.replace(tzinfo=timezone.utc))).total_seconds() < READY_AFTER_S


class GoogleCalendarScheduler:
    """The Scheduler port on one organisation's Google account. `transport` lets tests answer for Google (httpx.MockTransport)."""

    provider = "google"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, calendar_id: str = "primary",
                 transport: httpx.BaseTransport | None = None):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.calendar_id = calendar_id
        self.transport = transport

    # ---- plumbing
    def _http(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=TIMEOUT)

    def _token(self, fresh: bool = False) -> str:
        key = hashlib.sha256(f"{self.client_id}:{self.refresh_token}".encode()).hexdigest()
        with _tokens_lock:
            hit = _tokens.get(key)
            if hit and not fresh and hit[1] > time.time() + 60:
                return hit[0]
        with self._http() as http:
            r = http.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                           "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
        if r.status_code in (400, 401):
            raise GoogleAuthError(f"Google Calendar access no longer works ({_reason(r)}); connect it again in Settings", r.status_code)
        if r.status_code != 200:
            raise GoogleError(f"Google sign-in failed ({_reason(r)})", r.status_code)
        body = r.json()
        with _tokens_lock:
            _tokens[key] = (body["access_token"], time.time() + float(body.get("expires_in", 3600)))
        return body["access_token"]

    def _call(self, method: str, url: str, **kw) -> httpx.Response:
        """One API call, retried once with a fresh token if Google says the cached one has expired."""
        for attempt in (0, 1):
            with self._http() as http:
                r = http.request(method, url, headers={"Authorization": f"Bearer {self._token(fresh=attempt == 1)}"}, **kw)
            if r.status_code != 401:
                return r
        raise GoogleAuthError("Google Calendar access no longer works; connect it again in Settings", 401)

    def _cal(self, path: str = "") -> str:
        from urllib.parse import quote

        return f"{CALENDAR}/calendars/{quote(self.calendar_id, safe='')}/events{path}"

    # ---- the port
    def create_event(self, event: CalendarEvent) -> CalendarBooking:
        r = self._call("POST", self._cal(), params={"conferenceDataVersion": 1, "sendUpdates": "all"}, json=event_body(event))
        if r.status_code not in (200, 201):
            raise GoogleError(f"Google Calendar did not create the event ({_reason(r)})", r.status_code)
        body = r.json()
        link = meet_link(body)
        if not link and body.get("id"):  # the conference was still being created: read the event once more
            got = self.get_event(body["id"])
            link = meet_link(got or {})
        return CalendarBooking(event_id=body["id"], join_url=link)

    def cancel_event(self, event_id: str) -> None:
        from urllib.parse import quote

        r = self._call("DELETE", self._cal(f"/{quote(event_id, safe='')}"), params={"sendUpdates": "all"})
        if r.status_code not in (200, 204, 404, 410):  # 404/410: deleted already
            raise GoogleError(f"Google Calendar did not cancel the event ({_reason(r)})", r.status_code)

    def get_event(self, event_id: str) -> dict | None:
        from urllib.parse import quote

        r = self._call("GET", self._cal(f"/{quote(event_id, safe='')}"))
        if r.status_code in (404, 410):
            return None
        if r.status_code != 200:
            raise GoogleError(f"Google Calendar did not return the event ({_reason(r)})", r.status_code)
        return r.json()

    def find_recording(self, event_id: str) -> RecordingFile | None:
        event = self.get_event(event_id)
        att = recording_attachment(event or {})
        if not att:
            return None
        r = self._call("GET", f"{DRIVE}/files/{att['fileId']}",
                       params={"fields": "id,name,mimeType,size,modifiedTime,videoMediaMetadata", "supportsAllDrives": "true"})
        if r.status_code == 404:
            raise RecordingNotShared()  # attached, but this account cannot open it: not shared with it
        if r.status_code != 200:
            raise GoogleError(f"Google Drive did not return the recording ({_reason(r)})", r.status_code)
        f = r.json()
        if not int(f.get("size") or 0) or _modified_recently(f):
            return None  # attached, but Drive is still writing the file: not ready; the next round looks again
        millis = (f.get("videoMediaMetadata") or {}).get("durationMillis")
        return RecordingFile(file_id=f["id"], name=f.get("name") or att.get("title") or "", mime=f.get("mimeType") or att.get("mimeType"),
                             duration_s=int(millis) / 1000 if millis else None, size=int(f["size"]) if f.get("size") else None)

    def find_transcript(self, event_id: str) -> TranscriptFile | None:
        att = transcript_attachment(self.get_event(event_id) or {})
        return TranscriptFile(file_id=att["fileId"], name=att.get("title") or "") if att else None

    def fetch_transcript(self, file: TranscriptFile, max_bytes: int) -> str:
        """The transcript Doc exported as plain text (Drive files.export), at most `max_bytes` of it. Raises GoogleError when
        Drive refuses, and ValueError when the text is longer than that: the caller falls back to the recording."""
        for attempt in (0, 1):
            with self._http() as http, http.stream("GET", f"{DRIVE}/files/{file.file_id}/export",
                                                   params={"mimeType": "text/plain", "supportsAllDrives": "true"},
                                                   headers={"Authorization": f"Bearer {self._token(fresh=attempt == 1)}"}) as r:
                if r.status_code == 401 and attempt == 0:
                    continue
                if r.status_code != 200:
                    r.read()
                    raise GoogleError(f"Google Drive did not export the transcript ({_reason(r)})", r.status_code)
                body = bytearray()
                for chunk in r.iter_bytes(1 << 16):
                    body += chunk
                    if len(body) > max_bytes:
                        raise ValueError("The transcript is larger than the limit")
                return bytes(body).decode("utf-8", errors="replace")
        raise GoogleAuthError("Google Drive access no longer works; connect Google again in Settings", 401)

    def download(self, file: RecordingFile, sink, max_bytes: int | None = None) -> int:
        if max_bytes is not None and file.size and file.size > max_bytes:
            raise ValueError("The recording is larger than the upload limit")
        written = 0
        for attempt in (0, 1):
            with self._http() as http, http.stream("GET", f"{DRIVE}/files/{file.file_id}", params={"alt": "media", "supportsAllDrives": "true"},
                                                   headers={"Authorization": f"Bearer {self._token(fresh=attempt == 1)}"}) as r:
                if r.status_code == 401 and attempt == 0:
                    continue
                if r.status_code != 200:
                    r.read()
                    raise GoogleError(f"Google Drive did not send the recording ({_reason(r)})", r.status_code)
                for chunk in r.iter_bytes(1 << 20):
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise ValueError("The recording is larger than the upload limit")
                    sink.write(chunk)
                return written
        raise GoogleAuthError("Google Drive access no longer works; connect Google again in Settings", 401)


def _iso(dt: datetime) -> str:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reason(r: httpx.Response) -> str:
    try:
        body = r.json()
        err = body.get("error")
        if isinstance(err, dict):
            return f"{r.status_code}: {err.get('message') or err.get('status')}"[:200]
        return f"{r.status_code}: {body.get('error_description') or err}"[:200]
    except ValueError:
        return str(r.status_code)
