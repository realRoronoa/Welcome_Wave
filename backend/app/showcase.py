"""Production showcase demo accounts (`DEMO_SHOWCASE=true`).

Allows a live self-hosted / cloud deployment (with real Firebase Auth, Postgres RLS, Agora Voice, and Claude/Gemini AI)
to also offer one-click demo accounts underneath the sign-in form, isolated inside their own deterministic organisation
(`SHOWCASE_ORG_ID`) and authenticated with HMAC-SHA256 signed bearer tokens (`Bearer wwdemo.<payload>.<sig>`).

Off by default (`DEMO_SHOWCASE=false`), and never uses raw `X-Demo-User` headers or registers `/api/demo/*` routes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import legal
from app.adapters import recording_store
from app.adapters.memory import LocalRepoSource, signing_key
from app.agents.core.llm import OfflineLLM
from app.agents.core.schemas import (
    ActionItem,
    Chunk,
    Citation,
    KTLink,
    MeetingResult,
    ProposedTask,
    QAResult,
    Term,
    Utterance,
)
from app.agents.supervisor.graph import OPEN_FOR_PR, run_event
from app.api.auth import _set_tenant, container, refuse_deactivated, require
from app.ports import (
    AreaProgress,
    BlockerAlert,
    CheckinReview,
    DocFeedback,
    EnvironmentCheck,
    ExpertPreferences,
    Meeting,
    Onboarding,
    OnboardingProgram,
    Phase,
    ProgressSummary,
    Project,
    Repository,
    SurveyResponse,
    TaskMove,
    TaskPlacement,
    User,
    VerifiedAnswer,
)
from app.ratelimit import request_address
from app.seed import (
    BOARD,
    BOARD_MOVES,
    CURRENT,
    DEMO_ISSUES,
    DEMO_PR_FILES,
    DEMO_PREBOARDING_TOKEN,
    HISTORY,
    MOMENTS,
    PREVIOUS,
    SURVEY_SCORES,
    TRANSCRIPT,
    VIJAY_DAY,
)
from app.services import kt_review
from app.services.chunking import chunk_transcript
from app.services.embeddings import HashEmbedder
from app.services.kt_linker import link_screen_moments

log = logging.getLogger("showcase")
router = APIRouter(prefix="/showcase")

TOKEN_PREFIX = "wwdemo."
TOKEN_TTL_S = 7 * 86400
NS = uuid.UUID("77776465-6d6f-4000-8000-000000000000")
SHOWCASE_ORG_ID = "00000000-0000-4000-8000-000000000001"


def uid_of(key: str) -> str:
    return str(uuid.uuid5(NS, key))


SHOWCASE_PROJECT_ID = uid_of("proj-demo")
REPO_ID = uid_of("repo-demo")
MTG_ID = uid_of("mtg-1")

CORE_PEOPLE = (
    ("u-admin", "admin@demo.dev", "Alex (Admin)", "admin", "alex-admin"),
    ("u-priya", "priya@demo.dev", "Priya (Manager)", "manager", "priya-mgr"),
    ("u-exec", "elena@demo.dev", "Elena (Executive)", "executive", None),
    ("u-ravi", "ravi@demo.dev", "Ravi (Senior)", "expert", "ravi-dev"),
    ("u-meera", "meera@demo.dev", "Meera (Senior)", "expert", "meera-dev"),
    ("u-vijay", "vijay@demo.dev", "Vijay (New hire)", "new_hire", "vijay"),
)

_seed_lock = threading.Lock()
_seeded_containers: set[int] = set()


# ---------------------------------------------------------------- signed showcase tokens
def _secret(settings) -> bytes:
    raw = signing_key(settings.data_path, settings.token_encryption_key or settings.blob_signing_key)
    return hmac.new(raw, b"ww-showcase-token-v1", hashlib.sha256).digest()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_token(settings, user_id: str, ttl_s: int = TOKEN_TTL_S) -> str:
    body = _b64e(json.dumps({"uid": user_id, "org": SHOWCASE_ORG_ID, "exp": int(time.time()) + ttl_s},
                            separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_secret(settings), body.encode(), hashlib.sha256).digest())
    return f"{TOKEN_PREFIX}{body}.{sig}"


def verify_token(settings, token: str) -> str | None:
    if not token.startswith(TOKEN_PREFIX):
        return None
    rest = token[len(TOKEN_PREFIX):]
    try:
        body, sig = rest.split(".", 1)
        expected = _b64e(hmac.new(_secret(settings), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(_b64d(body).decode())
    except Exception:  # noqa: BLE001
        return None
    if claims.get("org") != SHOWCASE_ORG_ID or int(claims.get("exp") or 0) < time.time():
        return None
    uid = str(claims.get("uid") or "")
    return uid if uid else None


def resolve_user(c, token: str) -> User:
    uid = verify_token(c.settings, token)
    if not uid:
        raise HTTPException(401, "Invalid or expired token")
    ensure_seeded(c)
    _set_tenant(SHOWCASE_ORG_ID)
    try:
        user = c.store.get_user(uid)
    finally:
        _set_tenant(None)
    if not user or user.org_id != SHOWCASE_ORG_ID:
        raise HTTPException(401, "Invalid or expired token")
    return user


# ---------------------------------------------------------------- rate limiting
class _Attempts:
    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            if len(self.hits) > 10_000:
                self.hits = {k: q for k, q in self.hits.items() if q and now - q[-1] <= self.window_s}
            q = self.hits.setdefault(key, deque())
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


_login_attempts = _Attempts(10, 60.0)
BAD_LOGIN = "That email and password do not match a demo account."
_CONTAINER = Depends(container)
_NEW_HIRE = Depends(require("new_hire"))


# ---------------------------------------------------------------- HTTP routes
@router.get("/people")
def people(c=_CONTAINER):
    ensure_seeded(c)
    _set_tenant(SHOWCASE_ORG_ID)
    try:
        reader = User(id=uid_of("u-admin"), org_id=SHOWCASE_ORG_ID, email="", name="", role="admin")
        members, invited = {}, {}
        for p in c.store.projects_for(reader):
            for m in c.store.members(p.id):
                if m.status == "active":
                    members.setdefault(m.id, {"id": m.id, "name": m.name, "email": m.email, "role": m.role})
            for i in c.store.invitations(p.id):
                invited.setdefault(i["email"], {"invitation_id": i["id"], "project_id": p.id, "email": i["email"],
                                                "name": i.get("name"), "role": i["role"]})
        return {"members": sorted(members.values(), key=lambda m: m["name"]), "invited": list(invited.values())}
    finally:
        _set_tenant(None)


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


@router.post("/login")
def login(body: LoginIn, request: Request, c=_CONTAINER):
    email = body.email.strip().lower()
    client = request_address(request.scope)
    if not _login_attempts.allow(f"{email}|{client}"):
        raise HTTPException(429, "Too many sign-in attempts. Wait a minute and try again.", headers={"Retry-After": "60"})
    password_ok = hmac.compare_digest(body.password.encode(), c.settings.demo_password.encode())
    if password_ok:
        ensure_seeded(c)
    _set_tenant(SHOWCASE_ORG_ID)
    try:
        user = c.store.user_by_email(SHOWCASE_ORG_ID, email) if email else None
        if not password_ok or not user:
            raise HTTPException(401, BAD_LOGIN)
        refuse_deactivated(user)
        c.store.audit(user.org_id, user.id, "showcase.signed_in", user.id)
        return {"user": user, "token": sign_token(c.settings, user.id)}
    finally:
        _set_tenant(None)


class AcceptIn(BaseModel):
    project_id: str
    invitation_id: str
    accept: list[str] = Field(default_factory=list, max_length=8)


@router.post("/invitations/accept")
def accept_invitation(body: AcceptIn, request: Request, c=_CONTAINER):
    ensure_seeded(c)
    _set_tenant(SHOWCASE_ORG_ID)
    try:
        try:
            project = c.store.get_project(body.project_id)
        except Exception:  # noqa: BLE001
            project = None
        if not project or project.org_id != SHOWCASE_ORG_ID:
            raise HTTPException(404, "Project not found")
        inv = next((i for i in c.store.invitations(body.project_id) if str(i["id"]) == body.invitation_id), None)
        if not inv:
            raise HTTPException(404, "That invitation has been accepted or revoked")
        documents = legal.check_documents(body.accept, legal.PERSONAL)
        joined = c.store.accept_invitations(f"showcase:{inv['id']}", inv["email"], inv.get("name") or "", SHOWCASE_ORG_ID)
        if not joined:
            raise HTTPException(409, "That invitation can no longer be accepted")
        c.store.audit(SHOWCASE_ORG_ID, joined.id, "invitation.accepted", str(inv["id"]), {"showcase": True})
        if documents:
            legal.record(c, joined, documents, "invitation", request)
        return joined
    finally:
        _set_tenant(None)


class PullRequestIn(BaseModel):
    task_id: str
    merge: bool = False


@router.post("/pull-request")
def pull_request(body: PullRequestIn, user: User = _NEW_HIRE, c=_CONTAINER):
    from app.api.webhooks import _github_event

    if user.org_id != SHOWCASE_ORG_ID:
        raise HTTPException(403, "Demo pull requests are only available in the showcase organisation")
    t = c.store.get_task(body.task_id)
    ob = c.store.get_onboarding(t.onboarding_id) if t else None
    if not t or not ob or ob.new_hire_id != user.id:
        raise HTTPException(404, "Task not found")
    if t.kind != "code" or t.status not in OPEN_FOR_PR:
        raise HTTPException(409, "Only an open code task takes a pull request")
    if body.merge and not t.pr_number:
        raise HTTPException(409, "Open the pull request first")
    repo = next(iter(c.store.repos_for(ob.project_id)), None)
    if not repo:
        raise HTTPException(409, "This project has no repository")
    number = t.pr_number or 100 + int(hashlib.sha1(t.id.encode()).hexdigest(), 16) % 900
    sha = hashlib.sha1(f"{t.id}:{time.time_ns()}".encode()).hexdigest()
    payload = {
        "action": "closed" if body.merge else "synchronize" if t.pr_number else "opened",
        "repository": {"full_name": repo.full_name, "default_branch": repo.default_branch},
        "pull_request": {
            "number": number,
            "html_url": f"https://github.com/{repo.full_name}/pull/{number}",
            "head": {"sha": sha, "ref": f"task-{t.id.replace('-', '')[:8]}"},
            "body": f"Closes #{t.github_issue_number}" if t.github_issue_number else "",
            "user": {"login": user.github_login or ""},
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "merged": body.merge,
        },
    }
    out = _github_event(c, "pull_request", payload)
    after = c.store.get_task(t.id)
    return {"events": out.get("events", 0), "task": after, "review_id": c.store.latest_reviews([t.id]).get(t.id)}


# ---------------------------------------------------------------- idempotent Postgres / Memory seeder
def start_background_seed(c) -> None:
    def _bg():
        try:
            ensure_seeded(c)
        except Exception:  # noqa: BLE001
            log.exception("showcase background seed failed")

    threading.Thread(target=_bg, name="ww-showcase-seed", daemon=True).start()


def _at(start: date, day: float, hour: int = 10) -> datetime:
    return datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=day, hours=hour)


def ensure_seeded(c) -> None:
    cid = id(c)
    if cid in _seeded_containers:
        _set_tenant(SHOWCASE_ORG_ID)
        try:
            if c.store.org_name(SHOWCASE_ORG_ID):
                return
        finally:
            _set_tenant(None)
    with _seed_lock:
        _set_tenant(SHOWCASE_ORG_ID)
        try:
            if _already_seeded(c):
                _seeded_containers.add(cid)
                return
            _seed_showcase(c)
            _seeded_containers.add(cid)
            log.info("showcase organisation seeded", extra={"extra_fields": {"org_id": SHOWCASE_ORG_ID}})
        finally:
            _set_tenant(None)


def _already_seeded(c) -> bool:
    try:
        proj = c.store.get_project(SHOWCASE_PROJECT_ID)
    except Exception:  # noqa: BLE001
        proj = None
    if not proj:
        return False
    has_code = bool(c.index.all_paths(SHOWCASE_PROJECT_ID))
    has_program = c.lifecycle is None or c.lifecycle.get_program(uid_of("ob-vijay")) is not None
    return has_code and has_program


class _SafeEmbedder:
    """Uses the container's real embedder when available, falling back to HashEmbedder if the key/quota is unavailable."""

    def __init__(self, primary, dim: int):
        self.primary = primary
        self.fallback = HashEmbedder(dim)

    def embed(self, texts: list[str], task: str = "document") -> list[list[float]]:
        try:
            return self.primary.embed(texts, task)
        except Exception:  # noqa: BLE001
            return self.fallback.embed(texts, task)


