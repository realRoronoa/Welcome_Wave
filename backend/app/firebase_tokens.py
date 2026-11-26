"""Firebase ID tokens, verified without Google Cloud credentials.

firebase_admin.auth.verify_id_token builds the Admin SDK's HTTP client first, and that client needs Application Default
Credentials: on a Mac or any server outside Google Cloud there are none, so every sign-in failed with 401. Checking an
ID token needs no credentials at all: it is an RS256 JWT signed with one of Google's published securetoken keys. This
module does exactly the checks Firebase documents (https://firebase.google.com/docs/auth/admin/verify-id-tokens):
signature against the current public certificates, alg RS256, aud = the project id, iss = securetoken.google.com/<id>,
a non-empty sub, exp and iat (with a small clock skew) and an auth_time in the past.

The certificates are cached for as long as Google's Cache-Control max-age allows (about six hours), so a request costs
no network call. One thread downloads at a time and the others keep using what is cached, so a slow Google never queues
sign-ins behind a lock; a failed download keeps the last good certificates and is retried a little later. An unknown key id
(Google rotates keys, or a made-up token) refetches at most once a minute: made-up key ids cannot make the server download
on every request.
"""
from __future__ import annotations

import re
import threading
import time

import httpx
from google.auth import jwt as gjwt

CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
CLOCK_SKEW_S = 10
UNKNOWN_KID_REFETCH_S = 60.0  # an unknown key id triggers a download at most this often
RETRY_AFTER_FAILURE_S = 30.0  # a failed download keeps the cached certificates this long before trying again
FIRST_FETCH_WAIT_S = 12.0  # with nothing cached, a request waits for another thread's download this long


class InvalidToken(ValueError):
    """The token is malformed, expired, for another project, or not signed by Google."""


class _Certs:
    """Google's signing certificates. The lock only guards the cached values: the download itself runs outside it, by one
    thread at a time (single flight), while every other request keeps using the cached certificates."""

    def __init__(self, fetch=None, clock=time.time):
        self._fetch = fetch or self._download
        self._clock = clock
        self._lock = threading.Lock()
        self._done = threading.Condition(self._lock)
        self._certs: dict[str, str] = {}
        self._until = 0.0  # when the cached certificates go stale
        self._kid_refetch_at = 0.0  # the earliest time an unknown key id may download again
        self._fetching = False

    @staticmethod
    def _download() -> tuple[dict[str, str], float]:
        r = httpx.get(CERTS_URL, timeout=10)
        r.raise_for_status()
        m = re.search(r"max-age=(\d+)", r.headers.get("cache-control", ""))
        return r.json(), float(m.group(1)) if m else 3600.0

    def get(self, kid: str | None) -> dict[str, str]:
        with self._lock:
            now = self._clock()
            stale = now >= self._until
            unknown = bool(kid) and bool(self._certs) and kid not in self._certs and now >= self._kid_refetch_at
            if not (stale or unknown):
                return self._certs
            if self._fetching:  # another thread is downloading: use what is cached, or wait for it when nothing is
                if self._certs:
                    return self._certs
                self._done.wait_for(lambda: not self._fetching, timeout=FIRST_FETCH_WAIT_S)
                if self._certs:
                    return self._certs
                raise InvalidToken("Google's signing keys are not available yet")
            self._fetching = True
            if unknown:
                self._kid_refetch_at = now + UNKNOWN_KID_REFETCH_S
        try:
            certs, max_age = self._fetch()
        except Exception as e:  # noqa: BLE001 - keep serving the last good certificates; try again shortly
            with self._lock:
                self._fetching = False
                # nothing cached yet (a first download failed): nobody can sign in, so retry soon, but not on every request
                self._until = max(self._until, self._clock() + (RETRY_AFTER_FAILURE_S if self._certs else 2.0))
                self._done.notify_all()
                if self._certs:
                    return self._certs
            raise InvalidToken("Could not download Google's signing keys") from e
        with self._lock:
            self._fetching = False
            if isinstance(certs, dict) and certs:
                self._certs, self._until = certs, self._clock() + max(60.0, max_age)
            self._done.notify_all()
            return self._certs


_certs = _Certs()


def verify(token: str, project_id: str, certs: _Certs | None = None, now: float | None = None) -> dict:
    """The token's claims when it is a valid Firebase ID token for project_id; raises InvalidToken otherwise."""
    if not project_id:
        raise InvalidToken("No Firebase project is configured")
    try:
        header = gjwt.decode_header(token)
    except Exception as e:  # noqa: BLE001 - malformed
        raise InvalidToken("Malformed token") from e
    if header.get("alg") != "RS256":
        raise InvalidToken("Unexpected signing algorithm")
    kid = header.get("kid")
    keys = (certs or _certs).get(kid)
    if kid not in keys:
        raise InvalidToken("Unknown signing key")
    try:
        claims = gjwt.decode(token, certs={kid: keys[kid]}, audience=project_id, clock_skew_in_seconds=CLOCK_SKEW_S)
    except Exception as e:  # noqa: BLE001 - bad signature, expired, wrong audience
        raise InvalidToken(str(e)) from e
    now = time.time() if now is None else now
    if claims.get("iss") != f"https://securetoken.google.com/{project_id}":
        raise InvalidToken("Token issued for another project")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise InvalidToken("Token has no subject")
    if float(claims.get("auth_time", now + 1)) > now + CLOCK_SKEW_S:
        raise InvalidToken("Token signed in from the future")
    claims["uid"] = claims["sub"]
    return claims
