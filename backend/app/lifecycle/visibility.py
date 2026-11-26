"""What anyone can see about a hire, from one registry.

Every lifecycle payload about a hire that a manager or mentor reads is built here, from FIELDS, by `view(hc, keys)`. The
hire's own GET /api/onboardings/{id}/visibility returns *every* field of the registry, with who sees it, where it comes
from and how it is worked out, through the same builders: whatever a manager can read about a hire, the hire can read
too, with the same values. tests/test_visibility.py fails if a manager-facing endpoint returns a field this payload lacks.

Audiences are named exactly: the hire, their mentor, the project's managers, and where a datum goes to someone else, that
person: `expert` (whoever a question was routed to, often the mentor), `owner` (whoever owns the document or file a note is
about) and `admin` (the organisation's admins, who alone read the audit log).

Two things are kept from managers on purpose and say so: what a pre-submit review found (the hire's alone: no file, finding
or step of it is stored; that one ran, for whom and when, is recorded, and listed here under `private_reviews`) and survey
answers (anonymous, shown only in aggregate for groups of five or more). A plan waiting for approval shows as a count
until it is approved, as GET /api/tasks shows it."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from app.adapters import audit_store
from app.lifecycle import blockers as B
from app.lifecycle import checkins, progress
from app.lifecycle.context import HireCtx
from app.lifecycle.enrolment import plan_status
from app.services.dashboard import expert_answered, unanswered

HIDDEN_FROM_HIRE = frozenset({"proposed", "rejected"})  # the GET /tasks rule: a hire never sees an unapproved plan
MANAGER, MENTOR, HIRE = "manager", "mentor", "hire"
EXPERT, OWNER, ADMIN = "expert", "owner", "admin"
AUDIENCES = frozenset({HIRE, MENTOR, MANAGER, EXPERT, OWNER, ADMIN})
AUDIT_LATEST = 50  # entries of the hire's own actions shown


@dataclass(frozen=True)
class HireField:
    label: str
    visible_to: tuple[str, ...]
    source: str
    how: str
    build: Callable[[HireCtx], Any]


def _scout():
    from app.agents.specialists.scout import agent as scout  # the agents import the lifecycle services: imported when first used

    return scout


def _planner():
    from app.agents.specialists.planner import agent as planner

    return planner


def _profile(hc: HireCtx) -> dict:
    ctx, ob, p = hc.ctx, hc.ob, hc.program
    t = ctx.lc.get_template(p.template_id)
    team = ctx.lc.get_team(p.team_id) if p.team_id else None
    return {"onboarding_id": ob.id, "project_id": ob.project_id, "hire": ctx.person(ob.new_hire_id), "manager": ctx.person(ob.manager_id),
            "mentor": ctx.person(p.mentor_id), "team": {"id": team.id, "name": team.name} if team else None, "role_title": ob.role_title,
            "start_date": ob.start_date, "day": hc.day, "duration_days": p.duration_days, "status": ob.status, "focus_area": p.focus_area,
            "template": {"id": t.id, "key": t.key, "version": p.template_version, "title": t.title} if t else None}


def _plan(hc: HireCtx) -> dict:
    s = plan_status(hc.ctx, hc.ob, hc.program)
    return {**s, "proposed_tasks": sum(t.status == "proposed" for t in hc.tasks)}


def _plan_items(hc: HireCtx) -> list[dict]:
    return [{"id": t.id, "title": t.title, "kind": t.kind, "status": t.status, "due_date": t.due_date, "origin": t.origin,
             "pr_url": t.pr_url, "issue": t.github_issue_number} for t in hc.tasks if t.status not in HIDDEN_FROM_HIRE]


def _access(hc: HireCtx) -> list[dict]:
    return [{"id": a.id, "system": a.system, "owner": hc.ctx.person(a.owner_id), "status": a.status, "requested_at": a.requested_at,
             "granted_at": a.granted_at} for a in hc.access]


def _status(hc: HireCtx) -> dict:
    ms = progress.milestone_view(hc)
    overdue = [m["title"] for m in ms if m["state"] == "overdue"]
    nxt = next((m for m in ms if m["status"] == "pending"), None)
    open_alerts = hc.ctx.lc.blockers(hc.ob.id, open_only=True)
    due = checkins.state(hc)["due"]
    plan = plan_status(hc.ctx, hc.ob, hc.program)["status"]
    on_track = not overdue and not any(a.stage == "manager" for a in open_alerts)
    why = ([f"{len(overdue)} milestone{'s' if len(overdue) != 1 else ''} overdue."] if overdue else []) + \
          ([f"{len(open_alerts)} open blocker{'s' if len(open_alerts) != 1 else ''}."] if open_alerts else [])
    return {"on_track": on_track, "overdue_milestones": overdue, "open_blockers": len(open_alerts),
            "next_milestone": {"key": nxt["key"], "title": nxt["title"], "due_date": nxt["due_date"]} if nxt else None,
            "checkins_due": due, "plan": plan, "why": why or ["Nothing overdue, no open blockers."]}


def _questions(hc: HireCtx) -> dict:
    turns = hc.chats
    return {"asked": len(turns), "answered": sum(t.get("status") == "answered" for t in turns),
            "answered_by_expert": sum(expert_answered(t) for t in turns), "unanswered": sum(unanswered(t) for t in turns)}


def _requests_sent(hc: HireCtx) -> list[dict]:
    """Questions the hire sent to a person: in full, with the name of who they went to. This is what that person's inbox shows."""
    return [{"id": str(r["id"]), "question": r["question"], "routed_to": hc.ctx.person(r["expert_id"]), "status": r["status"],
             "asked_at": r["created_at"], "answered_at": r.get("answered_at"), "ai_draft_attached": bool(r.get("draft"))}
            for r in hc.ctx.store.expert_requests_by(hc.ob.project_id, hc.ob.new_hire_id)]


