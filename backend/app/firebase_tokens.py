"""Firebase ID tokens, verified without Google Cloud credentials.

firebase_admin.auth.verify_id_token builds the Admin SDK's HTTP client first, and that client needs Application Default
Credentials: on a Mac or any server outside Google Cloud there are none, so every sign-in failed with 401. Checking an
ID token needs no credentials at all: it is an RS256 JWT signed with one of Google's published securetoken keys. This
module does exactly the checks Firebase documents (https://firebase.google.com/docs/auth/admin/verify-id-tokens):
signature against the current public certificates, alg RS256, aud = the project id, iss = securetoken.google.com/<id>,
a non-empty sub, exp and iat (with a small clock skew) and an auth_time in the past.

The certificates are cached for as long as Google's Cache-Control max-age allows (about six hours), so a request costs
no network call; an unknown key id refetches once (Google rotates keys).
"""
from __future__ import annotations

import re
import threading
import time

import httpx
from google.auth import jwt as gjwt

CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
CLOCK_SKEW_S = 10


class InvalidToken(ValueError):
    """The token is malformed, expired, for another project, or not signed by Google."""


class _Certs:
    def __init__(self, fetch=None):
        self._fetch = fetch or self._download
        self._lock = threading.Lock()
        self._certs: dict[str, str] = {}
        self._until = 0.0

    @staticmethod
    def _download() -> tuple[dict[str, str], float]:
        r = httpx.get(CERTS_URL, timeout=10)
        r.raise_for_status()
        m = re.search(r"max-age=(\d+)", r.headers.get("cache-control", ""))
        return r.json(), float(m.group(1)) if m else 3600.0

    def get(self, kid: str | None) -> dict[str, str]:
        with self._lock:
            if time.time() >= self._until or (kid and kid not in self._certs):
                certs, max_age = self._fetch()
                self._certs, self._until = certs, time.time() + max(60.0, max_age)
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
