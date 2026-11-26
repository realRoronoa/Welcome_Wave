"""In-memory LifecycleStore (demo and tests). It sits next to the core MemoryStore and reads a few of its tables (the
onboardings, expert requests, tasks and reviews) where the lifecycle needs facts the Store protocol does not offer.

Every write announces the topic 'lifecycle' to the live hub (app/live.py), as Postgres triggers do in production."""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Callable

from app.adapters.memory import new_id
from app.ports import (TRANSCRIPT_SUFFIX, AccessItem, Acknowledgement, AreaProgress, BlockerAlert, CheckinReview, DocEdit, DocFeedback, EnvironmentCheck,
                       ExpertPreferences, Handover, MetricEvent, Milestone, OnboardingProgram, OrgSettings, Phase, ProgramTemplate,
                       ProgressSummary, SurveyResponse, TaskMove, TaskPlacement, Team, User, VerifiedAnswer)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryLifecycleStore:
    def __init__(self, core, announce: Callable[[dict], None] | None = None):
        self.core = core  # the MemoryStore (or the LiveStore wrapping it)
        self.announce = announce
        self.settings: dict[str, OrgSettings] = {}
        self.team_rows: dict[str, Team] = {}
        self.template_rows: dict[str, ProgramTemplate] = {}
        self.program_rows: dict[str, OnboardingProgram] = {}
        self.tokens: dict[str, str] = {}  # token hash -> onboarding id
        self.milestone_rows: dict[str, list[Milestone]] = defaultdict(list)
        self.access_rows: dict[str, AccessItem] = {}
        self.ack_rows: dict[str, list[Acknowledgement]] = defaultdict(list)
        self.env_rows: dict[str, list[EnvironmentCheck]] = defaultdict(list)
        self.area_rows: dict[tuple[str, str], AreaProgress] = {}
        self.blocker_rows: dict[str, BlockerAlert] = {}
        self.summary_rows: dict[tuple[str, object], ProgressSummary] = {}
        self.checkin_rows: dict[tuple[str, int, str], CheckinReview] = {}
        self.feedback_rows: dict[str, DocFeedback] = {}
        self.answer_rows: dict[str, VerifiedAnswer] = {}
        self.prefs: dict[str, ExpertPreferences] = {}
        self.survey_rows: list[SurveyResponse] = []
        self.handover_rows: dict[str, Handover] = {}
        self.metric_rows: list[MetricEvent] = []
        self.edit_rows: dict[str, DocEdit] = {}
        self.review_seen: dict[str, datetime] = {}  # review id -> first read: the memory store keeps no review timestamps
        self.phase_rows: dict[str, list[Phase]] = defaultdict(list)  # the board's milestones, per onboarding
        self.placement_rows: dict[str, TaskPlacement] = {}  # task id -> where a person put it
        self.move_rows: list[TaskMove] = []
        self._lock = threading.RLock()

    def _changed(self, project_id: str | None = None) -> None:
        if self.announce:
            self.announce({"t": "lifecycle", "p": project_id})

    def _org_of(self, onboarding_id: str) -> str | None:
        ob = self.core.get_onboarding(onboarding_id)
        return self.core.org_of_project(ob.project_id) if ob else None

    # ---- organisation settings and teams ----
    def org_settings(self, org_id):
        return self.settings.get(org_id) or OrgSettings(org_id=org_id)

    def update_org_settings(self, org_id, **fields):
        with self._lock:
            self.settings[org_id] = self.org_settings(org_id).model_copy(update=fields)
        self._changed()
        return self.settings[org_id]

    def teams(self, org_id, project_id=None):
        return [t for t in self.team_rows.values() if t.org_id == org_id and (project_id is None or t.project_id == project_id)]

    def get_team(self, team_id):
        return self.team_rows.get(team_id)

    def create_team(self, org_id, project_id, name):
        t = Team(id=new_id(), org_id=org_id, project_id=project_id, name=name)
        self.team_rows[t.id] = t
        self._changed(project_id)
        return t

    def update_team(self, team_id, **fields):
        t = self.team_rows.get(team_id)
        if not t:
            return None
        self.team_rows[team_id] = t = t.model_copy(update=fields)
        self._changed(t.project_id)
        return t

    # ---- templates ----
    def templates(self, org_id, include_inactive=False):
        rows = [t for t in self.template_rows.values() if t.org_id == org_id and (include_inactive or t.active)]
        return sorted(rows, key=lambda t: (t.role_track, t.level, t.key, -t.version))

    def get_template(self, template_id):
        return self.template_rows.get(template_id)

    def create_template(self, template):
        with self._lock:
            if any(t.org_id == template.org_id and t.key == template.key for t in self.template_rows.values()):
                raise ValueError(f"template key {template.key!r} exists")  # Postgres: UNIQUE (org_id, key, version)
            t = template.model_copy(update={"id": template.id or new_id(), "version": 1, "active": True})
            self.template_rows[t.id] = t
        self._changed()
        return t

    def new_template_version(self, template_id, **changes):
        with self._lock:
            old = self.template_rows.get(template_id)
            if not old or not old.active:
                return None
            self.template_rows[old.id] = old.model_copy(update={"active": False})
            new = old.model_copy(update={**changes, "id": new_id(), "version": old.version + 1, "active": True, "created_at": _now()})
            self.template_rows[new.id] = new
        self._changed()
        return new

    # ---- programs ----
    def create_program(self, program, milestones, access, acknowledgements):
        with self._lock:
            if program.onboarding_id in self.program_rows:
                return self.program_rows[program.onboarding_id]
            self.program_rows[program.onboarding_id] = program
            self.milestone_rows[program.onboarding_id] = list(milestones)
            for a in access:
                a = a.model_copy(update={"id": a.id or new_id()})
                self.access_rows[a.id] = a
            self.ack_rows[program.onboarding_id] = list(acknowledgements)
        self._changed(program.project_id)
        return program

    def get_program(self, onboarding_id):
        return self.program_rows.get(onboarding_id)

    def programs(self, org_id, project_id=None):
        return [p for p in self.program_rows.values() if p.org_id == org_id and (project_id is None or p.project_id == project_id)]

    def update_program(self, onboarding_id, **fields):
        with self._lock:
            p = self.program_rows[onboarding_id] = self.program_rows[onboarding_id].model_copy(update=fields)
        self._changed(p.project_id)

    def set_preboarding(self, onboarding_id, token_hash, expires_at):
        with self._lock:
            self.tokens = {h: o for h, o in self.tokens.items() if o != onboarding_id}  # a new link replaces the old one
            self.tokens[token_hash] = onboarding_id
            self.update_program(onboarding_id, preboarding_expires_at=expires_at)

    def program_by_token(self, token_hash):
        oid = self.tokens.get(token_hash)
        p = self.program_rows.get(oid) if oid else None
        return (p.onboarding_id, p.org_id, p.preboarding_expires_at) if p and p.preboarding_expires_at else None

    def ensure_user(self, org_id, email, name, github_login):
        with self._lock:
            found = next((u for u in self.core.users.values() if u.org_id == org_id and u.email.lower() == email.lower()), None)
            if found:
                return found
            user = self.core.add_user(User(id=new_id(), org_id=org_id, email=email.lower(), name=name, role="new_hire",
                                           github_login=github_login))
            self.core.left.add(user.id)  # no login bound until they accept their invitation, like a row with no firebase_uid
            return user

    def set_onboarding_status(self, onboarding_id, status):
        ob = self.core.get_onboarding(onboarding_id)
        if ob:
            self.core.onboard[onboarding_id] = ob.model_copy(update={"status": status})
            self._changed(ob.project_id)

    # ---- timeline ----
    def milestones(self, onboarding_id):
        return sorted(self.milestone_rows.get(onboarding_id, []), key=lambda m: (m.due_day, m.key))

    def set_milestones(self, onboarding_id, milestones):
        with self._lock:
            self.milestone_rows[onboarding_id] = [m.model_copy(update={"onboarding_id": onboarding_id}) for m in milestones]
        self._changed()

    def update_milestone(self, onboarding_id, key, **fields):
        with self._lock:
            rows = self.milestone_rows.get(onboarding_id, [])
            for i, m in enumerate(rows):
                if m.key == key:
                    rows[i] = m.model_copy(update=fields)
                    self._changed()
                    return rows[i]
        return None

    # ---- the milestone board ----
    def phases(self, onboarding_id):
        return sorted(self.phase_rows.get(onboarding_id, []), key=lambda p: (p.position, p.due_day, p.key))

    def ensure_phases(self, onboarding_id, phases):
        with self._lock:
            if not self.phase_rows.get(onboarding_id):
                self.phase_rows[onboarding_id] = [p.model_copy(update={"onboarding_id": onboarding_id}) for p in phases]
                self._changed()
        return self.phases(onboarding_id)

    def add_phase(self, phase):
        with self._lock:
            rows = self.phase_rows[phase.onboarding_id]
            if any(p.key == phase.key for p in rows):
                return None  # Postgres: UNIQUE (onboarding_id, key)
            rows.append(phase)
        self._changed()
        return phase

    def update_phase(self, onboarding_id, key, **fields):
        with self._lock:
            rows = self.phase_rows.get(onboarding_id, [])
            for i, p in enumerate(rows):
                if p.key == key:
                    rows[i] = p.model_copy(update=fields)
                    self._changed()
                    return rows[i]
        return None

    def delete_phase(self, onboarding_id, key, move_to):
        with self._lock:
            rows = self.phase_rows.get(onboarding_id, [])
            if not any(p.key == key for p in rows):
                return False
            self.phase_rows[onboarding_id] = [p for p in rows if p.key != key]
            for tid, row in list(self.placement_rows.items()):
                if row.onboarding_id == onboarding_id and row.phase_key == key:
                    self.placement_rows[tid] = row.model_copy(update={"phase_key": move_to, "position": None, "updated_at": _now()})
        self._changed()
        return True

    def placements(self, onboarding_id):
        return [r for r in self.placement_rows.values() if r.onboarding_id == onboarding_id]

    def save_placements(self, onboarding_id, rows):
        with self._lock:
            for r in rows:
                self.placement_rows[r.task_id] = r.model_copy(update={"onboarding_id": onboarding_id})
        if rows:
            self._changed()

    def add_task_move(self, move):
        with self._lock:
            move = move.model_copy(update={"id": move.id or new_id()})
            self.move_rows.append(move)
        self._changed()
        return move

    def task_moves(self, onboarding_id):
        return sorted((m for m in self.move_rows if m.onboarding_id == onboarding_id), key=lambda m: m.at)

    def access_items(self, onboarding_id):
        return sorted((a for a in self.access_rows.values() if a.onboarding_id == onboarding_id), key=lambda a: (a.requested_at, a.system))

    def get_access_item(self, item_id):
        return self.access_rows.get(item_id)

    def add_access_item(self, item):
        item = item.model_copy(update={"id": item.id or new_id()})
        self.access_rows[item.id] = item
        self._changed()
        return item

    def update_access_item(self, item_id, **fields):
        a = self.access_rows.get(item_id)
        if not a:
            return None
        self.access_rows[item_id] = a = a.model_copy(update=fields)
        self._changed()
        return a

    def acknowledgements(self, onboarding_id):
        return list(self.ack_rows.get(onboarding_id, []))

    def acknowledge(self, onboarding_id, doc_key, ip_hash, at):
        with self._lock:
            rows = self.ack_rows.get(onboarding_id, [])
            for i, a in enumerate(rows):
                if a.doc_key == doc_key:
                    if not a.acknowledged_at:
                        rows[i] = a.model_copy(update={"acknowledged_at": at})
                        self._changed()
                    return rows[i]
        return None

    def environment_checks(self, onboarding_id):
        return sorted(self.env_rows.get(onboarding_id, []), key=lambda e: e.ran_at)

    def add_environment_check(self, check):
        check = check.model_copy(update={"id": check.id or new_id()})
        self.env_rows[check.onboarding_id].append(check)
        self._changed()
        return check

    # ---- progress, blockers, summaries, check-ins ----
    def area_progress(self, onboarding_id):
        return sorted((a for (o, _), a in self.area_rows.items() if o == onboarding_id), key=lambda a: a.area)

    def save_area_progress(self, row):
        self.area_rows[(row.onboarding_id, row.area)] = row
        self._changed()
        return row

    def blockers(self, onboarding_id, open_only=False):
        rows = [b for b in self.blocker_rows.values() if b.onboarding_id == onboarding_id and (not open_only or b.resolved_at is None)]
        return sorted(rows, key=lambda b: b.opened_at)

    def get_blocker(self, blocker_id):
        return self.blocker_rows.get(blocker_id)

    def open_blocker(self, alert):
        with self._lock:
            key = (alert.onboarding_id, alert.rule, alert.subject)
            found = next((b for b in self.blocker_rows.values()
                          if (b.onboarding_id, b.rule, b.subject) == key and b.resolved_at is None), None)
            if found:
                return found
            alert = alert.model_copy(update={"id": alert.id or new_id()})
            self.blocker_rows[alert.id] = alert
        self._changed()
        return alert

    def update_blocker(self, blocker_id, **fields):
        b = self.blocker_rows.get(blocker_id)
        if not b:
            return None
        self.blocker_rows[blocker_id] = b = b.model_copy(update=fields)
        self._changed()
        return b

    def summaries(self, onboarding_id):
        return sorted((s for (o, _), s in self.summary_rows.items() if o == onboarding_id), key=lambda s: s.week_start, reverse=True)

    def save_summary(self, summary):
        key = (summary.onboarding_id, summary.week_start)
        old = self.summary_rows.get(key)
        summary = summary.model_copy(update={"id": old.id if old else (summary.id or new_id())})
        self.summary_rows[key] = summary
        self._changed()
        return summary

    def checkins(self, onboarding_id):
        return sorted((c for (o, _, _), c in self.checkin_rows.items() if o == onboarding_id), key=lambda c: (c.day, c.author_role))

    def save_checkin(self, checkin):
        key = (checkin.onboarding_id, checkin.day, checkin.author_role)
        old = self.checkin_rows.get(key)
        checkin = checkin.model_copy(update={"id": old.id if old else (checkin.id or new_id())})
        self.checkin_rows[key] = checkin
        self._changed()
        return checkin

    # ---- feedback, verified answers, expert preferences ----
    def add_feedback(self, feedback):
        feedback = feedback.model_copy(update={"id": feedback.id or new_id()})
        self.feedback_rows[feedback.id] = feedback
        self._changed(feedback.project_id)
        return feedback

    def get_feedback(self, feedback_id):
        return self.feedback_rows.get(feedback_id)

    def feedback(self, project_id):
        return sorted((f for f in self.feedback_rows.values() if f.project_id == project_id), key=lambda f: f.created_at, reverse=True)

    def update_feedback(self, feedback_id, **fields):
        f = self.feedback_rows.get(feedback_id)
        if not f:
            return None
        self.feedback_rows[feedback_id] = f = f.model_copy(update=fields)
        self._changed(f.project_id)
        return f

    def add_verified_answer(self, answer):
        answer = answer.model_copy(update={"id": answer.id or new_id()})
        self.answer_rows[answer.id] = answer
        self._changed(answer.project_id)
        return answer

    def verified_answers(self, project_id):
        return sorted((a for a in self.answer_rows.values() if a.project_id == project_id), key=lambda a: a.created_at, reverse=True)

    def bump_reuse(self, answer_id):
        with self._lock:
            a = self.answer_rows.get(answer_id)
            if a:
                self.answer_rows[answer_id] = a.model_copy(update={"reuse_count": a.reuse_count + 1})

    def answered_requests(self, project_id):
        return [{"id": r["id"], "question": r["question"], "answer": r["answer"], "answered_at": r.get("answered_at")}
                for r in self.core.expert_requests.values() if str(r["project_id"]) == str(project_id) and r["status"] == "answered"]

    def expert_preferences(self, user_id):
        return self.prefs.get(user_id) or ExpertPreferences(user_id=user_id)

    def save_expert_preferences(self, org_id, prefs):
        self.prefs[prefs.user_id] = prefs
        return prefs

    # ---- surveys, handovers, metrics ----
    def claim_survey_day(self, onboarding_id, day):
        with self._lock:
            p = self.program_rows[onboarding_id]
            if day in p.survey_days:
                return False
            self.program_rows[onboarding_id] = p.model_copy(update={"survey_days": sorted({*p.survey_days, day})})
        self._changed(p.project_id)
        return True

    def add_survey_response(self, response):
        self.survey_rows.append(response.model_copy(update={"id": response.id or new_id()}))

    def survey_responses(self, org_id):
        return [r for r in self.survey_rows if r.org_id == org_id]

    def handovers(self, org_id):
        return sorted((h for h in self.handover_rows.values() if h.org_id == org_id), key=lambda h: h.last_day)

    def get_handover(self, handover_id):
        return self.handover_rows.get(handover_id)

    def add_handover(self, handover):
        handover = handover.model_copy(update={"id": handover.id or new_id()})
        self.handover_rows[handover.id] = handover
        self._changed(handover.project_id)
        return handover

    def update_handover(self, handover_id, **fields):
        h = self.handover_rows.get(handover_id)
        if not h:
            return None
        self.handover_rows[handover_id] = h = h.model_copy(update=fields)
        self._changed(h.project_id)
        return h

    def set_onboarding_manager(self, onboarding_id, manager_id):
        ob = self.core.get_onboarding(onboarding_id)
        if ob:
            self.core.onboard[onboarding_id] = ob.model_copy(update={"manager_id": manager_id})
            self.core.project_members[ob.project_id].add(manager_id)
            self._changed(ob.project_id)

    def reassign_expert_request(self, request_id, expert_id):
        with self._lock:
            r = self.core.expert_requests.get(request_id)
            if not r or r.get("status") != "open":
                return False
            r["expert_id"] = expert_id
        self._changed(str(r.get("project_id")))
        return True

    def record_metric(self, org_id, project_id, kind, subject_id=None, props=None, at=None):
        self.metric_rows.append(MetricEvent(id=new_id(), org_id=org_id, project_id=project_id, kind=kind, subject_id=subject_id,
                                            at=at or _now(), props=props or {}))

    def metrics(self, org_id, kinds, since=None):
        return [m for m in self.metric_rows if m.org_id == org_id and m.kind in kinds and (since is None or m.at >= since)]

    # ---- approved doc edits, organisations for the daily tick ----
    def add_doc_edit(self, edit):
        with self._lock:
            if edit.approval_id and any(e.approval_id == edit.approval_id for e in self.edit_rows.values()):
                return next(e for e in self.edit_rows.values() if e.approval_id == edit.approval_id)  # one per approval
            edit = edit.model_copy(update={"id": edit.id or new_id()})
            self.edit_rows[edit.id] = edit
        self._changed(edit.project_id)
        return edit

    def doc_edits(self, project_id, target_kind=None, target_ref=None):
        rows = [e for e in self.edit_rows.values() if e.project_id == project_id and (target_kind is None or e.target_kind == target_kind)
                and (target_ref is None or e.target_ref == target_ref)]
        return sorted(rows, key=lambda e: e.created_at, reverse=True)

    def lifecycle_orgs(self):
        return sorted({p.org_id for p in self.program_rows.values() if p.completed_at is None})

    # ---- agent-owned tables ----
    def task_activity(self, task_ids):
        """The memory store keeps no update times: a task's creation stands in for its last change."""
        return {t: self.core.task_created[t] for t in task_ids if t in self.core.task_created}

    def reviews_for_tasks(self, task_ids):
        wanted, out = set(task_ids), []
        for r in self.core.review_rows.values():
            if r.task_id in wanted:
                seen = self.review_seen.setdefault(r.id, _now())
                out.append({"id": r.id, "task_id": r.task_id, "head_sha": r.head_sha, "recommendation": r.result.recommendation,
                            "findings": [f.model_dump() for f in r.result.findings], "decision": r.manager_decision, "created_at": seen})
        return out

    # ---- the organisation's data ----
    def _org_scope(self, org_id: str) -> dict:
        core = self.core
        projects = {p for p, proj in core.projects.items() if proj.org_id == org_id}
        users = {u for u, user in core.users.items() if user.org_id == org_id}
        obs = {o for o, ob in core.onboard.items() if ob.project_id in projects}
        tasks = {t for t, row in core.task_rows.items() if row.onboarding_id in obs}
        meetings = {m for m, row in core.meetings.items() if row.project_id in projects}
        return {"projects": projects, "users": users, "onboardings": obs, "tasks": tasks, "meetings": meetings}

    def _lifecycle_tables(self, org_id: str, scope: dict) -> dict[str, list]:
        obs, projects = scope["onboardings"], scope["projects"]
        mine = lambda rows: [r for r in rows if r.onboarding_id in obs]  # noqa: E731
        return {
            "org_settings": [s for s in self.settings.values() if s.org_id == org_id],
            "teams": [t for t in self.team_rows.values() if t.org_id == org_id],
            "program_templates": [t for t in self.template_rows.values() if t.org_id == org_id],
            "onboarding_programs": [p for p in self.program_rows.values() if p.org_id == org_id],
            "onboarding_milestones": mine(m for rows in self.milestone_rows.values() for m in rows),
            "access_items": mine(self.access_rows.values()),
            "acknowledgements": mine(a for rows in self.ack_rows.values() for a in rows),
            "environment_checks": mine(e for rows in self.env_rows.values() for e in rows),
            "area_progress": mine(self.area_rows.values()),
            "blocker_alerts": mine(self.blocker_rows.values()),
            "progress_summaries": mine(self.summary_rows.values()),
            "checkin_reviews": mine(self.checkin_rows.values()),
            "survey_responses": [s for s in self.survey_rows if s.org_id == org_id],
            "documentation_feedback": [f for f in self.feedback_rows.values() if f.project_id in projects],
            "verified_answers": [a for a in self.answer_rows.values() if a.project_id in projects],
            "expert_preferences": [p for u, p in self.prefs.items() if u in scope["users"]],
            "handovers": [h for h in self.handover_rows.values() if h.org_id == org_id],
            "metric_events": [m for m in self.metric_rows if m.org_id == org_id],
            "doc_edits": [e for e in self.edit_rows.values() if e.org_id == org_id],
            "onboarding_phases": mine(p for rows in self.phase_rows.values() for p in rows),
            "task_placements": mine(self.placement_rows.values()),
            "task_moves": mine(self.move_rows),
        }

    def org_blob_uris(self, org_id):
        core, s = self.core, self._org_scope(org_id)
        uris = {d["uri"] for d in core.document_rows.values() if d["project_id"] in s["projects"] and d.get("uri")}
        uris |= {m.recording_uri for m in core.meetings.values() if m.project_id in s["projects"] and m.recording_uri}
        uris |= {u + TRANSCRIPT_SUFFIX for u in uris if u.startswith("gs://") and "/recordings/" in u}  # Google's transcript beside it
        return sorted(uris)

    def org_thread_ids(self, org_id):
        core, s = self.core, self._org_scope(org_id)
        return sorted({a.thread_id for a in core.approval_rows.values() if a.project_id in s["projects"] and a.thread_id})

    def export_org(self, org_id):
        core, s = self.core, self._org_scope(org_id)
        dump = lambda rows: [r.model_dump(mode="json") if hasattr(r, "model_dump") else _plain(r) for r in rows]  # noqa: E731
        out = {
            "organizations": [{"id": org_id, "name": core.orgs.get(org_id, "")}],
            "users": dump(core.users[u] for u in s["users"]),
            "projects": dump(core.projects[p] for p in s["projects"]),
            "project_members": [{"project_id": p, "user_id": u} for p in s["projects"] for u in core.project_members[p]],
            "onboardings": dump(core.onboard[o] for o in s["onboardings"]),
            "repositories": dump(r for r in core.repos.values() if r.project_id in s["projects"]),
            "documents": dump(d for d in core.document_rows.values() if d["project_id"] in s["projects"]),
            "meetings": dump(core.meetings[m] for m in s["meetings"]),
            "meeting_notes": dump(n for m, n in core.notes.items() if m in s["meetings"]),
            "kt_links": dump(l for p in s["projects"] for l in core.links.get(p, [])),
            "tasks": dump(core.task_rows[t] for t in s["tasks"]),
            "approvals": dump(a for a in core.approval_rows.values() if a.project_id in s["projects"]),
            "reviews": dump(r for r in core.review_rows.values() if r.task_id in s["tasks"]),
            "chat_messages": dump(c for c in core.chats.values() if c["project_id"] in s["projects"]),
            "expert_requests": dump(r for r in core.expert_requests.values() if str(r["project_id"]) in s["projects"]),
            "notifications": dump(n for n in core.notifications if n["user_id"] in s["users"]),
            "invitations": dump(i for i in core.invites.values() if i["org_id"] == org_id),
            "audit_log": dump(a for a in core.audit_rows if a["org_id"] == org_id),
            "legal_acceptances": dump(a for a in core.legal_rows if a["org_id"] == org_id),
            # the connection without its credential, as Postgres's export leaves out every column with "token" in its name
            "calendar_connections": dump({k: v for k, v in row.items() if "token" not in k} for (o, _), row in core.calendar_conns.items()
                                         if o == org_id),
            "scheduling_links": dump({"user_id": u, "provider": prov, **row} for (u, prov), row in core.sched_links.items()
                                     if row["org_id"] == org_id),
            # adapters/recording_store.py keeps these on the core store
            "recording_removal_requests": dump(r for r in core.__dict__.get("recording_removal_rows", {}).values() if r["org_id"] == org_id),
        }
        out.update({k: dump(v) for k, v in self._lifecycle_tables(org_id, s).items()})
        return out

    def delete_org(self, org_id):
        core, s = self.core, self._org_scope(org_id)
        counts = {k: len(v) for k, v in self.export_org(org_id).items()}
        with self._lock:
            events = [k for k, r in core.outbox.items() if r["event"].get("project_id") in s["projects"] or r["event"].get("org_id") == org_id]
            counts["outbox"] = len(events)
            for k in events:
                core.outbox.pop(k, None)
            core.calendar_conns = {k: v for k, v in core.calendar_conns.items() if k[0] != org_id}
            requests = core.__dict__.get("recording_removal_rows", {})
            for k in [k for k, r in requests.items() if r["org_id"] == org_id]:
                del requests[k]
            core.sched_links = {k: v for k, v in core.sched_links.items() if v["org_id"] != org_id and k[0] not in s["users"]}
            core.runs[:] = [r for r in core.runs if r["project_id"] not in s["projects"]]
            gone = {i for i, v in core.installations.items() if v.get("org_id") == org_id}
            core.installations = {i: v for i, v in core.installations.items() if i not in gone}
            core.installation_senders = {i: v for i, v in core.installation_senders.items() if i not in gone}
            core.org_created.pop(org_id, None)
            for p in s["projects"]:
                core.projects.pop(p, None)
                core.project_members.pop(p, None)
                core.links.pop(p, None)
            for u in s["users"]:
                core.users.pop(u, None)
                core.left.discard(u)
            core.firebase = {uid: u for uid, u in core.firebase.items() if u not in s["users"]}
            for o in s["onboardings"]:
                core.onboard.pop(o, None)
            for t in s["tasks"]:
                core.task_rows.pop(t, None)
            for m in s["meetings"]:
                core.meetings.pop(m, None)
                core.notes.pop(m, None)
            core.repos = {k: r for k, r in core.repos.items() if r.project_id not in s["projects"]}
            core.document_rows = {k: d for k, d in core.document_rows.items() if d["project_id"] not in s["projects"]}
            core.approval_rows = {k: a for k, a in core.approval_rows.items() if a.project_id not in s["projects"]}
            core.review_rows = {k: r for k, r in core.review_rows.items() if r.task_id not in s["tasks"]}
            core.chats = {k: c for k, c in core.chats.items() if c["project_id"] not in s["projects"]}
            core.expert_requests = {k: r for k, r in core.expert_requests.items() if str(r["project_id"]) not in s["projects"]}
            core.notifications[:] = [n for n in core.notifications if n["user_id"] not in s["users"]]
            core.invites = {k: i for k, i in core.invites.items() if i["org_id"] != org_id}
            core.audit_rows[:] = [a for a in core.audit_rows if a["org_id"] != org_id]
            core.legal_rows[:] = [a for a in core.legal_rows if a["org_id"] != org_id]
            core.orgs.pop(org_id, None)
            self.settings.pop(org_id, None)
            self.team_rows = {k: t for k, t in self.team_rows.items() if t.org_id != org_id}
            self.template_rows = {k: t for k, t in self.template_rows.items() if t.org_id != org_id}
            self.program_rows = {k: p for k, p in self.program_rows.items() if p.org_id != org_id}
            self.tokens = {h: o for h, o in self.tokens.items() if o in self.program_rows}
            for o in s["onboardings"]:
                for rows in (self.milestone_rows, self.ack_rows, self.env_rows, self.phase_rows):
                    rows.pop(o, None)
            self.placement_rows = {k: r for k, r in self.placement_rows.items() if r.onboarding_id not in s["onboardings"]}
            self.move_rows = [m for m in self.move_rows if m.onboarding_id not in s["onboardings"]]
            keep = lambda rows: {k: r for k, r in rows.items() if r.onboarding_id not in s["onboardings"]}  # noqa: E731
            self.access_rows, self.area_rows, self.blocker_rows = keep(self.access_rows), keep(self.area_rows), keep(self.blocker_rows)
            self.summary_rows, self.checkin_rows = keep(self.summary_rows), keep(self.checkin_rows)
            self.feedback_rows = {k: f for k, f in self.feedback_rows.items() if f.project_id not in s["projects"]}
            self.answer_rows = {k: a for k, a in self.answer_rows.items() if a.project_id not in s["projects"]}
            self.prefs = {u: p for u, p in self.prefs.items() if u not in s["users"]}
            self.survey_rows = [r for r in self.survey_rows if r.org_id != org_id]
            self.handover_rows = {k: h for k, h in self.handover_rows.items() if h.org_id != org_id}
            self.metric_rows = [m for m in self.metric_rows if m.org_id != org_id]
            self.edit_rows = {k: e for k, e in self.edit_rows.items() if e.org_id != org_id}
        self._changed()
        return {k: n for k, n in counts.items() if n}


def _plain(row: dict) -> dict:
    """A memory row as JSON-ready data: datetimes as ISO text, nested models dumped."""
    out = {}
    for k, v in row.items():
        if hasattr(v, "model_dump"):
            v = v.model_dump(mode="json")
        elif isinstance(v, date):  # dates and datetimes
            v = v.isoformat()
        elif isinstance(v, (set, frozenset)):
            v = sorted(v)
        out[k] = v
    return out
