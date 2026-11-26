/* The product scenes' data: the seeded demo project (backend/app/seed.py, "Mini Policy Engine"), in the app's own shapes
   (lifecycle/types.ts). Every name, file, line, timestamp and title below is what the live demo shows: the repository is
   backend/sample_repo, the KT call is "KT #1: How validation works" (Ravi explains check_labels at 0:55), Vijay is twelve
   days into his program, and his first task is issue #15. The leadership figures are illustrative (labelled so on the page);
   their labels, units and explanations are the report's own (backend/app/lifecycle/reporting.py). */
import type { Milestone, ReportFigure } from "../../../lifecycle/types";

export const KT = {
  title: "KT #1: How validation works",
  speaker: "Ravi",
  recorded: "Recorded Thu 14:32",
  length: "4:20",
  lengthS: 260,
  moment: { at: "0:55", start: 55, end: 95, symbol: "check_labels" },
  line: "check_labels compares the required labels with what the resource has. Each missing label becomes a violation string.",
  decision: "Important decision: we never raise inside validation. We return a list of violations so the webhook can report all of them at once.",
  /** when Ravi (or Vijay) is speaking, seconds: the transcript's segments */
  speech: [[4, 18], [20, 52], [55, 95], [98, 140], [142, 180], [182, 230], [232, 260]] as [number, number][],
};

export const REPO = "mini-policy-engine";
export const FILE = "engine/validate.py";

/** backend/sample_repo/engine/validate.py, lines 9-23 (the real file) */
export const CODE: [number, string][] = [
  [9, "def validate_resource(resource: dict, policy: Policy) -> list[str]:"],
  [10, '    """Return a list of violations. An empty list means the resource is allowed."""'],
  [11, "    violations = []"],
  [12, "    for rule in policy.rules:"],
  [13, '        if rule.kind != resource.get("kind"):'],
  [14, "            continue"],
  [15, "        violations += check_labels(resource, rule)"],
  [16, "        violations += check_privileged(resource, rule)"],
  [17, "    return violations"],
  [18, ""],
  [19, ""],
  [20, "def check_labels(resource: dict, rule: Rule) -> list[str]:"],
  [21, '    labels = resource.get("metadata", {}).get("labels", {})'],
  [22, "    return [f\"{rule.name}: missing label '{key}'\" for key in rule.require_labels if key not in labels]"],
];
/** the lines the KT moment covers (the screen moment at 0:55 shows check_labels) */
export const EXPLAINED: [number, number] = [20, 22];

/** what else the call is linked to (the KT linker's links in the seed) */
export const LINKS = [
  { file: "engine/validate.py", symbol: "validate_resource", at: "0:20" },
  { file: "engine/validate.py", symbol: "check_labels", at: "0:55" },
  { file: "engine/mutate.py", symbol: "apply_defaults", at: "3:02" },
];

export const ANSWER = {
  q: "How does check_labels report missing labels?",
  a: "It returns one violation string per missing label instead of raising, so the webhook can report every violation at once.",
  sources: [
    { kind: "code" as const, label: "engine/validate.py:20–22" },
    { kind: "kt" as const, label: "KT #1 · 0:55" },
  ],
  verified: "Verified by Ravi",
};

const due = (d: number) => `2026-11-${String(2 + d).padStart(2, "0")}`;
const m = (key: string, title: string, due_day: number, state: Milestone["state"]): Milestone => ({
  key, title, due_day, due_date: due(due_day), status: state === "done" ? "done" : "pending", state,
  done_at: state === "done" ? due(due_day) : null, evidence: {}, auto: true,
});
/** Vijay's program, day 12 of 90 (the backend-mid template's milestones) */
export const DAY = 12;
export const DURATION = 90;
export const MILESTONES: Milestone[] = [
  m("preboarding_complete", "Pre-boarding complete", 0, "done"),
  m("environment_ready", "Environment ready", 2, "done"),
  m("meet_team", "Met the team", 4, "done"),
  m("first_pr_opened", "First pull request opened", 14, "due_soon"),
  m("service_traced", "Traced one request end to end", 21, "upcoming"),
  m("first_pr_merged", "First change merged", 30, "upcoming"),
  m("checkin_30", "Day-30 check-in", 30, "upcoming"),
  m("checkin_60", "Day-60 check-in", 60, "upcoming"),
  m("checkin_90", "Day-90 check-in", 90, "upcoming"),
];

export const TASK = { issue: 15, title: "Return a clear error when a policy has no name", label: "good first issue" };

/** the journey of scene 5, in order */
export const JOURNEY = [
  { key: "path", title: "Learning path", detail: "Day 12 of 90 · engine area" },
  { key: "kt", title: "Recorded explanation", detail: "KT #1 · How validation works · 0:55" },
  { key: "task", title: "First scoped task", detail: "#15 Return a clear error when a policy has no name" },
  { key: "pre", title: "Private pre-submission review", detail: "1 note before anyone else sees it" },
  { key: "pr", title: "Pull request", detail: "#16 · engine/policy.py · +9 −1 · test added" },
  { key: "ok", title: "Review and approval", detail: "Approved by Ravi · merged" },
] as const;

const fig = (key: string, label: string, unit: string, how: string, cur: number | null, prev: number | null, n: number, pn: number): ReportFigure => ({
  key, label, unit, how,
  current: { value: cur, n, display: cur === null ? "not enough data" : `${cur}${unit ? ` ${unit}` : ""}` },
  previous: { value: prev, n: pn, display: prev === null ? "not enough data" : `${prev}${unit ? ` ${unit}` : ""}` },
});

/** Illustrative: the report's own figures and explanations, with made-up values */
export const REPORT: ReportFigure[] = [
  fig("days_to_first_merge", "Median days to the first merged change", "days",
    "Start date to the 'first change merged' milestone; at least 3 hires.", 9, 21, 6, 5),
  fig("milestones_on_time", "Milestones done on time", "%",
    "Milestones done by their due date, of those due so far (skipped ones left out).", 86, 64, 42, 37),
  fig("kt_coverage", "Modules explained in a KT call", "%",
    "Modules with at least one linked KT moment, of all modules in the connected repositories.", 71, 38, 24, 24),
  fig("answered_from_sources", "Questions answered from sources", "%",
    "The hires' questions answered with cited sources, of all they asked.", 78, 41, 212, 130),
  fig("senior_hours_saved", "Senior time saved (estimate)", "hours",
    "(Questions answered without a person + verified answers and KT clips reused) x 15 minutes per avoided interruption.", 46, 12, 212, 130),
  fig("single_owner_critical_areas", "Single-owner critical areas", "",
    "Code areas holding a critical-risk file that one person alone owns. Nothing records it over time yet.", 2, null, 9, 0),
];
