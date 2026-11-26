"""GitHub repositories connected by URL, for self-hosted mode: no GitHub App, so no installation and no webhooks.

Public repositories need nothing. GITHUB_TOKEN (a read-only fine-grained token) adds private ones and raises GitHub's API
limit for this server from 60 to 5,000 calls an hour. The client has the GitHub App client's RepoSource methods, so the
agents do not know the difference; `RepoRouter` sends each call to the App client for a repository installed through it
and to this one otherwise. Like the App client, nothing here writes to GitHub.

The repository watcher (app/selfhost.py) polls through `branch_head`, `pulls` and `compare`. Those send the ETag of the
last answer: while nothing changed GitHub answers 304, which does not count against the limit.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from app.adapters.github import API, read_tarball

FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_URL = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/?#].*)?$")


def parse_repo(value: str) -> str | None:
    """'owner/repo' from a GitHub URL (https://github.com/owner/repo, .git, /tree/main ...) or from 'owner/repo' itself."""
    v = (value or "").strip()
    m = _URL.match(v)
    full = f"{m.group(1)}/{m.group(2)}" if m else v
    return full if FULL_NAME.match(full) and ".." not in full else None


class GitHubLimited(RuntimeError):
    """GitHub's API limit for this server is used up. Says until when, and how to raise it."""

    def __init__(self, reset_epoch: int | None, has_token: bool):
        at = datetime.fromtimestamp(reset_epoch, timezone.utc).strftime("%H:%M UTC") if reset_epoch else "later"
        hint = "" if has_token else " Add a read-only GITHUB_TOKEN to raise the limit from 60 to 5,000 calls an hour."
        super().__init__(f"GitHub's API limit for this server is used up until {at}.{hint}")
        self.reset_epoch = reset_epoch


class GitHubByUrl:
    def __init__(self, token: str = "", client: httpx.Client | None = None, etag_cache: int = 512):
        self.token = token.strip()
        self.http = client or httpx.Client(timeout=30, follow_redirects=True)
        self._etags: OrderedDict[str, tuple[str, object]] = OrderedDict()  # url -> (etag, body of that answer)
        self._etag_cap = etag_cache

    # ---- plumbing ----
    def _headers(self, accept: str = "application/vnd.github+json", etag: str | None = None) -> dict:
        h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "welcome-wave"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if etag:
            h["If-None-Match"] = etag
        return h

    def _check(self, r: httpx.Response) -> None:
        if r.status_code in {403, 429} and r.headers.get("x-ratelimit-remaining") == "0":
            reset = r.headers.get("x-ratelimit-reset")
            raise GitHubLimited(int(reset) if reset and reset.isdigit() else None, bool(self.token))
        r.raise_for_status()

    def _get(self, path: str, accept: str = "application/vnd.github+json", **params) -> httpx.Response:
        r = self.http.get(f"{API}{path}", params=params, headers=self._headers(accept))
        self._check(r)
        return r

    def _cached(self, path: str, **params):
        """GET JSON with the ETag of the last answer to the same request; a 304 returns that answer again."""
        key = f"{path}?{urlencode(sorted(params.items()))}"
        etag, body = self._etags.get(key, (None, None))
        r = self.http.get(f"{API}{path}", params=params, headers=self._headers(etag=etag))
        if r.status_code == 304 and etag:
            self._etags.move_to_end(key)
            return body
        self._check(r)
        body = r.json()
        if r.headers.get("etag"):
            self._etags[key] = (r.headers["etag"], body)
            self._etags.move_to_end(key)
            while len(self._etags) > self._etag_cap:
                self._etags.popitem(last=False)
        return body

    def _paged(self, path: str, **params) -> list:
        out, page = [], 1
        while True:
            batch = self._get(path, per_page=100, page=page, **params).json()
            out += batch
            if len(batch) < 100:
                return out
            page += 1

    # ---- RepoSource (installation_id is always None here) ----
    def files(self, full_name, ref, installation_id=None):
        return read_tarball(self.http, f"{API}/repos/{full_name}/tarball/{ref}", self._headers(), full_name)

    def file(self, full_name, path, ref, installation_id=None):
        try:
            return self._get(f"/repos/{full_name}/contents/{path}", accept="application/vnd.github.raw", ref=ref).text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def head_sha(self, full_name, ref, installation_id=None):
        try:
            return self._get(f"/repos/{full_name}/commits/{ref}", accept="application/vnd.github.sha").text.strip()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {404, 422}:
                return None
            raise

    def pr_files(self, full_name, number, installation_id=None):
        return self._paged(f"/repos/{full_name}/pulls/{number}/files")

    def check_runs(self, full_name, sha, installation_id=None):
        try:
            return self._get(f"/repos/{full_name}/commits/{sha}/check-runs").json().get("check_runs", [])
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {401, 403, 404}:  # check runs need a token on some repositories: none is not a failure
                return []
            raise

    def repo_info(self, full_name, installation_id=None):
        """None unless this server can read the repository (public, or private and readable with GITHUB_TOKEN)."""
        try:
            r = self._get(f"/repos/{full_name}").json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {401, 403, 404}:
                return None
            raise
        return {"full_name": r["full_name"], "default_branch": r.get("default_branch") or "main", "private": r.get("private", False)}

    def good_first_issues(self, full_name, installation_id=None):
        issues = self._get(f"/repos/{full_name}/issues", state="open", labels="good first issue", per_page=20).json()
        return [{"number": i["number"], "title": i["title"], "labels": [lbl["name"] for lbl in i.get("labels", [])]}
                for i in issues if "pull_request" not in i]

    def installation_repositories(self, installation_id):
        return []

    # ---- the repository watcher ----
    def branch_head(self, full_name: str, branch: str) -> str | None:
        """The commit the branch points to now, or None if the branch is gone."""
        try:
            return (self._cached(f"/repos/{full_name}/branches/{branch}").get("commit") or {}).get("sha")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def pulls(self, full_name: str, state: str) -> list[dict]:
        """The most recently updated pull requests in `state` (open or closed), newest first."""
        return self._cached(f"/repos/{full_name}/pulls", state=state, sort="updated", direction="desc", per_page=30)

    def compare(self, full_name: str, base: str, head: str) -> list[str] | None:
        """Paths touched between two commits (added, changed, removed or renamed from), or None when GitHub cannot say
        (base unknown after a force-push, or more than the 300 files a comparison lists): index the whole repository then."""
        try:
            body = self._get(f"/repos/{full_name}/compare/{base}...{head}").json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {404, 422}:
                return None
            raise
        files = body.get("files") or []
        if body.get("status") not in {"ahead", "identical"} or len(files) >= 300:
            return None
        paths = set()
        for f in files:
            paths.add(f["filename"])
            if f.get("previous_filename"):
                paths.add(f["previous_filename"])
        return sorted(paths)


class RepoRouter:
    """The GitHub App client for repositories installed through it, the URL client for every other."""

    def __init__(self, by_url: GitHubByUrl, app=None):
        self.by_url, self.app = by_url, app

    def _for(self, installation_id):
        return self.app if (installation_id and self.app) else self.by_url

    def files(self, full_name, ref, installation_id=None):
        return self._for(installation_id).files(full_name, ref, installation_id)

    def file(self, full_name, path, ref, installation_id=None):
        return self._for(installation_id).file(full_name, path, ref, installation_id)

    def head_sha(self, full_name, ref, installation_id=None):
        return self._for(installation_id).head_sha(full_name, ref, installation_id)

    def pr_files(self, full_name, number, installation_id=None):
        return self._for(installation_id).pr_files(full_name, number, installation_id)

    def check_runs(self, full_name, sha, installation_id=None):
        return self._for(installation_id).check_runs(full_name, sha, installation_id)

    def repo_info(self, full_name, installation_id=None):
        return self._for(installation_id).repo_info(full_name, installation_id)

    def good_first_issues(self, full_name, installation_id=None):
        return self._for(installation_id).good_first_issues(full_name, installation_id)

    def installation_repositories(self, installation_id):
        return self.app.installation_repositories(installation_id) if self.app else []

    def __getattr__(self, name):  # App-only calls (installation_account, user_admin_of)
        if self.app is None:
            raise AttributeError(name)
        return getattr(self.app, name)


def wait_hint(e: Exception) -> float:
    """Seconds until GitHub's limit resets, for a caller that should back off; a minute when unknown."""
    if isinstance(e, GitHubLimited) and e.reset_epoch:
        return max(60.0, e.reset_epoch - time.time() + 5)
    return 60.0
