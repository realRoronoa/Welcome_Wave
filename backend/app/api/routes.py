"""HTTP API used by the React app. Thin: validation, permissions, then hand off to the Supervisor or the store."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.agents.schemas import Chunk
from app.api.auth import Identity, check_project, container, current_user, identity, require
from app.api.outbox import publish
from app.container import ApprovalBusy, ApprovalNotReady, ApprovalStale
from app.lifecycle.context import Ctx
from app.ports import AI_OFF, User
from app.services import kt_review, scheduling
from app.services.coverage import coverage_map, gaps
from app.services.dashboard import build_dashboard

router = APIRouter()


def _need_ai(c) -> None:
    """Self-hosted without a Gemini key: refuse work that needs the model, plainly, instead of failing it in the background."""
    if not c.settings.ai_ready:
        raise HTTPException(503, AI_OFF)


# ---------------- public ----------------
@router.get("/config")
def public_config(c=Depends(container)):
    """What the web app needs before sign-in. Nothing here is secret."""
    s = c.settings
    return {
        "mode": s.mode,
        "env": s.env,
        "firebase": {"apiKey": s.firebase_api_key or "emulator", "authDomain": s.firebase_auth_domain or f"{s.firebase_project}.firebaseapp.com",
                     "projectId": s.firebase_project,
                     # development only: sign in against the Firebase Auth emulator (refused at startup when ENV=prod)
                     "emulator": f"http://{s.firebase_auth_emulator_host}" if s.firebase_auth_emulator_host and s.env != "prod" else None,
                     } if not s.is_demo else None,
        "ai": s.ai_ready,  # false: self-hosted without a Gemini key; the app says the AI is off where it is needed
        "repo_by_url": s.is_self_hosted,  # repositories are connected by their GitHub URL (no GitHub App needed)
        "recording_upload": not s.is_demo,  # a KT session's recording can be uploaded
        "signup_enabled": s.signup_enabled,
        "github_app": bool(s.github_app_slug),
        # scheduling (docs/specs/scheduling.md): Google Calendar can be connected (each organisation connects its own
        # account; GET /api/integrations says whether it has), and Calendly bookings arrive by webhook
        "google_calendar": s.is_demo or s.google_calendar_ready,
        "calendly": bool(s.calendly_webhook_signing_key),
        # email can really be sent (SMTP is set up): the web app says "Emailed" only when this is true, and shows the link otherwise
        "email": bool(c.deps.mailer is not None and c.deps.mailer.enabled),
        # demo only: the shared password the demo sign-in page shows and fills in for its one-click accounts
        "demo_password": s.demo_password if s.is_demo else None,
    }


# ---------------- accounts ----------------
class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    org_name: str = Field(min_length=1, max_length=120)
    project_name: str = Field(min_length=1, max_length=120)


@router.post("/signup", status_code=201)
def signup(body: SignupIn, who: Identity = Depends(identity), c=Depends(container)):
    """A verified login with no account creates an organisation and its first project, and becomes its admin."""
    if not c.settings.signup_enabled:
        raise HTTPException(403, detail={"code": "signup_closed", "message": "Sign-up is closed. Ask your manager for an invitation."})
    if c.settings.require_verified_email and not who.email_verified:
        raise HTTPException(403, detail={"code": "email_not_verified", "message": "Verify your email address first."})
    if c.store.user_by_firebase(who.uid):
        raise HTTPException(409, "This login already has an account")
    user = c.store.create_organization(who.uid, who.email, body.name.strip(), body.org_name.strip(), body.project_name.strip())
    return _me(c, user)


class AcceptIn(BaseModel):
    org_id: str


@router.post("/invitations/accept")
def accept_invitation(body: AcceptIn, who: Identity = Depends(identity), c=Depends(container)):
    """The signed-in person chose to join this organisation (GET /me listed it under `invitation_pending`)."""
    if c.settings.require_verified_email and not who.email_verified:
        raise HTTPException(403, detail={"code": "email_not_verified", "message": "Verify your email address first."})
    if c.store.user_by_firebase(who.uid):
        raise HTTPException(409, "This login already has an account")
    try:
        user = c.store.accept_invitations(who.uid, who.email, who.name, body.org_id)
    except (IntegrityError, ValueError):  # a concurrent accept (a double click) bound this login first
        raise HTTPException(409, "This login already has an account") from None
    if not user:
        raise HTTPException(404, "That invitation has expired or was revoked. Ask for a new one.")
    return _me(c, user)


@router.get("/me")
def me(user: User = Depends(current_user), c=Depends(container)):
    return _me(c, user)


@router.post("/me/leave", status_code=204)
def leave_organization(user: User = Depends(current_user), c=Depends(container)):
    """Undo a join (a wrong or lookalike organisation): the login is unbound, so it can accept another invitation."""
    if not c.store.leave_organization(user.id):
        raise HTTPException(409, "last_admin")
    c.store.audit(user.org_id, user.id, "member.left", user.id)


def _me(c, user: User) -> dict:
    from app.api.auth import _set_tenant

    _set_tenant(user.org_id)
    return {**user.model_dump(), "org_name": c.store.org_name(user.org_id),
            "projects": [p.model_dump() for p in c.store.projects_for(user)]}


# ---------------- projects, people, invitations ----------------
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=2000)


@router.get("/projects")
def projects(user: User = Depends(current_user), c=Depends(container)):
    return c.store.projects_for(user)


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, user: User = Depends(require("manager")), c=Depends(container)):
    p = c.store.create_project(user.org_id, body.name.strip(), body.description.strip(), user.id)
    c.store.audit(user.org_id, user.id, "project.created", p.id)
    return p


@router.get("/projects/{project_id}")
def project(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    p = check_project(c, user, project_id)
    out = {"project": p, "repositories": c.store.repos_for(project_id), "members": c.store.members(project_id),
           "onboardings": c.store.onboardings(project_id), "documents": c.store.documents(project_id)}
    if user.role in {"manager", "admin"}:
        out["invitations"] = c.store.invitations(project_id)
    return out


class InviteIn(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(manager|new_hire|expert|admin|executive)$")
    name: str | None = Field(None, max_length=120)
    github_login: str | None = Field(None, max_length=39, pattern=r"^[A-Za-z0-9-]*$")


@router.post("/projects/{project_id}/invitations", status_code=201)
def invite(project_id: str, body: InviteIn, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, project_id)
    if body.role in {"admin", "executive"} and user.role != "admin":
        raise HTTPException(403, f"Only an admin can invite an{'other admin' if body.role == 'admin' else ' executive'}")
    existing = c.store.user_by_email(user.org_id, str(body.email))
    if existing:  # already in this organisation: add them to the project; their organisation-wide role is unchanged
        c.store.add_member(project_id, existing.id)
        c.store.audit(user.org_id, user.id, "member.added", project_id, {"user_id": existing.id})
        return {"added": existing, "role_unchanged": existing.role != body.role}
    inv = c.store.create_invitation(user.org_id, project_id, str(body.email), body.role, body.name, body.github_login or None, user.id)
    c.store.audit(user.org_id, user.id, "invitation.created", project_id, {"email": str(body.email), "role": body.role})
    # the sign-in page itself (the site's front page is the public home page)
    return {"invited": inv, "sign_in_url": f"{c.settings.public_url.rstrip('/')}/login" if c.settings.public_url else None}


@router.delete("/projects/{project_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(project_id: str, invitation_id: str, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, project_id)
    if not c.store.revoke_invitation(invitation_id, project_id):
        raise HTTPException(404, "Invitation not found")
    c.store.audit(user.org_id, user.id, "invitation.revoked", invitation_id)


# ---------------- GitHub App: verified installation, then connect a repository ----------------
def _state(c, project_id: str, user_id: str, expires: int) -> str:
    key = ("state:" + c.settings.github_webhook_secret).encode()
    return hmac.new(key, f"{project_id}.{user_id}.{expires}".encode(), hashlib.sha256).hexdigest()[:32]


@router.get("/github/install-url")
def github_install_url(project_id: str, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, project_id)
    if not c.settings.github_app_slug:
        return {"url": None}
    expires = int(time.time()) + 3600
    state = f"{project_id}.{expires}.{_state(c, project_id, user.id, expires)}"
    return {"url": f"https://github.com/apps/{c.settings.github_app_slug}/installations/new?state={state}"}


class GitHubCallback(BaseModel):
    code: str
    installation_id: str = Field(pattern=r"^\d+$")
    state: str


@router.post("/github/callback")
def github_callback(body: GitHubCallback, user: User = Depends(require("manager")), c=Depends(container)):
    """GitHub redirects here after installation. An installation is bound to this organisation only if the person in the
    browser installed it (GitHub's signed `installation.created` webhook records who) or, when that webhook was never
    recorded (an older installation, a lost delivery), administers the GitHub account it is on, as GitHub itself says.
    Seeing a repository of the installation is not enough: a collaborator could otherwise connect an org's private code.

    GitHub's code works once, so everything that can still change is checked before it is spent: on 409
    `installation_unconfirmed` the browser retries with the same code. Every refusal is the same 403 `not_installer`,
    which never says who installed the app or which organisation has it."""
    from app.adapters.github import oauth_user

    try:
        project_id, expires, sig = body.state.split(".")
    except ValueError:
        raise HTTPException(400, "Bad state") from None
    if int(expires) < time.time() or not hmac.compare_digest(sig, _state(c, project_id, user.id, int(expires))):
        raise HTTPException(400, "This installation link has expired; start again from Settings.")
    check_project(c, user, project_id)
    if not _callback_limit.allow(user.id, 20):
        raise HTTPException(429, "Too many attempts; wait a minute, then start again from Settings.", headers={"Retry-After": "60"})
    bound = c.store.installation_org(body.installation_id)
    if bound and bound != user.org_id:
        raise HTTPException(403, "not_installer")
    if not bound:
        sender = c.store.installation_sender(body.installation_id)
        lookup = getattr(c.deps.repos, "installation_account", None)
        account = lookup(body.installation_id) if not sender and lookup else None
        # Unknown to both, or installed minutes ago: its installation.created webhook is normally seconds away, so wait for it
        # (the browser retries) rather than judge the installer by account admin rights, which a repository admin lacks.
        if not sender and (not account or _age_s(account.get("created_at")) < 300):
            raise HTTPException(409, "installation_unconfirmed")
        gh = oauth_user(c.settings.github_client_id, c.settings.github_client_secret, body.code)
        if not gh:
            raise HTTPException(400, "GitHub did not accept the sign-in code; start again from Settings.")
        if sender:
            allowed, login = int(sender["sender_id"]) == gh["id"], sender["account_login"]
        else:
            allowed, login = c.deps.repos.user_admin_of(gh["token"], account), account["login"]
        if not allowed or not c.store.bind_installation(body.installation_id, user.org_id, login, user.id):
            raise HTTPException(403, "not_installer")
        c.store.audit(user.org_id, user.id, "github.installation_bound", body.installation_id,
                      {"account": login, "github_user": gh["login"], "via": "installer" if sender else "account_admin"})
    shown = next((i["account_login"] for i in c.store.org_installations() if str(i["installation_id"]) == body.installation_id), "")
    return {"project_id": project_id, "installation_id": body.installation_id, "account": shown,
            "repositories": c.deps.repos.installation_repositories(body.installation_id)}


@router.get("/github/installations")
def github_installations(user: User = Depends(require("manager")), c=Depends(container)):
    """Installations already bound to this organisation, so a new project can connect a repository without reinstalling."""
    return c.store.org_installations()


@router.get("/github/repositories")
def github_repositories(installation_id: str, user: User = Depends(require("manager")), c=Depends(container)):
    if c.settings.is_demo:
        return [{"full_name": "demo/mini-policy-engine", "default_branch": "main", "private": False}]
    if c.store.installation_org(installation_id) != user.org_id:
        raise HTTPException(404, "Installation not found")
    return c.deps.repos.installation_repositories(installation_id)


class RepoIn(BaseModel):
    full_name: str = Field(max_length=300)  # owner/repo, or (self-hosted) the repository's GitHub URL
    installation_id: str | None = Field(None, pattern=r"^\d+$")
    default_branch: str | None = None


@router.post("/projects/{project_id}/repositories", status_code=202)
def connect_repo(project_id: str, body: RepoIn, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, project_id)
    from app.adapters.github_url import parse_repo

    full_name = parse_repo(body.full_name)
    if not full_name:
        raise HTTPException(422, "Give the repository as owner/name or its GitHub URL, e.g. https://github.com/owner/name")
    body = body.model_copy(update={"full_name": full_name})
    branch = body.default_branch or "main"
    if c.settings.is_self_hosted and not body.installation_id:  # connected by URL: this server reads it directly
        _need_ai(c)
        try:
            info = c.deps.repos.repo_info(body.full_name, None)
        except Exception as e:  # GitHub unreachable or its limit used up: say so, do not pretend the repository is missing
            raise HTTPException(502, f"Could not reach GitHub: {e}") from None
        if not info:
            hint = "" if c.settings.github_token else " For a private repository, add a read-only GITHUB_TOKEN to the server."
            raise HTTPException(404, f"GitHub has no public repository {body.full_name}.{hint}")
        body = body.model_copy(update={"full_name": info["full_name"]})
        branch = body.default_branch or info["default_branch"]
    elif not c.settings.is_demo:
        if not body.installation_id or c.store.installation_org(body.installation_id) != user.org_id:
            raise HTTPException(403, "Install the GitHub App for this organisation first")
        info = c.deps.repos.repo_info(body.full_name, body.installation_id)
        if not info:
            raise HTTPException(404, "The GitHub App installation cannot read that repository")
        branch = body.default_branch or info["default_branch"]
    other = [r for r in c.store.repos_for(project_id) if r.full_name.lower() != body.full_name.lower()]
    if other:  # the index, the code view and the coverage map are per project: one repository each
        raise HTTPException(409, f"This project is connected to {other[0].full_name}. Create another project for a second repository.")
    repo = c.store.create_repo(project_id, body.full_name, body.installation_id, branch)
    if repo is None:  # a concurrent request connected a different repository first
        raise HTTPException(409, "This project already has a repository. Create another project for a second repository.")
    c.store.audit(user.org_id, user.id, "repo.connected", repo.id, {"full_name": body.full_name})
    publish(c, "ingestion", {"type": "repo.connected", "project_id": project_id, "repository_id": repo.id,
                             "ref": branch, "dedupe": f"connect:{repo.id}:{uuid.uuid4().hex}"})  # every (re)connect indexes
    return repo


# ---------------- documents ----------------
ALLOWED_DOCS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain",
}


@router.post("/projects/{project_id}/documents", status_code=202)
async def upload_document(project_id: str, request: Request, file: UploadFile = File(...), title: str = Form(""),
                          user: User = Depends(require("manager", "expert")), c=Depends(container)):
    await run_in_threadpool(check_project, c, user, project_id)
    _need_ai(c)
    limit = c.settings.max_upload_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit + 64 * 1024:
        raise HTTPException(413, f"Max {c.settings.max_upload_mb} MB")
    name = (file.filename or "document").rsplit("/", 1)[-1]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in ALLOWED_DOCS:
        raise HTTPException(415, "Upload a PDF, Word (.docx), Markdown or text file")
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Max {c.settings.max_upload_mb} MB")
    mime = ALLOWED_DOCS[ext]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name)[:120]
    doc_title = (title or name)[:200]

    def store_it():  # blocking I/O runs in the threadpool, never on the event loop
        bucket = c.settings.gcs_bucket or "local"
        uri = c.deps.blobs.put(f"gs://{bucket}/docs/{project_id}/{uuid.uuid4().hex}/{safe}", data, mime)
        doc_id = c.store.create_document(project_id, doc_title, uri, mime, user.id)
        try:
            publish(c, "ingestion", {"type": "document.uploaded", "project_id": project_id, "document_id": doc_id, "uri": uri,
                                     "title": doc_title, "mime": mime, "dedupe": f"doc:{doc_id}"})
        except Exception:
            c.store.delete_document(doc_id)  # no event recorded: never leave a row that stays "pending" for good
            raise
        c.store.audit(user.org_id, user.id, "document.uploaded", doc_id, {"title": doc_title})
        return doc_id

    return {"document_id": await run_in_threadpool(store_it)}


def _document(c, user: User, document_id: str) -> dict:
    d = c.store.get_document(document_id) or _404()
    check_project(c, user, str(d["project_id"]))
    return d


@router.get("/documents/{document_id}")
def document_view(document_id: str, user: User = Depends(current_user), c=Depends(container)):
    """The text the chat cites, page by page: what a 'Runbook, page 2' source opens."""
    from app.agents.ingestion import extract_pages

    d = _document(c, user, document_id)
    pages: list[str] = []
    if d.get("status") == "ready":
        try:
            pages = extract_pages(c.deps.blobs.get(d["uri"]), d["mime"])
        except Exception:  # noqa: BLE001 - the file is gone or unreadable: show the document without its text
            pages = []
    edits = c.lifecycle.doc_edits(str(d["project_id"]), "doc", document_id) if c.lifecycle is not None else []
    return {"document": {k: v for k, v in d.items() if k != "uri"}, "pages": [{"page": i + 1, "text": t} for i, t in enumerate(pages)],
            "accepted_edits": [e.model_dump(mode="json") for e in edits]}  # approved doc_edit proposals (the Keeper)


@router.get("/documents/{document_id}/file")
def document_file(document_id: str, user: User = Depends(current_user), c=Depends(container)):
    d = _document(c, user, document_id)
    ext = next((e for e, m in ALLOWED_DOCS.items() if m == d["mime"]), "")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", d["title"])[:120] or "document"
    if ext and not name.lower().endswith(ext):
        name += ext
    return Response(c.deps.blobs.get(d["uri"]), media_type=d["mime"],
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"})


@router.delete("/projects/{project_id}/documents/{document_id}", status_code=204)
def delete_document(project_id: str, document_id: str, user: User = Depends(require("manager", "expert")), c=Depends(container)):
    """Remove a document and everything the chat could cite from it. Answers that cited it are marked out of date."""
    check_project(c, user, project_id)
    d = c.store.get_document(document_id)
    if not d or str(d["project_id"]) != project_id:
        raise HTTPException(404, "Document not found")
    gone = c.index.delete_document(project_id, document_id)
    c.store.mark_answers_stale(gone)
    c.store.delete_document(document_id)
    c.store.audit(user.org_id, user.id, "document.deleted", document_id, {"title": d["title"]})


# ---------------- onboardings ----------------
class OnboardingIn(BaseModel):
    new_hire_id: str
    start_date: date
    role_title: str = Field("Software Engineer", max_length=120)


@router.post("/projects/{project_id}/onboardings", status_code=202)
def start_onboarding(project_id: str, body: OnboardingIn, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, project_id)
    _need_ai(c)
    hire = c.store.get_user(body.new_hire_id)
    if not hire or hire.org_id != user.org_id or not c.store.is_member(project_id, hire.id):
        raise HTTPException(422, "The new hire must be a member of this project")
    existing = next((o for o in c.store.onboardings(project_id) if o.new_hire_id == hire.id), None)
    if existing:  # "ask for the plan again": allowed only while there is no plan to keep
        key = f"ob:{existing.id}"
        if any(t.status not in {"proposed", "rejected"} for t in c.store.tasks(onboarding_id=existing.id)):
            raise HTTPException(409, f"{hire.name} already has an active onboarding in this project")
        if any(p.get("onboarding_id") == existing.id for a in c.store.approvals(project_id, "pending") if a.kind == "task_plan"
               for p in a.payload.get("plans", [])):
            raise HTTPException(409, f"A plan for {hire.name} is waiting for your approval")
        # A finished run (the model failed, or proposed nothing) would make the same key a duplicate: forget it first.
        status = c.store.event_status(key)
        if status == "busy" or (status == "done" and not c.store.reset_event(key)):
            raise HTTPException(409, "plan_in_progress")
        ob = existing
    else:
        ob = c.store.create_onboarding(project_id, body.new_hire_id, user.id, body.start_date, body.role_title)
    c.store.audit(user.org_id, user.id, "onboarding.started", ob.id, {"new_hire_id": hire.id})
    publish(c, "reviews", {"type": "onboarding.started", "project_id": project_id, "onboarding_id": ob.id, "dedupe": f"ob:{ob.id}"})
    return ob


# ---------------- chat ----------------
class _RateLimiter:
    """Per-user sliding window. Per instance, so the real ceiling is limit x instances - enough to stop a runaway
    client from burning the model budget; Cloud Armor or an API gateway is the next step if that is not enough."""

    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


def _age_s(iso: str | None) -> float:
    """Seconds since a GitHub timestamp; unknown counts as old."""
    try:
        return time.time() - datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return float("inf")


_chat_limit = _RateLimiter()
_callback_limit = _RateLimiter()  # GitHub callbacks: each one can cost a GitHub API call on the app's shared quota


class Question(BaseModel):
    project_id: str
    question: str = Field(max_length=2000)


@router.post("/chat")
def ask(body: Question, user: User = Depends(current_user), c=Depends(container)):
    check_project(c, user, body.project_id)
    if not body.question.strip():
        raise HTTPException(422, "Empty question")
    _need_ai(c)
    if not _chat_limit.allow(user.id, c.settings.chat_rate_per_minute):
        raise HTTPException(429, "Too many questions in a minute; try again shortly.", headers={"Retry-After": "30"})
    out = c.handle({"type": "chat.question", "project_id": body.project_id, "user_id": user.id, "question": body.question.strip()})
    if out["result"].get("escalated"):
        raise HTTPException(503, "The assistant is unavailable right now; your manager has been notified.")
    return out["result"]


@router.get("/chat/history")
def chat_history(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    check_project(c, user, project_id)
    return c.store.chat_history(project_id, user.id)


class Feedback(BaseModel):
    value: int = Field(ge=-1, le=1)


@router.post("/chat/{message_id}/feedback", status_code=204)
def chat_feedback(message_id: str, body: Feedback, user: User = Depends(current_user), c=Depends(container)):
    if not c.store.chat_feedback(message_id, user.id, body.value):
        raise HTTPException(404, "Message not found")


# ---------------- meetings: KT sessions (docs/specs/scheduling.md) ----------------
class MeetingIn(BaseModel):
    project_id: str
    title: str = Field(min_length=1, max_length=200)
    # When: none makes an unscheduled session, held whenever it suits. Naive times are read as UTC.
    starts_at: datetime | None = None
    duration_min: int = Field(30, ge=5, le=240)
    attendees: list[str] = Field(default_factory=list, max_length=50)  # project members to invite (the booker always is)
    # Where: Google Calendar with a Meet link, or anywhere ('manual', with an optional link). None: Google when connected.
    provider: Literal["google", "manual"] | None = None
    join_url: str | None = Field(None, max_length=500)


def _session_request(body, title: str) -> scheduling.SessionRequest:
    link = (body.join_url or "").strip() or None
    if link and not re.match(r"^https://[^\s]+$", link):
        raise HTTPException(422, "The meeting link must start with https://")
    return scheduling.SessionRequest(title=title, starts_at=body.starts_at, duration_min=body.duration_min, attendee_ids=body.attendees,
                                     provider=body.provider, join_url=link)


def _book(c, project, user: User, req, meeting_id: str | None = None):
    try:
        if meeting_id:
            return scheduling.reschedule(c, project.org_id, user, meeting_id, req)
        return scheduling.book(c, project.id, project.org_id, user, req)
    except scheduling.SchedulingUnavailable:
        raise HTTPException(409, "Connect Google Calendar in Settings, Integrations first.") from None
    except scheduling.CalendarFailed as e:
        raise HTTPException(502, f"Google Calendar did not book the session: {e}"[:300]) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


def _tell_attendees(c, m, user: User, verb: str) -> None:
    """In-app notice for everyone invited (Google also sends its own invitation)."""
    for a in m.attendees:
        if a.user_id and a.user_id != user.id:
            c.store.notify(a.user_id, "meeting", f"{user.name} {verb} the KT session '{m.title}'.", f"/meetings?call={m.id}&project={m.project_id}")


@router.post("/meetings")
def create_meeting(body: MeetingIn, user: User = Depends(current_user), c=Depends(container)):
    """Schedule a KT session: on the organisation's Google Calendar with a Meet link (Google sends the invitations), or held
    anywhere ('manual': an optional link, its recording uploaded afterwards). Booking with Calendly happens on Calendly."""
    project = check_project(c, user, body.project_id)
    m = _book(c, project, user, _session_request(body, body.title.strip()))
    c.store.audit(user.org_id, user.id, "meeting.created", m.id, {"provider": m.provider})
    _tell_attendees(c, m, user, "invited you to")
    return {"meeting": m}


class ScheduleIn(BaseModel):
    starts_at: datetime
    duration_min: int = Field(30, ge=5, le=240)
    attendees: list[str] = Field(default_factory=list, max_length=50)
    provider: Literal["google", "manual"] | None = None
    join_url: str | None = Field(None, max_length=500)


@router.post("/meetings/{meeting_id}/schedule")
def schedule_meeting(meeting_id: str, body: ScheduleIn, user: User = Depends(current_user), c=Depends(container)):
    """Give an unscheduled session (booked from the KT planner or a call review without a time) its time and place."""
    m = c.store.get_meeting(meeting_id) or _404()
    project = check_project(c, user, m.project_id)
    if user.role not in {"manager", "admin"} and m.created_by != user.id and not any(a.user_id == user.id for a in m.attendees):
        raise HTTPException(403, "Only a manager, whoever booked this session or someone invited to it can schedule it")
    if m.status != "scheduled" or m.calendar_event_id:
        raise HTTPException(409, "This session is already on a calendar" if m.calendar_event_id else "This session has already happened")
    m = _book(c, project, user, _session_request(body, m.title), meeting_id=m.id)
    c.store.audit(user.org_id, user.id, "meeting.scheduled", m.id, {"provider": m.provider})
    _tell_attendees(c, m, user, "scheduled")
    return {"meeting": m}


@router.post("/meetings/{meeting_id}/cancel")
def cancel_meeting(meeting_id: str, user: User = Depends(current_user), c=Depends(container)):
    """A manager, or whoever booked the session, cancels one that has not happened, so a session nobody holds does not keep
    its slot (or the KT planner session it was booked for) for good. A held or recorded session is a 409. The calendar
    event is cancelled too (Google tells the attendees); `warning` says what is left to do by hand (a Calendly booking).
    Whoever was sent the booking hears that it is off, and the booking notice no longer asks them to join."""
    m = c.store.get_meeting(meeting_id) or _404()
    check_project(c, user, m.project_id)
    if user.role not in {"manager", "admin"} and m.created_by != user.id:
        raise HTTPException(403, "Only a manager or the person who booked this call can cancel it")
    if not c.store.cancel_meeting(m.id):
        raise HTTPException(409, "Only a call that has not started can be cancelled")
    warning = scheduling.cancel_event(c, user.org_id, m)
    c.store.audit(user.org_id, user.id, "meeting.cancelled", m.id, {"kt_key": m.kt_key, "provider": m.provider} if m.kt_key else {"provider": m.provider})
    booking = f"/meetings?call={m.id}"
    told = set(c.store.notification_recipients(booking, "kt_request")) | {a.user_id for a in m.attendees if a.user_id}
    for uid in sorted(told - {user.id}):
        c.store.notify(uid, "meeting", f"{user.name} cancelled the KT session '{m.title}'." + (" It can be booked again from the KT planner." if m.kt_key else ""),
                       f"/meetings?project={m.project_id}")
    c.store.close_notifications(booking)
    return {"meeting": c.store.get_meeting(m.id), "warning": warning}


def _may_add_recording(user: User, m) -> None:
    if user.role not in {"manager", "admin", "expert"} and m.created_by != user.id:
        raise HTTPException(403, "Only a manager, an expert or whoever booked this call can add its recording")


@router.post("/meetings/{meeting_id}/recording/import", status_code=202)
async def import_recording(meeting_id: str, user: User = Depends(current_user), c=Depends(container)):
    """Bring a Google Calendar session's recording in from Google Drive (Meet attaches it to the event once someone recorded
    the meeting). The recording then takes the upload's path: notes, KT links and the managers' transcript review. The
    poller does the same by itself after a session ends; this is the button for "now"."""
    m = await run_in_threadpool(c.store.get_meeting, meeting_id) or _404()
    await run_in_threadpool(check_project, c, user, m.project_id)
    _may_add_recording(user, m)
    _need_ai(c)
    from app.adapters.google_calendar import GoogleAuthError, GoogleError

    try:
        out = await run_in_threadpool(scheduling.import_recording, c, user.org_id, m, user.id)
    except scheduling.NoRecordingYet:
        raise HTTPException(409, detail={"code": "no_recording_yet", "message": "No recording on the calendar event yet. "
                                         "Meet saves it to Drive a few minutes after the call, if someone recorded it."}) from None
    except scheduling.AlreadyRecorded:
        raise HTTPException(409, "This call already has its recording") from None
    except scheduling.ImportNotPossible as e:
        raise HTTPException(409, str(e)) from None
    except GoogleAuthError:
        raise HTTPException(409, "Google Calendar access no longer works. Connect it again in Settings, Integrations.") from None
    except GoogleError as e:
        raise HTTPException(502, f"Google did not send the recording: {e}"[:300]) from None
    except ValueError:
        raise HTTPException(413, f"The recording is larger than the {c.settings.max_recording_mb} MB limit") from None
    return {"meeting": out}


RECORDING_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}


@router.post("/meetings/{meeting_id}/recording", status_code=202)
async def upload_recording(meeting_id: str, request: Request, file: UploadFile = File(...), duration_s: float = Form(...),
                           user: User = Depends(current_user), c=Depends(container)):
    """A KT session's recording, uploaded: a session held anywhere (Calendly, any link), or a Meet recording that is not on
    the calendar event. Its notes, KT links and the managers' transcript review follow exactly as for an imported one. The
    browser reads the video's length (`duration_s`); the analysis is cut into segments by it."""
    if c.settings.is_demo:
        raise HTTPException(404, "Not Found")
    m = await run_in_threadpool(c.store.get_meeting, meeting_id) or _404()
    await run_in_threadpool(check_project, c, user, m.project_id)
    _may_add_recording(user, m)
    _need_ai(c)
    if m.status in {"processing", "ready"}:
        raise HTTPException(409, "This call already has its recording")
    if m.status in {"live", "cancelled"}:
        raise HTTPException(409, "End the call first" if m.status == "live" else "This call was cancelled")
    if not 1 <= duration_s <= 4 * 3600:
        raise HTTPException(422, "The recording must be between a second and four hours long")
    name = (file.filename or "").rsplit("/", 1)[-1]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in RECORDING_TYPES:
        raise HTTPException(415, "Upload the recording as an MP4, WebM, MOV or MKV video")
    limit = c.settings.max_recording_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit + 64 * 1024 or (file.size or 0) > limit:
        raise HTTPException(413, f"Max {c.settings.max_recording_mb} MB")

    def store_it():
        bucket = c.settings.gcs_bucket or "local"
        uri = f"gs://{bucket}/recordings/{m.id}/{uuid.uuid4().hex}{ext}"
        file.file.seek(0)
        put_file = getattr(c.deps.blobs, "put_file", None)
        if put_file:
            put_file(uri, file.file, RECORDING_TYPES[ext])
        else:
            c.deps.blobs.put(uri, file.file.read(), RECORDING_TYPES[ext])
        if not c.store.claim_meeting_import(m.id, uri, round(duration_s, 3)):  # another upload (or an import) got there first
            raise HTTPException(409, "This call already has its recording")
        publish(c, "meetings", {"type": "meeting.recording_ready", "project_id": m.project_id, "meeting_id": m.id,
                                "recording_uri": uri, "duration_s": round(duration_s, 3), "dedupe": f"rec:{m.id}:{uuid.uuid4().hex[:8]}"})
        c.store.audit(user.org_id, user.id, "meeting.recording_uploaded", m.id, {"duration_s": round(duration_s)})
        return c.store.get_meeting(m.id)

    return {"meeting": await run_in_threadpool(store_it)}


