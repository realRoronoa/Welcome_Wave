"""Read-only GitHub App client. There is deliberately no method that writes to GitHub."""
from __future__ import annotations

import hashlib
import hmac
import tarfile
import tempfile
import time

import httpx

from app.services.chunking import MAX_FILE_BYTES, should_index

API = "https://api.github.com"
MAX_TARBALL_BYTES = 500 * 1024 * 1024


def oauth_user(client_id: str, client_secret: str, code: str, http: httpx.Client | None = None) -> dict | None:
    """Exchange the one-time code GitHub sends after installation (with "Request user authorization during installation")
    for the GitHub identity of the person in the browser: {"id", "login", "token"}, or None if the code is invalid or used.
    The token is the user's; it is only used for the membership check below and never stored."""
    http = http or httpx.Client(timeout=30)
    tok = http.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"},
                    data={"client_id": client_id, "client_secret": client_secret, "code": code})
    tok.raise_for_status()
    access = tok.json().get("access_token")
    if not access:
        return None
    r = http.get(f"{API}/user", headers={"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json",
                                         "X-GitHub-Api-Version": "2022-11-28"})
    r.raise_for_status()
    return {"id": int(r.json()["id"]), "login": r.json()["login"], "token": access}


def read_tarball(http: httpx.Client, url: str, headers: dict, full_name: str, limits: tuple[int, int] | None = None) -> dict[str, str]:
    """The indexable text files of a repository tarball, streamed to disk (not memory): {path: text}.

    What is kept in memory is bounded while reading: past twice INDEX_MAX_FILES files or twice INDEX_MAX_MB of text (`limits`,
    (files, MB), from the settings by default; 0 = none) it stops with SourceTooLarge. Twice: the exact limits count only what
    is indexed (ingestion._plan, which also leaves out large data files), so a repository within them is never refused here."""
    from app.config import get_settings
    from app.ports import SourceTooLarge

    if limits is None:
        s = get_settings()
        limits = (s.index_max_files, s.index_max_mb)
    max_files, max_bytes = 2 * limits[0], 2 * limits[1] * 1024 * 1024
    out: dict[str, str] = {}
    held = 0
    with tempfile.TemporaryFile() as tmp:
        size = 0
        with http.stream("GET", url, headers=headers, timeout=300) as r:
            r.raise_for_status()
            for block in r.iter_bytes(1 << 20):
                size += len(block)
                if size > MAX_TARBALL_BYTES:
                    raise ValueError(f"{full_name} is larger than {MAX_TARBALL_BYTES >> 20} MB compressed; index a smaller repository")
                tmp.write(block)
        tmp.seek(0)
        with tarfile.open(fileobj=tmp, mode="r:gz") as tar:
            for m in tar:
                if not m.isfile():
                    continue
                rel = m.name.split("/", 1)[1] if "/" in m.name else m.name  # strip "<owner>-<repo>-<sha>/"
                if should_index(rel, m.size) or (rel.endswith("CODEOWNERS") and m.size <= MAX_FILE_BYTES):
                    held += m.size
                    if max_files and len(out) >= max_files:
                        raise SourceTooLarge(f"{full_name} has more than {len(out):,} files to index; the limit is "
                                             f"{limits[0]:,} (INDEX_MAX_FILES).")
                    if max_bytes and held > max_bytes:
                        raise SourceTooLarge(f"{full_name} has more than {max_bytes // 1048576:,} MB of text to index; the limit "
                                             f"is {limits[1]:,} MB (INDEX_MAX_MB).")
                    out[rel] = tar.extractfile(m).read().decode("utf-8", errors="replace")
    return out


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


