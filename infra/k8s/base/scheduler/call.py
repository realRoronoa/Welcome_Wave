"""Make one of the API's scheduled calls, as Cloud Scheduler does on Google Cloud (infra/terraform/events.tf).

    python /scripts/call.py /internal/outbox/flush

The call carries a Google-signed OIDC token for this pod's Google service account (on GKE, Workload Identity maps the
`scheduler` Kubernetes service account to ww-scheduler), with OUTBOX_FLUSH_AUDIENCE as its audience. The API accepts no
other caller of /internal/* (backend/app/api/outbox.py, _scheduler_only), and the call never leaves the cluster.

Exits non-zero when the call fails, so the Job is marked failed and `kubectl get jobs` shows it.
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

PATHS = ("/internal/outbox/flush", "/internal/recordings/poll", "/internal/lifecycle/tick", "/internal/audit/purge")


def id_token(audience: str) -> str:
    import google.auth.transport.requests
    import google.oauth2.id_token

    return google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)


def call(path: str, environ=os.environ) -> int:
    if path not in PATHS:
        print(f"unknown scheduled call {path!r}; one of {', '.join(PATHS)}", file=sys.stderr)
        return 2
    api = environ.get("WW_API_URL", "http://api:8080").rstrip("/")
    timeout = float(environ.get("WW_CALL_TIMEOUT_S", "60"))
    audience = environ.get("OUTBOX_FLUSH_AUDIENCE", "")
    if not audience:
        print("OUTBOX_FLUSH_AUDIENCE is not set: the API turns its scheduled calls off without it", file=sys.stderr)
        return 2
    try:
        token = id_token(audience)
    except Exception as e:  # no Workload Identity binding, no metadata server, no credentials
        print(f"{path}: could not get a Google token for the scheduler's service account: {e}", file=sys.stderr)
        return 1
    request = urllib.request.Request(api + path, data=b"", method="POST", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            print(f"{path}: {response.status} {response.read(500).decode(errors='replace')}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"{path}: {e.code} {e.read(500).decode(errors='replace')}", file=sys.stderr)
    except OSError as e:  # refused, timed out, name not found
        print(f"{path}: {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(call(sys.argv[1] if len(sys.argv) > 1 else ""))
