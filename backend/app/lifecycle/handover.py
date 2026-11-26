"""Offboarding handover: everything a person who is leaving holds, handed to a successor so nobody starts from scratch.

Three steps. Agents propose, people decide: the Keeper drafts, and each step after that is a person's action, audited.

  1. Draft    create(), which the Keeper runs on handover.requested: the checklist below, the KT sessions to record (the
              Keeper adds them with agendas) and a note to the person. A successor is suggested: another holder of their
              code, else a manager.
  2. Transfer transfer(): moves what Welcome Wave itself holds to the successor (mentees, hires they manage, questions
              routed to them, access items, documentation feedback) and records the successor as the new owner of their
              code, documents and recordings. Run again, it moves whatever has landed on the person since.
  3. Complete complete(): after a person confirms (the API asks them to type the leaver's name). Open gaps must be accepted
              by name, and are kept on the plan and in the audit log. An admin may deactivate the leaver's account in the
              same step: they can no longer sign in, and everything they recorded or wrote stays.

The transfer set (transfer_set) is read live on every request, so it shows what is still on the person now, and what has
moved and to whom. Sections:
  code        files CODEOWNERS gives them (alone or with others), with how much a recorded KT session explains, and a
              GitHub link
  documents   repository docs they own, repository docs their KT sessions showed or explained, documents they uploaded,
              suggested doc wording waiting on them, and documents a manager attached to this handover
  kt          the recordings they spoke in and the explanations they gave (file, symbol, clip); changed code flagged
  sessions    KT sessions they host on the calendar, and the sessions planned for them to record before they leave
  people      hires they mentor and hires they manage, with those hires' open tasks and milestones still to come
  questions   questions routed to them and still open
  approvals   pending approvals that name them, and approvals only they could decide
  access      access items they still have to grant
  feedback    documentation feedback in their queue
Every item has a state: `moves` (the transfer hands it over), `todo` (a person acts: record, book, answer, re-record) or
`done`. Gaps are what would leave the successor starting from scratch: no successor, a module no KT session explains,
files only they hold, changed explanations, CODEOWNERS still naming them, no document for their code, sessions after their
last day, approvals nobody else decides.

Welcome Wave never writes to a repository: for CODEOWNERS the transfer set suggests the replacement lines, and the gap
closes when the repository's CODEOWNERS no longer names the person (it is re-read on every push)."""
from __future__ import annotations

import re
import secrets
from collections import defaultdict
from datetime import date
from functools import cached_property

from app.lifecycle.context import Ctx
from app.ports import Handover, LastAdmin, User
from app.services import knowledge as K
from app.services.chunking import module_of
from app.services.codeowners import owners_for, parse_codeowners
from app.services.knowledge_risk import build_knowledge_risk
from app.services.kt_planner import build_plan

DOC_EXTS = (".md", ".mdx", ".markdown", ".rst", ".adoc", ".txt")
ITEMS_SHOWN = 100  # items per section in one response; `count` and the totals cover every item
ATTACHABLE_SHOWN = 300  # repository docs offered when attaching
MAX_ATTACHMENTS = 100
MAX_TICKED = 1000
ORPHAN_SHARE = 0.5  # as knowledge risk: a file only they hold, explained less than this, leaves with them
CLOSED_TASKS = frozenset({"done", "rejected"})
CAN_MANAGE = frozenset({"manager", "admin"})
MOVES = ("mentee", "managed", "request", "access", "feedback")  # what transfer() moves inside Welcome Wave
SECTIONS = (  # key, title, what the transfer does with it (shown under the title)
    ("code", "Code they own", "CODEOWNERS gives these files to them. The successor becomes their owner of record."),
    ("documents", "Documents", "Docs they own, wrote or showed in KT, and anything attached here."),
    ("kt", "KT recordings and explanations", "What they recorded stays searchable. The successor becomes the person to ask."),
    ("sessions", "KT sessions", "Sessions they host, and what to record before they leave."),
    ("people", "Mentees and hires they manage", "Each program, its open tasks and milestones move to the successor."),
    ("questions", "Questions routed to them", "Open questions go to the successor's inbox."),
    ("approvals", "Approvals", "Decisions that wait on them."),
    ("access", "Access to grant", "Access items they still have to grant."),
    ("feedback", "Documentation feedback", "Reader feedback in their queue."),
)
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_HTTPS = re.compile(r"^https://[^\s<>\"']{3,2000}$")
_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