def _seed_base_rows(c, today: date, now: datetime, tpl_titles: dict[str, str]) -> None:
    store = c.store
    active_people = list(CORE_PEOPLE) + [
        (k, f"{k[2:]}@demo.dev", name, "new_hire", gh) for k, name, gh, *_ in CURRENT
    ]
    past_people = [(k, f"{k[2:]}@demo.dev", name) for k, name, *_ in PREVIOUS]
    onboardings = [
        (uid_of("ob-vijay"), uid_of("u-vijay"), today - timedelta(days=VIJAY_DAY), "Software Engineer", "active"),
        *[
            (uid_of(f"ob-{k[2:]}"), uid_of(k), today - timedelta(days=day), tpl_titles.get(tkey, "Software Engineer"), "active")
            for k, _name, _gh, day, tkey, *_ in CURRENT
        ],
        *[
            (uid_of(f"ob-{k[2:]}"), uid_of(k), today - timedelta(days=ago), tpl_titles.get(tkey, "Software Engineer"), "completed")
            for k, _name, ago, tkey, *_ in PREVIOUS
        ],
    ]

    if recording_store.is_postgres(store):
        from sqlalchemy import text

        with store.tx() as conn:
            conn.execute(text("INSERT INTO organizations (id, name) VALUES (CAST(:o AS uuid), :n) ON CONFLICT (id) DO NOTHING"),
                         {"o": SHOWCASE_ORG_ID, "n": "Welcome Wave Demo"})
            conn.execute(text("""INSERT INTO projects (id, org_id, name, description)
                                 VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :n, :d) ON CONFLICT (id) DO NOTHING"""),
                         {"p": SHOWCASE_PROJECT_ID, "o": SHOWCASE_ORG_ID, "n": "Mini Policy Engine",
                          "d": "Kubernetes admission policy engine (demo)"})
            for key, email, name, role, gh in active_people:
                uid = uid_of(key)
                conn.execute(text("""
                    INSERT INTO users (id, org_id, firebase_uid, email, name, role, github_login)
                    VALUES (CAST(:id AS uuid), CAST(:o AS uuid), :fuid, :email, :name, CAST(:role AS user_role), :gh)
                    ON CONFLICT (id) DO UPDATE SET
                        firebase_uid = COALESCE(users.firebase_uid, EXCLUDED.firebase_uid),
                        deactivated_at = NULL
                """), {"id": uid, "o": SHOWCASE_ORG_ID, "fuid": f"showcase:{key}", "email": email, "name": name, "role": role, "gh": gh})
                conn.execute(text("""INSERT INTO project_members (project_id, user_id, org_id)
                                     VALUES (CAST(:p AS uuid), CAST(:u AS uuid), CAST(:o AS uuid)) ON CONFLICT DO NOTHING"""),
                             {"p": SHOWCASE_PROJECT_ID, "u": uid, "o": SHOWCASE_ORG_ID})
            for key, email, name in past_people:
                conn.execute(text("""
                    INSERT INTO users (id, org_id, firebase_uid, email, name, role)
                    VALUES (CAST(:id AS uuid), CAST(:o AS uuid), NULL, :email, :name, 'new_hire')
                    ON CONFLICT (id) DO NOTHING
                """), {"id": uid_of(key), "o": SHOWCASE_ORG_ID, "email": email, "name": name})
            conn.execute(text("""
                INSERT INTO repositories (id, org_id, project_id, full_name, default_branch, status)
                VALUES (CAST(:id AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), 'demo/mini-policy-engine', 'main', 'pending')
                ON CONFLICT (project_id) DO NOTHING
            """), {"id": REPO_ID, "o": SHOWCASE_ORG_ID, "p": SHOWCASE_PROJECT_ID})
            conn.execute(text("""
                INSERT INTO meetings (id, org_id, project_id, title, room, status, duration_s, started_at, created_by, provider)
                VALUES (CAST(:id AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), :title, 'kt-showcase-1', 'processing', 270,
                        :started_at, CAST(:by AS uuid), 'manual')
                ON CONFLICT (id) DO NOTHING
            """), {"id": MTG_ID, "o": SHOWCASE_ORG_ID, "p": SHOWCASE_PROJECT_ID, "title": "KT #1: How validation works",
                   "started_at": now - timedelta(days=2), "by": uid_of("u-priya")})
            for oid, nh_id, sdate, rtitle, status in onboardings:
                conn.execute(text("""
                    INSERT INTO onboardings (id, org_id, project_id, new_hire_id, manager_id, role_title, start_date, status)
                    VALUES (CAST(:id AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), CAST(:nh AS uuid), CAST(:mgr AS uuid),
                            :rt, :sd, :st)
                    ON CONFLICT (id) DO NOTHING
                """), {"id": oid, "o": SHOWCASE_ORG_ID, "p": SHOWCASE_PROJECT_ID, "nh": nh_id, "mgr": uid_of("u-priya"),
                       "rt": rtitle, "sd": sdate, "st": status})
        return

    inner = store.__dict__.get("_store", store)
    inner.orgs[SHOWCASE_ORG_ID] = "Welcome Wave Demo"
    store.add_project(Project(id=SHOWCASE_PROJECT_ID, org_id=SHOWCASE_ORG_ID, name="Mini Policy Engine",
                              description="Kubernetes admission policy engine (demo)"))
    for key, email, name, role, gh in active_people:
        uid = uid_of(key)
        store.add_user(User(id=uid, org_id=SHOWCASE_ORG_ID, email=email, name=name, role=role, github_login=gh), SHOWCASE_PROJECT_ID)
        inner.firebase[f"showcase:{key}"] = uid
    for key, email, name in past_people:
        uid = uid_of(key)
        store.add_user(User(id=uid, org_id=SHOWCASE_ORG_ID, email=email, name=name, role="new_hire"))
        inner.left.add(uid)
    store.add_repo(Repository(id=REPO_ID, project_id=SHOWCASE_PROJECT_ID, full_name="demo/mini-policy-engine"))
    store.add_meeting(Meeting(id=MTG_ID, project_id=SHOWCASE_PROJECT_ID, title="KT #1: How validation works",
                              room="kt-showcase-1", status="processing", duration_s=270, recording_uri=None,
                              started_at=now - timedelta(days=2)))
    for oid, nh_id, sdate, rtitle, status in onboardings:
        store.add_onboarding(Onboarding(id=oid, project_id=SHOWCASE_PROJECT_ID, new_hire_id=nh_id, manager_id=uid_of("u-priya"),
                                        start_date=sdate, role_title=rtitle, status=status))