def _reviews(hc: HireCtx) -> list[dict]:
    """Reviews of the hire's pull requests (findings counted; the review itself is at GET /api/reviews/{id}) and the manager's
    decision on each."""
    titles = {t.id: t.title for t in hc.tasks}
    return [{"id": r["id"], "task_id": r["task_id"], "task": titles.get(r["task_id"]), "head_sha": (r.get("head_sha") or "")[:7],
             "recommendation": r["recommendation"], "findings": len(r.get("findings") or []), "decision": r.get("decision"),
             "created_at": r["created_at"]} for r in hc.ctx.lc.reviews_for_tasks(list(titles))]


def _feedback_sent(hc: HireCtx) -> list[dict]:
    """The notes the hire left on documentation, code, KT calls and answers, with the owner each went to."""
    return [{"id": f.id, "target_kind": f.target_kind, "target_ref": f.target_ref, "kind": f.kind, "note": f.note, "status": f.status,
             "owner": hc.ctx.person(f.owner_id), "created_at": f.created_at}
            for f in hc.ctx.lc.feedback(hc.ob.project_id) if f.created_by == hc.ob.new_hire_id]


def _audit_trail(hc: HireCtx) -> dict:
    """What the audit log holds about the hire's own actions (their questions routed, check-ins written, areas recorded,
    documents acknowledged, ...): the newest AUDIT_LATEST entries and how many there are in all."""
    rows, total = audit_store.page(hc.ctx.store, audit_store.AuditFilter(org_id=hc.program.org_id, actor=hc.ob.new_hire_id), AUDIT_LATEST, 0)
    return {"total": total, "latest": [{"at": r["created_at"], "action": r["action"], "target": r["target"], "detail": r.get("detail") or {}}
                                       for r in rows]}


def _acceptances(hc: HireCtx) -> list[dict]:
    """What is kept each time the hire accepted a legal document: which, which version, when, how, the network the click
    came from (never the full address) and the browser's description. Admins read the same in the organisation's export."""
    rows = hc.ctx.store.legal_acceptances(hc.program.org_id, user_id=hc.ob.new_hire_id)
    return [{"document": r["document"], "version": r["version"], "accepted_at": r["accepted_at"], "method": r.get("method"),
             "network": r.get("ip_prefix"), "browser": r.get("user_agent")} for r in rows]


def _removal_requests(hc: HireCtx) -> list[dict]:
    """The hire's requests to have a KT session's recording removed, with the reason they wrote and the outcome."""
    from app.adapters import recording_store

    rows = recording_store.removal_requests(hc.ctx.store, project_id=hc.ob.project_id)
    return [{"meeting_id": r["meeting_id"], "reason": r.get("reason") or "", "status": r["status"], "note": r.get("note") or "",
             "created_at": r["created_at"], "decided_at": r.get("decided_at"), "decided_by": hc.ctx.person(r.get("decided_by"))}
            for r in rows if str(r.get("requested_by")) == hc.ob.new_hire_id]


def _private_reviews(hc: HireCtx) -> list[dict]:
    """The run records of the hire's own pre-submit reviews: when each ran and how it ended, the only things kept of one (the
    project's run list shows managers the same records)."""
    runs = hc.ctx.store.list_runs(hc.ob.project_id, 500)
    mine = [r for r in runs if r.get("trigger") == "presubmit.requested" and str((_detail(r)).get("user_id") or "") == hc.ob.new_hire_id]
    return [{"at": r.get("at"), "status": r.get("status")} for r in mine]