class GitHubApp:
    def __init__(self, app_id: str, private_key: str, client: httpx.Client | None = None):
        self.app_id, self.private_key = app_id, private_key.replace("\\n", "\n")
        self.http = client or httpx.Client(timeout=30, follow_redirects=True)
        self._tokens: dict[str, tuple[str, float]] = {}

    def _app_jwt(self) -> str:
        import jwt  # PyJWT[crypto]

        now = int(time.time())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id}, self.private_key, algorithm="RS256")

    def _token(self, installation_id: str) -> str:
        tok, exp = self._tokens.get(installation_id, ("", 0))
        if time.time() < exp - 120:
            return tok
        r = self.http.post(f"{API}/app/installations/{installation_id}/access_tokens",
                           headers={"Authorization": f"Bearer {self._app_jwt()}", "Accept": "application/vnd.github+json"})
        r.raise_for_status()
        tok = r.json()["token"]
        self._tokens[installation_id] = (tok, time.time() + 3600)
        return tok

    def _get(self, installation_id, path, accept="application/vnd.github+json", **params):
        r = self.http.get(f"{API}{path}", params=params,
                          headers={"Authorization": f"Bearer {self._token(installation_id)}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"})
        r.raise_for_status()
        return r

    def _paged(self, installation_id, path, **params):
        out, page = [], 1
        while True:
            batch = self._get(installation_id, path, per_page=100, page=page, **params).json()
            out += batch
            if len(batch) < 100:
                return out
            page += 1

    # ---- RepoSource ----
    def files(self, full_name, ref, installation_id):
        """Whole repo in one request: stream the tarball to disk (not memory) and keep indexable text files."""
        headers = {"Authorization": f"Bearer {self._token(installation_id)}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        return read_tarball(self.http, f"{API}/repos/{full_name}/tarball/{ref}", headers, full_name)

    def file(self, full_name, path, ref, installation_id):
        try:
            return self._get(installation_id, f"/repos/{full_name}/contents/{path}", accept="application/vnd.github.raw", ref=ref).text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def head_sha(self, full_name, ref, installation_id):
        """The commit `ref` (a branch, tag or sha) points to now, or None if there is no such ref."""
        try:
            return self._get(installation_id, f"/repos/{full_name}/commits/{ref}", accept="application/vnd.github.sha").text.strip()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {404, 422}:
                return None
            raise

    def pr_files(self, full_name, number, installation_id):
        return self._paged(installation_id, f"/repos/{full_name}/pulls/{number}/files")

    def check_runs(self, full_name, sha, installation_id):
        return self._get(installation_id, f"/repos/{full_name}/commits/{sha}/check-runs").json().get("check_runs", [])

    def repo_info(self, full_name, installation_id):
        """None unless this installation can read the repository - the check before connecting it."""
        try:
            r = self._get(installation_id, f"/repos/{full_name}").json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {403, 404}:
                return None
            raise
        return {"full_name": r["full_name"], "default_branch": r.get("default_branch") or "main", "private": r.get("private", True)}

    def installation_repositories(self, installation_id: str) -> list[dict]:
        out, page = [], 1
        while True:
            body = self._get(installation_id, "/installation/repositories", per_page=100, page=page).json()
            out += [{"full_name": r["full_name"], "default_branch": r.get("default_branch") or "main", "private": r.get("private", True)}
                    for r in body.get("repositories", [])]
            if len(out) >= body.get("total_count", 0) or not body.get("repositories"):
                return out
            page += 1

    def installation_account(self, installation_id: str) -> dict | None:
        """The account an installation is on, from GitHub itself: {"login", "id", "type", "created_at"}, or None if this app has
        no such installation. For installations whose `installation.created` webhook was never recorded (e.g. made before it was)."""
        r = self.http.get(f"{API}/app/installations/{installation_id}", headers={
            "Authorization": f"Bearer {self._app_jwt()}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        a = r.json()["account"]
        return {"login": a["login"], "id": int(a["id"]), "type": a.get("type", ""), "created_at": r.json().get("created_at")}

    def user_admin_of(self, user_token: str, account: dict) -> bool:
        """Whether the user behind `user_token` administers `account`: is that user, or an active admin of that organisation.
        The organisation check needs the app's read-only "Members" permission; without it GitHub answers 403, i.e. no."""
        headers = {"Authorization": f"Bearer {user_token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if account["type"] == "User":
            r = self.http.get(f"{API}/user", headers=headers)
            r.raise_for_status()
            return int(r.json()["id"]) == account["id"]
        if account["type"] == "Organization":
            r = self.http.get(f"{API}/user/memberships/orgs/{account['login']}", headers=headers)
            if r.status_code in {403, 404}:
                return False
            r.raise_for_status()
            return r.json().get("role") == "admin" and r.json().get("state") == "active"
        return False

    def good_first_issues(self, full_name, installation_id):
        issues = self._get(installation_id, f"/repos/{full_name}/issues", state="open", labels="good first issue", per_page=20).json()
        return [{"number": i["number"], "title": i["title"], "labels": [l["name"] for l in i.get("labels", [])]}
                for i in issues if "pull_request" not in i]


def events_from_webhook(kind: str, payload: dict, project_id: str, repository_id: str) -> list[dict]:
    """Translate GitHub webhooks into Supervisor events."""
    inst = str((payload.get("installation") or {}).get("id") or "") or None
    full_name = (payload.get("repository") or {}).get("full_name", "")
    base = {"project_id": project_id, "repository_id": repository_id, "full_name": full_name, "installation_id": inst}
    if kind == "push":
        changed, removed = set(), set()
        for c in payload.get("commits") or []:
            changed |= set(c.get("added", [])) | set(c.get("modified", []))
            removed |= set(c.get("removed", []))
        branch = (payload.get("ref") or "").removeprefix("refs/heads/")
        default = (payload.get("repository") or {}).get("default_branch", "main")
        if branch != default or not payload.get("after"):
            return []
        # Re-read every touched path at the branch head; ingestion drops the ones that no longer exist. (A file removed
        # and re-added within one push appears in both sets, so subtracting would lose it.)
        return [{**base, "type": "repo.pushed", "after": payload["after"], "branch": branch, "default_branch": default,
                 "changed_paths": sorted(changed | removed), "removed_paths": [], "dedupe": f"push:{payload['after']}"}]
    if kind == "pull_request":
        pr, action = payload.get("pull_request"), payload.get("action")
        head = (pr or {}).get("head") or {}
        if not isinstance(pr, dict) or not isinstance(pr.get("number"), int) or not pr.get("html_url") or not head.get("sha"):
            return []  # not a pull request event we can act on (a malformed or partial payload): ignored, never a 500
        # updated_at orders the PR's events by when GitHub sent them: reviews of two quick pushes can finish either way round
        common = {**base, "pr_number": pr["number"], "pr_url": pr["html_url"], "head_sha": head["sha"],
                  "branch": head.get("ref") or "", "body": pr.get("body") or "", "author": (pr.get("user") or {}).get("login") or "",
                  "pr_updated_at": pr.get("updated_at")}
        if action in {"opened", "reopened", "ready_for_review"}:
            return [{**common, "type": "pr.opened", "dedupe": f"pr:{pr['number']}:{head['sha']}"}]
        if action == "synchronize":
            return [{**common, "type": "pr.updated", "dedupe": f"pr:{pr['number']}:{head['sha']}"}]
        if action == "closed" and pr.get("merged"):
            return [{**common, "type": "pr.merged", "dedupe": f"merged:{pr['number']}"}]
    return []
