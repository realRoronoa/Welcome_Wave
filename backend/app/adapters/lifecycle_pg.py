"""Postgres LifecycleStore (db/migrations/010_lifecycle.sql). Every statement runs under row-level security for the
request's organisation (the tenant PgStore sets), so the org_id arguments only fill in new rows. The pre-boarding link is
looked up before any tenant is known, through the narrow definer function preboarding_lookup()."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.adapters.postgres import USER_COLS, _Db, _is_uuid, _s, current_org
from app.ports import (TRANSCRIPT_SUFFIX, AccessItem, Acknowledgement, AreaProgress, BlockerAlert, CheckinReview, DocEdit, DocFeedback, EnvironmentCheck,
                       ExpertPreferences, Handover, MetricEvent, Milestone, OnboardingProgram, OrgSettings, Phase, ProgramTemplate,
                       ProgressSummary, SurveyResponse, TaskMove, TaskPlacement, Team, User, VerifiedAnswer)

# jsonb columns of the lifecycle tables (bound as JSON text and cast)
_JSON = frozenset({"setup", "milestones", "acknowledgements", "access_items", "plan_items", "first_task", "completion", "evidence",
                   "results", "body", "answers", "citations", "office_hours", "plan", "props", "agent_modes", "completion_draft",
                   "feedback_ids", "phases"})
# Never exported: derived search data, and anything that is or protects a credential (link tokens, API tokens, OAuth secrets).
_EXPORT_SKIP = frozenset({"embedding", "tsv", "search_text", "firebase_uid", "ip_hash"})
# Never exported, whole tables: derived search data. `chunks` is a copy of every indexed file, document page and transcript
# line, rebuilt from GitHub, the stored documents and the call notes (which are exported); exporting it would hold the whole
# indexed repository in memory for one request.
_EXPORT_SKIP_TABLES = frozenset({"chunks"})
# The app may not delete these rows itself (015_audit_append_only.sql, 019_legal.sql): delete_org removes them through
# delete_org_audit() and delete_org_legal(), once the organisation row is gone
_APPEND_ONLY = frozenset({"audit_log", "legal_acceptances"})
# Tables whose ids name an organisation's events in processed_events' dedupe keys ('ob:<id>', 'doc:<id>', 'connect:<repo>:..',
# 'rec:<meeting>:..', 'push:<sha>:<repo>', 'feedback:<id>', 'mail:invitation:<id>', 'tick:<org>:<day>')
_EVENT_ID_TABLES = ("projects", "onboardings", "repositories", "documents", "meetings", "documentation_feedback", "invitations")
_UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# org export and deletion read or remove a whole organisation in one statement per table: more than the app's 30 s default
_BULK = ("SET LOCAL statement_timeout = '10min'", "SET LOCAL lock_timeout = '30s'")


def _bind(values: dict) -> dict:
    return {k: json.dumps(v, default=str) if k in _JSON and v is not None else v for k, v in values.items()}


def _cols(values: dict) -> tuple[str, str]:
    return ", ".join(values), ", ".join(f"CAST(:{k} AS jsonb)" if k in _JSON else f":{k}" for k in values)


def _plain(v):
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, memoryview)):
        return None
    return v


class PgLifecycleStore(_Db):
    def _insert(self, table: str, values: dict, tail: str = "") -> dict | None:
        values = {k: v for k, v in values.items() if not (k == "id" and not v)}  # empty id: the database makes one
        cols, params = _cols(values)
        return self.one(f"INSERT INTO {table} ({cols}) VALUES ({params}) {tail} RETURNING *", **_bind(values))

    def _update(self, table: str, where: str, fields: dict, **key) -> dict | None:
        if not fields:
            return self.one(f"SELECT * FROM {table} WHERE {where}", **key)
        sets = ", ".join(f"{k} = CAST(:f_{k} AS jsonb)" if k in _JSON else f"{k} = :f_{k}" for k in fields)
        return self.one(f"UPDATE {table} SET {sets} WHERE {where} RETURNING *", **key, **{f"f_{k}": v for k, v in _bind(fields).items()})

    @staticmethod
    def _org() -> str | None:
        return current_org.get()

    # ---- organisation settings and teams ----
    def org_settings(self, org_id):
        r = self.one("SELECT * FROM org_settings WHERE org_id = :o", o=org_id)
        return OrgSettings(**_s(r)) if r else OrgSettings(org_id=org_id)

    def update_org_settings(self, org_id, **fields):
        values = {"org_id": org_id, **fields}
        cols, params = _cols(values)
        sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in fields) or "org_id = EXCLUDED.org_id"
        r = self.one(f"INSERT INTO org_settings ({cols}) VALUES ({params}) ON CONFLICT (org_id) DO UPDATE SET {sets} RETURNING *",
                     **_bind(values))
        return OrgSettings(**_s(r))

    def teams(self, org_id, project_id=None):
        if project_id is not None and not _is_uuid(project_id):
            return []
        rows = self.rows("SELECT * FROM teams WHERE (CAST(:p AS uuid) IS NULL OR project_id = CAST(:p AS uuid)) ORDER BY name, id",
                         p=project_id)
        return [Team(**_s(r)) for r in rows]

    def get_team(self, team_id):
        r = self.one("SELECT * FROM teams WHERE id = CAST(:i AS uuid)", i=team_id) if _is_uuid(team_id) else None
        return Team(**_s(r)) if r else None

    def create_team(self, org_id, project_id, name):
        return Team(**_s(self._insert("teams", {"org_id": org_id, "project_id": project_id, "name": name})))

    def update_team(self, team_id, **fields):
        r = self._update("teams", "id = CAST(:i AS uuid)", fields, i=team_id) if _is_uuid(team_id) else None
        return Team(**_s(r)) if r else None

    # ---- templates ----
    def templates(self, org_id, include_inactive=False):
        rows = self.rows("SELECT * FROM program_templates WHERE (:all OR active) ORDER BY role_track, level, key, version DESC",
                         all=include_inactive)
        return [ProgramTemplate(**_s(r)) for r in rows]

    def get_template(self, template_id):
        r = self.one("SELECT * FROM program_templates WHERE id = CAST(:i AS uuid)", i=template_id) if _is_uuid(template_id) else None
        return ProgramTemplate(**_s(r)) if r else None

    def create_template(self, template):
        values = template.model_dump(exclude={"id", "version", "active", "created_at"})
        return ProgramTemplate(**_s(self._insert("program_templates", values)))

    def new_template_version(self, template_id, **changes):
        """The old version stops being active and the new one starts, in one transaction (one active version per key)."""
        if not _is_uuid(template_id):
            return None
        with self.tx() as c:
            old = c.execute(text("UPDATE program_templates SET active = false WHERE id = CAST(:i AS uuid) AND active RETURNING *"),
                            {"i": template_id}).mappings().first()
            if old is None:
                return None
            values = {**ProgramTemplate(**_s(dict(old))).model_dump(exclude={"id", "created_at"}), **changes,
                      "version": old["version"] + 1, "active": True}
            cols, params = _cols(values)
            new = c.execute(text(f"INSERT INTO program_templates ({cols}) VALUES ({params}) RETURNING *"), _bind(values)).mappings().one()
        return ProgramTemplate(**_s(dict(new)))

    # ---- programs ----
    def create_program(self, program, milestones, access, acknowledgements):
        org = program.org_id
        with self.tx() as c:
            values = program.model_dump()
            cols, params = _cols(values)
            r = c.execute(text(f"""INSERT INTO onboarding_programs ({cols}) VALUES ({params})
                                   ON CONFLICT (onboarding_id) DO NOTHING RETURNING *"""),
                          _bind(values)).mappings().first()
            if r is None:  # a second enrolment of the same onboarding keeps the first program
                return self.get_program(program.onboarding_id)
            for rows, table in ((milestones, "onboarding_milestones"), (access, "access_items"), (acknowledgements, "acknowledgements")):
                for m in rows:
                    v = {k: val for k, val in m.model_dump().items() if not (k == "id" and not val)}
                    v["org_id"] = org
                    cols, params = _cols(v)
                    c.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), _bind(v))
        return OnboardingProgram(**_s(dict(r)))

    def set_milestones(self, onboarding_id, milestones):
        with self.tx() as c:
            c.execute(text("DELETE FROM onboarding_milestones WHERE onboarding_id = CAST(:i AS uuid)"), {"i": onboarding_id})
            org = self._org()
            for m in milestones:
                v = {**m.model_dump(), "onboarding_id": onboarding_id, "org_id": org}
                cols, params = _cols(v)
                c.execute(text(f"INSERT INTO onboarding_milestones ({cols}) VALUES ({params})"), _bind(v))

    def get_program(self, onboarding_id):
        if not _is_uuid(onboarding_id):
            return None
        r = self.one("SELECT * FROM onboarding_programs WHERE onboarding_id = CAST(:i AS uuid)", i=onboarding_id)
        return OnboardingProgram(**_s(r)) if r else None

    def programs(self, org_id, project_id=None):
        if project_id is not None and not _is_uuid(project_id):
            return []
        rows = self.rows("""SELECT * FROM onboarding_programs WHERE (CAST(:p AS uuid) IS NULL OR project_id = CAST(:p AS uuid))
                            ORDER BY created_at""",
                         p=project_id)
        return [OnboardingProgram(**_s(r)) for r in rows]

    def update_program(self, onboarding_id, **fields):
        self._update("onboarding_programs", "onboarding_id = CAST(:i AS uuid)", fields, i=onboarding_id)

    def set_preboarding(self, onboarding_id, token_hash, expires_at):
        self.run("""UPDATE onboarding_programs SET preboarding_token_hash = :h, preboarding_expires_at = :e
                    WHERE onboarding_id = CAST(:i AS uuid)""", h=token_hash, e=expires_at, i=onboarding_id)

    def program_by_token(self, token_hash):
        r = self.one("SELECT onboarding_id, org_id, expires_at FROM preboarding_lookup(:h)", h=token_hash)
        return (str(r["onboarding_id"]), str(r["org_id"]), r["expires_at"]) if r and r["expires_at"] else None

    def ensure_user(self, org_id, email, name, github_login):
        """A user row with no login bound: accept_invitations binds it when they accept, like someone coming back."""
        cols = USER_COLS
        with self.tx() as c:
            r = c.execute(text(f"SELECT {cols} FROM users WHERE org_id = CAST(:o AS uuid) AND lower(email) = lower(:e)"),
                          {"o": org_id, "e": email}).mappings().first()
            if r is None:
                r = c.execute(text(f"""INSERT INTO users (org_id, email, name, role, github_login)
                                       VALUES (:o, lower(:e), :n, 'new_hire', :g)
                                       RETURNING {cols}"""), {"o": org_id, "e": email, "n": name, "g": github_login}).mappings().one()
        return User(**_s(dict(r)))

    def set_onboarding_status(self, onboarding_id, status):
        self.run("UPDATE onboardings SET status = :s WHERE id = CAST(:i AS uuid)", s=status, i=onboarding_id)

    # ---- timeline ----
    def milestones(self, onboarding_id):
        if not _is_uuid(onboarding_id):
            return []
        rows = self.rows("SELECT * FROM onboarding_milestones WHERE onboarding_id = CAST(:i AS uuid) ORDER BY due_day, key",
                         i=onboarding_id)
        return [Milestone(**_s(r)) for r in rows]

    def update_milestone(self, onboarding_id, key, **fields):
        r = self._update("onboarding_milestones", "onboarding_id = CAST(:i AS uuid) AND key = :k", fields, i=onboarding_id, k=key)
        return Milestone(**_s(r)) if r else None

    # ---- the milestone board (018_board.sql) ----
    def phases(self, onboarding_id):
        if not _is_uuid(onboarding_id):
            return []
        rows = self.rows("SELECT * FROM onboarding_phases WHERE onboarding_id = CAST(:i AS uuid) ORDER BY position, due_day, key",
                         i=onboarding_id)
        return [Phase(**_s(r)) for r in rows]

    def ensure_phases(self, onboarding_id, phases):
        """Written only when the program has none: the first read of a board copies its template's milestones. Two first
        reads at once both try; the advisory lock on the onboarding makes the second see the first one's rows."""
        if not _is_uuid(onboarding_id):
            return []
        with self.tx() as c:
            c.execute(text("SELECT pg_advisory_xact_lock(hashtext('board:' || :i))"), {"i": onboarding_id})
            have = c.execute(text("SELECT count(*) FROM onboarding_phases WHERE onboarding_id = CAST(:i AS uuid)"),
                             {"i": onboarding_id}).scalar()
            if not have:
                org = self._org()
                for ph in phases:
                    v = {**ph.model_dump(exclude={"created_at"}), "onboarding_id": onboarding_id, "org_id": org}
                    cols, params = _cols(v)
                    c.execute(text(f"INSERT INTO onboarding_phases ({cols}) VALUES ({params}) ON CONFLICT (onboarding_id, key) DO NOTHING"),
                              _bind(v))
        return self.phases(onboarding_id)

    def add_phase(self, phase):
        v = {**phase.model_dump(exclude={"created_at"}), "org_id": self._org()}
        r = self._insert("onboarding_phases", v, "ON CONFLICT (onboarding_id, key) DO NOTHING")
        return Phase(**_s(r)) if r else None

    def update_phase(self, onboarding_id, key, **fields):
        if not _is_uuid(onboarding_id):
            return None
        r = self._update("onboarding_phases", "onboarding_id = CAST(:i AS uuid) AND key = :k", fields, i=onboarding_id, k=key)
        return Phase(**_s(r)) if r else None

    def delete_phase(self, onboarding_id, key, move_to):
        if not _is_uuid(onboarding_id):
            return False
        with self.tx() as c:
            gone = c.execute(text("DELETE FROM onboarding_phases WHERE onboarding_id = CAST(:i AS uuid) AND key = :k"),
                             {"i": onboarding_id, "k": key}).rowcount
            if gone:
                c.execute(text("""UPDATE task_placements SET phase_key = :to, position = NULL, updated_at = now()
                                  WHERE onboarding_id = CAST(:i AS uuid) AND phase_key = :k"""), {"i": onboarding_id, "k": key, "to": move_to})
        return bool(gone)

    def placements(self, onboarding_id):
        if not _is_uuid(onboarding_id):
            return []
        rows = self.rows("SELECT * FROM task_placements WHERE onboarding_id = CAST(:i AS uuid)", i=onboarding_id)
        return [TaskPlacement(**_s(r)) for r in rows]

    def save_placements(self, onboarding_id, rows):
        if not rows or not _is_uuid(onboarding_id):
            return
        org = self._org()
        with self.tx() as c:
            for r in rows:
                c.execute(text("""INSERT INTO task_placements (task_id, org_id, onboarding_id, phase_key, position, updated_at)
                                  VALUES (CAST(:t AS uuid), :o, CAST(:i AS uuid), :k, :p, now())
                                  ON CONFLICT (task_id) DO UPDATE SET phase_key = EXCLUDED.phase_key, position = EXCLUDED.position,
                                                                       updated_at = now()"""),
                          {"t": r.task_id, "o": org, "i": onboarding_id, "k": r.phase_key, "p": r.position})

    def add_task_move(self, move):
        v = {**move.model_dump(exclude={"id"}), "org_id": self._org()}
        r = _s(self._insert("task_moves", v))
        return TaskMove(**{**r, "id": str(r["id"])})

    def task_moves(self, onboarding_id):
        if not _is_uuid(onboarding_id):
            return []
        rows = self.rows("SELECT * FROM task_moves WHERE onboarding_id = CAST(:i AS uuid) ORDER BY at, id", i=onboarding_id)
        return [TaskMove(**{**_s(r), "id": str(r["id"])}) for r in rows]

    def access_items(self, onboarding_id):
        rows = self.rows("SELECT * FROM access_items WHERE onboarding_id = CAST(:i AS uuid) ORDER BY requested_at, system", i=onboarding_id)
        return [AccessItem(**_s(r)) for r in rows]

    def get_access_item(self, item_id):
        r = self.one("SELECT * FROM access_items WHERE id = CAST(:i AS uuid)", i=item_id) if _is_uuid(item_id) else None
        return AccessItem(**_s(r)) if r else None

    def add_access_item(self, item):
        return AccessItem(**_s(self._insert("access_items", {**item.model_dump(), "org_id": self._org()})))

    def update_access_item(self, item_id, **fields):
        r = self._update("access_items", "id = CAST(:i AS uuid)", fields, i=item_id) if _is_uuid(item_id) else None
        return AccessItem(**_s(r)) if r else None

    def acknowledgements(self, onboarding_id):
        rows = self.rows("SELECT * FROM acknowledgements WHERE onboarding_id = CAST(:i AS uuid) ORDER BY id", i=onboarding_id)
        return [Acknowledgement(**_s(r)) for r in rows]

    def acknowledge(self, onboarding_id, doc_key, ip_hash, at):
        r = self.one("""UPDATE acknowledgements SET acknowledged_at = coalesce(acknowledged_at, :at), ip_hash = coalesce(ip_hash, :ip)
                        WHERE onboarding_id = CAST(:i AS uuid) AND doc_key = :k RETURNING *""",
                     at=at, ip=ip_hash, i=onboarding_id, k=doc_key)
        return Acknowledgement(**_s(r)) if r else None

    def environment_checks(self, onboarding_id):
        rows = self.rows("SELECT * FROM environment_checks WHERE onboarding_id = CAST(:i AS uuid) ORDER BY ran_at", i=onboarding_id)
        return [EnvironmentCheck(**_s(r)) for r in rows]

    def add_environment_check(self, check):
        return EnvironmentCheck(**_s(self._insert("environment_checks", {**check.model_dump(), "org_id": self._org()})))

    # ---- progress, blockers, summaries, check-ins ----
    def area_progress(self, onboarding_id):
        rows = self.rows("SELECT * FROM area_progress WHERE onboarding_id = CAST(:i AS uuid) ORDER BY area", i=onboarding_id)
        return [AreaProgress(**_s(r)) for r in rows]

    def save_area_progress(self, row):
        r = self._insert("area_progress", {**row.model_dump(), "org_id": self._org()},
                         """ON CONFLICT (onboarding_id, area) DO UPDATE SET evidence = EXCLUDED.evidence, self_level = EXCLUDED.self_level,
                            updated_at = EXCLUDED.updated_at""")
        return AreaProgress(**_s(r))

    def blockers(self, onboarding_id, open_only=False):
        rows = self.rows("""SELECT * FROM blocker_alerts WHERE onboarding_id = CAST(:i AS uuid) AND (NOT :open OR resolved_at IS NULL)
                            ORDER BY opened_at, id""", i=onboarding_id, open=open_only)
        return [BlockerAlert(**_s(r)) for r in rows]

    def get_blocker(self, blocker_id):
        r = self.one("SELECT * FROM blocker_alerts WHERE id = CAST(:i AS uuid)", i=blocker_id) if _is_uuid(blocker_id) else None
        return BlockerAlert(**_s(r)) if r else None

    def open_blocker(self, alert):
        """Two detections at once open one alert: the partial unique index keeps one open per rule and subject."""
        r = self._insert("blocker_alerts", {**alert.model_dump(), "org_id": self._org()},
                         "ON CONFLICT (onboarding_id, rule, subject) WHERE resolved_at IS NULL DO NOTHING")
        if r is None:
            r = self.one("""SELECT * FROM blocker_alerts WHERE onboarding_id = CAST(:i AS uuid) AND rule = :r AND subject = :s
                            AND resolved_at IS NULL""", i=alert.onboarding_id, r=alert.rule, s=alert.subject)
        return BlockerAlert(**_s(r))

    def update_blocker(self, blocker_id, **fields):
        r = self._update("blocker_alerts", "id = CAST(:i AS uuid)", fields, i=blocker_id) if _is_uuid(blocker_id) else None
        return BlockerAlert(**_s(r)) if r else None

    def summaries(self, onboarding_id):
        rows = self.rows("SELECT * FROM progress_summaries WHERE onboarding_id = CAST(:i AS uuid) ORDER BY week_start DESC",
                         i=onboarding_id)
        return [ProgressSummary(**_s(r)) for r in rows]

    def save_summary(self, summary):
        r = self._insert("progress_summaries", {**summary.model_dump(), "org_id": self._org()},
                         "ON CONFLICT (onboarding_id, week_start) DO UPDATE SET body = EXCLUDED.body, created_at = EXCLUDED.created_at")
        return ProgressSummary(**_s(r))

    def checkins(self, onboarding_id):
        rows = self.rows("SELECT * FROM checkin_reviews WHERE onboarding_id = CAST(:i AS uuid) ORDER BY day, author_role", i=onboarding_id)
        return [CheckinReview(**_s(r)) for r in rows]

    def save_checkin(self, checkin):
        r = self._insert("checkin_reviews", {**checkin.model_dump(), "org_id": self._org()},
                         """ON CONFLICT (onboarding_id, day, author_role) DO UPDATE SET answers = EXCLUDED.answers,
                            author_id = EXCLUDED.author_id, created_at = EXCLUDED.created_at""")
        return CheckinReview(**_s(r))

    # ---- feedback, verified answers, expert preferences ----
    def add_feedback(self, feedback):
        return DocFeedback(**_s(self._insert("documentation_feedback", {**feedback.model_dump(), "org_id": self._org()})))

    def get_feedback(self, feedback_id):
        r = self.one("SELECT * FROM documentation_feedback WHERE id = CAST(:i AS uuid)", i=feedback_id) if _is_uuid(feedback_id) else None
        return DocFeedback(**_s(r)) if r else None

    def feedback(self, project_id):
        rows = self.rows("SELECT * FROM documentation_feedback WHERE project_id = CAST(:p AS uuid) ORDER BY created_at DESC", p=project_id)
        return [DocFeedback(**_s(r)) for r in rows]

    def update_feedback(self, feedback_id, **fields):
        r = self._update("documentation_feedback", "id = CAST(:i AS uuid)", fields, i=feedback_id) if _is_uuid(feedback_id) else None
        return DocFeedback(**_s(r)) if r else None

    def add_verified_answer(self, answer):
        values = {**answer.model_dump(), "org_id": self._org()}
        if not _is_uuid(values.get("source_request_id")):
            values["source_request_id"] = None
        return VerifiedAnswer(**_s(self._insert("verified_answers", values)))

    def verified_answers(self, project_id):
        rows = self.rows("SELECT * FROM verified_answers WHERE project_id = CAST(:p AS uuid) ORDER BY created_at DESC", p=project_id)
        return [VerifiedAnswer(**_s(r)) for r in rows]

    def bump_reuse(self, answer_id):
        if _is_uuid(answer_id):
            self.run("UPDATE verified_answers SET reuse_count = reuse_count + 1 WHERE id = CAST(:i AS uuid)", i=answer_id)

    def answered_requests(self, project_id):
        return [_s(r) for r in self.rows("""SELECT id, question, answer, answered_at FROM expert_requests
                                           WHERE project_id = CAST(:p AS uuid) AND status = 'answered' ORDER BY answered_at DESC""",
                                        p=project_id)]

    def expert_preferences(self, user_id):
        r = self.one("SELECT * FROM expert_preferences WHERE user_id = CAST(:u AS uuid)", u=user_id) if _is_uuid(user_id) else None
        return ExpertPreferences(**_s(r)) if r else ExpertPreferences(user_id=user_id)

    def save_expert_preferences(self, org_id, prefs):
        r = self._insert("expert_preferences", {**prefs.model_dump(), "org_id": org_id},
                         """ON CONFLICT (user_id) DO UPDATE SET max_interruptions_per_day = EXCLUDED.max_interruptions_per_day,
                            office_hours = EXCLUDED.office_hours, digest = EXCLUDED.digest""")
        return ExpertPreferences(**_s(r))

    # ---- surveys, handovers, metrics ----
    def claim_survey_day(self, onboarding_id, day):
        """One statement: the day is added only if it is not there yet, so of two simultaneous submissions one gets a row back."""
        if not _is_uuid(onboarding_id):
            return False
        r = self.one("""UPDATE onboarding_programs SET survey_days = array_append(survey_days, CAST(:d AS int))
                        WHERE onboarding_id = CAST(:i AS uuid) AND NOT (CAST(:d AS int) = ANY(survey_days))
                        RETURNING onboarding_id""", i=onboarding_id, d=day)
        return r is not None

    def add_survey_response(self, response):
        self._insert("survey_responses", response.model_dump())

    def survey_responses(self, org_id):
        return [SurveyResponse(**_s(r)) for r in self.rows("SELECT * FROM survey_responses")]

    def handovers(self, org_id):
        return [Handover(**_s(r)) for r in self.rows("SELECT * FROM handovers ORDER BY last_day, id")]

    def get_handover(self, handover_id):
        r = self.one("SELECT * FROM handovers WHERE id = CAST(:i AS uuid)", i=handover_id) if _is_uuid(handover_id) else None
        return Handover(**_s(r)) if r else None

    def add_handover(self, handover):
        return Handover(**_s(self._insert("handovers", handover.model_dump())))

    def update_handover(self, handover_id, **fields):
        r = self._update("handovers", "id = CAST(:i AS uuid)", fields, i=handover_id) if _is_uuid(handover_id) else None
        return Handover(**_s(r)) if r else None

    def set_onboarding_manager(self, onboarding_id, manager_id):
        if _is_uuid(onboarding_id) and _is_uuid(manager_id):
            self.run("UPDATE onboardings SET manager_id = CAST(:m AS uuid) WHERE id = CAST(:i AS uuid)", m=manager_id, i=onboarding_id)

    def reassign_expert_request(self, request_id, expert_id):
        if not (_is_uuid(request_id) and _is_uuid(expert_id)):
            return False
        r = self.one("""UPDATE expert_requests SET expert_id = CAST(:e AS uuid) WHERE id = CAST(:i AS uuid) AND status = 'open'
                        RETURNING id""", e=expert_id, i=request_id)
        return r is not None

    def record_metric(self, org_id, project_id, kind, subject_id=None, props=None, at=None):
        values = {"org_id": org_id, "project_id": project_id, "kind": kind, "subject_id": subject_id, "props": props or {}}
        self._insert("metric_events", {**values, **({"at": at} if at else {})})

    def metrics(self, org_id, kinds, since=None):
        rows = self.rows("""SELECT * FROM metric_events WHERE kind = ANY(:k) AND (CAST(:s AS timestamptz) IS NULL OR at >= :s)
                            ORDER BY at, id""", k=list(kinds), s=since)
        return [MetricEvent(**_s({**r, "id": str(r["id"])})) for r in rows]

    # ---- approved doc edits, organisations for the daily tick ----
    def add_doc_edit(self, edit):
        values = {**edit.model_dump(), "org_id": self._org()}
        for k in ("approval_id", "owner_id", "accepted_by"):
            if not _is_uuid(values.get(k)):
                values[k] = None
        r = self._insert("doc_edits", values, "ON CONFLICT (approval_id) WHERE approval_id IS NOT NULL DO NOTHING")
        if r is None:  # one per approval: a retried decision finds the first
            r = self.one("SELECT * FROM doc_edits WHERE approval_id = CAST(:a AS uuid)", a=values["approval_id"])
        return DocEdit(**_s(r))

    def doc_edits(self, project_id, target_kind=None, target_ref=None):
        if not _is_uuid(project_id):
            return []
        rows = self.rows(
            "SELECT * FROM doc_edits WHERE project_id = CAST(:p AS uuid) AND (CAST(:k AS text) IS NULL OR target_kind = :k) "
            "AND (CAST(:r AS text) IS NULL OR target_ref = :r) ORDER BY created_at DESC, id", p=project_id, k=target_kind, r=target_ref)
        return [DocEdit(**_s(r)) for r in rows]

    def lifecycle_orgs(self):
        return [str(r["org_id"]) for r in self.rows("SELECT org_id FROM lifecycle_orgs()")]

    # ---- agent-owned tables ----
    def task_activity(self, task_ids):
        ids = [t for t in task_ids if _is_uuid(t)]
        rows = self.rows("SELECT id, updated_at FROM tasks WHERE id = ANY(CAST(:t AS uuid[]))", t=ids) if ids else []
        return {str(r["id"]): r["updated_at"] for r in rows}

    def reviews_for_tasks(self, task_ids):
        ids = [t for t in task_ids if _is_uuid(t)]
        if not ids:
            return []
        return [_s(r) for r in self.rows("""SELECT id, task_id, head_sha, recommendation::text AS recommendation, findings,
                                                  manager_decision::text AS decision, created_at
                                           FROM reviews WHERE task_id = ANY(CAST(:t AS uuid[])) ORDER BY created_at""", t=ids)]

    # ---- the organisation's data ----
    def _org_tables(self, c) -> dict[str, list[str]]:
        """Every table with an org_id column, and the columns an export may show. Read from the catalog, so tables added
        by later migrations are exported and deleted too."""
        rows = c.execute(text("""SELECT c.table_name, c.column_name FROM information_schema.columns c
                                 JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                                 WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                                   AND c.table_name IN (SELECT table_name FROM information_schema.columns
                                                        WHERE table_schema = 'public' AND column_name = 'org_id')
                                 ORDER BY c.table_name, c.ordinal_position""")).all()
        out: dict[str, list[str]] = {}
        for table, col in rows:
            if col not in _EXPORT_SKIP and "token" not in col and "secret" not in col:
                out.setdefault(table, []).append(col)
        return out

    def org_blob_uris(self, org_id):
        rows = self.rows("""SELECT gcs_uri AS uri FROM documents WHERE gcs_uri IS NOT NULL
                            UNION SELECT recording_uri FROM meetings WHERE recording_uri IS NOT NULL""")
        uris = {r["uri"] for r in rows}
        uris |= {u + TRANSCRIPT_SUFFIX for u in uris if u.startswith("gs://") and "/recordings/" in u}  # Google's transcript beside it
        return sorted(uris)

    def org_thread_ids(self, org_id):
        return sorted(r["thread_id"] for r in self.rows("SELECT DISTINCT thread_id FROM approvals WHERE thread_id <> ''"))

    def export_org(self, org_id):
        with self.tx() as c:
            for stmt in _BULK:
                c.execute(text(stmt))
            out = {"organizations": [{k: _plain(v) for k, v in r.items()} for r in
                                     c.execute(text("SELECT id, name, created_at FROM organizations")).mappings()]}
            for table, cols in self._org_tables(c).items():
                if table in _EXPORT_SKIP_TABLES:
                    continue
                select = ", ".join(f'"{col}"' for col in cols)
                out[table] = [{k: _plain(v) for k, v in r.items()} for r in c.execute(text(f'SELECT {select} FROM "{table}"')).mappings()]
        return out

    def delete_org(self, org_id):
        """Everything of the organisation, in one transaction, with row-level security keeping every statement inside it.
        Each table is emptied in turn; one that another still points at (a foreign key without a cascade) waits for a later
        round, so the order needs no list of tables and covers tables later migrations add. The organisation row goes after
        them, then its audit entries (through delete_org_audit(): the app role cannot delete audit rows itself), and the
        dedupe keys of its events (processed_events: ids only, no org_id), found by the ids they name."""
        with self.tx() as c:
            for stmt in _BULK:
                c.execute(text(stmt))
            tables = self._org_tables(c)
            counts = {t: c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in tables}
            # the outbox holds ids only and has no org_id (so no row-level security): its events name a project or the org
            counts["outbox"] = c.execute(text("""DELETE FROM outbox WHERE event->>'project_id' IN (SELECT id::text FROM projects)
                                                    OR event->>'org_id' = :o"""), {"o": org_id}).rowcount
            ids = " UNION ALL ".join(f'SELECT id::text FROM "{t}"' for t in _EVENT_ID_TABLES if t in tables)
            counts["processed_events"] = c.execute(text(f"""
                WITH ids(id) AS (SELECT CAST(:o AS text){f' UNION ALL {ids}' if ids else ''})
                DELETE FROM processed_events p
                 WHERE EXISTS (SELECT 1 FROM regexp_matches(p.dedupe_key, '{_UUID}', 'g') AS m(u)
                                WHERE m.u[1] IN (SELECT id FROM ids))"""), {"o": org_id}).rowcount
            c.execute(text("""DELETE FROM github_installation_senders
                              WHERE installation_id IN (SELECT installation_id FROM github_installations)"""))
            pending = [t for t in tables if counts[t] and t not in _APPEND_ONLY]
            while pending:
                waiting = []
                for t in pending:
                    step = c.begin_nested()
                    try:
                        c.execute(text(f'DELETE FROM "{t}"'))
                        step.commit()
                    except IntegrityError:
                        step.rollback()
                        waiting.append(t)
                if len(waiting) == len(pending):
                    raise RuntimeError(f"organisation data still referenced: {waiting}")
                pending = waiting
            c.execute(text("DELETE FROM organizations WHERE id = CAST(:o AS uuid)"), {"o": org_id})
            if "audit_log" in tables:
                counts["audit_log"] = c.execute(text("SELECT delete_org_audit(CAST(:o AS uuid))"), {"o": org_id}).scalar()
            if "legal_acceptances" in tables:
                counts["legal_acceptances"] = c.execute(text("SELECT delete_org_legal(CAST(:o AS uuid))"), {"o": org_id}).scalar()
        return {t: n for t, n in counts.items() if n}