def _seed_showcase(c) -> None:
    from app.lifecycle import board, handover, hires, preboarding, summaries
    from app.lifecycle import templates as T
    from app.lifecycle.clock import Clock, business_days, week_start
    from app.lifecycle.context import Ctx, HireCtx

    deps, store, lc = c.deps, c.store, c.lifecycle
    today, now = date.today(), datetime.now(timezone.utc)
    tpl = {t.key: t for t in T.list_for(lc, SHOWCASE_ORG_ID)} if lc is not None else {}
    _seed_base_rows(c, today, now, {k: v.title for k, v in tpl.items()})

    repo_root = Path(c.settings.demo_repo_path)
    if not repo_root.is_absolute():
        repo_root = Path(__file__).resolve().parents[1] / repo_root
    local_repo = LocalRepoSource(str(repo_root), issues=DEMO_ISSUES, default_pr=DEMO_PR_FILES)

    real_llm, real_repos = deps.llm, deps.repos
    real_embedder = getattr(deps.index, "embedder", None)
    deps.llm = OfflineLLM()
    if hasattr(deps.repos, "local") and deps.repos.local is None:
        deps.repos.local = local_repo
    elif not hasattr(deps.repos, "local") and not isinstance(deps.repos, LocalRepoSource):
        deps.repos = local_repo
    if real_embedder is not None:
        deps.index.embedder = _SafeEmbedder(real_embedder, c.settings.embedding_dim)

    try:
        repo = store.repos_for(SHOWCASE_PROJECT_ID)[0]
        if not c.index.all_paths(SHOWCASE_PROJECT_ID):
            run_event(c.graph, {"type": "repo.connected", "project_id": SHOWCASE_PROJECT_ID, "repository_id": repo.id, "ref": "main"},
                      f"showcase:repo:{SHOWCASE_ORG_ID}")

        m = store.get_meeting(MTG_ID)
        if m and store.meeting_notes(m.id) is None:
            utterances = [Utterance(start_s=a, end_s=b, speaker=s, text=t) for a, b, s, t in TRANSCRIPT]
            links = link_screen_moments(SHOWCASE_PROJECT_ID, m.id, m.title, MOMENTS, utterances, deps.index, meeting_end_s=270)
            result = MeetingResult(
                meeting_id=m.id, project_id=SHOWCASE_PROJECT_ID, title=m.title,
                summary="Ravi explained the validation flow: validate_resource loops over matching rules, check_labels and "
                        "check_privileged return violation strings, and apply_defaults adds labels before validation runs.",
                decisions=["Validation returns a list of violations and never raises"],
                action_items=[ActionItem(text="Vijay to read engine/policy.py before Friday", owner_hint="Vijay", at_s=232),
                              ActionItem(text="Vijay to look at issue #12 as a first PR", owner_hint="Vijay", at_s=245)],
                terms=[Term(term="violation", definition="A message describing a rule the resource breaks")],
                utterances=utterances, screen_moments=MOMENTS, kt_links=links,
            )
            store.save_meeting_result(result)
            store.add_kt_links(SHOWCASE_PROJECT_ID, links)
            deps.index.upsert(chunk_transcript(SHOWCASE_PROJECT_ID, m.id, m.title, utterances))
            recording_store.confirm_consent(store, m.id, uid_of("u-ravi"), legal.VERSIONS["recording"])
            row, _ = recording_store.create_removal_request(
                store, SHOWCASE_ORG_ID, SHOWCASE_PROJECT_ID, m.id, uid_of("u-meera"),
                "I name a customer near the end. Can this come out?",
            )
            recording_store.close_removal_requests(
                store, m.id, recording_store.DECLINED, uid_of("u-priya"),
                "Checked with Meera: that customer is already public. The recording stays.", row["id"],
            )
            kt_review.open_review(store, SHOWCASE_PROJECT_ID, m.id, m.title)

        ob_vijay_id = uid_of("ob-vijay")
        if not store.tasks(onboarding_id=ob_vijay_id) and not store.approvals(SHOWCASE_PROJECT_ID, "pending"):
            run_event(c.graph, {"type": "onboarding.started", "project_id": SHOWCASE_PROJECT_ID, "onboarding_id": ob_vijay_id},
                      f"showcase:plan:{ob_vijay_id}")

        if lc is not None and lc.get_program(ob_vijay_id) is None:
            u_priya = uid_of("u-priya")
            u_admin = uid_of("u-admin")
            u_ravi = uid_of("u-ravi")
            u_meera = uid_of("u-meera")
            u_vijay = uid_of("u-vijay")
            ctx = Ctx(SimpleNamespace(lifecycle=lc, store=store, index=deps.index, deps=deps, settings=deps.settings), Clock(),
                      store.get_user(u_priya))

            existing_teams = {tm.name: tm for tm in lc.teams(SHOWCASE_ORG_ID, SHOWCASE_PROJECT_ID)}
            teams = {
                "engine": existing_teams.get("Policy Engine") or lc.create_team(SHOWCASE_ORG_ID, SHOWCASE_PROJECT_ID, "Policy Engine"),
                "webhooks": existing_teams.get("Admission Webhooks") or lc.create_team(SHOWCASE_ORG_ID, SHOWCASE_PROJECT_ID, "Admission Webhooks"),
            }
            lc.save_expert_preferences(SHOWCASE_ORG_ID, ExpertPreferences(user_id=u_ravi, max_interruptions_per_day=3, digest=True))

            def program(ob: Onboarding, key: str, team: str, mentor: str | None, focus: str, done: dict[str, float]) -> HireCtx:
                t = tpl[key]
                lc.create_program(
                    OnboardingProgram(
                        onboarding_id=ob.id, org_id=SHOWCASE_ORG_ID, project_id=SHOWCASE_PROJECT_ID,
                        template_id=t.id, template_version=t.version, duration_days=t.duration_days,
                        mentor_id=mentor, team_id=teams[team].id, focus_area=focus,
                        plan_approved_at=_at(ob.start_date, -5), created_at=_at(ob.start_date, -7),
                    ),
                    T.milestones_for(t, ob.id, t.duration_days),
                    T.access_for(t, ob.id, {"manager": u_priya, "mentor": mentor, "admin": u_admin}),
                    T.acknowledgements_for(t, ob.id),
                )
                for a in lc.access_items(ob.id):
                    lc.update_access_item(a.id, requested_at=_at(ob.start_date, -7))
                for k, day_done in done.items():
                    lc.update_milestone(
                        ob.id, k, status="done", done_at=_at(ob.start_date, day_done),
                        evidence={"auto": True} if k in T.AUTO_MILESTONES else {"by": u_priya, "note": "Seeded"},
                    )
                return HireCtx(ctx, ob, lc.get_program(ob.id))

            def acknowledge_all(hc: HireCtx, day_done: float, keep: int = 0) -> None:
                items = lc.acknowledgements(hc.ob.id)
                for a in items[:len(items) - keep]:
                    lc.acknowledge(hc.ob.id, a.doc_key, "", _at(hc.ob.start_date, day_done))

            def grant(hc: HireCtx, day_done: float, keep: int = 0) -> None:
                items = lc.access_items(hc.ob.id)
                for a in items[:len(items) - keep]:
                    lc.update_access_item(a.id, status="granted", granted_at=_at(hc.ob.start_date, day_done))

            def env_check(hc: HireCtx, at: datetime, failing: dict[str, str] | None = None) -> None:
                failing = failing or {}
                results = [{"name": n, "ok": n not in failing, "detail": failing.get(n, "found")} for n, _ in preboarding.tools_for(hc)]
                lc.add_environment_check(EnvironmentCheck(id="", onboarding_id=hc.ob.id, ran_at=at, ok=not failing, results=results))

            def add_tasks(hc: HireCtx, rows: list[tuple]) -> None:
                made = store.create_tasks(hc.ob.id, [ProposedTask(title=t, description=t, kind=k, due_in_days=max(1, min(60, d)),
                                                                  related_paths=paths)
                                                     for t, k, d, _, paths, _ in rows], hc.ob.start_date)
                for row, (_, _, d, st, _, pr) in zip(made, rows):
                    store.update_task(row.id, status=st, **({"pr_number": pr,
                                                             "pr_url": f"https://github.com/demo/mini-policy-engine/pull/{pr}"} if pr else {}))
                    if pr and st == "done":
                        lc.record_metric(SHOWCASE_ORG_ID, SHOWCASE_PROJECT_ID, "pr.merged", row.id, {"pr": pr}, _at(hc.ob.start_date, d + 5))

            def checkin(hc: HireCtx, day_n: int, late: int = 1) -> None:
                for role, author, answers in (("hire", hc.ob.new_hire_id, {"going_well": "Pairing sessions help.", "confidence": 4}),
                                              ("manager", u_priya, {"strengths": "Careful reviews.", "on_track": 4})):
                    lc.save_checkin(CheckinReview(id="", onboarding_id=hc.ob.id, day=day_n, author_role=role, author_id=author,
                                                  answers=answers, created_at=_at(hc.ob.start_date, day_n + late)))

            def survey(team: str, start: date, day_n: int, n: int) -> None:
                scores = SURVEY_SCORES[n % len(SURVEY_SCORES)]
                lc.add_survey_response(SurveyResponse(
                    id="", org_id=SHOWCASE_ORG_ID, team_id=teams[team].id,
                    cohort=f"{start.year}-Q{(start.month - 1) // 3 + 1}", day=day_n,
                    answers=dict(zip(("clarity", "support", "docs", "tools", "belonging"), scores)),
                ))

            vijay = program(store.get_onboarding(ob_vijay_id), "backend-mid", "engine", u_ravi, "engine",
                            {"preboarding_complete": -1, "environment_ready": 2, "meet_team": 4})
            acknowledge_all(vijay, -2)
            grant(vijay, 1, keep=1)
            opened = today - timedelta(days=1)
            while business_days(opened, today) < 1:
                opened -= timedelta(days=1)
            env_check(vijay, _at(vijay.ob.start_date, 1), {"docker": "not found"})
            env_check(vijay, _at(vijay.ob.start_date, 2))
            env_check(vijay, _at(opened, -1, 16), {"docker": "Cannot connect to the Docker daemon"})
            env_check(vijay, now - timedelta(hours=3), {"docker": "Cannot connect to the Docker daemon"})
            lc.open_blocker(BlockerAlert(
                id="", onboarding_id=ob_vijay_id, rule="environment", subject="env", stage="mentor", notified_stage="mentor",
                opened_at=_at(opened, 0, 9),
                evidence={"failing_since": _at(opened, -1, 16).isoformat(), "failing": ["docker"], "checks": 2, "hours": 40},
            ))
            lc.save_area_progress(AreaProgress(
                onboarding_id=ob_vijay_id, area="engine", self_level="exploring", updated_at=now - timedelta(days=2),
                evidence={"steps": [{"title": "Code tour: engine/policy.py, load_policy", "at": (now - timedelta(days=2)).isoformat()}]},
            ))
            for fb in (
                DocFeedback(id="", project_id=SHOWCASE_PROJECT_ID, target_kind="code", target_ref="engine/policy.py", kind="outdated",
                            owner_id=u_ravi, note="The module docstring still describes the old YAML loader.", created_by=u_vijay,
                            created_at=now - timedelta(days=3)),
                DocFeedback(id="", project_id=SHOWCASE_PROJECT_ID, target_kind="meeting", target_ref=MTG_ID, kind="unclear",
                            owner_id=u_priya, note="The part about default labels (around 3:00) goes by fast.", created_by=u_vijay,
                            created_at=now - timedelta(days=1)),
            ):
                lc.add_feedback(fb)
            for back in (14, 7):
                wstart = week_start(today - timedelta(days=back))
                if wstart + timedelta(days=7) > vijay.ob.start_date:
                    lc.save_summary(ProgressSummary(id="", onboarding_id=ob_vijay_id, week_start=wstart,
                                                    body=summaries.build(vijay, wstart), created_at=_at(wstart, 6, 17)))

            arjun = program(store.get_onboarding(uid_of("ob-arjun")), "backend-junior", "webhooks", u_meera, "webhooks", {})
            acknowledge_all(arjun, -1, keep=1)
            grant(arjun, 0, keep=3)
            env_check(arjun, _at(arjun.ob.start_date, 0, 11))
            lc.set_preboarding(arjun.ob.id, preboarding.token_hash(DEMO_PREBOARDING_TOKEN), now + timedelta(days=preboarding.LINK_DAYS))
            add_tasks(arjun, [("Set up your development environment", "learn", 2, "done", [], None),
                              ("Meet your mentor and the team", "meet", 3, "approved", [], None),
                              ("Read the README and the architecture notes", "learn", 5, "approved", [], None)])

            sara = program(store.get_onboarding(uid_of("ob-sara")), "backend-senior", "webhooks", u_meera, "webhooks",
                           {"preboarding_complete": -2, "environment_ready": 1, "access_ready": 2, "meet_team": 3,
                            "first_pr_opened": 12, "service_traced": 18, "first_pr_merged": 19, "checkin_30": 31})
            acknowledge_all(sara, -3)
            grant(sara, 1)
            env_check(sara, _at(sara.ob.start_date, 1))
            add_tasks(sara, [("Read the README and the architecture notes", "learn", 5, "done", [], None),
                             ("Add a request size limit to the webhook server", "code", 14, "done", ["webhooks/server.py"], 101),
                             ("Write a runbook for webhook timeouts", "learn", 50, "in_progress", [], None)])
            checkin(sara, 30)
            survey("webhooks", sara.ob.start_date, 30, 1)
            lc.update_program(sara.ob.id, survey_days=[30])

            tom = program(store.get_onboarding(uid_of("ob-tom")), "sre-mid", "engine", u_ravi, "engine",
                          {"preboarding_complete": -1, "environment_ready": 2, "access_ready": 4, "meet_team": 5,
                           "first_pr_opened": 9, "first_pr_merged": 16, "checkin_30": 31, "oncall_shadow": 38,
                           "area_working": 44, "checkin_60": 62, "area_contributed": 70})
            acknowledge_all(tom, -2)
            grant(tom, 2)
            env_check(tom, _at(tom.ob.start_date, 2))
            add_tasks(tom, [("Shadow an on-call shift", "meet", 20, "done", [], None),
                            ("Add a metric for rejected resources", "code", 14, "done", ["engine/validate.py"], 98)])
            checkin(tom, 30)
            checkin(tom, 60)
            for d, n in ((30, 2), (60, 3)):
                survey("engine", tom.ob.start_date, d, n)
            lc.update_program(tom.ob.id, survey_days=[30, 60])

            for key, _name, _ago, tkey, team, pr_day, merge_day, done_day in PREVIOUS:
                ob = store.get_onboarding(uid_of(f"ob-{key[2:]}"))
                t = tpl[tkey]
                late = 3 if key in {"u-noah", "u-kofi"} else 0
                done_map = {mk["key"]: T.scale(mk["due_day"], t.duration_days) + (late if mk["key"].startswith("checkin") else 0)
                            for mk in t.milestones}
                done_map.update(first_pr_opened=pr_day, first_pr_merged=merge_day)
                hc = program(ob, tkey, team, u_ravi if team == "engine" else u_meera, team, done_map)
                acknowledge_all(hc, -2)
                grant(hc, 2)
                env_check(hc, _at(ob.start_date, 1))
                add_tasks(hc, [("Read the README and the architecture notes", "learn", 5, "done", [], None),
                               (f"First change: {'validation messages' if team == 'engine' else 'webhook errors'}", "code", 14, "done",
                                ["engine/validate.py" if team == "engine" else "webhooks/server.py"], 60 + pr_day)])
                for d in T.checkin_days(t.duration_days):
                    checkin(hc, d, late=1 + late)
                    if not (key == "u-noah" and d == 90):
                        survey(team, ob.start_date, d, len(key) + d)
                lc.update_program(ob.id, completed_at=_at(ob.start_date, done_day),
                                  completion={"decision": "complete", "note": "Signed off at the completion review.",
                                              "by": u_priya, "at": _at(ob.start_date, done_day).isoformat(), "unmet": []})

            va = lc.add_verified_answer(VerifiedAnswer(
                id="", project_id=SHOWCASE_PROJECT_ID, question="Who decides the on-call rota?",
                answer="Priya sets it each month; swaps are agreed in the team channel and noted in the rota.",
                verified_by=u_ravi, reuse_count=2, created_at=now - timedelta(days=20),
            ))
            verifier = store.get_user(va.verified_by)
            vname = verifier.name if verifier else "an expert"
            deps.index.upsert([Chunk(
                id=f"va-{va.id}", project_id=SHOWCASE_PROJECT_ID, source_type="verified_answer",
                ref={"question": va.question, "expert_id": va.verified_by, "expert_name": vname, "verified_answer_id": va.id},
                content=f"Q: {va.question}\nA (verified by {vname}): {va.answer}",
            )])

            out = run_event(c.graph, {"type": "handover.requested", "project_id": SHOWCASE_PROJECT_ID, "user_id": u_priya,
                                      "person_id": u_meera, "last_day": (today + timedelta(days=21)).isoformat()},
                            f"showcase:handover:{SHOWCASE_ORG_ID}")
            h = lc.get_handover(out["result"]["id"])
            if h:
                items = h.plan.get("items", [])
                handover.update(ctx, h, "in_progress", [i["key"] for i in items[:1]], [], None, False)

            clip = KTLink(meeting_id=MTG_ID, meeting_title="KT #1: How validation works", file_path="engine/mutate.py",
                          symbol="apply_defaults", start_s=182, end_s=230, confidence=0.9, method="symbol")
            kt_cit = Citation(chunk_id=None, source_type="transcript", label="KT #1: How validation works, 3:02",
                              ref={"meeting_id": MTG_ID, "start_s": 182})
            guide_cit = Citation(chunk_id=None, source_type="doc", label="docs/CONTRIBUTING.md", ref={"path": "docs/CONTRIBUTING.md"})
            store.save_chat(SHOWCASE_PROJECT_ID, u_vijay, "Where do the default labels come from?",
                            QAResult(status="answered",
                                     answer="They are added by apply_defaults before validation runs; Ravi walks through it in the first KT session.",
                                     citations=[kt_cit], kt_clips=[clip], confidence=0.82))
            store.save_chat(SHOWCASE_PROJECT_ID, uid_of("u-arjun"), "How do I run the tests locally?",
                            QAResult(status="answered", answer="Run pytest from the repository root, as the contributing guide says.",
                                     citations=[guide_cit], confidence=0.74))
            question = "Who signs off a change to the staging deploy checklist?"
            draft = ("I found nothing about the staging deploy checklist in the code, the documents or the KT sessions. CODEOWNERS "
                     "gives the engine folder to one person, so the engine owner may be the one to ask. This draft is unverified.")
            rid = store.create_expert_request(SHOWCASE_PROJECT_ID, question, u_ravi, u_vijay, None, draft=draft)
            store.notify(u_ravi, "expert_request", f"Vijay asked: {question}", f"/experts?project={SHOWCASE_PROJECT_ID}")
            lc.record_metric(SHOWCASE_ORG_ID, SHOWCASE_PROJECT_ID, "interruption", u_ravi, {"request_id": rid, "urgent": False},
                             now - timedelta(days=1))
            store.set_scheduling_link(SHOWCASE_ORG_ID, u_ravi, "calendly", "https://calendly.com/ravi-demo/kt-session")

            # Seed milestone boards & approved tasks (including an open code task for Vijay so Voice/Tasks/PR review work immediately)
            for raw_oid, rows in BOARD.items():
                oid = uid_of(raw_oid)
                hc = hires.load(ctx, oid)
                board.phases(hc)
                made = store.create_tasks(oid, [ProposedTask(title=t, description=d, kind=k, due_in_days=day_n,
                                                             source="meeting" if kt else "plan")
                                                for t, k, day_n, _, kt, d in rows], hc.ob.start_date)
                for row, (_title, _, _, st, _, _) in zip(made, rows):
                    store.update_task(row.id, status=st)
            vijay_code = store.create_tasks(ob_vijay_id, [ProposedTask(
                title="Validate privileged init containers (Issue #12)",
                description="Extend check_privileged in engine/validate.py so initContainers are checked alongside containers, returning violation strings without raising.",
                kind="code",
                due_in_days=16,
                acceptance_criteria=["Checks spec.initContainers in engine/validate.py", "Returns violation strings and never raises", "Unit test added"],
                related_paths=["engine/validate.py"],
                issue_number=12,
                source="first_pr",
            )], vijay.ob.start_date)
            for row in vijay_code:
                store.update_task(row.id, status="in_progress")

            ob_sara_id = uid_of("ob-sara")
            lc.add_phase(Phase(onboarding_id=ob_sara_id, key="design_review", title="Leading a design review", due_day=75, position=4))
            lc.update_phase(ob_sara_id, "fully_on_board", position=5)
            for raw_oid, title, to, day_n, ago in BOARD_MOVES:
                oid = uid_of(raw_oid)
                ob = store.get_onboarding(oid)
                task = next((t for t in store.tasks(onboarding_id=oid) if t.title == title), None)
                if not task:
                    continue
                phases = {p.key: p for p in lc.phases(oid)}
                old_day = (task.due_date - ob.start_date).days
                frm = next((p for p in sorted(phases.values(), key=lambda p: p.due_day) if p.due_day >= old_day), None)
                if not frm or to not in phases:
                    continue
                due = ob.start_date + timedelta(days=day_n)
                store.update_task(task.id, due_date=due)
                lc.save_placements(oid, [TaskPlacement(task_id=task.id, onboarding_id=oid, phase_key=to)])
                lc.add_task_move(TaskMove(onboarding_id=oid, task_id=task.id, from_phase=frm.key, to_phase=to,
                                          from_title=frm.title, to_title=phases[to].title, from_due=task.due_date,
                                          to_due=due, actor_id=u_priya, at=now - timedelta(days=ago, hours=3)))

            for name, trigger, st, summary_txt, _ago in HISTORY:
                store.record_run(SHOWCASE_PROJECT_ID, name, trigger, st, {"summary": summary_txt, "demo_history": True})

        # Legal acceptances so showcase demo logins open directly on the workspace without a blocking modal
        for key, *_ in list(CORE_PEOPLE) + [(k, "", "", "", "") for k, *_ in CURRENT]:
            uid = uid_of(key)
            for doc in legal.PERSONAL:
                store.record_legal_acceptance(SHOWCASE_ORG_ID, uid, doc, legal.VERSIONS[doc], "prompt")
        store.record_legal_acceptance(SHOWCASE_ORG_ID, uid_of("u-admin"), "dpa", legal.VERSIONS["dpa"], "organisation")
    finally:
        deps.llm = real_llm
        deps.repos = real_repos
        if real_embedder is not None:
            deps.index.embedder = real_embedder