class HandoverError(Exception):
    """A request the handover cannot take as it stands. `status` is the HTTP status the API answers with; `detail` an
    optional body (open gaps)."""

    def __init__(self, status: int, message: str, detail: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.detail = status, message, detail


# ================================================================== draft (the Keeper runs create on handover.requested)
def draft(ctx: Ctx, project_id: str, person: User, last_day: date) -> dict:
    """The checklist the Keeper starts from: files only they hold that KT explains less than half of, the KT planner's
    sessions they present, open questions routed to them, hires they mentor, access items and feedback they own."""
    store, lc = ctx.store, ctx.lc
    project = store.get_project(project_id)
    risk = build_knowledge_risk(store, ctx.index, project.id, project.org_id)
    exposure = next((p["exposure"] for p in risk["people"] if p["user_id"] == person.id), None)
    sessions = build_plan(store, ctx.index, project.id, project.org_id, ctx.user, person.id)["sessions"]
    requests = [r for r in store.expert_requests_for(person.id) if str(r.get("project_id")) == project_id]
    mentees = [p for p in lc.programs(project.org_id, project_id) if p.mentor_id == person.id and not p.completed_at]
    access = [a for p in lc.programs(project.org_id, project_id) for a in lc.access_items(p.onboarding_id)
              if a.owner_id == person.id and a.status == "requested"]
    feedback = [f for f in lc.feedback(project_id) if f.owner_id == person.id and f.status == "open"]
    co_owners = [o["user_id"] for f in risk["files"] if any(x["user_id"] == person.id for x in f["owners"])
                 for o in f["owners"] if o["user_id"] and o["user_id"] != person.id]
    managers = [m for m in store.managers(project_id) if m.id != person.id]
    successor = co_owners[0] if co_owners else (managers[0].id if managers else None)
    items = []
    for path in (exposure or {}).get("orphaned_files", []):
        items.append({"key": f"file:{path}", "kind": "file", "title": f"Explain {path} in a recorded KT session", "ref": path, "done": False})
    for s in sessions:
        items.append({"key": f"session:{s['key']}", "kind": "session", "title": f"Book and record: {s['title']}", "ref": s["key"],
                      "minutes": s.get("est_minutes"), "done": False})
    for r in requests:
        items.append({"key": f"request:{r['id']}", "kind": "request", "title": f"Answer or hand over: {r['question'][:120]}",
                      "ref": str(r["id"]), "done": False})
    for p in mentees:
        items.append({"key": f"mentee:{p.onboarding_id}", "kind": "mentee",
                      "title": f"New mentor for {(ctx.person(store.get_onboarding(p.onboarding_id).new_hire_id) or {}).get('name')}",
                      "ref": p.onboarding_id, "done": False})
    for a in access:
        items.append({"key": f"access:{a.id}", "kind": "access", "title": f"Grant or hand over: {a.system}", "ref": a.id, "done": False})
    for f in feedback:
        items.append({"key": f"feedback:{f.id}", "kind": "feedback", "title": f"Feedback on {f.target_ref[:80]} ({f.kind})", "ref": f.id,
                      "done": False})
    exposure = exposure or {}
    orphaned = exposure.get("orphaned_files", [])
    return {"successor_id": successor, "handover_minutes": exposure.get("handover_minutes", 0.0), "orphaned_files": orphaned,
            "items": items, "attachments": [], "ticked": [],
            "why": [f"{len(orphaned)} files only {person.name} holds, explained less than half in KT sessions.",
                    f"{len(sessions)} KT sessions the planner gives them to present.",
                    "The successor co-owns their code." if co_owners else
                    "The successor is a manager of the project: nobody co-owns their code."]}


def create(ctx: Ctx, project_id: str, person: User, last_day: date) -> Handover:
    h = ctx.lc.add_handover(Handover(id="", org_id=person.org_id, project_id=project_id, user_id=person.id, last_day=last_day,
                                     plan=draft(ctx, project_id, person, last_day), created_by=ctx.user.id, created_at=ctx.clock.now()))
    ctx.store.notify(person.id, "handover", f"A handover plan was started for your last day, {K.fmt_day(last_day)}.",
                     f"/handovers?project={project_id}")
    return h


def update(ctx: Ctx, h: Handover, status: str | None, done: list[str], undone: list[str], successor_id: str | None,
           reassign: bool) -> tuple[Handover, dict | None]:
    """Tick or untick checklist items (any item key of the transfer set: the checklist's own, or a `todo` a person did
    outside Welcome Wave, like a CODEOWNERS change), change the status or the successor, and with `reassign` run the
    transfer to the successor. Returns the handover and, when a transfer ran, what it moved (as transfer()).

    Everything is checked before anything is written: a key that is not an item of this handover, or a successor who may
    not take over (check_successor), is refused and nothing changes."""
    plan = dict(h.plan)
    known = {i["key"] for i in plan.get("items", [])}
    unknown = {k for k in done if k not in known}
    if unknown and unknown - item_keys(ctx, h):
        raise HandoverError(422, "Only items of this handover can be ticked. Reload it and try again.")
    to = None
    if reassign:
        sid = successor_id or plan.get("successor_id")
        if sid:
            to = check_successor(ctx, h, ctx.store.get_user(sid))
    elif successor_id:
        check_successor(ctx, h, ctx.store.get_user(successor_id))
    plan["items"] = [{**i, "done": (i["key"] in done) or (i["done"] and i["key"] not in undone)} for i in plan.get("items", [])]
    ticked = (set(plan.get("ticked", [])) | unknown) - set(undone)
    plan["ticked"] = sorted(ticked)[:MAX_TICKED]
    if successor_id:
        plan["successor_id"] = successor_id
    fields = {"plan": plan}
    if status:
        fields["status"] = status
    h = ctx.lc.update_handover(h.id, **fields)
    moved = None
    if to is not None:
        h, moved = transfer(ctx, h, to)
    return h, moved


SUCCESSOR_ROLES = frozenset({"expert", "manager", "admin"})  # as the API's PERSON_ROLES: who may take work over


def check_successor(ctx: Ctx, h: Handover, to: User | None) -> User:
    """`to` may take over this handover now: someone else in the organisation, active, and still a member of the project who
    can hold the work (not a new hire). Checked on every path that moves work (transfer, update with reassign, complete),
    because the successor stored on the plan (the Keeper's suggestion, or an earlier choice) may have left since."""
    if to is None or to.org_id != h.org_id:
        raise HandoverError(422, "Choose an active successor first")
    if to.id == h.user_id:
        raise HandoverError(422, "The successor must be someone else")
    if to.status != "active":
        raise HandoverError(422, f"{bare_name(to.name)}'s account is deactivated. Choose an active successor.")
    if to.role not in SUCCESSOR_ROLES or not ctx.store.is_member(h.project_id, to.id):
        raise HandoverError(422, f"{bare_name(to.name)} is no longer a member of this project who can take this work over. "
                                 "Choose another successor.")
    return to


# ================================================================== helpers
def github_url(repo, path: str) -> str | None:
    """The file on GitHub at the repository's default branch, or None when the repository is not a GitHub one."""
    if repo is None or not _GITHUB_REPO.match(repo.full_name or ""):
        return None
    from urllib.parse import quote

    return f"https://github.com/{repo.full_name}/blob/{quote(repo.default_branch or 'main', safe='/')}/{quote(path, safe='/')}"


def is_doc(path: str) -> bool:
    return path.lower().endswith(DOC_EXTS)


def bare_name(name: str | None) -> str:
    """'Meera (Senior)' -> 'Meera': what a person types to confirm."""
    return _SUFFIX.sub("", name or "").strip()


def confirms(typed: str, person: User) -> bool:
    t = " ".join((typed or "").split()).casefold()
    return bool(t) and t in {" ".join(person.name.split()).casefold(), " ".join(bare_name(person.name).split()).casefold()}


def _first(name: str | None) -> str:
    return (bare_name(name) or "They").split()[0]


def _code_href(path: str) -> str:
    from urllib.parse import quote

    return f"#/code?path={quote(path, safe='')}"


def _pct(x: float) -> str:
    return f"{round(100 * x)}% explained in KT"


def codeowners_change(text: str, login: str, new_login: str | None) -> list[dict]:
    """The CODEOWNERS lines that name `login`, each with its replacement naming `new_login` instead (None when the
    successor has no GitHub login). A line that would be left with no owner keeps the new one."""
    out = []
    want = login.lower().lstrip("@")
    for raw in (text or "").splitlines():
        body, _, comment = raw.partition("#")
        parts = body.split()
        if len(parts) < 2 or not any(p.lstrip("@").lower() == want for p in parts[1:]):
            continue
        owners = [p for p in parts[1:] if p.lstrip("@").lower() != want]
        after = None
        if new_login:
            if not any(p.lstrip("@").lower() == new_login.lower() for p in owners):
                owners.append(f"@{new_login}")
            after = " ".join([parts[0], *owners]) + (f"  #{comment}" if comment.strip() else "")
        out.append({"before": raw.strip(), "after": after})
    return out


def _item(key: str, title: str, state: str, *, detail: str = "", href: str | None = None, url: str | None = None,
          owner_id: str | None = None, kt: bool = False, gap: str | None = None) -> dict:
    return {"key": key, "title": title, "state": state, "detail": detail, "href": href, "url": url, "owner_id": owner_id,
            "kt": kt, "gap": gap}


# ================================================================== the transfer set
class _Reads:
    """Everything one handover reads, once."""

    def __init__(self, ctx: Ctx, h: Handover):
        store, lc = ctx.store, ctx.lc
        self.ctx, self.h, self.pid, self.plan = ctx, h, h.project_id, h.plan or {}
        self.person = store.get_user(h.user_id)
        self.org_id = h.org_id
        sid = self.plan.get("successor_id")
        self.successor = store.get_user(sid) if sid else None
        transfer = self.plan.get("transfer") or {}
        self.transferred_to = store.get_user(transfer["to"]) if transfer.get("to") else None
        self.moved = {k: set(v) for k, v in (transfer.get("moved") or {}).items()}
        # what a person marked done: ticks, and the drafted checklist's own items they ticked (the transfer also marks the items it
        # moved as done; those are not a person's ticks, and their state is read live)
        self.ticked = set(self.plan.get("ticked", [])) | {i["key"] for i in self.plan.get("items", [])
                                                          if i.get("done") and i.get("kind") not in MOVES}
        for i in self.plan.get("items", []):  # the checklist's own refs: what was on them when the plan was drafted
            if i.get("kind") in MOVES:
                self.moved.setdefault(i["kind"], set()).add(str(i["ref"]))
        repos = store.repos_for(self.pid)
        self.repo = repos[0] if repos else None
        self.codeowners_text = store.codeowners(self.pid)
        self.rules = parse_codeowners(self.codeowners_text)
        self.login = (self.person.github_login or "").lower().lstrip("@") if self.person else ""
        self.paths = ctx.index.all_paths(self.pid)
        self.path_set = set(self.paths)
        self.links = store.kt_links(self.pid)
        self.members = store.members(self.pid)
        self.speakers = K.Speakers(store, self.members)
        self.meetings = store.list_meetings(self.pid)
        self.programs = lc.programs(self.org_id, self.pid)
        self.onboardings = store.onboardings(self.pid)
        self.names: dict[str, str] = {u.id: u.name for u in self.members}

    def name(self, user_id: str | None) -> str:
        if not user_id:
            return "nobody"
        if user_id not in self.names:
            u = self.ctx.store.get_user(user_id)
            self.names[user_id] = u.name if u else "someone who left"
        return bare_name(self.names[user_id])

    @cached_property
    def owned(self) -> dict[str, list[str]]:
        """Indexed paths CODEOWNERS gives to the person (alone or with others): path -> every owner handle, lower case."""
        out = {}
        if self.login:
            for path in self.paths:
                handles = list(dict.fromkeys(o.lower().lstrip("@") for o in owners_for(self.rules, path)))
                if self.login in handles:
                    out[path] = handles
        return out

    @cached_property
    def recordings(self) -> list[tuple]:
        """(meeting, seconds they spoke) for ready recordings they spoke in or booked themselves."""
        out = []
        for m in self.meetings:
            if m.status != "ready":
                continue
            spoke = self.speakers.seconds_by_user([m.id]).get(self.person.id, 0.0)
            if spoke > 0 or m.created_by == self.person.id:
                out.append((m, spoke))
        return out

    @property
    def new_owner(self) -> User | None:
        """Who their code, documents and recordings go to: whoever the transfer moved things to, else the successor."""
        return self.transferred_to or self.successor

    def moves_or_done(self) -> str:
        return "done" if self.transferred_to else "moves"


def transfer_set(ctx: Ctx, h: Handover, keys_only: bool = False):
    """GET /api/handovers/{id}/transfer: every section with its items, the gaps, completeness, the suggested CODEOWNERS
    change and what can be attached. Read-only. With `keys_only`, the set of every item's and gap's key instead (all of
    them, not only the ITEMS_SHOWN a response lists): what a person may tick."""
    r = _Reads(ctx, h)
    if r.person is None:
        raise HandoverError(404, "The person this handover is for is no longer in the organisation")
    code, coverage, modules = _code(r)
    documents = _documents(r)
    kt, stale = _kt(r)
    sessions = _sessions(r)
    people, managed_stuck = _people(r)
    questions = _questions(r)
    approvals, undecided = _approvals(r)
    access = _access(r)
    feedback = _feedback(r)
    by_key = {"code": code, "documents": documents, "kt": kt, "sessions": sessions, "people": people, "questions": questions,
              "approvals": approvals, "access": access, "feedback": feedback}
    sections = []
    for key, title, how in SECTIONS:
        items = sorted(by_key[key], key=lambda i: ({"todo": 0, "moves": 1, "done": 2}[i["state"]], i["title"].lower()))
        sections.append({"key": key, "title": title, "how": how, "count": len(items),
                         "done": sum(i["state"] == "done" for i in items), "moves": sum(i["state"] == "moves" for i in items),
                         "todo": sum(i["state"] == "todo" for i in items), "items": items[:ITEMS_SHOWN],
                         "more": max(0, len(items) - ITEMS_SHOWN)})
    owned_paths = [i["key"].split(":", 1)[1] for i in code]
    new_login = (r.new_owner.github_login or None) if r.new_owner else None
    co_lines = codeowners_change(r.codeowners_text, r.login, new_login) if r.login else []
    gaps = _gaps(r, sections, modules, stale, managed_stuck, undecided, co_lines, owned_paths)
    if keys_only:
        return {i["key"] for items in by_key.values() for i in items} | {g["key"] for g in gaps}
    total = sum(s["count"] for s in sections)
    done = sum(s["done"] for s in sections)
    listed = {i["key"].split(":", 1)[1] for i in documents if i["key"].startswith("doc:")}
    listed |= {a.get("ref") for a in r.plan.get("attachments", []) if a.get("kind") == "repo"}
    attachable_repo = [{"path": p, "url": github_url(r.repo, p)} for p in r.paths if is_doc(p) and p not in listed][:ATTACHABLE_SHOWN]
    attached_docs = {a.get("ref") for a in r.plan.get("attachments", []) if a.get("kind") == "document"}
    uploaded_listed = {i["key"].split(":", 1)[1] for i in documents if i["key"].startswith("upload:")}
    attachable_docs = [{"id": str(d["id"]), "title": d.get("title") or "Untitled document"} for d in ctx.store.documents(r.pid)
                       if str(d["id"]) not in attached_docs and str(d["id"]) not in uploaded_listed][:ATTACHABLE_SHOWN]
    person, owner = r.person, r.new_owner
    return {
        "handover_id": h.id, "status": h.status, "last_day": h.last_day.isoformat(),
        "days_left": (h.last_day - ctx.clock.today()).days,
        "person": {"id": person.id, "name": person.name, "role": person.role, "github_login": person.github_login,
                   "status": person.status},
        "successor": _who(r.successor), "new_owner": _who(owner),
        "transfer": r.plan.get("transfer"), "completed": r.plan.get("completed") if h.status == "done" else None,
        "repository": {"full_name": r.repo.full_name, "branch": r.repo.default_branch,
                       "url": f"https://github.com/{r.repo.full_name}" if _GITHUB_REPO.match(r.repo.full_name or "") else None}
        if r.repo else None,
        "sections": sections, "gaps": gaps,
        "completeness": {"total": total, "done": done, "moves": sum(s["moves"] for s in sections),
                         "todo": sum(s["todo"] for s in sections), "percent": round(100 * done / total) if total else 100},
        "coverage": coverage,
        "codeowners": {"login": person.github_login, "new_login": new_login, "lines": co_lines},
        "attachable": {"repository": attachable_repo, "documents": attachable_docs},
        "can_complete": h.status != "done" and r.successor is not None and r.successor.status == "active",
        "ticked": sorted(r.ticked),  # keys a person marked done: the page offers to undo these
        "note": r.plan.get("note"),  # the Keeper's note to the person leaving
    }


def item_keys(ctx: Ctx, h: Handover) -> set[str]:
    """Every item and gap key of the transfer set: what a person may tick (a gap a person closed outside Welcome Wave, like
    a CODEOWNERS change, is ticked by its key)."""
    try:
        return transfer_set(ctx, h, keys_only=True)
    except HandoverError:
        return set()


def _who(u: User | None) -> dict | None:
    return {"id": u.id, "name": u.name, "role": u.role, "github_login": u.github_login} if u else None


def _code(r: _Reads) -> tuple[list[dict], dict, dict[str, dict]]:
    """Their CODEOWNERS files (docs go to the documents section), and per module how much KT explains what they own."""
    view = K.code_view(r.ctx.index, r.pid)
    states = K.span_states(view.files, r.links)
    lines: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # path -> [total, recorded, stale]
    for path, spans in view.files.items():
        for s in spans:
            st = states[s].state
            row = lines[path]
            row[0] += s.lines
            row[1] += s.lines if st == "recorded" else 0
            row[2] += s.lines if st == "stale" else 0
    items: list[dict] = []
    modules: dict[str, dict] = {}
    totals = [0, 0, 0]
    if not r.login:
        return items, {"lines": 0, "recorded_share": None, "github_login": None}, modules
    first = _first(r.person.name)
    owner = r.new_owner
    for path, handles in r.owned.items():
        if is_doc(path):
            continue
        alone = handles == [r.login]
        share = None
        if path in lines:
            t, rec, st = lines[path]
            share = K.recorded_share(rec, st, t)
            for i, v in enumerate((t, rec, st)):
                totals[i] += v
            if alone:
                m = modules.setdefault(module_of(path), {"files": 0, "lines": 0, "explained": 0})
                m["files"] += 1
                m["lines"] += t
                m["explained"] += rec + st
        others = [h for h in handles if h != r.login]
        key = f"code:{path}"
        ticked = key in r.ticked or f"file:{path}" in r.ticked
        if not alone:
            state, detail, to = "done", f"Also owned by {', '.join('@' + h for h in others[:3])}", None
        elif share is not None and share < ORPHAN_SHARE and not ticked:
            state, detail, to = "todo", f"Only {first} · {_pct(share)}: record a KT session before they leave", owner.id if owner else None
        else:
            state = r.moves_or_done()
            detail = f"Only {first}" + (f" · {_pct(share)}" if share is not None else "")
            to = owner.id if owner else None
        items.append(_item(key, path, state, detail=detail, href=_code_href(path), url=github_url(r.repo, path), owner_id=to,
                           kt=bool(share)))
    coverage = {"lines": totals[0], "recorded_share": round(K.recorded_share(totals[1], totals[2], totals[0]), 2) if totals[0] else None,
                "github_login": r.person.github_login}
    return items, coverage, modules


def _documents(r: _Reads) -> list[dict]:
    store, lc = r.ctx.store, r.ctx.lc
    owner = r.new_owner
    to = owner.id if owner else None
    first = _first(r.person.name)
    items: dict[str, dict] = {}
    for path, handles in r.owned.items():
        if is_doc(path):
            if handles == [r.login]:
                items[f"doc:{path}"] = _item(f"doc:{path}", path, r.moves_or_done(), detail=f"Only {first} owns it",
                                             href=_code_href(path), url=github_url(r.repo, path), owner_id=to)
            else:
                others = [h for h in handles if h != r.login]
                items[f"doc:{path}"] = _item(f"doc:{path}", path, "done", detail=f"Also owned by {', '.join('@' + h for h in others[:3])}",
                                             href=_code_href(path), url=github_url(r.repo, path))
    # repository docs their KT sessions showed or explained
    for m, _ in r.recordings:
        notes = r.speakers.notes(m.id)
        seen = {p for sm in (notes.screen_moments if notes else []) for p in sm.visible_paths}
        seen |= {lk.file_path for lk in r.links if lk.meeting_id == m.id}
        for path in sorted(p for p in seen if p in r.path_set and is_doc(p)):
            items.setdefault(f"doc:{path}", _item(f"doc:{path}", path, "done", detail=f"Shown in {K.short_title(m.title)}",
                                                  href=_code_href(path), url=github_url(r.repo, path), kt=True))
    for d in store.documents(r.pid):
        if d.get("uploaded_by") and str(d["uploaded_by"]) == r.person.id:
            did = str(d["id"])
            items[f"upload:{did}"] = _item(f"upload:{did}", d.get("title") or "Untitled document", r.moves_or_done(),
                                           detail=f"Uploaded by {first}", href=f"#/documents/{did}", owner_id=to)
    for e in lc.doc_edits(r.pid):
        if e.owner_id == r.person.id and e.status == "suggested":
            href = _code_href(e.target_ref) if e.target_kind == "code" else (f"#/documents/{e.target_ref}" if e.target_kind == "doc" else None)
            items[f"edit:{e.id}"] = _item(f"edit:{e.id}", e.title[:160], r.moves_or_done(),
                                          detail="Suggested wording for them to apply in the repository", href=href, owner_id=to)
    for a in r.plan.get("attachments", []):
        who = a.get("owner_id") or to
        href = (_code_href(a["ref"]) if a["kind"] == "repo" else f"#/documents/{a['ref']}" if a["kind"] == "document" else None)
        detail = f"Attached by {r.name(a.get('added_by'))}" + (f" · {a['note']}" if a.get("note") else "")
        items[f"attach:{a['id']}"] = _item(f"attach:{a['id']}", a.get("title") or a.get("ref") or a.get("url") or "Attachment", "done",
                                           detail=detail, href=href, url=a.get("url"), owner_id=who)
    return list(items.values())


def _kt(r: _Reads) -> tuple[list[dict], int]:
    owner = r.new_owner
    to = owner.id if owner else None
    items: list[dict] = []
    for m, spoke in r.recordings:
        files = {lk.file_path for lk in r.links if lk.meeting_id == m.id and lk.confidence >= K.MIN_CONFIDENCE}
        detail = (f"{max(1, round(spoke / 60))} min of them speaking" if spoke else "Booked by them") + (f" · {len(files)} files" if files else "")
        items.append(_item(f"rec:{m.id}", m.title, r.moves_or_done(), detail=detail, href=f"#/meetings/{m.id}", owner_id=to, kt=True))
    # the explanations they gave: KT links whose clip they were speaking in
    fresh = {(lk.file_path, lk.symbol) for lk in K.counted(r.links) if not lk.code_changed_since}
    seen: set[tuple] = set()
    stale = 0
    for lk in sorted(K.counted(r.links), key=lambda x: (x.file_path, x.symbol or "", x.start_s)):
        _, who = r.speakers.for_link(lk)
        if who is None or who.id != r.person.id:
            continue
        k = (lk.meeting_id, lk.file_path, lk.symbol)
        if k in seen:
            continue
        seen.add(k)
        where = f"{lk.file_path}{' · ' + lk.symbol if lk.symbol else ''}"
        key = f"clip:{lk.meeting_id}:{lk.file_path}:{lk.symbol or ''}"
        href = f"#/meetings/{lk.meeting_id}?t={int(lk.start_s)}"
        if lk.code_changed_since and (lk.file_path, lk.symbol) not in fresh and key not in r.ticked:
            stale += 1
            items.append(_item(key, where, "todo", detail=f"The code changed since {K.short_title(lk.meeting_title)}: re-record it before "
                                                         f"{K.fmt_day(r.h.last_day)}", href=href, kt=True, owner_id=to, gap="stale"))
        else:
            items.append(_item(key, where, r.moves_or_done(), detail=f"Explained in {K.short_title(lk.meeting_title)} at {K.fmt_ts(lk.start_s)}",
                               href=href, kt=True, owner_id=to))
    return items, stale


def _sessions(r: _Reads) -> list[dict]:
    items: list[dict] = []
    last = r.h.last_day
    first = _first(r.person.name)
    booked: dict[str, str] = {}
    for m in r.meetings:
        if m.kt_key and m.status in {"scheduled", "live", "processing", "ready"}:
            booked.setdefault(m.kt_key, m.status)
        if m.status not in {"scheduled", "live"}:
            continue
        presenter = m.attendees[0].user_id if (m.kt_key and m.attendees) else None
        if m.created_by != r.person.id and presenter != r.person.id:
            continue
        starts = m.starts_at or m.started_at
        key = f"host:{m.id}"
        when = K.fmt_day(starts.date()) if starts else "no date set"
        if starts is not None and starts.date() > last and key not in r.ticked:
            items.append(_item(key, m.title, "todo", detail=f"{first} hosts it on {when}, after their last day: rebook it",
                               href=f"#/meetings/{m.id}", gap="hosted_after"))
        else:
            items.append(_item(key, m.title, "done", detail=f"{first} hosts it on {when}", href=f"#/meetings/{m.id}"))
    planned = r.plan.get("kt_sessions")
    if planned is None:  # a plan from before the Keeper: the KT planner's sessions for them, live
        try:
            planned = build_plan(r.ctx.store, r.ctx.index, r.pid, r.org_id, r.ctx.user, r.person.id)["sessions"]
        except Exception:  # noqa: BLE001 - the planner's view is context, not the plan
            planned = []
    for s in planned:
        key = f"session:{s['key']}"
        minutes = s.get("est_minutes")
        size = f"about {round(minutes)} min" if minutes else "a short session"
        if booked.get(s["key"]) or key in r.ticked:
            state, detail = "done", f"{'Recorded' if booked.get(s['key']) == 'ready' else 'Booked' if booked.get(s['key']) else 'Done'} · {size}"
        else:
            state, detail = "todo", f"Book and record before {K.fmt_day(last)} · {size}"
        items.append(_item(key, s["title"], state, detail=detail, href=f"#/kt-planner?session={s['key']}", kt=state == "done" and booked.get(s["key"]) == "ready",
                           gap="session" if state == "todo" else None))
    return items


def _hire_work(r: _Reads, onboarding_id: str) -> str:
    tasks = [t for t in r.ctx.store.tasks(onboarding_id=onboarding_id) if t.status not in CLOSED_TASKS]
    left = [m for m in r.ctx.lc.milestones(onboarding_id) if m.status == "pending"]
    nxt = min(left, key=lambda m: m.due_day) if left else None
    parts = [f"{len(tasks)} open task{'s' if len(tasks) != 1 else ''}", f"{len(left)} milestone{'s' if len(left) != 1 else ''} to go"]
    if nxt:
        parts.append(f"next: {nxt.title} (day {nxt.due_day})")
    return " · ".join(parts)


def _manager_target(r: _Reads) -> User | None:
    """Who takes over the hires they manage: the successor when they may manage, else another manager of the project."""
    to = r.new_owner
    if to is not None and to.role in CAN_MANAGE and to.status == "active":
        return to
    return next((m for m in r.ctx.store.managers(r.pid) if m.id != r.person.id and m.status == "active"), None)


def _people(r: _Reads) -> tuple[list[dict], int]:
    items: list[dict] = []
    obs = {o.id: o for o in r.ctx.store.onboardings(r.pid, active_only=False)}
    progs = {p.onboarding_id: p for p in r.programs}
    owner = r.new_owner

    def hire_name(oid: str) -> str:
        ob = obs.get(oid)
        return r.name(ob.new_hire_id) if ob else "A hire"

    for oid in sorted({p.onboarding_id for p in r.programs if p.mentor_id == r.person.id and not p.completed_at}
                      | r.moved.get("mentee", set())):
        p = progs.get(oid)
        if p is None or (p.completed_at and p.mentor_id == r.person.id):
            continue
        name = hire_name(oid)
        if p.mentor_id == r.person.id:
            items.append(_item(f"mentee:{oid}", f"Mentor of {name}", "moves", detail=_hire_work(r, oid), href=f"#/program/{oid}",
                               owner_id=owner.id if owner else None))
        else:
            items.append(_item(f"mentee:{oid}", f"Mentor of {name}", "done", detail=f"Now mentored by {r.name(p.mentor_id)} · {_hire_work(r, oid)}",
                               href=f"#/program/{oid}", owner_id=p.mentor_id))
    target = _manager_target(r)
    stuck = 0
    for oid in sorted({o.id for o in r.onboardings if o.manager_id == r.person.id} | r.moved.get("managed", set())):
        ob = obs.get(oid)
        if ob is None or ob.status != "active":
            continue
        name = r.name(ob.new_hire_id)
        if ob.manager_id == r.person.id:
            if target is None:
                stuck += 1
                items.append(_item(f"managed:{oid}", f"Manager of {name}", "todo", detail="Nobody else on the project can manage this hire: "
                                   "add a manager to the project", href=f"#/program/{oid}", gap="managed"))
            else:
                items.append(_item(f"managed:{oid}", f"Manager of {name}", "moves", detail=f"Moves to {bare_name(target.name)} · {_hire_work(r, oid)}",
                                   href=f"#/program/{oid}", owner_id=target.id))
        else:
            items.append(_item(f"managed:{oid}", f"Manager of {name}", "done", detail=f"Now managed by {r.name(ob.manager_id)}",
                               href=f"#/program/{oid}", owner_id=ob.manager_id))
    for ob in r.onboardings:  # the person is themselves a hire on this project
        if ob.new_hire_id == r.person.id and ob.status == "active":
            key = f"own:{ob.id}"
            items.append(_item(key, "Their own onboarding program", "done" if key in r.ticked else "todo",
                               detail=f"{_hire_work(r, ob.id)}: close or pause it", href=f"#/program/{ob.id}"))
    return items, stuck


def _questions(r: _Reads) -> list[dict]:
    store = r.ctx.store
    owner = r.new_owner
    items: list[dict] = []
    open_now = {str(q["id"]): q for q in store.expert_requests_for(r.person.id) if str(q.get("project_id")) == str(r.pid)}
    for rid, q in open_now.items():
        asked = q.get("created_at")
        items.append(_item(f"request:{rid}", (q.get("question") or "A question")[:160], "moves",
                           detail=f"Asked by {bare_name(q.get('asked_by_name')) or 'a hire'}" + (f" on {K.fmt_day(asked)}" if hasattr(asked, "strftime") else ""),
                           owner_id=owner.id if owner else None))
    for rid in sorted(r.moved.get("request", set()) - set(open_now)):
        q = store.get_expert_request(rid)
        if not q or str(q.get("project_id")) != str(r.pid):
            continue
        detail = "Answered" if q.get("status") != "open" else f"Now with {r.name(str(q.get('expert_id')))}"
        items.append(_item(f"request:{rid}", (q.get("question") or "A question")[:160], "done", detail=detail,
                           owner_id=str(q.get("expert_id")) if q.get("expert_id") else None))
    return items


def _approvals(r: _Reads) -> tuple[list[dict], int]:
    store = r.ctx.store
    pending = store.approvals(r.pid, "pending")
    items: list[dict] = []
    first = _first(r.person.name)
    for a in pending:
        p = a.payload or {}
        if a.kind == "rerecord" and (p.get("speaker") or {}).get("id") == r.person.id:
            what = f"Re-record {p.get('meeting_title') or 'a KT session'}"
        elif a.kind == "doc_edit" and p.get("owner_id") == r.person.id:
            what = f"Suggested wording for {str(p.get('title') or 'a document')[:120]}"
        else:
            continue
        key = f"approval:{a.id}"
        items.append(_item(key, what, "done" if key in r.ticked else "todo", detail=f"Waits on a decision that names {first}",
                           href=f"#/approvals/{a.id}", gap="approval"))
    undecided = 0
    managers = store.managers(r.pid)
    if pending and r.person.role in CAN_MANAGE and any(m.id == r.person.id for m in managers):
        others = [m for m in managers if m.id != r.person.id and m.status == "active"]
        title = f"{len(pending)} pending approval{'s' if len(pending) != 1 else ''} they can decide"
        if others:
            items.append(_item("approvals:decide", title, "done", detail=f"{bare_name(others[0].name)} decides them too", href="#/approvals"))
        else:
            undecided = len(pending)
            items.append(_item("approvals:decide", title, "done" if "approvals:decide" in r.ticked else "todo",
                               detail="Nobody else on this project decides approvals: add a manager", href="#/approvals", gap="approvals"))
    return items, undecided


def _access(r: _Reads) -> list[dict]:
    lc = r.ctx.lc
    owner = r.new_owner
    items: list[dict] = []
    seen: set[str] = set()
    for p in r.programs:
        for a in lc.access_items(p.onboarding_id):
            if a.owner_id == r.person.id and a.status == "requested":
                seen.add(a.id)
                items.append(_item(f"access:{a.id}", a.system, "moves", detail=f"For {r.name(_hire_of(r, p.onboarding_id))}",
                                   href=f"#/program/{p.onboarding_id}", owner_id=owner.id if owner else None))
    for aid in sorted(r.moved.get("access", set()) - seen):
        a = lc.get_access_item(aid)
        if a is None:
            continue
        detail = "Granted" if a.status == "granted" else ("Not needed" if a.status == "not_needed" else f"Now with {r.name(a.owner_id)}")
        items.append(_item(f"access:{aid}", a.system, "done", detail=f"{detail} · for {r.name(_hire_of(r, a.onboarding_id))}",
                           href=f"#/program/{a.onboarding_id}", owner_id=a.owner_id))
    return items


def _hire_of(r: _Reads, onboarding_id: str) -> str | None:
    ob = r.ctx.store.get_onboarding(onboarding_id)
    return ob.new_hire_id if ob else None


def _feedback(r: _Reads) -> list[dict]:
    lc = r.ctx.lc
    owner = r.new_owner
    items: list[dict] = []
    seen: set[str] = set()
    for f in lc.feedback(r.pid):
        if f.owner_id == r.person.id and f.status == "open":
            seen.add(f.id)
            items.append(_item(f"feedback:{f.id}", f"{f.target_ref[:120]} ({f.kind})", "moves", detail=(f.note or "")[:160],
                               href=f"#/feedback?project={r.pid}", owner_id=owner.id if owner else None))
    for fid in sorted(r.moved.get("feedback", set()) - seen):
        f = lc.get_feedback(fid)
        if f is None:
            continue
        detail = f"Now with {r.name(f.owner_id)}" if f.status == "open" else f.status.capitalize()
        items.append(_item(f"feedback:{fid}", f"{f.target_ref[:120]} ({f.kind})", "done", detail=detail,
                           href=f"#/feedback?project={r.pid}", owner_id=f.owner_id))
    return items


def _gaps(r: _Reads, sections: list[dict], modules: dict[str, dict], stale: int, managed_stuck: int, undecided: int,
          co_lines: list[dict], owned_paths: list[str]) -> list[dict]:
    """What would leave the successor starting from scratch, most important first. Each says what closes it."""
    first = _first(r.person.name)
    count = {s["key"]: s for s in sections}
    gaps = []

    def gap(key: str, title: str, detail: str, *, severity: str = "warning", href: str | None = None, action: str | None = None, n: int = 1):
        gaps.append({"key": key, "title": title, "detail": detail, "severity": severity, "href": href, "action": action, "count": n})

    if r.new_owner is None:
        holds = sum(s["count"] for s in sections)
        gap("successor", "Nobody takes over yet", f"Choose a successor: {holds} item{'s' if holds != 1 else ''} would have no new owner.",
            severity="blocker", action="successor")
    elif r.new_owner.status != "active":
        gap("successor", "The successor's account is deactivated", "Choose an active successor.", severity="blocker", action="successor")
    for mod, m in sorted(modules.items()):
        if m["lines"] and not m["explained"] and "module:" + mod not in r.ticked:
            gap(f"module:{mod}", f"{mod}/ has no recorded KT session",
                f"{m['files']} file{'s' if m['files'] != 1 else ''} only {first} owns, and no recording explains any of it.",
                href="#/kt-planner", action="plan", n=m["files"])
    code = count["code"]
    orphaned = sum(1 for i in code["items"] if i["state"] == "todo")
    if orphaned:
        gap("orphaned", f"{orphaned} file{'s' if orphaned != 1 else ''} only {first} holds",
            "Explained less than half in recorded KT. Record a session, or tick a file once it is explained.", href="#/knowledge-risk",
            action="plan", n=orphaned)
    if stale:
        gap("stale", f"{stale} explanation{'s' if stale != 1 else ''} out of date", f"The code changed since {first} explained it. "
            f"Re-record before {K.fmt_day(r.h.last_day)}.", href="#/kt-planner", action="plan", n=stale)
    sessions = sum(1 for i in count["sessions"]["items"] if i.get("gap") == "session")
    if sessions:
        gap("sessions", f"{sessions} KT session{'s' if sessions != 1 else ''} still to book", f"Planned for {first} to record before "
            f"{K.fmt_day(r.h.last_day)}.", href="#/kt-planner", action="plan", n=sessions)
    after = sum(1 for i in count["sessions"]["items"] if i.get("gap") == "hosted_after")
    if after:
        gap("hosted_after", f"{after} session{'s' if after != 1 else ''} after their last day", "They host it: rebook it with the successor.",
            href="#/meetings", n=after)
    if co_lines and "codeowners" not in r.ticked:
        gap("codeowners", f"CODEOWNERS still names @{r.person.github_login}",
            f"{len(co_lines)} line{'s' if len(co_lines) != 1 else ''} to change in the repository. Welcome Wave never writes to it: "
            "copy the change into a pull request.", action="codeowners", n=len(co_lines))
    if owned_paths and not count["documents"]["count"]:
        gap("no_docs", "No document for the code they own", f"Nothing written down covers what {first} owns. Attach the design notes, "
            "runbooks or READMEs the successor needs.", action="attach")
    if managed_stuck:
        gap("managed", f"{managed_stuck} hire{'s' if managed_stuck != 1 else ''} would have no manager",
            "Add another manager to the project.", severity="blocker", n=managed_stuck)
    if undecided and "approvals:decide" not in r.ticked:
        gap("approvals", f"{undecided} approval{'s' if undecided != 1 else ''} nobody else decides", "Add another manager to the project.",
            href="#/approvals", n=undecided)
    named = sum(1 for i in count["approvals"]["items"] if i.get("gap") == "approval" and i["state"] == "todo")
    if named:
        gap("approval_named", f"{named} decision{'s' if named != 1 else ''} name{'s' if named == 1 else ''} {first}",
            "Decide them, or reject them so the successor gets a fresh proposal.", href="#/approvals", n=named)
    own = sum(1 for i in count["people"]["items"] if i["key"].startswith("own:") and i["state"] == "todo")
    if own:
        gap("own_program", "Their own onboarding is still open", "Close or pause it on their program page.", n=own)
    return gaps


# ================================================================== transfer
def transfer(ctx: Ctx, h: Handover, to: User) -> tuple[Handover, dict]:
    """Move what Welcome Wave holds to `to`: mentees, hires they manage (to `to` when they may manage, else another manager
    of the project), open questions routed to them, access items and feedback they own. `to` becomes the owner of record
    of their code, documents and recordings. Idempotent: run again, it moves only what is still on the person."""
    if h.status == "done":
        raise HandoverError(409, "This handover is complete. Reopen it to change it.")
    store, lc = ctx.store, ctx.lc
    person = store.get_user(h.user_id)
    if person is None:
        raise HandoverError(404, "The person this handover is for is no longer in the organisation")
    check_successor(ctx, h, to)
    pid = h.project_id
    plan = dict(h.plan)
    r = _Reads(ctx, h)
    moved: dict[str, list[str]] = {k: [] for k in MOVES}
    for p in r.programs:
        if p.mentor_id == person.id and not p.completed_at:
            lc.update_program(p.onboarding_id, mentor_id=to.id)
            moved["mentee"].append(p.onboarding_id)
    r.transferred_to = to
    manager = _manager_target(r)
    if manager is not None:
        for ob in r.onboardings:
            if ob.manager_id == person.id and ob.status == "active" and ob.new_hire_id != manager.id:
                lc.set_onboarding_manager(ob.id, manager.id)
                moved["managed"].append(ob.id)
    for q in store.expert_requests_for(person.id):
        if str(q.get("project_id")) == str(pid) and lc.reassign_expert_request(str(q["id"]), to.id):
            moved["request"].append(str(q["id"]))
    for p in r.programs:
        for a in lc.access_items(p.onboarding_id):
            if a.owner_id == person.id and a.status == "requested":
                lc.update_access_item(a.id, owner_id=to.id)
                moved["access"].append(a.id)
    for f in lc.feedback(pid):
        if f.owner_id == person.id and f.status == "open":
            lc.update_feedback(f.id, owner_id=to.id)
            moved["feedback"].append(f.id)
    before = (plan.get("transfer") or {}).get("moved") or {}
    plan["transfer"] = {"to": to.id, "by": ctx.user.id if ctx.user else None, "at": ctx.clock.now().isoformat(),
                        "moved": {k: sorted(set(before.get(k, [])) | set(moved[k])) for k in MOVES}}
    plan["successor_id"] = to.id
    plan["reassigned_to"] = to.id
    done_refs = {(k, ref) for k, refs in moved.items() for ref in refs}
    plan["items"] = [{**i, "done": True} if (i.get("kind"), str(i.get("ref"))) in done_refs else i for i in plan.get("items", [])]
    h = lc.update_handover(h.id, plan=plan, **({"status": "in_progress"} if h.status == "draft" else {}))
    counts = {k: len(v) for k, v in moved.items()}
    name = bare_name(person.name)
    parts = [f"{n} {label}" for label, n in (("mentees", counts["mentee"]), ("hires to manage", counts["managed"]),
                                              ("open questions", counts["request"]), ("access items", counts["access"]),
                                              ("feedback items", counts["feedback"])) if n]
    store.notify(to.id, "handover", f"You take over from {name}: " + (", ".join(parts) if parts else "their code, documents and KT recordings")
                 + ".", f"/handovers?project={pid}")
    if manager is not None and manager.id != to.id and counts["managed"]:
        store.notify(manager.id, "handover", f"You now manage {counts['managed']} hire(s) {name} managed.", f"/handovers?project={pid}")
    for oid in moved["mentee"]:
        ob = store.get_onboarding(oid)
        if ob:
            store.notify(ob.new_hire_id, "handover", f"{bare_name(to.name)} is your mentor now: {name} handed over before leaving.",
                         f"/program/{oid}")
    if counts["request"]:
        store.notify(to.id, "expert_request", f"{counts['request']} question(s) {name} had open are in your inbox.", f"/experts?project={pid}")
    return h, counts


# ================================================================== attachments
def add_attachment(ctx: Ctx, h: Handover, kind: str, ref: str | None, url: str | None, title: str | None, note: str | None,
                   owner_id: str | None) -> tuple[Handover, dict]:
    """Attach a document the successor needs: a file of the indexed repository (with its GitHub link), a document uploaded
    to the project, or any https link. Who owns it next defaults to the successor."""
    if h.status == "done":
        raise HandoverError(409, "This handover is complete. Reopen it to change it.")
    plan = dict(h.plan)
    attachments = list(plan.get("attachments", []))
    if len(attachments) >= MAX_ATTACHMENTS:
        raise HandoverError(409, f"A handover holds at most {MAX_ATTACHMENTS} attachments")
    title = " ".join((title or "").split())[:200] or None
    note = " ".join((note or "").split())[:500] or None
    already = HandoverError(409, "That document is already in the handover: they own or uploaded it")
    if kind == "repo":
        path = (ref or "").strip().lstrip("/")
        if not path or path not in set(ctx.index.all_paths(h.project_id)):
            raise HandoverError(422, "That file is not in the project's indexed repository. Check the path, e.g. docs/CONTRIBUTING.md")
        person = ctx.store.get_user(h.user_id)
        login = (person.github_login or "").lower().lstrip("@") if person else ""
        if login and is_doc(path) and login in {o.lower().lstrip("@") for o in owners_for(parse_codeowners(ctx.store.codeowners(h.project_id)), path)}:
            raise already
        repos = ctx.store.repos_for(h.project_id)
        item = {"kind": "repo", "ref": path, "title": title or path, "url": github_url(repos[0] if repos else None, path)}
    elif kind == "document":
        doc = ctx.store.get_document(ref or "") if ref else None
        if not doc or str(doc.get("project_id")) != str(h.project_id):
            raise HandoverError(422, "That document is not in this project")
        if doc.get("uploaded_by") and str(doc["uploaded_by"]) == h.user_id:
            raise already
        item = {"kind": "document", "ref": str(doc["id"]), "title": title or doc.get("title") or "Untitled document", "url": None}
    else:
        link = (url or "").strip()
        if not _HTTPS.match(link):
            raise HandoverError(422, "Links start with https://")
        if not title:
            raise HandoverError(422, "Give the link a title")
        item = {"kind": "link", "ref": None, "title": title, "url": link}
    same = (item["kind"], item["ref"] or item["url"])
    if any((a["kind"], a.get("ref") or a.get("url")) == same for a in attachments):
        raise HandoverError(409, "That document is already attached")
    item.update(id=secrets.token_hex(6), note=note, owner_id=owner_id, added_by=ctx.user.id if ctx.user else None,
                added_at=ctx.clock.now().isoformat())
    attachments.append(item)
    plan["attachments"] = attachments
    return ctx.lc.update_handover(h.id, plan=plan), item


def remove_attachment(ctx: Ctx, h: Handover, attachment_id: str) -> tuple[Handover, dict]:
    if h.status == "done":
        raise HandoverError(409, "This handover is complete. Reopen it to change it.")
    plan = dict(h.plan)
    attachments = list(plan.get("attachments", []))
    gone = next((a for a in attachments if a.get("id") == attachment_id), None)
    if gone is None:
        raise HandoverError(404, "Attachment not found")
    plan["attachments"] = [a for a in attachments if a.get("id") != attachment_id]
    return ctx.lc.update_handover(h.id, plan=plan), gone


# ================================================================== completion
def held_elsewhere(ctx: Ctx, h: Handover, person: User) -> list[str]:
    """The names of the organisation's other projects where the person still holds work a deactivated account would strand:
    active hires they manage or mentor, open questions routed to them, access items they still have to grant, feedback in
    their queue. Deactivating them is refused while any is left (each project gets its own handover)."""
    store, lc = ctx.store, ctx.lc
    open_questions = {str(q.get("project_id")) for q in store.expert_requests_for(person.id)}
    out = []
    for p in store.projects_for(ctx.user if ctx.user and ctx.user.org_id == h.org_id else person):
        if p.org_id != h.org_id or p.id == h.project_id:
            continue
        programs = [x for x in lc.programs(h.org_id, p.id) if not x.completed_at]
        active = {o.id for o in store.onboardings(p.id)}
        holds = (any(o.manager_id == person.id for o in store.onboardings(p.id))
                 or any(x.mentor_id == person.id and x.onboarding_id in active for x in programs)
                 or str(p.id) in open_questions
                 or any(a.owner_id == person.id and a.status == "requested" for x in programs for a in lc.access_items(x.onboarding_id))
                 or any(f.owner_id == person.id and f.status == "open" for f in lc.feedback(p.id)))
        if holds:
            out.append(p.name)
    return out


def complete(ctx: Ctx, h: Handover, accept_gaps: bool, deactivate: bool) -> dict:
    """Finish the handover after a person confirmed it (the API checks who may, and the typed name). Moves whatever is still
    on the person, records the open gaps that were accepted, and with `deactivate` deactivates the leaver's account."""
    if h.status == "done":
        raise HandoverError(409, "This handover is already complete")
    store = ctx.store
    person = store.get_user(h.user_id)
    if person is None:
        raise HandoverError(404, "The person this handover is for is no longer in the organisation")
    sid = (h.plan or {}).get("successor_id")
    to = check_successor(ctx, h, store.get_user(sid) if sid else None)
    report = transfer_set(ctx, h)
    open_gaps = [g for g in report["gaps"] if g["key"] != "successor"]
    if open_gaps and not accept_gaps:
        raise HandoverError(409, f"{len(open_gaps)} gap{'s are' if len(open_gaps) != 1 else ' is'} still open. Close them, or accept them "
                                 "to complete anyway.", {"code": "open_gaps", "gaps": open_gaps})
    if deactivate:
        if ctx.user and person.id == ctx.user.id:
            raise HandoverError(409, "You cannot deactivate your own account. Ask another admin.")
        others = [x for x in ctx.lc.handovers(h.org_id) if x.user_id == person.id and x.id != h.id and x.status != "done"]
        if others:
            raise HandoverError(409, "Another handover for them is still open. Complete it before deactivating their account.")
        elsewhere = held_elsewhere(ctx, h, person)
        if elsewhere:
            names = ", ".join(elsewhere[:3]) + (f" and {len(elsewhere) - 3} more" if len(elsewhere) > 3 else "")
            raise HandoverError(409, f"{bare_name(person.name)} still holds work in {names}. Start a handover there first, or "
                                     "complete this one without deactivating their account.",
                                {"code": "held_elsewhere", "projects": elsewhere})
        if person.role == "admin" and person.status == "active":
            admins = [m for m in store.org_members(h.org_id) if m.get("role") == "admin" and m.get("status") == "active" and m["id"] != person.id]
            if not admins:
                raise HandoverError(409, "They are the organisation's last active admin. Make someone else an admin first.")
    h, moved = transfer(ctx, h, to)
    deactivated = False
    if deactivate and person.status != "deactivated":
        try:
            deactivated = store.set_member_status(h.org_id, person.id, "deactivated") is not None
        except LastAdmin:
            raise HandoverError(409, "They are the organisation's last active admin. Make someone else an admin first.") from None
    plan = dict(h.plan)
    plan["completed"] = {"by": ctx.user.id if ctx.user else None, "at": ctx.clock.now().isoformat(), "to": to.id,
                         "accepted_gaps": [{"key": g["key"], "title": g["title"]} for g in open_gaps], "deactivated": deactivated,
                         "percent": report["completeness"]["percent"]}
    h = ctx.lc.update_handover(h.id, plan=plan, status="done")
    name = bare_name(person.name)
    store.notify(to.id, "handover", f"{name}'s handover is complete. What they held is yours now.", f"/handovers?project={h.project_id}")
    if not deactivated:
        store.notify(person.id, "handover", "Your handover is complete. Thank you for handing everything over.", f"/handovers?project={h.project_id}")
    return {"handover": h, "moved": moved, "deactivated": deactivated, "accepted_gaps": plan["completed"]["accepted_gaps"]}
