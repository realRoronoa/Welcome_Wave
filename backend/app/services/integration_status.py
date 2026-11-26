"""Is each integration set up, and does it work? One place for the two readers: `./scripts/selfhost.sh check`
(scripts/check_integrations.py, which reads the settings from the environment and prints PASS or FAIL per integration) and
GET /api/integrations/status (admins; Settings shows it).

Each check answers {key, name, configured, ok, detail}:
  configured  the settings that integration needs are present.
  ok          True: a real call to the service worked. False: it was made and failed. None: not tested live (nothing to call
              without a person or a delivery: an OAuth client, a Calendly signing key; or the integration is not configured).
  detail      one plain sentence. Built only from safe parts (a host and port, a count, an HTTP status, an exception's type):
              never a key, a token, a password or a response body, so nothing here can leak one.

The live calls (a few kilobytes each, run side by side, 8 seconds at most):
  Gemini      GET  generativelanguage.googleapis.com/v1beta/models         (the key goes in a header, not the address)
  Firebase    POST identitytoolkit.googleapis.com/v1/accounts:createAuthUri, for an address that cannot exist
  GitHub      GET  api.github.com/user                                      (a read-only token needs no scopes for it)
  SMTP        connect and log in; no message is sent
  LangSmith   GET  <endpoint>/api/v1/sessions?limit=1                       (only when tracing is turned on)
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

TIMEOUT_S = 8.0
CACHE_S = 300  # GET /api/integrations/status re-checks at most this often per organisation
TRANSPORT = None  # tests set an httpx.MockTransport here: no real call is made
KEYS = ("gemini", "firebase", "github", "google_calendar", "calendly", "smtp", "langsmith")
NAMES = {"gemini": "Gemini (the AI)", "firebase": "Firebase sign-in", "github": "GitHub", "google_calendar": "Google Calendar and Drive",
         "calendly": "Calendly", "smtp": "Email (SMTP)", "langsmith": "LangSmith tracing"}


def _item(key: str, configured: bool, ok: bool | None, detail: str) -> dict:
    return {"key": key, "name": NAMES[key], "configured": configured, "ok": ok, "detail": detail}


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_S, transport=TRANSPORT, follow_redirects=False)


def _failed(e: Exception) -> str:
    """Why a call did not complete, without the address (a Firebase key travels in it) or any response text."""
    if isinstance(e, httpx.TimeoutException):
        return "the service did not answer in time"
    return f"could not connect ({type(e).__name__})"


# ---------------------------------------------------------------- the checks
def gemini(s, live: bool) -> dict:
    if s.mode == "gcp" and not s.gemini_api_key:
        return _item("gemini", True, None, "Vertex AI, through the server's service account (not tested here).")
    if not s.gemini_api_key:
        why = "The demo answers with a built-in offline model." if s.is_demo else "No GEMINI_API_KEY: the AI is off."
        return _item("gemini", False, None, why)
    if not live:
        return _item("gemini", True, None, "A key is set (not tested).")
    try:
        with _client() as http:
            r = http.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 50},
                         headers={"x-goog-api-key": s.gemini_api_key})
    except Exception as e:  # noqa: BLE001
        return _item("gemini", True, False, _failed(e))
    if r.status_code == 200:
        n = len((r.json() or {}).get("models") or [])
        return _item("gemini", True, True, f"The key is accepted; {n} model{'s' if n != 1 else ''} listed.")
    if r.status_code in (400, 401, 403):
        return _item("gemini", True, False, f"Google refused the key (HTTP {r.status_code}). Make a new one at aistudio.google.com/app/apikey.")
    return _item("gemini", True, False, f"Google answered HTTP {r.status_code}.")


def firebase(s, live: bool) -> dict:
    if s.is_demo:
        return _item("firebase", False, None, "The demo has its own sign-in page: no Firebase.")
    if s.firebase_auth_emulator_host:
        return _item("firebase", True, None, "Using the local Firebase emulator (not tested here).")
    if not (s.firebase_api_key and s.firebase_project):
        return _item("firebase", False, None, "FIREBASE_API_KEY and FIREBASE_PROJECT_ID are needed for sign-in.")
    if not live:
        return _item("firebase", True, None, "A web API key and project are set (not tested).")
    probe = f"ww-check-{secrets.token_hex(6)}@example.invalid"  # an address that cannot be registered: the call writes nothing
    try:
        with _client() as http:
            r = http.post("https://identitytoolkit.googleapis.com/v1/accounts:createAuthUri", params={"key": s.firebase_api_key},
                          json={"identifier": probe, "continueUri": (s.public_url or "http://localhost").rstrip("/") + "/"})
    except Exception as e:  # noqa: BLE001
        return _item("firebase", True, False, _failed(e))
    if r.status_code == 200:
        return _item("firebase", True, True, "The web API key is accepted by Identity Toolkit.")
    reason = ""
    try:
        reason = str(((r.json() or {}).get("error") or {}).get("status") or "")
    except ValueError:
        pass
    hint = {"INVALID_ARGUMENT": " Check FIREBASE_API_KEY (Project settings -> General -> Web API key).",
            "PERMISSION_DENIED": " Turn on Authentication in the Firebase console and the Identity Toolkit API."}.get(reason, "")
    return _item("firebase", True, False, f"Firebase refused the call (HTTP {r.status_code}).{hint}")


def github(s, live: bool) -> dict:
    if not s.github_token:
        if s.github_app_id:
            return _item("github", True, None, "A GitHub App is configured (not tested here).")
        return _item("github", False, None, "No GITHUB_TOKEN: public repositories only, 60 API calls an hour.")
    if not live:
        return _item("github", True, None, "A token is set (not tested).")
    try:
        with _client() as http:
            r = http.get("https://api.github.com/user", headers={"Authorization": f"Bearer {s.github_token}", "Accept": "application/vnd.github+json",
                                                                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "welcome-wave-check"})
    except Exception as e:  # noqa: BLE001
        return _item("github", True, False, _failed(e))
    if r.status_code == 200:
        limit = r.headers.get("x-ratelimit-limit")
        return _item("github", True, True, "The token is accepted" + (f"; {limit} API calls an hour." if limit else "."))
    if r.status_code in (401, 403):
        return _item("github", True, False, f"GitHub refused the token (HTTP {r.status_code}): expired, revoked or mistyped.")
    return _item("github", True, False, f"GitHub answered HTTP {r.status_code}.")


def google_calendar(s, connected: bool | None = None) -> dict:
    if s.is_demo:
        return _item("google_calendar", False, None, "The demo has a built-in calendar.")
    if not (s.google_oauth_client_id or s.google_oauth_client_secret):
        return _item("google_calendar", False, None, "No GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET: sessions are held elsewhere.")
    if not s.google_calendar_ready:
        missing = "GOOGLE_OAUTH_CLIENT_SECRET" if s.google_oauth_client_id else "GOOGLE_OAUTH_CLIENT_ID"
        return _item("google_calendar", True, False, f"{missing} is missing.")
    if not s.google_oauth_client_id.endswith(".apps.googleusercontent.com"):
        return _item("google_calendar", True, False, "The client ID should end in .apps.googleusercontent.com (a Web application OAuth client).")
    redirect = s.google_oauth_redirect_uri or ((s.public_url or "").rstrip("/") + "/api/integrations/google/callback" if s.public_url else "")
    where = f" Authorised redirect URI: {redirect}" if redirect else " Set PUBLIC_URL so the redirect URI is known."
    state = {True: " An organisation account is connected.", False: " No organisation account is connected yet (Settings -> Integrations).",
             None: ""}[connected]
    return _item("google_calendar", True, None, "The OAuth client is set (a sign-in is needed to test it)." + state + where)


def calendly(s) -> dict:
    if not s.calendly_webhook_signing_key:
        return _item("calendly", False, None, "No CALENDLY_WEBHOOK_SIGNING_KEY: bookings are not received.")
    where = f" Webhook address: {s.public_url.rstrip('/')}/webhooks/calendly" if s.public_url else ""
    return _item("calendly", True, None, "The webhook signing key is set (only a real booking can test it)." + where)


def smtp(s, live: bool) -> dict:
    if not s.email_ready:
        if s.smtp_host:
            return _item("smtp", False, None, "SMTP_FROM (or SMTP_USER) is missing: there is no From address.")
        return _item("smtp", False, None, "No SMTP_HOST: nothing is emailed; the app shows links to share.")
    if not live:
        return _item("smtp", True, None, f"{s.smtp_host}:{s.smtp_port} is set (not tested).")
    from app.adapters.mailer import MailError, SmtpMailer

    try:
        note = SmtpMailer(s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password, s.smtp_sender, timeout=TIMEOUT_S).check()
    except MailError as e:
        return _item("smtp", True, False, f"{s.smtp_host}:{s.smtp_port}: {e}")
    return _item("smtp", True, True, note + "; no message was sent.")


def langsmith(s, live: bool) -> dict:
    key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY") or ""
    if not s.langsmith_tracing:
        return _item("langsmith", False, None, "Tracing is off: nothing is sent to LangSmith." + (" (A key is set but unused.)" if key else ""))
    if not key:
        return _item("langsmith", False, False, "LANGSMITH_TRACING is on but there is no LANGSMITH_API_KEY.")
    if not live:
        return _item("langsmith", True, None, "Tracing is on and a key is set (not tested).")
    endpoint = (os.environ.get("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com").rstrip("/")
    if not endpoint.startswith("https://"):
        return _item("langsmith", True, False, "LANGSMITH_ENDPOINT must be an https address.")
    try:
        with _client() as http:
            r = http.get(f"{endpoint}/api/v1/sessions", params={"limit": 1}, headers={"x-api-key": key})
    except Exception as e:  # noqa: BLE001
        return _item("langsmith", True, False, _failed(e))
    if r.status_code == 200:
        return _item("langsmith", True, True, "The key is accepted.")
    if r.status_code in (401, 403):
        return _item("langsmith", True, False, f"LangSmith refused the key (HTTP {r.status_code}); the EU region needs its own endpoint.")
    return _item("langsmith", True, False, f"LangSmith answered HTTP {r.status_code}.")


# ---------------------------------------------------------------- all of them
def check_all(s, live: bool = True, google_connected: bool | None = None) -> list[dict]:
    """The seven integrations, in KEYS order. The live calls run side by side; one that fails or hangs cannot hide the others."""
    tasks = {"gemini": lambda: gemini(s, live), "firebase": lambda: firebase(s, live), "github": lambda: github(s, live),
             "google_calendar": lambda: google_calendar(s, google_connected), "calendly": lambda: calendly(s),
             "smtp": lambda: smtp(s, live), "langsmith": lambda: langsmith(s, live)}

    def run(key: str) -> dict:
        try:
            return tasks[key]()
        except Exception as e:  # noqa: BLE001 - a bug in one check is a result for that check, never a crash
            return _item(key, True, False, f"The check itself failed ({type(e).__name__}).")

    with ThreadPoolExecutor(len(KEYS)) as pool:
        return list(pool.map(run, KEYS))


_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def cached(org_id: str, s, refresh: bool = False, google_connected: bool | None = None) -> tuple[list[dict], float]:
    """The organisation's status, re-checked at most every CACHE_S seconds. Returns (items, when they were checked)."""
    now = time.time()
    with _lock:
        hit = _cache.get(org_id)
        if hit and not refresh and now - hit[0] < CACHE_S:
            return hit[1], hit[0]
    items = check_all(s, True, google_connected)
    with _lock:
        _cache[org_id] = (now, items)
        if len(_cache) > 200:
            _cache.pop(next(iter(_cache)))
    return items, now


def clear_cache() -> None:
    with _lock:
        _cache.clear()
