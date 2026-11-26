/** Shapes of the lifecycle API (backend/app/api/lifecycle.py; docs/specs/*.md). Dates are ISO strings: YYYY-MM-DD for days,
 *  full timestamps for moments. Every ranked or detected result carries `why`, shown as it is. */

export type Person = { id: string; name: string } | null;

/* ---------- homes ---------- */
/** `explain`: the agents' explanation block (agentModel.ts), on anything an agent produced. Absent on older servers. */
export type Explained = { explain?: unknown };
export type FocusItem = Explained & { key: string; priority: number; text: string; link: string; why: string };
export type Figure = { label: string; value: number | string | null; how: string };
export type HomeBase<R extends string, S> = { role: R; focus: FocusItem[]; figure: Figure | null; sections: S; why?: string[] };

export type Profile = {
  onboarding_id: string; project_id: string; hire: Person; manager: Person; mentor: Person;
  team: { id: string; name: string } | null; role_title: string; start_date: string; day: number; duration_days: number;
  status: string; focus_area: string | null; template: { id: string; key: string; version: number; title: string } | null;
};
export type CheckinDue = { day: number; author_role: "hire" | "manager"; due_date: string; overdue: boolean };
export type HireStatus = {
  on_track: boolean; overdue_milestones: string[]; open_blockers: number;
  next_milestone: { key: string; title: string; due_date: string } | null; checkins_due: CheckinDue[]; plan: PlanState; why: string[];
};
export type HireCard = { profile: Profile; status: HireStatus };

export type HireHome = HomeBase<"new_hire", { program: HireCard | null; first_task: FirstTaskBrief | null }>;
export type ManagerHome = HomeBase<"manager", { hires: HireCard[]; plans_awaiting_approval: number }>;
export type ExpertHome = HomeBase<"expert", {
  digest: { id: string; question: string; created_at: string }[]; mentees: HireCard[];
  preferences: { max_interruptions_per_day: number; office_hours: { days: string[]; start: string; end: string } | null; digest: boolean };
}>;
export type SetupStep = { key: string; title: string; status: "done" | "skipped" | "todo"; why: string; how: string };
export type AdminHome = HomeBase<"admin", { setup: SetupStep[] }>;
export type ExecHome = HomeBase<"executive", { report: ReportFigure[]; as_of: string }>;
export type Home = HireHome | ManagerHome | ExpertHome | AdminHome | ExecHome;

/* ---------- program ---------- */
export type PlanState = "approved" | "awaiting_approval" | "drafting" | "not_started";
export type MilestoneState = "done" | "skipped" | "overdue" | "due_soon" | "upcoming";
export type Milestone = {
  key: string; title: string; due_day: number; due_date: string; status: "pending" | "done" | "skipped"; state: MilestoneState;
  done_at: string | null; evidence: Record<string, unknown>; auto: boolean;
};
export type PlanItem = { id: string; title: string; kind: string; status: string; due_date: string | null; origin: string; pr_url: string | null; issue: number | null };
export type AccessItem = { id: string; system: string; owner: Person; status: "requested" | "granted" | "not_needed"; requested_at: string; granted_at: string | null };
export type Ack = { doc_key: string; title: string; acknowledged_at: string | null };
export type EnvCheck = { ran_at: string; ok: boolean; results: { name: string; ok: boolean; detail: string }[] };
export type Program = {
  profile: Profile;
  plan: Explained & { status: PlanState; approved_at?: string | null; pending_approval_id: string | null; proposed_tasks: number; why: string[] };
  milestones: Milestone[]; plan_items: PlanItem[]; access: AccessItem[]; acknowledgements: Ack[]; environment_checks: EnvCheck[];
  preboarding: { expires_at: string | null }; first_task: FirstTaskBrief | null;
};

/* ---------- first task ---------- */
export type Candidate = Explained & {
  id: string; kind: "issue" | "task"; title: string; task_id: string | null; issue: number | null; status: string; files: string[];
  score?: number; factors?: Record<string, number>; why: string[]; evidence?: Record<string, unknown>;
};
export type Shortlist = Explained & {
  focus_area: string | null; candidates: Candidate[]; excluded: Candidate[]; weights: Record<string, number>; selected: string | null;
};
export type KtClipRef = { meeting_id: string; meeting_title?: string; title?: string; at_s?: number; start_s?: number; file_path?: string; path?: string };
export type FirstTaskBrief = Explained & {
  candidate_id: string; title: string; issue: number | null; files: string[]; score: number; why: string[];
  owners: unknown[]; tests: string[]; kt_clips: KtClipRef[]; acceptance_criteria: string[]; next: string | string[];
  task_id: string | null; selected_at: string; selected_by: string | Person;
};

/* ---------- progress, blockers, summaries ---------- */
export type Level = "not_started" | "exploring" | "working" | "contributed";
export type Area = {
  area: string; level: Level; self_level: Level | null;
  evidence: { steps: { title?: string; at?: string }[]; watched: unknown[]; questions: unknown[]; merged: unknown[] }; why: string[];
};
export type Blocker = Explained & {
  id: string; rule: string; subject: string; text: string; stage: "hire" | "mentor" | "manager"; opened_at: string;
  business_days_open: number; resolved_at: string | null; evidence: Record<string, unknown>; why: string[];
};
export type Citation = { kind: string; ref: string; label: string };
export type Summary = Explained & {
  id: string; week_start: string; created_at: string;
  body: Explained & { week_start: string; day: number; lines: { text: string; citations: Citation[] }[]; why?: string[] };
};