def _assistant_runs(hc: HireCtx) -> list[dict]:
    """The run records the assistant leaves for each question the hire asks it (the Guide's runs): when, and the summary our
    code wrote (how it was answered). Managers read these records, and the sources each run used, in the project's run list;
    the question's words are never in them."""
    runs = hc.ctx.store.list_runs(hc.ob.project_id, 500)
    mine = [r for r in runs if r.get("agent") == "guide" and str(_detail(r).get("user_id") or "") == hc.ob.new_hire_id]
    return [{"at": r.get("at"), "status": r.get("status"), "summary": _detail(r).get("summary")} for r in mine]


def _detail(run: dict) -> dict:
    d = run.get("detail") or {}
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            return {}
    return d if isinstance(d, dict) else {}


FIELDS: dict[str, HireField] = {
    "profile": HireField("Who, when and with whom", (HIRE, MENTOR, MANAGER), "onboardings, onboarding_programs",
                         "Your start date, role, team, manager, mentor and template; day is days since your start date.", _profile),
    "plan": HireField("Your plan's approval", (HIRE, MENTOR, MANAGER), "approvals (task_plan)",
                      "Read from the plan approvals in your manager's Approvals; proposals awaiting approval are counted.", _plan),
    "milestones": HireField("Milestones", (HIRE, MENTOR, MANAGER), "onboarding_milestones",
                            "From your program's template; some are marked done automatically, with the evidence that did it.",
                            progress.milestone_view),
    "plan_items": HireField("Plan items (tasks)", (HIRE, MENTOR, MANAGER), "tasks", "Your approved tasks and their status.", _plan_items),
    "access": HireField("Access requests", (HIRE, MENTOR, MANAGER), "access_items",
                        "What was requested for you, from whom, and when it was granted.", _access),
    "acknowledgements": HireField("Documents acknowledged", (HIRE, MENTOR, MANAGER), "acknowledgements",
                                  "When you acknowledged each document (the address is stored only as a keyed hash).",
                                  lambda hc: [{"doc_key": a.doc_key, "title": a.title,
                                               "acknowledged_at": a.acknowledged_at} for a in hc.acknowledgements]),
    "environment_checks": HireField("Environment checks", (HIRE, MENTOR, MANAGER), "environment_checks",
                                    "What the environment check script reported: tool names, versions and pass or fail.",
                                    lambda hc: [{"ran_at": e.ran_at, "ok": e.ok, "results": e.results} for e in hc.environment_checks]),
    "preboarding": HireField("Pre-boarding link", (HIRE, MENTOR, MANAGER), "onboarding_programs",
                             "Whether a link is live and when it expires (the link itself is never stored).",
                             lambda hc: {"expires_at": hc.program.preboarding_expires_at}),
    "first_task": HireField("Your first task's brief", (HIRE, MENTOR, MANAGER), "onboarding_programs.first_task",
                            "The task your manager selected from the shortlist, and why.", lambda hc: hc.program.first_task),
    "first_task_shortlist": HireField("First-task shortlist", (HIRE, MANAGER), "issues, tasks, KT links, CODEOWNERS",
                                      "Candidates scored on focus, explanation, size, tests and quiet files, with reasons (the Scout).",
                                      lambda hc: _scout().shortlist_view(hc)),
    "areas": HireField("Progress by area", (HIRE, MENTOR, MANAGER), "tasks, KT links, chat, area_progress",
                       "Levels from kinds of evidence (steps, clips watched, questions answered, merged changes); no scores, no ranking.",
                       progress.areas),
    "blockers": HireField("Blockers", (HIRE, MENTOR, MANAGER), "blocker_alerts",
                          "Rules over your tasks, questions (counted, not quoted), CI, environment checks and reviews, with what each saw.",
                          lambda hc: B.view(hc, hc.ctx.lc.blockers(hc.ob.id))),
    "summaries": HireField("Weekly summaries", (HIRE, MANAGER), "progress_summaries",
                           "Counted from records, each line citing its source.",
                           lambda hc: [s.model_dump() for s in hc.ctx.lc.summaries(hc.ob.id)]),
    "checkins": HireField("Check-ins", (HIRE, MANAGER), "checkin_reviews", "Your answers and your manager's, at day 30, 60 and 90.",
                          checkins.state),
    "completion": HireField("Completion review", (HIRE, MANAGER), "milestones, check-ins, blockers, areas, access items",
                            "Each criterion with its evidence, the Planner's drafted recommendation, and the manager's decision when "
                            "made.", lambda hc: _planner().completion_view(hc)),
    "status": HireField("On track", (HIRE, MENTOR, MANAGER), "milestones, blockers, check-ins",
                        "On track: no overdue milestone and no blocker that reached your manager.", _status),
    "questions": HireField("Questions you asked (counts)", (HIRE,), "chat_messages, expert_requests",
                           "How many questions you asked, how many were answered from sources, how many a person answered "
                           "after the assistant handed them on, and how many still wait for an answer. Only you see these "
                           "counts; your manager and mentor never see them or your words.", _questions),
    "export": HireField("Your conversations, in the organisation's export", (HIRE, ADMIN), "chat_messages, notifications",
                        "Your organisation's admins can export all of its data, as the party responsible for it. That export "
                        "holds your conversations with the assistant in full, and your notifications. Each export is audited.",
                        lambda hc: {"conversations": len(hc.chats)}),
    "assistant_runs": HireField("That the assistant answered you (run records)", (HIRE, MANAGER), "agent_runs",
                                "Each question you ask the assistant leaves a run record: that it answered a question for you, "
                                "when, how (from sources, or handed to a person) and which sources it used. Your managers can "
                                "read these records in the project's run list. Your words are not in them.", _assistant_runs),
    "requests": HireField("Questions you sent to a person", (HIRE, EXPERT), "expert_requests",
                          "A question you asked a person, or that the assistant handed on, is read by that person in full, with your "
                          "name, in their inbox (and in their daily digest). Often that is your mentor; it can be a code owner.",
                          _requests_sent),
    "reviews": HireField("Reviews of your pull requests", (HIRE, MENTOR, MANAGER), "reviews",
                         "The Reviewer's findings on each pull request (counted here), and your manager's decision on the review.",
                         _reviews),
    "feedback": HireField("Documentation feedback you left", (HIRE, MANAGER, OWNER), "documentation_feedback",
                          "Each note you left on a document, file, KT call or answer, with who owns it. Managers read all of a "
                          "project's feedback; the owner reads the notes about what they own.", _feedback_sent),
    "private_reviews": HireField("Private reviews you ran (when only)", (HIRE, MANAGER), "agent_runs",
                                 "Each pre-submit review of your branch leaves a record that one ran, for you, and when, and "
                                 "whether it finished: no file, finding or step of it. Your managers can see these records in the "
                                 "project's run list; the review itself is shown only to you.", _private_reviews),
    "removal_requests": HireField("Recording removal requests you made", (HIRE, MANAGER), "recording_removal_requests",
                                  "Each time you asked for a KT session's recording to be removed: the session, the reason you "
                                  "wrote, and what a manager decided. The project's managers and your organisation's admins "
                                  "read them.", _removal_requests),
    "acceptances": HireField("Records of what you accepted", (HIRE, ADMIN), "legal_acceptances",
                             "Each time you accepted the terms, the privacy policy or the recording notice: the document, its "
                             "version, when and how, the network your click came from (the first part of the address, never all "
                             "of it) and your browser's description. Your organisation's admins read them in its export.",
                             _acceptances),
    "audit": HireField("The audit trail of your actions", (HIRE, ADMIN), "audit_log",
                       f"Your own actions as the organisation's audit log records them (the newest {AUDIT_LATEST}). Only the "
                       "organisation's admins read the log; reading it is audited too.", _audit_trail),
}