@router.get("/meetings")
def meetings(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    """The project's KT sessions, newest start first."""
    check_project(c, user, project_id)
    return c.store.list_meetings(project_id)


@router.get("/meetings/{meeting_id}/notes")
def meeting_notes(meeting_id: str, user: User = Depends(current_user), c=Depends(container)):
    """The call's notes. The transcript is the managers' (they review it and book follow-ups from it): everyone else gets
    the notes without it, and `transcript` says which it is ('full', 'managers_only', or None before notes exist)."""
    m = c.store.get_meeting(meeting_id) or _404()
    check_project(c, user, m.project_id)
    notes = c.store.meeting_notes(meeting_id)
    full = kt_review.can_read_transcript(user)
    if notes is not None and not full:
        notes = notes.model_copy(update={"utterances": []})
    return {"meeting": m, "notes": notes, "transcript": None if notes is None else "full" if full else "managers_only"}


@router.get("/meetings/{meeting_id}/followups")
def meeting_followups(meeting_id: str, user: User = Depends(require("manager")), c=Depends(container)):
    """The KT sessions this call gives reasons for (its action items, code it named but never showed), as the KT planner
    plans them, with their booking state. Managers book them from the call's transcript or its review in Approvals."""
    m = c.store.get_meeting(meeting_id) or _404()
    project = check_project(c, user, m.project_id)
    return {"sessions": kt_review.followups(c.store, c.index, project.id, project.org_id, m.id)}


@router.get("/meetings/{meeting_id}/recording")
def recording_url(meeting_id: str, user: User = Depends(current_user), c=Depends(container)):
    m = c.store.get_meeting(meeting_id) or _404()
    check_project(c, user, m.project_id)
    signer = getattr(c.deps.blobs, "signed_url", None)
    return {"url": signer(m.recording_uri) if (signer and m.recording_uri) else None}


# ---------------- code, KT links, coverage (the headline feature) ----------------
@router.get("/code/files")
def code_files(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    check_project(c, user, project_id)
    explained = {}
    for l in c.store.kt_links(project_id):
        explained[l.file_path] = explained.get(l.file_path, 0) + 1
    return [{"path": p, "kt_clips": explained.get(p, 0)} for p in c.index.all_paths(project_id)]


@router.get("/code/explanations")
def code_explanations(project_id: str, path: str, user: User = Depends(current_user), c=Depends(container)):
    """'Who explained this file, when, and where in the recording' - powers the clip buttons in the code view."""
    check_project(c, user, project_id)
    suggested = c.lifecycle.doc_edits(project_id, "code", path) if c.lifecycle is not None else []
    return {"path": path, "symbols": [s.__dict__ for s in c.index.symbols_in(project_id, path)],
            "clips": c.store.kt_links(project_id, path=path),
            # approved doc_edit proposals for this file: suggestions its owner applies; Welcome Wave never writes to the repository
            "suggested_edits": [e.model_dump(mode="json") for e in suggested]}


@router.get("/code/file")
def code_file(project_id: str, path: str, user: User = Depends(current_user), c=Depends(container)):
    check_project(c, user, project_id)
    if path not in set(c.index.all_paths(project_id)):
        raise HTTPException(404, "File is not indexed")
    repo = (c.store.repos_for(project_id) or [None])[0]
    text = c.deps.repos.file(repo.full_name, path, repo.last_indexed_sha or repo.default_branch, repo.installation_id) if repo else None
    return {"path": path, "lines": (text or "").splitlines()}


@router.get("/coverage")
def coverage(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    check_project(c, user, project_id)
    rows = coverage_map(c.index.all_paths(project_id), c.store.kt_links(project_id))
    return {"modules": rows, "gaps": gaps(rows)}


# ---------------- tasks, approvals, reviews ----------------
UNAPPROVED = frozenset({"proposed", "rejected"})  # a plan's drafts: the managers' until approved (GET /approvals)


def _is_manager(user: User) -> bool:
    return user.role in {"manager", "admin"}


def _mentors(c, onboarding_id: str, user: User) -> bool:
    """The caller mentors this onboarding (its program names them; app/lifecycle)."""
    program = c.lifecycle.get_program(onboarding_id) if c.lifecycle is not None else None
    return bool(program and program.mentor_id == user.id)


def _mentored(c, project_id: str, user: User) -> set[str]:
    """The project's onboardings the caller mentors, in one read."""
    if c.lifecycle is None:
        return set()
    return {p.onboarding_id for p in c.lifecycle.programs(user.org_id, project_id) if p.mentor_id == user.id}


@router.get("/tasks")
def list_tasks(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    """Each task with its new hire's name (`assignee`), its newest pull request review (`review_id`), when it has one, and for
    managers whether a review of it waits in Approvals (`review_pending`). Managers see every task of the project, a plan
    awaiting approval included; anyone else sees only approved tasks, and only of the active onboardings they are related
    to: a hire their own, a mentor their mentees' (docs/PERMISSIONS.md: who sees a hire's data)."""
    check_project(c, user, project_id)
    obs = {o.id: o for o in c.store.onboardings(project_id, active_only=False)}
    if _is_manager(user):
        rows = c.store.tasks(project_id=project_id)
    else:
        mentored = _mentored(c, project_id, user)
        mine = [o.id for o in obs.values() if o.status == "active" and (o.new_hire_id == user.id or o.id in mentored)]
        rows = [t for oid in mine for t in c.store.tasks(onboarding_id=oid) if t.status not in UNAPPROVED]
    names = {u.id: u.name for u in c.store.members(project_id)}
    reviews = c.store.latest_reviews([t.id for t in rows if t.pr_number])
    waiting = {a.payload.get("task_id") for a in c.store.approvals(project_id, "pending") if a.kind == "review"} if _is_manager(user) else set()
    return [{**t.model_dump(mode="json"), "assignee": names.get(obs[t.onboarding_id].new_hire_id) if t.onboarding_id in obs else None,
             "review_id": reviews.get(t.id), "review_pending": t.id in waiting} for t in rows]


class TaskUpdate(BaseModel):
    status: str


# Who moves a task where. The new hire works through it; once it is submitted, a manager decides it is done or needs changes
# (a pull request review in Approvals does the same for code; a merged pull request marks it done).
HIRE_MOVES = {"approved": {"in_progress"}, "in_progress": {"submitted"}, "changes_requested": {"in_progress", "submitted"}}
MANAGER_MOVES = {"submitted": {"done", "changes_requested"}}


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate, user: User = Depends(current_user), c=Depends(container)):
    t = c.store.get_task(task_id) or _404()
    ob = c.store.get_onboarding(t.onboarding_id) or _404()
    manager = user.id != ob.new_hire_id and user.role in {"manager", "admin"}
    if user.id != ob.new_hire_id:
        if not manager:
            raise HTTPException(404, "Not found")
        check_project(c, user, ob.project_id)  # a manager of this project (any admin of its organisation)
    if body.status in MANAGER_MOVES.get(t.status, set()):
        if not manager:
            raise HTTPException(403, "Your manager decides when a submitted task is done")
        if any(a.status == "pending" for a in c.store.review_approvals(t.id)):
            raise HTTPException(409, "A review of this pull request is waiting in Approvals; decide it there.")
    elif body.status not in HIRE_MOVES.get(t.status, set()):
        raise HTTPException(409, f"Cannot move a task from {t.status} to {body.status}")
    c.store.update_task(task_id, status=body.status)
    link = f"/tasks?project={ob.project_id}"
    if user.id == ob.new_hire_id and body.status in {"in_progress", "submitted"} and ob.manager_id != user.id:
        text = (f"{user.name} submitted '{t.title}'. Mark it done or ask for changes in Team tasks." if body.status == "submitted"
                else f"{user.name} started '{t.title}'.")
        c.store.notify(ob.manager_id, "task", text, link)
    elif user.id != ob.new_hire_id and body.status in {"done", "changes_requested"}:
        text = f"{user.name} marked '{t.title}' as done." if body.status == "done" else f"{user.name} asked for changes to '{t.title}'."
        c.store.notify(ob.new_hire_id, "task", text, link)
    c.store.audit(user.org_id, user.id, "task.moved", t.id, {"from": t.status, "to": body.status})
    return c.store.get_task(task_id)


@router.get("/approvals")
def approvals(project_id: str, status: str | None = "pending", user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, project_id)
    return c.store.approvals(project_id, status)


class TaskEdit(BaseModel):
    title: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=4000)
    due_date: date | None = None


class Decision(BaseModel):
    approve: list[str] = []  # task_plan: ids of tasks to approve (others are rejected)
    edits: dict[str, TaskEdit] = {}  # task_plan: {task_id: {title, description, due_date}}
    decision: str | None = None  # review: approved | changes_requested; rerecord, doc_edit, template_change: approved | rejected
    text: str | None = Field(None, max_length=8000)  # doc_edit: the wording as the manager edited it (else the Keeper's draft)
    milestones: list[str] | None = None  # task_plan of an enrolment: the milestone keys to keep (default: all of the plan's)


PROPOSALS = frozenset({"rerecord", "doc_edit", "template_change"})  # the Keeper's and the Planner's (docs/AGENTS.md)


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, body: Decision, user: User = Depends(require("manager")), c=Depends(container)):
    a = c.store.get_approval(approval_id) or _404()
    check_project(c, user, a.project_id)
    if a.status == "rejected" and not a.decided_by:  # closed by the system: a newer review of the same task replaced it
        raise HTTPException(409, _STALE)
    if a.status != "pending":
        raise HTTPException(409, "Already decided")
    if a.kind == kt_review.KIND:
        return _decide_kt_review(c, a, body, user)
    if a.kind == "review" and body.decision not in {"approved", "changes_requested"}:
        raise HTTPException(422, "decision must be approved or changes_requested")
    if a.kind in PROPOSALS and body.decision not in {"approved", "rejected"}:
        raise HTTPException(422, "decision must be approved or rejected")
    if a.kind == "template_change" and user.role != "admin":
        raise HTTPException(403, "An admin decides a template change: templates are the admins' to change")
    if body.text is not None and a.kind != "doc_edit":
        raise HTTPException(422, "text is for a doc_edit only")
    if a.kind == "task_plan":
        proposed = {t["id"] for p in a.payload.get("plans", []) for t in p.get("tasks", [])}
        if not set(body.approve) <= proposed or not set(body.edits) <= proposed:
            raise HTTPException(422, "Unknown task ids in the decision")
        planned = {m["key"] for p in a.payload.get("plans", []) for m in p.get("milestones", [])}
        if body.milestones is not None and not set(body.milestones) <= planned:
            raise HTTPException(422, "Unknown milestone keys in the decision")
    decision = {"approve": body.approve, "edits": {k: v.model_dump(exclude_none=True, mode="json") for k, v in body.edits.items()},
                "decision": body.decision, "user_id": user.id, **({"text": body.text} if body.text is not None else {}),
                **({"milestones": body.milestones} if body.milestones is not None else {})}
    try:
        out = c.decide(a, decision)
    except ApprovalBusy:
        raise HTTPException(409, "This decision is already being applied") from None
    except ApprovalStale:
        raise HTTPException(409, _STALE) from None
    except ApprovalNotReady:
        raise HTTPException(409, "still_preparing") from None
    after = c.store.get_approval(approval_id)
    applied = after.decision or decision  # a retry after a failed resume re-applies the first decision, not this request's
    c.store.audit(user.org_id, user.id, f"approval.{a.kind}.decided", a.id,
                  {"approved": len(applied.get("approve", [])), "decision": applied.get("decision"), "reapplied": applied != decision})
    _mail_invitations(c, a.project_id, out.get("result"))
    return {"approval": after, "result": out.get("result")}


def _mail_invitations(c, project_id: str, result: dict | None) -> None:
    """The invitations a plan's approval made get an email through the outbox (ids only: the worker composes it), when SMTP is set
    up. Never part of the decision: a failure to record the event is logged and the invitation's link is still returned."""
    mailer = c.deps.mailer
    if not (mailer is not None and mailer.enabled):
        return
    for e in (result or {}).get("enrolments", []):
        inv = e.get("invitation")
        if inv and inv.get("id"):
            try:
                publish(c, "reviews", {"type": "mail.invitation", "project_id": project_id, "onboarding_id": e["onboarding_id"],
                                       "invitation_id": inv["id"], "dedupe": f"mail:invitation:{inv['id']}"})
            except Exception:  # noqa: BLE001
                logging.getLogger("mailing").warning("invitation email not queued", exc_info=True)


_STALE = "This proposal is out of date and was closed; a newer one is in your approvals."
_KEY = re.compile(r"^[0-9a-f]{12}$")


def _decide_kt_review(c, a, body: Decision, user: User):
    """A KT call review: `approve` lists the follow-up session keys to book (none approves the review alone)."""
    if len(body.approve) > 50 or not all(_KEY.match(k) for k in body.approve):
        raise HTTPException(422, "approve must list KT planner session keys")
    try:
        result = kt_review.decide(c.store, c.index, c.settings, a, user, body.approve)
    except kt_review.ReviewBusy:
        raise HTTPException(409, "This decision is already being applied") from None
    c.store.audit(user.org_id, user.id, "approval.kt_followup.decided", a.id,
                  {"meeting_id": a.payload.get("meeting_id"), "booked": len(result["booked"]), "skipped": len(result["skipped"])})
    return {"approval": c.store.get_approval(a.id), "result": result}


@router.get("/reviews/{review_id}")
def get_review(review_id: str, user: User = Depends(current_user), c=Depends(container)):
    """A pull request review: the task's hire, their mentor and the project's managers read it; anyone else gets 404."""
    r = c.store.get_review(review_id) or _404()
    t = c.store.get_task(r.task_id) or _404()
    ob = c.store.get_onboarding(t.onboarding_id) or _404()
    check_project(c, user, ob.project_id)
    if not (_is_manager(user) or user.id == ob.new_hire_id or _mentors(c, ob.id, user)):
        raise HTTPException(404, "Not found")
    # Where the decision stands: pending, decided, or closed without one (a newer commit replaced this review, or the pull
    # request was merged first), and the newest review of the task when it is another one.
    apps = c.store.review_approvals(t.id)
    mine = [a for a in apps if a.payload.get("review_id") == r.id]
    last = mine[-1] if mine else None
    state = None if not last else "pending" if last.status == "pending" else "closed" if not last.decided_by else "decided"
    newest = max(apps, key=lambda a: (a.payload.get("pr_updated_at") or "", a.created_at)) if apps else None
    newer = newest.payload.get("review_id") if newest and newest.payload.get("review_id") != r.id else None
    return {**r.model_dump(), "task": t, "approval_state": state, "newer_review_id": newer}


# ---------------- experts ----------------
@router.get("/experts/requests")
def my_requests(user: User = Depends(require("expert", "manager")), c=Depends(container)):
    return c.store.expert_requests_for(user.id)


class ExpertAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)