/* ---------- check-ins, survey, completion ---------- */
export type Question = { id: string; text: string; scale?: boolean };
export type CheckinReview = { id: string; day: number; author_role: "hire" | "manager"; author_id: string; answers: Record<string, string | number>; created_at: string };
export type Checkins = {
  days: number[]; questions: { hire: Question[]; manager: Question[] }; reviews: CheckinReview[]; due: CheckinDue[];
};
export type Criterion = { key: string; label: string; met: boolean; evidence: Record<string, unknown>; why: string };
export type Completion = Explained & {
  day: number; duration_days: number; criteria: Criterion[]; ready: boolean;
  /** the Planner's draft of the decision (agents contract): the manager decides */
  draft?: { decision: "complete" | "extend" | string; extend_days?: number; note?: string; ai?: boolean } | null;
  signed_off: null | { decision: string; note?: string | null; by?: string | Person; at?: string; unmet?: string[]; extend_days?: number };
  completed_at: string | null;
};

/* ---------- templates and teams ---------- */
export type Team = { id: string; org_id: string; project_id: string; name: string };
export type TemplateMilestone = { key: string; title: string; due_day: number };
export type Template = {
  id: string; key: string; version: number; title: string; role_track: string; level: string; duration_days: number;
  milestones: TemplateMilestone[]; acknowledgements: { doc_key: string; title: string; summary?: string }[];
  access_items: { system: string; owner: string }[];
  plan_items: { title: string; kind: string; due_day: number; description?: string; acceptance_criteria?: string[] }[];
  active: boolean; created_at?: string;
};

/* ---------- reports ---------- */
export type FigureValue = { value: number | null; n: number; display: string };
export type ReportFigure = { key: string; label: string; unit: string; how: string; current: FigureValue; previous: FigureValue };
export type SurveyTeam = { team_id: string; team: string; n: number; status: "ok" | "not_enough_data"; scores: Record<string, number> | null; how: string };
export type SurveySummary = { questions: Question[]; min_group: number; teams: SurveyTeam[] };
export type Report = {
  as_of: string; window_days: number; projects: string[]; figures: ReportFigure[]; surveys: SurveySummary;
  assumptions: Record<string, number>; why: string[];
};

/* ---------- handovers, feedback, people, audit ---------- */
export type HandoverItem = { key: string; kind: string; title: string; ref: string; done: boolean; minutes?: number };
export type Handover = Explained & {
  id: string; project_id: string; user_id: string; last_day: string; status: "draft" | "in_progress" | "done";
  plan: Explained & { successor_id: string | null; handover_minutes: number; orphaned_files: string[]; items: HandoverItem[]; why: string[] };
  created_by: string; created_at: string;
};
export type Feedback = {
  id: string; project_id: string; target_kind: "doc" | "code" | "meeting" | "answer"; target_ref: string; kind: "unclear" | "outdated";
  note: string | null; owner_id: string; status: "open" | "resolved" | "dismissed"; created_by: string; created_at: string;
};
export type Member = { id: string; name: string; email: string; role: string; status: "active" | "deactivated"; projects: { id: string; name: string }[] };
/** POST /org/members/{id}/deactivate: the member, and what was waiting on them. */
export type Deactivated = Member & {
  needs_reassignment?: {
    expert_requests: { id: string; project_id: string; created_at: string }[];
    mentoring: { onboarding_id: string; project_id: string; hire: { id: string; name: string } | null }[];
  };
};
/** GET /audit: one page of the audit log. target_kind/target_name name the target when it is a person, an onboarding (the
 *  hire's name), a meeting, a project, the organisation itself, an access item ("GitHub access for Arjun"), a handover (the
 *  leaver's name) or documentation feedback (what it was on); target_link is where it lives in the app (an API-style path). */
export type AuditSort = "time" | "action" | "actor" | "target";
export type AuditOrder = "desc" | "asc";
export type AuditTargetKind = "person" | "onboarding" | "meeting" | "project" | "organisation" | "access" | "handover" | "feedback" | "task";
export type AuditRow = { id: number; created_at: string; actor_id: string | null; actor_name: string | null; actor_email: string | null; action: string; target: string; detail: unknown;
  target_kind?: AuditTargetKind | null; target_name?: string | null; target_link?: string | null };
export type AuditPage = { items: AuditRow[]; total: number; limit: number; offset: number; next_offset: number | null; retention_days: number;
  sort?: AuditSort; order?: AuditOrder; page?: number; pages?: number; as_of?: number | null };
/** GET /audit/facets: what the log holds, for the filter menus. */
export type AuditFacets = { actions: { action: string; count: number }[]; actors: { id: string; name: string | null; email: string | null; count: number }[];
  system: number; first_at: string | null; last_at: string | null; retention_days: number };

/* ---------- visibility ---------- */
export type VisibilityField = { label: string; visible_to: string[]; source: string; how: string; value: unknown };
export type Visibility = { onboarding_id: string; hire: Person; statement: string; private: string[]; data: Record<string, VisibilityField> };

/* ---------- config, demo clock, enrolment ---------- */
export type LifecycleConfig = {
  features: Record<string, boolean>; survey_min_group: number; report_min_median: number; report_min_rate: number;
  checkin_days: number[]; preboarding_link_days: number; tracks: string[]; levels: string[];
  /** the lifecycle's today (YYYY-MM-DD) and, in the demo, how many days the clock has moved */
  today?: string; offset_days?: number;
};
export type Advance = { offset_days: number; today: string; programs: { onboarding_id: string; day: number }[] };
export type Enrolled = {
  onboarding: { id: string; project_id: string; new_hire_id: string; start_date: string; status: string };
  program: unknown;
  /** the invitation to share (no email is sent); with the draft-program flow it may be created only when the plan is approved */
  invitation: { id: string; email: string; expires_at?: string; url?: string; link?: string; invite_url?: string } | null;
  plan: { status: PlanState; pending_approval_id: string | null; proposed_tasks?: number; why?: string[] } | null;
};