PROGRAM = ("profile", "plan", "milestones", "plan_items", "access", "acknowledgements", "environment_checks", "preboarding", "first_task")
CARD = ("profile", "status")


def view(hc: HireCtx, keys) -> dict:
    return {k: FIELDS[k].build(hc) for k in keys}


def visibility(hc: HireCtx) -> dict:
    return {"onboarding_id": hc.ob.id, "hire": hc.ctx.person(hc.ob.new_hire_id),
            "statement": "Everything your manager, your mentor, the people who answer your questions and your organisation's admins "
                         "can see about you in Welcome Wave is listed here, with who sees each part and the same values.",
            "private": ["What your pre-submit reviews find is yours alone: no file, finding or step of one is stored or shown to "
                        "anyone else. That a review ran, and when, is recorded (see 'Private reviews you ran').",
                        "Survey answers carry no name and are shown only as averages of five or more people.",
                        "A plan waiting for approval shows as a count until it is approved.",
                        "The KT planner and knowledge risk count new hires' questions towards the code they are about, as "
                        "'asked 3 times by new hires': never your name, never your words.",
                        "What you ask the assistant is not shown to your manager or mentor. A question handed on to a person "
                        "is read by that person in full, with your name (see 'Questions you sent to a person'). Managers see "
                        "that the assistant answered you, when, and from which sources, never your words (see 'That the "
                        "assistant answered you'). Your organisation's admins can export all of its data, your conversations "
                        "included (see 'Your conversations, in the organisation's export')."],
            "data": {k: {"label": f.label, "visible_to": list(f.visible_to), "source": f.source, "how": f.how, "value": f.build(hc)}
                     for k, f in FIELDS.items()}}