@router.post("/experts/requests/{request_id}/answer")
def answer_request(request_id: str, body: ExpertAnswer, user: User = Depends(require("expert", "manager")), c=Depends(container)):
    req = c.store.get_expert_request(request_id) or _404()
    check_project(c, user, str(req["project_id"]))
    if str(req["expert_id"]) != user.id and user.role not in {"manager", "admin"}:
        raise HTTPException(403, "This question was routed to someone else")
    if req["status"] != "open":
        raise HTTPException(409, "Already answered")
    r = c.store.answer_expert_request(request_id, body.answer) or _404()
    chunk = Chunk(id=f"verified-{request_id}", project_id=str(r["project_id"]), source_type="verified_answer",
                  ref={"question": r["question"], "expert_id": user.id, "expert_name": user.name, "request_id": request_id},
                  content=f"Q: {r['question']}\nA (verified by {user.name}): {body.answer}")
    c.index.upsert([chunk])  # the next person who asks gets this answer, with its source
    if r.get("asked_by"):  # the asker's turn shows the answer (chat history joins it); the link opens that turn
        mid = f"message={r['message_id']}&" if r.get("message_id") else ""
        c.store.notify(str(r["asked_by"]), "expert_answer", f"{user.name} answered: {r['question'][:120]}", f"/chat?{mid}project={r['project_id']}")
    # the same question handed on (the mentor's copy, when the owner answers an escalation): answered too, its notices closed
    siblings = [x for x in c.store.open_expert_requests(str(r["project_id"]))
                if str(x["id"]) != str(request_id) and str(x.get("asked_by")) == str(r.get("asked_by"))
                and ((r.get("message_id") and str(x.get("message_id")) == str(r["message_id"])) or x["question"] == r["question"])]
    for x in siblings:
        c.store.answer_expert_request(str(x["id"]), body.answer)
    if r.get("message_id"):
        c.store.close_notifications(f"/experts?message={r['message_id']}")
    c.store.audit(user.org_id, user.id, "expert.answered", request_id, {"siblings_closed": len(siblings)} if siblings else None)
    return {"ok": True}


# ---------------- manager views and notifications ----------------
@router.get("/dashboard")
def dashboard(project_id: str, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, project_id)
    return build_dashboard(Ctx.of(c, user), project_id)


@router.get("/runs")
def runs(project_id: str, user: User = Depends(require("manager")), c=Depends(container)):
    check_project(c, user, project_id)
    return c.store.list_runs(project_id)


@router.get("/notifications")
def notifications(user: User = Depends(current_user), c=Depends(container)):
    return c.store.notifications_for(user.id)


class ReadIn(BaseModel):
    ids: list[str] | None = None  # None marks everything read


@router.post("/notifications/read")
def read_notifications(body: ReadIn, user: User = Depends(current_user), c=Depends(container)):
    return {"marked": c.store.mark_notifications_read(user.id, body.ids)}


def _404():
    raise HTTPException(404, "Not found")


# Settings -> Integrations (Google Calendar, Calendly links): their own module, served under /api with these routes.
from app.api import integrations as _integrations  # noqa: E402

router.include_router(_integrations.router)
