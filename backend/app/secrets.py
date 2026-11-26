"""Load secrets from Secret Manager into the environment at process start.

Cloud Run injects secrets itself (`--set-secrets`). GKE workers use this instead, authenticating with Workload
Identity, so no secret is ever copied into a Kubernetes Secret or an image:

    GSM_SECRETS="DATABASE_URL=ww-database-url-proxy,GITHUB_PRIVATE_KEY=ww-github-app-private-key"
"""
from __future__ import annotations

import os


def load_from_secret_manager(spec: str | None = None, project: str | None = None) -> list[str]:
    spec = spec if spec is not None else os.environ.get("GSM_SECRETS", "")
    pairs = [p.split("=", 1) for p in spec.split(",") if "=" in p]
    if not pairs:
        return []
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, default_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    project = project or os.environ.get("GCP_PROJECT") or default_project
    session = AuthorizedSession(creds)
    loaded = []
    for env, secret in pairs:
        env, secret = env.strip(), secret.strip()
        if os.environ.get(env):
            continue  # an explicit value wins (local runs, tests)
        r = session.get(f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{secret}/versions/latest:access", timeout=15)
        r.raise_for_status()
        import base64

        os.environ[env] = base64.b64decode(r.json()["payload"]["data"]).decode()
        loaded.append(env)
    return loaded
