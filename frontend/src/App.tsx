import { useMemo, useState, type ReactNode } from "react";

type Role = "newhire" | "expert" | "manager" | "admin";
type ViewName =
  | "roadmap"
  | "ask"
  | "verify"
  | "reverify"
  | "dashboard"
  | "sources"
  | "team"
  | "users"
  | "profile";

type User = {
  name: string;
  email: string;
  department: string;
  role: Role;
};

type Citation = { label: string; ref: string };
type QAItem = {
  id: string;
  domain: string;
  owner: string;
  question: string;
  answer: string;
  citations: Citation[];
  status: "verified" | "draft";
  verifiedBy?: string;
  verifiedAt?: string;
  roles: Role[];
  stale?: boolean;
  staleReason?: string;
};

type SourceItem = {
  name: string;
  role: string;
  status: "indexed" | "syncing" | "error";
  synced: string;
};

const ROLE_LABEL: Record<Role, string> = {
  newhire: "New Hire",
  expert: "Domain Expert",
  manager: "Manager",
  admin: "Admin",
};

const VIEW_META: Record<ViewName, { eyebrow: string; title: string; desc: string }> = {
  roadmap: {
    eyebrow: "Proactive roadmap",
    title: "Your first week",
    desc: "Generated the moment you were added — no prompt required, built only from content tagged for your role.",
  },
  ask: {
    eyebrow: "Grounded Q&A",
    title: "Ask & Evidence",
    desc: "Every answer is generated strictly from retrieved, role-permitted context and carries a trust label plus clickable evidence.",
  },
  verify: {
    eyebrow: "Verification & trust",
    title: "Verify Queue",
    desc: "Turn a correct AI draft into the canonical, owner-stamped answer everyone sees next.",
  },
  reverify: {
    eyebrow: "Staleness & freshness",
    title: "Re-verification Queue",
    desc: "Verified answers whose source material changed, waiting on a fresh confirmation.",
  },
  dashboard: {
    eyebrow: "Manager analytics",
    title: "Coverage & gaps",
    desc: "Where documentation is thin, and how much of your knowledge base is actually trusted.",
  },
  sources: {
    eyebrow: "Ingestion",
    title: "Source Connect",
    desc: "Read-only connections only — Welcome Wave never writes back to anything it indexes.",
  },
  team: {
    eyebrow: "Department visibility",
    title: "My Team",
    desc: "New hires assigned to your department, with onboarding progress and question activity.",
  },
  users: {
    eyebrow: "Role & permission scoping",
    title: "Users & Departments",
    desc: "Manage functional roles and department scope for every Welcome Wave identity.",
  },
  profile: {
    eyebrow: "Personal workspace",
    title: "My Profile",
    desc: "Your identity, department scope, onboarding progress, and activity in Welcome Wave.",
  },
};

const DEFAULT_USERS: User[] = [
  { name: "Aman Nautiyal", email: "aman@company.com", department: "Engineering", role: "newhire" },
  { name: "Priya N.", email: "priya@company.com", department: "Engineering", role: "expert" },
  { name: "Meera Pillai", email: "meera@company.com", department: "Sales", role: "manager" },
  { name: "Sanjay Admin", email: "sanjay@company.com", department: "All", role: "admin" },
];

const DEPARTMENTS = ["Engineering", "Sales", "HR"];

const INITIAL_QA: QAItem[] = [
  {
    id: "ask-1",
    domain: "Onboarding",
    owner: "Aman Nautiyal",
    question: "What's my first task this week?",
    answer:
      "Set up your local dev environment using the README in the platform repo, then shadow one live retrieval request in the #onboarding notes.",
    citations: [{ label: "onboarding-checklist.md", ref: "§ Week 1" }],
    status: "verified",
    verifiedBy: "Aman Nautiyal",
    verifiedAt: "Week 1 · Mon",
    roles: ["newhire"],
  },
  {
    id: "ask-2",
    domain: "Access & Permissions",
    owner: "Priya N.",
    question: "How do I request access to the CRM data source?",
    answer:
      "Ask your manager to add you as a viewer role in the CRM connector config — there is currently no self-service request flow.",
    citations: [{ label: "source-access.md", ref: "§ Access requests" }],
    status: "draft",
    roles: ["newhire", "expert", "admin"],
  },
  {
    id: "ask-3",
    domain: "Finance Systems",
    owner: "J. Rao",
    question: "Who owns the payroll integration docs?",
    answer:
      "The payroll integration is documented by the Finance Systems team; the most recently listed owner is J. Rao.",
    citations: [{ label: "finance-systems-wiki export", ref: "Payroll section" }],
    status: "draft",
    roles: ["newhire", "manager", "admin"],
  },
  {
    id: "ask-4",
    domain: "Access & Permissions",
    owner: "Priya N.",
    question: "What happens if a verified answer's source changes?",
    answer:
      "The dependency link to the changed chunk trips a staleness flag. The answer is visually downgraded until a domain expert re-confirms it.",
    citations: [{ label: "staleness-flow.md", ref: "§ Dependency graph" }],
    status: "verified",
    verifiedBy: "Priya N.",
    verifiedAt: "Week 3 · Wed",
    roles: ["expert"],
    stale: true,
    staleReason: "source-access.md was edited 2 days ago",
  },
  {
    id: "ask-5",
    domain: "Sales Enablement",
    owner: "T. Alvarez",
    question: "What's the current discount approval limit for reps?",
    answer:
      "Reps can approve discounts up to 12% without escalation; anything above requires sign-off from a sales manager.",
    citations: [{ label: "sales-playbook.docx", ref: "§ Discount policy" }],
    status: "verified",
    verifiedBy: "T. Alvarez",
    verifiedAt: "Week 2 · Fri",
    roles: ["newhire", "manager"],
    stale: true,
    staleReason: "sales-playbook.docx was updated yesterday",
  },
];

const ROLE_DATA: Record<Role, { title: string; overview: string; readFirst: { t: string; meta: string }[]; task: string }> = {
  newhire: {
    title: "Engineering · New Hire",
    overview: "This roadmap is generated from content tagged for the Engineering role only — not a generic template.",
    readFirst: [
      { t: "Local environment setup guide", meta: "platform-repo · README.md" },
      { t: "How retrieval + verification fits together", meta: "architecture.md · § End-to-end flow" },
      { t: "Who to ask in each domain", meta: "team-directory.md" },
    ],
    task: "Set up your local environment and shadow one live retrieval request.",
  },
  expert: {
    title: "Platform · Domain Expert",
    overview: "Your roadmap surfaces the review duties tied to your domain, not general reading.",
    readFirst: [
      { t: "What 'verify' actually commits to", meta: "verification-workflow.md" },
      { t: "How staleness dependency links work", meta: "staleness-flow.md" },
      { t: "Your current pending drafts", meta: "2 waiting in Verify Queue" },
    ],
    task: "Clear the two drafts currently waiting in your Verify Queue.",
  },
  manager: {
    title: "Platform Team · Manager",
    overview: "Your roadmap points at gaps in your team's documentation, not general onboarding steps.",
    readFirst: [
      { t: "Reading the coverage dashboard", meta: "manager-guide.md" },
      { t: "This week's top unanswered question", meta: "CRM data source access" },
      { t: "Re-verification backlog owner list", meta: "1 item currently flagged" },
    ],
    task: "Review this week's verified-coverage gaps on the Dashboard.",
  },
  admin: {
    title: "Platform Ops · Admin",
    overview: "Your roadmap starts at the ingestion layer — nothing is indexed until you connect a source.",
    readFirst: [
      { t: "Read-only ingestion guarantee", meta: "security-design.md · § 9.4" },
      { t: "Scoping include/exclude before indexing", meta: "source-connect.md" },
      { t: "Assigning sources to roles", meta: "role-scoping.md" },
    ],
    task: "Connect your team's first knowledge source.",
  },
};

const NAV_ITEMS: Array<{ view: ViewName; label: string; roles: Role[] }> = [
  { view: "roadmap", label: "Roadmap", roles: ["newhire", "expert", "manager", "admin"] },
  { view: "ask", label: "Ask & Evidence", roles: ["newhire", "expert", "manager", "admin"] },
  { view: "verify", label: "Verify Queue", roles: ["expert", "admin"] },
  { view: "reverify", label: "Re-verification", roles: ["expert", "admin"] },
  { view: "dashboard", label: "Dashboard", roles: ["manager", "admin"] },
  { view: "sources", label: "Source Connect", roles: ["admin"] },
  { view: "team", label: "My Team", roles: ["expert", "manager"] },
  { view: "users", label: "Users & Departments", roles: ["admin"] },
  { view: "profile", label: "Profile", roles: ["newhire", "expert", "manager", "admin"] },
];

const STYLES = `
  :root {
    --ink:#14171C;
    --ink-soft:#3C4038;
    --muted:#6E7166;
    --paper:#ECEEE7;
    --surface:#FBFBF8;
    --border:#D7D9CE;
    --border-soft:#E3E5DB;
    --violet:#6A4FD1;
    --violet-tint:#EEEAFB;
    --teal:#0C7D76;
    --teal-tint:#E4F3F1;
    --amber:#A9720C;
    --amber-tint:#FBF0DD;
    --coral:#C8492F;
    --coral-tint:#FBEAE5;
    --radius:3px;
  }
  * { box-sizing:border-box; }
  html, body { margin:0; padding:0; }
  body {
    background:var(--paper); color:var(--ink); font-family:'Inter',sans-serif; font-size:15px; line-height:1.6; -webkit-font-smoothing:antialiased;
  }
  h1,h2,h3,h4 { font-family:'Source Serif 4',serif; font-weight:600; margin:0; letter-spacing:-0.01em; color:var(--ink); }
  p { margin:0; }
  button { font-family:inherit; }
  .mono { font-family:'IBM Plex Mono',monospace; font-size:0.82em; letter-spacing:0.01em; }
  a { color:inherit; }
  .app { display:grid; grid-template-columns:256px 1fr; min-height:100vh; min-width:0; }
  .sidebar { background:var(--ink); color:#EDEDE6; padding:26px 18px; display:flex; flex-direction:column; position:sticky; top:0; height:100vh; }
  .brand { display:flex; align-items:baseline; gap:9px; padding:2px 6px 22px; border-bottom:1px solid rgba(255,255,255,0.11); margin-bottom:18px; }
  .brand-mark { width:8px; height:8px; border-radius:1px; background:var(--teal); flex:none; transform:translateY(-2px) rotate(45deg); }
  .brand-name { font-family:'Source Serif 4',serif; font-weight:600; font-size:19px; color:#fff; }
  .brand-sub { font-size:11.5px; color:#8F9288; margin-top:1px; padding-left:6px; font-style:italic; font-family:'Source Serif 4',serif; }
  .nav-group { margin-bottom:6px; }
  .nav-label { font-size:11px; text-transform:none; color:#83857D; padding:4px 10px 6px; font-weight:500; }
  .nav-item { display:flex; align-items:center; gap:10px; width:100%; text-align:left; background:none; border:none; color:#BFC2B7; padding:9px 10px; border-radius:3px; font-size:14px; cursor:pointer; position:relative; transition:background .12s ease, color .12s ease; border-left:2px solid transparent; }
  .nav-item:hover { background:rgba(255,255,255,0.06); color:#fff; }
  .nav-item.active { background:rgba(255,255,255,0.09); color:#fff; }
  .nav-item[data-view="roadmap"].active { border-left-color:var(--violet); }
  .nav-item[data-view="ask"].active { border-left-color:var(--teal); }
  .nav-item[data-view="verify"].active { border-left-color:var(--amber); }
  .nav-item[data-view="reverify"].active { border-left-color:var(--coral); }
  .nav-item[data-view="dashboard"].active { border-left-color:var(--teal); }
  .nav-item[data-view="sources"].active { border-left-color:var(--violet); }
  .nav-item[data-view="team"].active { border-left-color:var(--teal); }
  .nav-item[data-view="users"].active { border-left-color:var(--coral); }
  .nav-item[data-view="profile"].active { border-left-color:var(--violet); }
  .nav-icon { width:16px; height:16px; flex:none; opacity:.9; }
  .nav-count { margin-left:auto; font-size:11px; font-family:'IBM Plex Mono',monospace; background:rgba(255,255,255,0.14); color:#fff; border-radius:9px; padding:1px 6px; min-width:18px; text-align:center; }
  .sidebar-footer { margin-top:auto; padding:10px 8px 2px; font-size:11.5px; color:#8F9288; border-top:1px solid rgba(255,255,255,0.1); padding-top:14px; }
  .sidebar-footer .row { display:flex; justify-content:space-between; margin-bottom:7px; }
  .sidebar-footer .row span:last-child { color:#DADCD2; font-family:'IBM Plex Mono',monospace; font-size:11px; }
  main { padding:40px 56px 70px; max-width:1360px; min-width:0; overflow:hidden; }
  .topbar { display:flex; align-items:flex-start; justify-content:space-between; gap:24px; margin-bottom:28px; flex-wrap:wrap; }
  .topbar-title .eyebrow { font-size:12.5px; color:var(--muted); margin-bottom:4px; }
  .topbar-title h1 { font-size:32px; font-weight:600; }
  .topbar-title .desc { color:var(--muted); font-size:14px; margin-top:7px; max-width:56ch; }
  .topbar-actions { position:relative; display:flex; align-items:flex-start; }
  .profile-menu-trigger { display:flex; align-items:center; gap:9px; border:1px solid var(--border); background:var(--surface); color:var(--ink); border-radius:3px; padding:7px 10px; cursor:pointer; text-align:left; min-width:190px; }
  .profile-menu-trigger:hover { border-color:#A9ABA0; background:var(--paper); }
  .profile-menu-avatar { width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center; background:var(--violet-tint); color:var(--violet); font-family:'Source Serif 4',serif; font-weight:600; font-size:12px; flex:none; }
  .profile-menu-copy { display:flex; flex-direction:column; min-width:0; flex:1; }
  .profile-menu-name { font-size:12.5px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .profile-menu-role { font-size:11px; color:var(--muted); white-space:nowrap; }
  .profile-menu-chevron { font-size:12px; color:var(--muted); }
  .profile-menu { position:absolute; right:0; top:calc(100% + 7px); z-index:25; width:190px; padding:5px; background:var(--surface); border:1px solid var(--border); border-radius:3px; box-shadow:0 12px 24px rgba(20,23,17,.12); }
  .profile-menu[hidden] { display:none; }
  .profile-menu button { display:block; width:100%; border:0; background:transparent; color:var(--ink-soft); padding:8px 10px; text-align:left; font-size:12.5px; cursor:pointer; border-radius:2px; }
  .profile-menu button:hover { background:var(--paper); color:var(--ink); }
  .profile-menu .menu-danger { color:var(--coral); }
  .view { display:none; opacity:0; transform:translateY(6px); }
  .view.active { display:block; animation:viewIn .28s ease forwards; }
  @keyframes viewIn { to { opacity:1; transform:translateY(0); } }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:26px 30px; position:relative; min-width:0; }
  .card + .card { margin-top:16px; }
  .accent-bar { position:absolute; top:0; left:0; bottom:0; width:3px; }
  .accent-violet .accent-bar { background:var(--violet); }
  .accent-teal .accent-bar { background:var(--teal); }
  .accent-amber .accent-bar { background:var(--amber); }
  .accent-coral .accent-bar { background:var(--coral); }
  .accent-violet, .accent-teal, .accent-amber, .accent-coral { padding-left:27px; }
  .section-heading { display:flex; align-items:baseline; gap:10px; margin-bottom:16px; }
  .section-heading h2 { font-size:18px; font-weight:600; }
  .section-heading .count { font-size:12.5px; color:var(--muted); font-style:italic; font-family:'Source Serif 4',serif; }
  .btn { display:inline-flex; align-items:center; gap:6px; border-radius:3px; border:1px solid var(--border); background:var(--surface); color:var(--ink); font-size:13.5px; padding:8px 15px; cursor:pointer; transition:background .12s ease, border-color .12s ease, transform .1s ease; }
  .btn:hover { border-color:#A9ABA0; }
  .btn-primary { background:var(--ink); color:#fff; border-color:var(--ink); }
  .btn-primary:hover { background:#2B2F27; }
  .btn-ghost { border-color:transparent; background:transparent; }
  .btn-ghost:hover { background:var(--paper); }
  .btn-sm { padding:5px 11px; font-size:12.5px; }
  .btn:disabled { opacity:.45; cursor:not-allowed; }
  .btn-teal { background:var(--teal); color:#fff; border-color:var(--teal); }
  .btn-teal:hover { background:#0A6B65; }
  .btn-amber { background:var(--amber); color:#fff; border-color:var(--amber); }
  .btn-amber:hover { background:#8F5E09; }
  .btn-coral { background:var(--coral); color:#fff; border-color:var(--coral); }
  .btn-coral:hover { background:#A93B24; }
  .btn-violet { background:var(--violet); color:#fff; border-color:var(--violet); }
  .btn-violet:hover { background:#5940B5; }
  .btn:active { transform:scale(.96); }
  .badge { display:inline-flex; align-items:center; gap:6px; font-size:11.5px; font-weight:600; padding:4px 9px; line-height:1.35; border-radius:2px; }
  .badge svg { width:12px; height:12px; flex:none; }
  .badge-verified { font-family:'IBM Plex Mono',monospace; text-transform:uppercase; letter-spacing:0.05em; background:var(--amber-tint); color:#7A560A; border:1px solid #D8B676; transform:rotate(-1.4deg); }
  .badge-draft { font-family:'Source Serif 4',serif; font-style:italic; font-weight:500; font-size:13px; background:transparent; color:var(--ink-soft); border:1px dashed #ACAEA0; border-radius:20px; padding:3px 11px; }
  .badge-stale { font-family:'IBM Plex Mono',monospace; text-transform:uppercase; letter-spacing:0.05em; background:var(--coral-tint); color:#9C3A24; border:1px solid #E3A594; }
  .badge-teal { background:var(--teal-tint); color:#0B4F4A; border-radius:20px; font-size:12px; }
  .badge-violet { background:var(--violet-tint); color:#4A3798; border-radius:20px; font-size:12px; }
  .icon-btn { border:none; background:none; cursor:pointer; color:var(--muted); padding:4px; border-radius:3px; display:inline-flex; }
  .icon-btn:hover { background:var(--paper); color:var(--ink); }
  input[type=text], input[type=search], input[type=email], textarea, select {
    font-family:inherit; font-size:14px; border:1px solid var(--border); border-radius:3px; padding:9px 12px; background:var(--surface); color:var(--ink); width:100%;
  }
  input:focus, textarea:focus, select:focus, button:focus-visible, .nav-item:focus-visible { outline:2px solid var(--teal); outline-offset:1px; }
  label { font-size:13px; font-weight:500; color:var(--ink-soft); display:block; margin-bottom:6px; }
  .muted { color:var(--muted); }
  .small { font-size:12.5px; }
  .roadmap-grid { display:grid; grid-template-columns:1.6fr 1fr; gap:20px; }
  .checklist { list-style:none; margin:12px 0 0; padding:0; }
  .checklist li { display:flex; gap:10px; align-items:flex-start; padding:10px 0; border-bottom:1px solid var(--border-soft); }
  .checklist li:last-child { border-bottom:none; }
  .checklist input[type=checkbox] { margin-top:3px; width:16px; height:16px; accent-color:var(--violet); flex:none; }
  .checklist label { margin:0; font-weight:400; font-size:14px; color:var(--ink); cursor:pointer; }
  .checklist li.done label { color:var(--muted); text-decoration:line-through; }
  .checklist .item-meta { font-size:12px; color:var(--muted); margin-top:2px; }
  .roadmap-progress { margin:16px 0 4px; padding:12px 0 4px; border-top:1px solid var(--border-soft); }
  .roadmap-progress-head { display:flex; justify-content:space-between; gap:12px; font-size:12px; color:var(--muted); margin-bottom:7px; }
  .roadmap-progress-track { height:6px; background:var(--border-soft); border-radius:2px; overflow:hidden; }
  .roadmap-progress-fill { height:100%; width:0; background:var(--violet); transition:width .25s ease; }
  .task-callout { background:var(--violet-tint); border:1px solid #DCD3F7; border-left:3px solid var(--violet); border-radius:2px; padding:16px 19px; margin-top:16px; }
  .task-callout .label { font-size:12.5px; color:#5C46B4; font-weight:600; font-style:italic; font-family:'Source Serif 4',serif; margin-bottom:6px; }
  .task-callout p { font-size:14.5px; }
  .ask-box { display:flex; gap:8px; }
  .chip-row { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
  .chip { border:1px solid var(--border); background:var(--surface); border-radius:20px; padding:6px 12px; font-size:12.5px; cursor:pointer; color:var(--ink-soft); }
  .chip:hover { border-color:var(--teal); color:var(--teal); }
  .answer-block { margin-top:22px; padding-top:20px; border-top:1px solid var(--border-soft); }
  .answer-head { display:flex; align-items:center; gap:10px; margin-bottom:12px; flex-wrap:wrap; }
  .answer-question { font-size:13px; color:var(--muted); font-style:italic; font-family:'Source Serif 4',serif; }
  .answer-text { font-size:15.5px; line-height:1.7; color:var(--ink); }
  .citation-list { display:flex; flex-direction:column; gap:7px; margin-top:16px; }
  .citation { display:flex; align-items:center; gap:8px; font-size:12px; font-family:'IBM Plex Mono',monospace; color:#0B4F4A; background:var(--teal-tint); border-radius:2px; padding:7px 11px; width:fit-content; cursor:pointer; border:1px solid transparent; }
  .citation:hover { border-color:var(--teal); }
  .citation svg { width:13px; height:13px; }
  .state-empty, .state-error, .state-loading { display:flex; flex-direction:column; align-items:flex-start; gap:8px; padding:26px 4px 6px; color:var(--muted); }
  .state-empty svg, .state-error svg { width:26px; height:26px; opacity:.7; }
  .skeleton { height:13px; border-radius:2px; background:linear-gradient(90deg,#E4E6DB 25%,#F1F2EB 37%,#E4E6DB 63%); background-size:400% 100%; animation:shimmer 1.3s ease infinite; margin-bottom:8px; }
  @keyframes shimmer { 0% { background-position:100% 0; } 100% { background-position:0 0; } }
  .demo-link { font-size:12px; color:var(--muted); text-decoration:underline; cursor:pointer; background:none; border:none; padding:0; margin-top:16px; }
  .demo-link:hover { color:var(--ink); }
  .queue-row { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; padding:16px 0; border-bottom:1px solid var(--border-soft); }
  .queue-row:last-child { border-bottom:none; }
  .queue-row.leaving { opacity:0; transform:translateX(6px); transition:opacity .35s, transform .35s; }
  .queue-q { font-family:'Source Serif 4',serif; font-size:16px; font-weight:600; margin-bottom:5px; }
  .queue-a { font-size:13.5px; color:var(--ink-soft); max-width:60ch; }
  .queue-meta { font-size:12px; color:var(--muted); margin-top:7px; display:flex; gap:10px; flex-wrap:wrap; font-family:'IBM Plex Mono',monospace; }
  .queue-actions { display:flex; gap:8px; flex:none; padding-top:2px; }
  .filter-row { display:flex; gap:10px; align-items:center; margin-bottom:16px; }
  .filter-row select { width:auto; padding:7px 10px; font-size:13px; }
  .kpi-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:18px; margin-bottom:18px; }
  .kpi { padding:18px 20px; }
  .kpi .kpi-label { font-size:12.5px; color:var(--muted); margin-bottom:10px; display:flex; align-items:center; gap:6px; }
  .kpi .kpi-value { font-family:'Source Serif 4',serif; font-size:32px; font-weight:600; }
  .kpi .kpi-sub { font-size:12px; color:var(--muted); margin-top:5px; font-style:italic; font-family:'Source Serif 4',serif; }
  .bar-row { display:flex; align-items:center; gap:12px; margin-bottom:13px; }
  .bar-row .bar-label { width:120px; font-size:13px; flex:none; color:var(--ink-soft); }
  .bar-track { flex:1; height:7px; border-radius:1px; background:var(--border-soft); overflow:hidden; }
  .bar-fill { height:100%; transition:width .7s cubic-bezier(.22,.9,.3,1); }
  .bar-row .bar-value { width:44px; text-align:right; font-size:12px; color:var(--muted); flex:none; font-family:'IBM Plex Mono',monospace; }
  .rank-list { list-style:none; margin:0; padding:0; }
  .rank-list li { display:flex; justify-content:space-between; gap:10px; padding:10px 0; border-bottom:1px solid var(--border-soft); font-size:13.5px; }
  .rank-list li:last-child { border-bottom:none; }
  .rank-list .n { color:var(--muted); font-family:'IBM Plex Mono',monospace; font-size:12px; margin-right:8px; }
  .rank-list .count { color:var(--muted); font-family:'IBM Plex Mono',monospace; font-size:12.5px; }
  .dropzone { border:1.5px dashed var(--border); border-radius:2px; padding:28px; text-align:center; color:var(--muted); font-size:13.5px; background:var(--paper); cursor:pointer; }
  .dropzone:hover { border-color:var(--teal); color:var(--teal); }
  .form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr)); gap:16px; margin-top:16px; }
  .form-field { margin-bottom:14px; }
  .scope-row { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
  .scope-chip { display:flex; align-items:center; gap:6px; border:1px solid var(--border); border-radius:20px; padding:5px 10px 5px 12px; font-size:12.5px; background:var(--surface); }
  .scope-chip button { border:none; background:none; cursor:pointer; color:var(--muted); padding:0; display:flex; }
  .lock-note { display:flex; align-items:center; gap:8px; font-size:12.5px; color:var(--ink-soft); background:var(--paper); border:1px solid var(--border); border-radius:6px; padding:10px 12px; margin-top:16px; }
  .lock-note svg { width:14px; height:14px; flex:none; color:var(--muted); }
  .profile-grid { display:grid;grid-template-columns:minmax(240px,.8fr) minmax(0,1.2fr);gap:20px; }
  .profile-identity-card { display:flex;flex-direction:column;justify-content:center;min-height:238px; }
  .profile-avatar { width:64px;height:64px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:var(--violet-tint);color:var(--violet);font-family:'Source Serif 4',serif;font-size:23px;font-weight:600;margin-bottom:16px; }
  .profile-badges { display:flex;gap:7px;flex-wrap:wrap;margin-top:16px; }
  .profile-details { margin:0;display:grid;gap:0; }
  .profile-details div { display:flex;justify-content:space-between;gap:20px;padding:12px 0;border-bottom:1px solid var(--border-soft); }
  .profile-details div:last-child { border-bottom:none; }
  .profile-details dt { color:var(--muted);font-size:13px; }
  .profile-details dd { margin:0;color:var(--ink);font-size:13.5px;text-align:right;overflow-wrap:anywhere; }
  .profile-metrics { display:grid;grid-template-columns:repeat(3,1fr);gap:14px; }
  .profile-metrics>div { display:flex;flex-direction:column;gap:3px;padding:14px;background:var(--paper);border:1px solid var(--border-soft);border-radius:3px;min-width:0; }
  .profile-metric-value { font-family:'Source Serif 4',serif;font-size:24px;font-weight:600;overflow-wrap:anywhere; }
  .profile-metric-label { font-size:12px;color:var(--muted); }
  .profile-access-list { display:flex;gap:8px;flex-wrap:wrap;margin-top:14px; }
  .profile-access-list span { border:1px solid var(--border);background:var(--surface);color:var(--ink-soft);border-radius:20px;padding:6px 11px;font-size:12.5px; }
  table.sources-table { width:100%; border-collapse:collapse; margin-top:8px; font-size:13.5px; }
  table.sources-table th { text-align:left; font-size:11.5px; color:var(--muted); font-weight:500; padding:8px 10px; border-bottom:1px solid var(--border); }
  table.sources-table td { padding:11px 10px; border-bottom:1px solid var(--border-soft); vertical-align:middle; }
  .status-pill { display:inline-flex; align-items:center; gap:6px; font-size:12px; padding:3px 8px; border-radius:20px; }
  .status-indexed { background:var(--amber-tint); color:#7A560A; }
  .status-syncing { background:var(--teal-tint); color:#0B6567; }
  .status-error { background:var(--coral-tint); color:#9C3A24; }
  .dot { width:6px;height:6px;border-radius:50%; background:currentColor; }
  .menu-toggle { display:none; }
  .scrim { display:none; position:fixed; inset:0; background:rgba(0,0,0,.3); z-index:15; }
  .landing { min-height:100vh; display:flex; align-items:center; justify-content:center; padding:48px 24px; position:relative; overflow:hidden; }
  .landing::before { content:''; position:absolute; inset:0; background: radial-gradient(600px 380px at 12% -8%, rgba(106,79,209,0.14), transparent 60%), radial-gradient(560px 360px at 90% 8%, rgba(12,125,118,0.13), transparent 60%), radial-gradient(520px 340px at 78% 96%, rgba(169,114,12,0.11), transparent 60%), radial-gradient(520px 320px at 6% 96%, rgba(200,73,47,0.10), transparent 60%); pointer-events:none; }
  .landing-inner { max-width:960px; width:100%; position:relative; }
  .landing-brand { display:flex; align-items:center; justify-content:center; gap:10px; margin-bottom:12px; }
  .landing-brand .brand-cluster { display:flex; gap:3px; }
  .landing-brand .brand-cluster span { width:9px;height:9px;border-radius:2px; }
  .landing-brand .brand-name { font-family:'Source Serif 4',serif; font-weight:600; font-size:23px; color:var(--ink); }
  .landing-tagline { text-align:center; color:var(--ink-soft); font-size:16px; margin-bottom:28px; font-family:'Source Serif 4',serif; font-style:italic; }
  .how-strip { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:38px; }
  .how-step { background:var(--surface); border:1px solid var(--border-soft); border-radius:4px; padding:14px 16px; display:flex; gap:12px; align-items:flex-start; }
  .how-step .num { font-family:'IBM Plex Mono',monospace; font-size:11px; font-weight:600; width:22px; height:22px; border-radius:50%; flex:none; display:flex; align-items:center; justify-content:center; color:#fff; }
  .how-step p { font-size:13px; color:var(--ink-soft); line-height:1.5; }
  .how-step strong { display:block; font-size:13.5px; color:var(--ink); margin-bottom:2px; font-weight:600; }
  .landing-heading { text-align:center; font-size:14px; color:var(--muted); margin-bottom:18px; letter-spacing:0.02em; }
  .role-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }
  .role-card { text-align:left; cursor:pointer; border:1px solid var(--border); background:var(--surface); border-radius:6px; padding:0; transition:border-color .18s ease, transform .18s ease, box-shadow .18s ease; display:flex; flex-direction:column; overflow:hidden; opacity:0; transform:translateY(10px); animation:cardIn .5s ease forwards; }
  .role-card:nth-child(1) { animation-delay:.05s; }
  .role-card:nth-child(2) { animation-delay:.12s; }
  .role-card:nth-child(3) { animation-delay:.19s; }
  .role-card:nth-child(4) { animation-delay:.26s; }
  @keyframes cardIn { to { opacity:1; transform:translateY(0); } }
  .role-card .card-top { height:5px; flex:none; }
  .role-card .card-body { padding:19px 18px 18px; display:flex; flex-direction:column; flex:1; }
  .role-card:hover { transform:translateY(-4px); box-shadow:0 14px 28px rgba(20,23,17,0.10); }
  .role-card[data-role="newhire"]:hover { border-color:var(--violet); }
  .role-card[data-role="expert"]:hover { border-color:var(--amber); }
  .role-card[data-role="manager"]:hover { border-color:var(--teal); }
  .role-card[data-role="admin"]:hover { border-color:var(--coral); }
  .role-card:focus-visible { outline:2px solid var(--teal); outline-offset:2px; }
  .role-icon { width:34px; height:34px; border-radius:50%; display:flex; align-items:center; justify-content:center; margin-bottom:13px; flex:none; }
  .role-icon svg { width:17px;height:17px; }
  .role-card h3 { font-size:16.5px; margin-bottom:6px; }
  .role-card .role-desc { font-size:12.5px; color:var(--muted); line-height:1.55; margin-bottom:14px; min-height:52px; }
  .access-tags { display:flex; flex-direction:column; gap:5px; margin-bottom:16px; }
  .access-tag { display:flex; align-items:center; gap:7px; font-size:12px; color:var(--ink-soft); }
  .access-tag .dot { width:6px;height:6px;border-radius:50%; flex:none; }
  .role-card .continue { margin-top:auto; font-size:12.5px; font-weight:600; color:var(--ink); display:flex; align-items:center; gap:5px; padding-top:12px; border-top:1px solid var(--border-soft); }
  .landing-footnote { text-align:center; font-size:12.5px; color:var(--muted); margin-top:30px; max-width:56ch; margin-left:auto; margin-right:auto; }
  .switch-role-btn { display:inline-flex; align-items:center; gap:6px; border:1px solid var(--border); background:var(--surface); border-radius:3px; padding:8px 13px; font-size:13px; color:var(--ink-soft); cursor:pointer; flex:none; height:fit-content; transition:transform .12s ease; }
  .switch-role-btn:hover { border-color:#A9ABA0; color:var(--ink); }
  .switch-role-btn:active { transform:scale(.96); }
  .switch-role-btn svg { width:13px;height:13px; }
  .role-pill { display:inline-flex; align-items:center; gap:6px; font-size:11.5px; color:#A9ACA0; padding:3px 0 14px; font-style:italic; font-family:'Source Serif 4',serif; }
  .role-pill .dot { width:6px;height:6px;border-radius:50%; background:#5EC9CB; }
  #consoleShell { display:none; }
  #loginView { display:none; }
  .login-card { max-width:380px; width:100%; margin:0 auto; background:var(--surface); border:1px solid var(--border); border-radius:6px; padding:36px 34px 30px; position:relative; box-shadow:0 24px 60px rgba(20,23,17,0.09); }
  .login-card .card-top { position:absolute; top:0; left:0; right:0; height:4px; border-radius:6px 6px 0 0; background:linear-gradient(90deg,var(--violet),var(--teal),var(--amber),var(--coral)); }
  .login-field { margin-bottom:16px; }
  .login-detection { background:var(--teal-tint); border:1px solid var(--border); color:var(--ink-soft); border-radius:3px; padding:9px 11px; font-size:12.5px; margin-bottom:16px; }
  .login-detection strong { color:var(--teal); }
  .sso-btn { width:100%; justify-content:center; padding:10px 14px; font-size:14px; font-weight:500; margin-bottom:10px; }
  .login-divider { display:flex; align-items:center; gap:10px; margin:18px 0; font-size:11.5px; color:var(--muted); }
  .login-divider::before, .login-divider::after { content:''; flex:1; height:1px; background:var(--border-soft); }
  .login-footnote { text-align:center; font-size:12px; color:var(--muted); margin-top:16px; }
  .info-tip { position:relative; display:inline-flex; align-items:center; justify-content:center; width:16px; height:16px; border-radius:50%; border:1px solid var(--border); color:var(--muted); font-size:10.5px; font-family:'IBM Plex Mono',monospace; cursor:help; flex:none; }
  .info-tip::after { content:attr(data-tip); position:absolute; bottom:135%; left:50%; transform:translateX(-50%) translateY(4px); background:var(--ink); color:#F3F4EE; font-family:'Inter',sans-serif; font-size:12px; font-weight:400; line-height:1.4; padding:9px 12px; border-radius:4px; width:230px; opacity:0; visibility:hidden; transition:opacity .15s ease, transform .15s ease; pointer-events:none; z-index:30; }
  .info-tip::before { content:''; position:absolute; bottom:120%; left:50%; transform:translateX(-50%); border:5px solid transparent; border-top-color:var(--ink); opacity:0; visibility:hidden; transition:opacity .15s ease; z-index:30; }
  .info-tip:hover::after, .info-tip:focus-visible::after { opacity:1; visibility:visible; transform:translateX(-50%) translateY(0); }
  .info-tip:hover::before, .info-tip:focus-visible::before { opacity:1; visibility:visible; }
  .legend-row { display:flex; gap:18px; flex-wrap:wrap; margin:2px 0 20px; padding-bottom:16px; border-bottom:1px solid var(--border-soft); }
  .legend-item { display:flex; align-items:center; gap:7px; font-size:12.5px; color:var(--ink-soft); }
  .legend-swatch { width:10px;height:10px;border-radius:2px; flex:none; }
  .toast-stack { position:fixed; bottom:22px; right:22px; z-index:50; display:flex; flex-direction:column; gap:8px; align-items:flex-end; }
  .toast { background:var(--ink); color:#F3F4EE; padding:11px 16px; border-radius:4px; font-size:13px; display:flex; align-items:center; gap:9px; box-shadow:0 10px 26px rgba(0,0,0,0.18); animation:toastIn .25s ease, toastOut .3s ease 2.7s forwards; max-width:320px; }
  .toast .dot { width:7px;height:7px;border-radius:50%; flex:none; }
  @keyframes toastIn { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }
  @keyframes toastOut { to { opacity:0; transform:translateY(-6px); } }
  @media (max-width:880px) {
    .role-grid { grid-template-columns:1fr 1fr; }
    .how-strip { grid-template-columns:1fr; }
    .app { grid-template-columns:1fr; }
    .sidebar { position:fixed; z-index:20; left:0; top:0; height:100vh; width:230px; transform:translateX(-100%); transition:transform .2s ease; }
    .sidebar.open { transform:translateX(0); }
    main { padding:20px 18px 50px; }
    .menu-toggle { display:inline-flex; align-items:center; gap:8px; border:1px solid var(--border); background:var(--surface); border-radius:6px; padding:8px 10px; margin-bottom:16px; cursor:pointer; font-size:13px; }
    .roadmap-grid { grid-template-columns:1fr; }
    .kpi-grid { grid-template-columns:1fr; }
    .form-grid { grid-template-columns:1fr; }
    .profile-grid { grid-template-columns:1fr; }
    .profile-metrics { grid-template-columns:1fr 1fr 1fr; }
    .topbar { flex-direction:column; }
    .scrim.show { display:block; }
  }
  @media (max-width:560px) { .role-grid { grid-template-columns:1fr; } }
  @media (prefers-reduced-motion: reduce) { * { animation-duration:0.001ms !important; transition-duration:0.001ms !important; } }
`;

function checkIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 10l4 4 8-9" />
    </svg>
  );
}

function draftIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M5 15l1-4 8-8 3 3-8 8-4 1z" />
    </svg>
  );
}

function staleIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M10 4v6l4 2" />
      <circle cx="10" cy="10" r="7" />
    </svg>
  );
}

function docIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M6 3h6l3 3v11H6z" />
      <path d="M9 3v3H6" />
    </svg>
  );
}

export default function App() {
  const [authStep, setAuthStep] = useState<"landing" | "login">("landing");
  const [pendingRole, setPendingRole] = useState<Role>("newhire");
  const [selectedRole, setSelectedRole] = useState<Role>("newhire");
  const [signedIn, setSignedIn] = useState(false);
  const [identity, setIdentity] = useState<User>(DEFAULT_USERS[0]);
  const [currentView, setCurrentView] = useState<ViewName>("roadmap");
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [loginEmail, setLoginEmail] = useState("");
  const [users, setUsers] = useState<User[]>(DEFAULT_USERS);
  const [departments, setDepartments] = useState<string[]>(DEPARTMENTS);
  const [qa, setQa] = useState<QAItem[]>(INITIAL_QA);
  const [sources, setSources] = useState<SourceItem[]>([
    { name: "employee-handbook.pdf", role: "HR", status: "indexed", synced: "2 hours ago" },
    { name: "welcomewave (GitHub repo)", role: "Engineering", status: "indexed", synced: "14 minutes ago" },
    { name: "sales-playbook.docx", role: "Sales", status: "syncing", synced: "now" },
  ]);
  const [roadmapProgress, setRoadmapProgress] = useState<Record<string, boolean>>({});
  const [roadmapChecks, setRoadmapChecks] = useState<Record<string, Record<number, boolean>>>({});
  const [domainFilter, setDomainFilter] = useState("all");
  const [ownerFilter, setOwnerFilter] = useState("all");
  const [askInput, setAskInput] = useState("");
  const [askResult, setAskResult] = useState<ReactNode | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [newDepartment, setNewDepartment] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserDepartment, setNewUserDepartment] = useState(DEPARTMENTS[0]);
  const [newUserRole, setNewUserRole] = useState<Role>("newhire");
  const [usersMessage, setUsersMessage] = useState("");

  const activeRole = selectedRole;

  const visibleNav = useMemo(
    () => NAV_ITEMS.filter((item) => item.roles.includes(activeRole)),
    [activeRole],
  );

  const toastStack = useMemo(() => [], []);

  function showToast(message: string, color = "var(--teal)") {
    const stack = document.getElementById("toastStack");
    if (!stack) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.innerHTML = `<span class="dot" style="background:${color}"></span>${message}`;
    stack.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

  function detectIdentity(email: string, role: Role): User {
    const normalized = email.trim().toLowerCase();
    const known = users.find((user) => user.email.toLowerCase() === normalized);
    if (known) return known;

    const name = email
      .split("@")[0]
      .replace(/[._-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());

    return {
      name,
      email: email.trim(),
      department: role === "admin" ? "All" : "Engineering",
      role,
    };
  }

  function signInWithRole(role: Role, maybeEmail?: string) {
    const email = (maybeEmail ?? loginEmail).trim();
    if (!email) {
      setLoginEmail("");
      showToast("Enter your work email to continue.", "var(--coral)");
      return;
    }

    const value = detectIdentity(email, role);
    setIdentity(value);
    setSelectedRole(role);
    setSignedIn(true);
    setAuthStep("landing");
    setCurrentView("roadmap");
    setProfileMenuOpen(false);
    showToast(`Signed in as ${value.name} · ${ROLE_LABEL[role]}`, "var(--teal)");
  }

  function switchRole() {
    setSignedIn(false);
    setAuthStep("landing");
    setProfileMenuOpen(false);
  }

  function signOut() {
    setSignedIn(false);
    setAuthStep("landing");
    setProfileMenuOpen(false);
    setLoginEmail("");
    showToast("You have been signed out.", "var(--teal)");
  }

  function updateRoadmapProgress(total: number, checks: Record<number, boolean>) {
    const complete = Object.keys(checks).filter((index) => checks[Number(index)]).length;
    const percent = total ? Math.round((complete / total) * 100) : 0;
    const progressLabel = document.getElementById("roadmapProgressLabel");
    const progressFill = document.getElementById("roadmapProgressFill");
    if (progressLabel) progressLabel.textContent = `${complete} of ${total} complete`;
    if (progressFill) progressFill.style.width = `${percent}%`;
  }

  function toggleTaskComplete() {
    const taskKey = identity.email;
    setRoadmapProgress((prev) => ({ ...prev, [taskKey]: !prev[taskKey] }));
    showToast("Task marked complete — progress is visible to your team.", "var(--teal)");
  }

  function handleChecklistToggle(index: number, checked: boolean) {
    const checklistKey = `${identity.email}:${selectedRole}`;
    const next = { ...(roadmapChecks[checklistKey] ?? {}) };
    next[index] = checked;
    setRoadmapChecks((prev) => ({ ...prev, [checklistKey]: next }));
    updateRoadmapProgress(ROLE_DATA[selectedRole].readFirst.length, next);
  }

  const pendingDrafts = qa.filter((item) => item.status === "draft");
  const flagged = qa.filter((item) => item.stale);

  const topBarName = identity?.name ?? "Your name";
  const topBarRole = `${ROLE_LABEL[activeRole]} · ${identity.department}`;
  const topBarInitials = identity.name
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const renderRoadmapList = () => {
    const checklistKey = `${identity.email}:${activeRole}`;
    const savedChecks = roadmapChecks[checklistKey] ?? {};
    const total = ROLE_DATA[activeRole].readFirst.length;
    const complete = Object.keys(savedChecks).filter((index) => savedChecks[Number(index)]).length;

    return (
      <>
        <div className="roadmap-progress" aria-live="polite">
          <div className="roadmap-progress-head">
            <span>Reading progress</span>
            <strong id="roadmapProgressLabel">{complete} of {total} complete</strong>
          </div>
          <div className="roadmap-progress-track">
            <div id="roadmapProgressFill" className="roadmap-progress-fill" style={{ width: `${total ? Math.round((complete / total) * 100) : 0}%` }} />
          </div>
        </div>
        <ul className="checklist" aria-label="Read this first">
          {ROLE_DATA[activeRole].readFirst.map((item, index) => (
            <li key={item.t} className={savedChecks[index] ? "done" : ""}>
              <input
                type="checkbox"
                id={`rf-${index}`}
                checked={Boolean(savedChecks[index])}
                onChange={(event) => handleChecklistToggle(index, event.target.checked)}
              />
              <div>
                <label htmlFor={`rf-${index}`}>{item.t}</label>
                <div className="item-meta mono">{item.meta}</div>
              </div>
            </li>
          ))}
        </ul>
      </>
    );
  };

  function runAsk(question: string) {
    const normalized = question.trim();
    const area = document.getElementById("askResultArea");
    if (!area) return;

    area.innerHTML = `
      <div class="answer-block state-loading">
        <div class="skeleton" style="width:70%"></div>
        <div class="skeleton" style="width:92%"></div>
        <div class="skeleton" style="width:40%"></div>
      </div>
    `;

    setTimeout(() => {
      const match = qa.find((item) => item.question.toLowerCase() === normalized.toLowerCase());
      if (!match) {
        area.innerHTML = `
          <div class="answer-block state-empty">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="10" cy="10" r="7"/><path d="M10 7v4M10 14h.01"/></svg>
            <div><strong style="color:var(--ink)">No grounded answer found.</strong> Nothing in your role's knowledge base covers this yet — try rephrasing, or flag it for a domain expert.</div>
          </div>
        `;
        return;
      }

      const badge = match.stale
        ? `<span class="badge badge-stale">${staleIcon()}Needs re-verification</span>`
        : match.status === "verified"
          ? `<span class="badge badge-verified">${checkIcon()}Verified · ${match.verifiedBy}</span>`
          : `<span class="badge badge-draft">${draftIcon()}AI draft · unverified</span>`;

      area.innerHTML = `
        <div class="answer-block">
          <div class="answer-head">${badge}<span class="answer-question">for “${match.question}”</span></div>
          <p class="answer-text">${match.answer}</p>
          ${match.stale ? `<p class="small" style="color:#9C3A24;margin-top:8px;">Flagged because ${match.staleReason}.</p>` : ""}
          <div class="citation-list">
            ${match.citations
              .map((citation) => `<span class="citation">${docIcon()}${citation.label} · ${citation.ref}</span>`)
              .join("")}
          </div>
        </div>
      `;
    }, normalized && qa.some((item) => item.question.toLowerCase() === normalized.toLowerCase()) && qa.find((item) => item.question.toLowerCase() === normalized.toLowerCase())?.status === "verified" ? 250 : 750);
  }

  const askChips = qa.filter((item) => item.roles.includes(activeRole));

  function verifyItem(item: QAItem) {
    setQa((prev) =>
      prev.map((entry) =>
        entry.id === item.id
          ? { ...entry, status: "verified", verifiedBy: "You", verifiedAt: "just now", stale: false, staleReason: undefined }
          : entry,
      ),
    );
    showToast("Verified — this is now the answer everyone gets.", "var(--amber)");
  }

  function reconfirmItem(item: QAItem) {
    setQa((prev) => prev.map((entry) => (entry.id === item.id ? { ...entry, stale: false, staleReason: undefined } : entry)));
    showToast("Re-confirmed — staleness flag cleared.", "var(--coral)");
  }

  function renderSourcesList() {
    return (
      <table className="sources-table">
        <thead>
          <tr>
            <th>Source</th>
            <th>Role</th>
            <th>Status</th>
            <th>Last synced</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {sources.map((source) => {
            const statusClass =
              source.status === "indexed" ? "status-indexed" : source.status === "syncing" ? "status-syncing" : "status-error";
            const statusText = source.status === "indexed" ? "Indexed" : source.status === "syncing" ? "Syncing" : "Error";
            return (
              <tr key={source.name}>
                <td>{source.name}</td>
                <td>{source.role}</td>
                <td>
                  <span className={`status-pill ${statusClass}`}>
                    <span className="dot" />
                    {statusText}
                  </span>
                </td>
                <td className="mono small">{source.synced}</td>
                <td>
                  <button
                    className="btn btn-sm"
                    onClick={() => {
                      const affected = qa.filter(
                        (item) => item.status === "verified" && item.citations.some((citation) => citation.label === source.name),
                      );
                      setQa((prev) =>
                        prev.map((item) =>
                          item.status === "verified" && item.citations.some((citation) => citation.label === source.name)
                            ? { ...item, stale: true, staleReason: `${source.name} was updated just now` }
                            : item,
                        ),
                      );
                      showToast(
                        affected.length ? `${affected.length} answer(s) flagged for re-verification.` : "Source re-indexed. No dependent verified answers were found.",
                        affected.length ? "var(--amber)" : "var(--teal)",
                      );
                    }}
                  >
                    Simulate update
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  }

  function addDepartment() {
    const trimmed = newDepartment.trim();
    if (!trimmed) {
      setUsersMessage("Enter a department name first.");
      return;
    }
    if (departments.some((item) => item.toLowerCase() === trimmed.toLowerCase())) {
      setUsersMessage("That department already exists.");
      return;
    }
    setDepartments((prev) => [...prev, trimmed]);
    setNewDepartment("");
    setUsersMessage(`${trimmed} added.`);
    setNewUserDepartment(trimmed);
  }

  function addUser() {
    const name = newUserName.trim();
    const email = newUserEmail.trim();
    if (!name || !email) {
      setUsersMessage("Enter a name and email first.");
      return;
    }
    const user: User = {
      name,
      email,
      department: newUserDepartment,
      role: newUserRole,
    };
    setUsers((prev) => [...prev, user]);
    setNewUserName("");
    setNewUserEmail("");
    setUsersMessage(`${name} added as ${ROLE_LABEL[newUserRole]}.`);
  }

  function renderTeamTable() {
    const department = identity.department;
    const team = users.filter((user) => user.role === "newhire" && user.department === department);
    return team.length ? (
      <table className="sources-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>First task</th>
            <th>Questions asked</th>
          </tr>
        </thead>
        <tbody>
          {team.map((user) => {
            const done = Boolean(roadmapProgress[user.email]);
            const asks = qa.filter((item) => item.roles.includes("newhire") && item.owner === user.name).length;
            return (
              <tr key={user.email}>
                <td>{user.name}</td>
                <td>
                  <span className={`status-pill ${done ? "status-indexed" : "status-syncing"}`}>
                    <span className="dot" />
                    {done ? "Completed" : "In progress"}
                  </span>
                </td>
                <td>{asks}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    ) : (
      <div className="small muted">No new hires in this department yet.</div>
    );
  }

  function renderUsersTable() {
    return (
      <table className="sources-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Department</th>
            <th>Role</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={`${user.email}-${user.role}`}>
              <td>{user.name}</td>
              <td className="mono small">{user.email}</td>
              <td>
                <span className="badge badge-teal">{user.department}</span>
              </td>
              <td>{ROLE_LABEL[user.role]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  function renderProfile() {
    const roleLabel = ROLE_LABEL[identity.role];
    const completed = Boolean(roadmapProgress[identity.email]);
    const questionCount = qa.filter((item) => item.owner === identity.name).length;
    const accessLevel =
      identity.role === "admin"
        ? "Organization-wide"
        : identity.role === "manager"
          ? "Department analytics"
          : identity.role === "expert"
            ? "Department review"
            : "Department-scoped";
    const scopeText =
      identity.role === "admin"
        ? "You can manage connected sources, users, departments, and organization-wide analytics."
        : `Your answers and roadmap are limited to content tagged for ${identity.department}.`;

    const accessList =
      identity.role === "admin"
        ? ["All departments", "Source management", "User administration", "Dashboard analytics"]
        : identity.role === "expert"
          ? ["Roadmap", "Ask & Evidence", "Verify Queue", "Re-verification", "My Team"]
          : identity.role === "manager"
            ? ["Roadmap", "Ask & Evidence", "Dashboard", "My Team"]
            : ["Roadmap", "Ask & Evidence"];

    return (
      <>
        <div className="profile-grid">
          <div className="card accent-violet profile-identity-card">
            <div className="accent-bar" />
            <div className="profile-avatar">{topBarInitials}</div>
            <div className="section-heading" style={{ marginBottom: "4px" }}>
              <h2>{identity.name}</h2>
            </div>
            <p className="muted">{identity.email}</p>
            <div className="profile-badges">
              <span className="badge badge-violet">{roleLabel}</span>
              <span className="badge badge-teal">{identity.department}</span>
            </div>
          </div>

          <div className="card accent-teal">
            <div className="accent-bar" />
            <div className="section-heading">
              <h2>Personal details</h2>
            </div>
            <dl className="profile-details">
              <div><dt>Work email</dt><dd>{identity.email}</dd></div>
              <div><dt>Functional role</dt><dd>{roleLabel}</dd></div>
              <div><dt>Department scope</dt><dd>{identity.department}</dd></div>
              <div><dt>Access level</dt><dd>{accessLevel}</dd></div>
            </dl>
          </div>
        </div>

        <div className="card accent-amber">
          <div className="accent-bar" />
          <div className="section-heading">
            <h2>My onboarding activity</h2>
          </div>
          <div className="profile-metrics">
            <div>
              <span className="profile-metric-value">{questionCount}</span>
              <span className="profile-metric-label">questions asked</span>
            </div>
            <div>
              <span className="profile-metric-value">{completed ? "Completed" : "In progress"}</span>
              <span className="profile-metric-label">roadmap task</span>
            </div>
            <div>
              <span className="profile-metric-value">Today</span>
              <span className="profile-metric-label">last active</span>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="section-heading">
            <h2>What you can access</h2>
          </div>
          <p className="muted small">{scopeText}</p>
          <div className="profile-access-list">
            {accessList.map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
        </div>
      </>
    );
  }

  const viewMeta = VIEW_META[currentView];

  return (
    <>
      <style>{STYLES}</style>
      <div className="toast-stack" id="toastStack" aria-live="polite" />

      {authStep === "landing" && !signedIn && (
        <section className="landing" id="landingView">
          <div className="landing-inner">
            <div className="landing-brand">
              <span className="brand-cluster" aria-hidden="true">
                <span style={{ background: "var(--violet)" }} />
                <span style={{ background: "var(--teal)" }} />
                <span style={{ background: "var(--amber)" }} />
                <span style={{ background: "var(--coral)" }} />
              </span>
              <span className="brand-name">Welcome Wave</span>
            </div>
            <p className="landing-tagline">Grounded answers. Verified trust. No guesswork onboarding.</p>

            <div className="how-strip">
              <div className="how-step">
                <span className="num" style={{ background: "var(--violet)" }}>1</span>
                <p><strong>Pick a role</strong>Your roadmap and permissions are scoped to it immediately.</p>
              </div>
              <div className="how-step">
                <span className="num" style={{ background: "var(--teal)" }}>2</span>
                <p><strong>Ask anything in scope</strong>Every answer is generated only from retrieved, permitted content.</p>
              </div>
              <div className="how-step">
                <span className="num" style={{ background: "var(--amber)" }}>3</span>
                <p><strong>Experts verify it</strong>A confirmed answer is served instantly — flagged again if its source changes.</p>
              </div>
            </div>

            <p className="landing-heading">SELECT A ROLE TO CONTINUE</p>

            <div className="role-grid" role="group" aria-label="Select a role">
              {[
                { role: "newhire", title: "New Hire", desc: "Land on a role-specific roadmap and ask grounded questions from day one.", accent: "var(--violet)" },
                { role: "expert", title: "Domain Expert", desc: "Review AI drafts and confirm the answers your domain is responsible for.", accent: "var(--amber)" },
                { role: "manager", title: "Manager", desc: "See where documentation is thin and how much of it is actually trusted.", accent: "var(--teal)" },
                { role: "admin", title: "Admin", desc: "Connect knowledge sources and assign them to roles across the console.", accent: "var(--coral)" },
              ].map((card, index) => (
                <button
                  key={card.role}
                  className="role-card"
                  data-role={card.role}
                  onClick={() => {
                    setPendingRole(card.role as Role);
                    setAuthStep("login");
                  }}
                  style={{ animationDelay: `${index * 0.12}s` }}
                >
                  <div className="card-top" style={{ background: card.accent }} />
                  <div className="card-body">
                    <div className="role-icon" style={{ background: `${card.accent}-tint` as string, color: card.accent }}>
                      {card.role === "newhire" && (
                        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M10 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" /></svg>
                      )}
                      {card.role === "expert" && (
                        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M10 3l6 2.5v4c0 4-2.5 6.8-6 7.5-3.5-.7-6-3.5-6-7.5v-4L10 3z" /><path d="M7.5 10l1.8 1.8L13 8" /></svg>
                      )}
                      {card.role === "manager" && (
                        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M4 15V9M10 15V5m6 10v-6" /></svg>
                      )}
                      {card.role === "admin" && (
                        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7"><rect x="3" y="4" width="14" height="12" rx="1.5" /><path d="M3 8h14" /></svg>
                      )}
                    </div>
                    <h3>{card.title}</h3>
                    <p className="role-desc">{card.desc}</p>
                    <div className="access-tags">
                      <span className="access-tag"><span className="dot" style={{ background: "var(--violet)" }} />Roadmap</span>
                      <span className="access-tag"><span className="dot" style={{ background: "var(--teal)" }} />Ask &amp; Evidence</span>
                      {card.role === "expert" && <span className="access-tag"><span className="dot" style={{ background: "var(--amber)" }} />Verify Queue</span>}
                      {card.role === "admin" && <span className="access-tag"><span className="dot" style={{ background: "var(--amber)" }} />Verify Queue + Dashboard</span>}
                    </div>
                    <span className="continue">Continue as {card.title} →</span>
                  </div>
                </button>
              ))}
            </div>

            <p className="landing-footnote">In production this role is assigned automatically from your company directory — this preview lets you choose one to explore the console.</p>
          </div>
        </section>
      )}

      {authStep === "login" && !signedIn && (
        <section className="landing" id="loginView" style={{ display: "flex" }}>
          <div className="login-card">
            <div className="card-top" />
            <div className="landing-brand" style={{ marginBottom: "6px" }}>
              <span className="brand-cluster" aria-hidden="true">
                <span style={{ background: "var(--violet)" }} />
                <span style={{ background: "var(--teal)" }} />
                <span style={{ background: "var(--amber)" }} />
                <span style={{ background: "var(--coral)" }} />
              </span>
              <span className="brand-name">Welcome Wave</span>
            </div>
            <p className="landing-tagline" style={{ marginBottom: "24px", fontSize: "13.5px" }}>Sign in to your onboarding console</p>
            <div className="login-detection">Selected role: <strong>{ROLE_LABEL[pendingRole]}</strong></div>
            <div className="login-field">
              <label htmlFor="loginEmail">Work email</label>
              <input type="email" id="loginEmail" value={loginEmail} onChange={(event) => setLoginEmail(event.target.value)} placeholder="you@company.com" />
            </div>
            <button className="btn btn-primary sso-btn" onClick={() => signInWithRole(pendingRole, loginEmail)}>Continue</button>
            <div className="login-divider">or</div>
            <button className="btn sso-btn" onClick={() => {
              const fallback = users.find((user) => user.role === pendingRole);
              const nextEmail = fallback ? fallback.email : "you@company.com";
              setLoginEmail(nextEmail);
              signInWithRole(pendingRole, nextEmail);
            }}>
              <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="3" y="3" width="14" height="14" rx="2" /><path d="M3 8h14" /></svg>
              Continue with company SSO
            </button>
            <button className="demo-link" type="button" onClick={() => setAuthStep("landing")}>Back to role selection</button>
            <p className="login-footnote">This preview skips real authentication — enter anything and continue.</p>
          </div>
        </section>
      )}

      {signedIn && (
        <div className="app" id="consoleShell" style={{ display: "grid" }}>
          <aside className={`sidebar ${sidebarOpen ? "open" : ""}`} aria-label="Primary navigation">
            <div className="brand">
              <span className="brand-mark" aria-hidden="true" />
              <span className="brand-name">Welcome&nbsp;Wave</span>
            </div>
            <div className="brand-sub" style={{ marginBottom: "2px" }}>Onboarding console · v2.0</div>
            <div className="role-pill" style={{ marginBottom: "12px" }}><span className="dot" />Viewing as <span>{ROLE_LABEL[activeRole]}</span></div>

            <nav className="nav-group" aria-label="Console sections">
              {visibleNav.map((item) => (
                <button
                  key={item.view}
                  className={`nav-item ${currentView === item.view ? "active" : ""}`}
                  data-view={item.view}
                  onClick={() => {
                    setCurrentView(item.view as ViewName);
                    setSidebarOpen(false);
                  }}
                >
                  {item.view === "roadmap" && (
                    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M3 10c2-4 5-6 7-6s5 2 7 6c-2 4-5 6-7 6s-5-2-7-6z" /><circle cx="10" cy="10" r="2" /></svg>
                  )}
                  {item.view === "ask" && (
                    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M4 4h12v9H8l-4 3z" /></svg>
                  )}
                  {item.view === "verify" && (
                    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M4 10l4 4 8-9" /></svg>
                  )}
                  {item.view === "reverify" && (
                    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M10 4v6l4 2" /><circle cx="10" cy="10" r="7" /></svg>
                  )}
                  {item.view === "dashboard" && (
                    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M4 15V9M10 15V5m6 10v-6" /></svg>
                  )}
                  {item.view === "sources" && (
                    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="3" y="4" width="14" height="12" rx="1.5" /><path d="M3 8h14" /></svg>
                  )}
                  {item.view === "team" && (
                    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="10" cy="6" r="3" /><path d="M4 17c0-3 2.7-5 6-5s6 2 6 5M15 4.5a2.5 2.5 0 0 1 2 4.2M17 13.5c1.7.6 2.7 1.8 3 3.5" /></svg>
                  )}
                  {item.view === "users" && (
                    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="7" cy="7" r="3" /><path d="M2 17c0-3 2.2-5 5-5s5 2 5 5M14 6h5M16.5 3.5v5" /></svg>
                  )}
                  {item.view === "profile" && (
                    <svg className="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="10" cy="6" r="3" /><path d="M3.5 17c.8-3 3-4.5 6.5-4.5s5.7 1.5 6.5 4.5" /></svg>
                  )}
                  {item.label}
                  {item.view === "verify" && <span className="nav-count">{pendingDrafts.length}</span>}
                  {item.view === "reverify" && <span className="nav-count">{flagged.length}</span>}
                </button>
              ))}
            </nav>

            <div className="sidebar-footer">
              <div className="row"><span>Active sources</span><span>{sources.filter((source) => source.status !== "error").length}</span></div>
              <div className="row"><span>Verified coverage</span><span>{Math.round((qa.filter((item) => item.status === "verified").length / qa.length) * 100)}%</span></div>
              <div className="row"><span>Avg. answer latency</span><span>1.8s</span></div>
            </div>
          </aside>

          <div className={`scrim ${sidebarOpen ? "show" : ""}`} onClick={() => setSidebarOpen(false)} />

          <main>
            <button className="menu-toggle" onClick={() => setSidebarOpen(true)} aria-label="Open navigation">
              <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M3 5h14M3 10h14M3 15h14" /></svg>
              Menu
            </button>

            <div className="topbar">
              <div className="topbar-title">
                <div className="eyebrow">{viewMeta.eyebrow}</div>
                <h1>{viewMeta.title}</h1>
                <p className="desc">{viewMeta.desc}</p>
              </div>
              <div className="topbar-actions">
                <button className="profile-menu-trigger" aria-expanded={profileMenuOpen} onClick={() => setProfileMenuOpen((prev) => !prev)}>
                  <span className="profile-menu-avatar">{topBarInitials}</span>
                  <span className="profile-menu-copy">
                    <span className="profile-menu-name">{topBarName}</span>
                    <span className="profile-menu-role">{topBarRole}</span>
                  </span>
                  <span className="profile-menu-chevron">⌄</span>
                </button>
                <div className="profile-menu" hidden={!profileMenuOpen}>
                  <button type="button" onClick={() => { setProfileMenuOpen(false); setCurrentView("profile"); }}>View profile</button>
                  <button type="button" onClick={() => { setProfileMenuOpen(false); switchRole(); }}>Switch role</button>
                  <button type="button" className="menu-danger" onClick={() => signOut()}>Sign out</button>
                </div>
              </div>
            </div>

            {currentView === "roadmap" && (
              <section className="view active">
                <div className="roadmap-grid">
                  <div>
                    <div className="card accent-violet">
                      <div className="accent-bar" />
                      <div className="section-heading"><h2>{ROLE_DATA[activeRole].title}</h2></div>
                      <p className="muted" style={{ marginBottom: "4px" }}>{ROLE_DATA[activeRole].overview}</p>
                      {renderRoadmapList()}
                    </div>
                  </div>
                  <div>
                    <div className="card accent-violet">
                      <div className="accent-bar" />
                      <div className="section-heading"><h2>Suggested first task</h2></div>
                      <div className="task-callout">
                        <p className="label">TODAY</p>
                        <p>{ROLE_DATA[activeRole].task}</p>
                        <button className="btn btn-primary btn-sm" onClick={toggleTaskComplete} disabled={Boolean(roadmapProgress[identity.email])} style={{ marginTop: "12px" }}>
                          {roadmapProgress[identity.email] ? "Task completed" : "Mark task complete"}
                        </button>
                      </div>
                    </div>
                    <div className="card accent-teal" style={{ marginTop: "14px" }}>
                      <div className="accent-bar" />
                      <div className="section-heading"><h2>Jump to Ask</h2></div>
                      <p className="muted small">Have a specific question already? Skip ahead — every answer is grounded and labeled.</p>
                      <button className="btn btn-primary" style={{ marginTop: "12px" }} onClick={() => setCurrentView("ask")}>Ask a question →</button>
                    </div>
                  </div>
                </div>
              </section>
            )}

            {currentView === "ask" && (
              <section className="view active">
                <div className="card accent-teal">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Ask anything in your scope</h2>
                    <span className="info-tip" tabIndex={0} data-tip="Answers are generated only from content retrieved for your role — nothing outside your permitted scope is ever used.">i</span>
                  </div>
                  <div className="legend-row">
                    <span className="legend-item"><span className="legend-swatch" style={{ background: "var(--amber)" }} />Verified — confirmed by a domain expert</span>
                    <span className="legend-item"><span className="legend-swatch" style={{ background: "#ACAEA0" }} />AI draft — not yet reviewed</span>
                    <span className="legend-item"><span className="legend-swatch" style={{ background: "var(--coral)" }} />Needs re-verification — source changed since last check</span>
                  </div>
                  <div className="ask-box">
                    <input type="text" value={askInput} onChange={(event) => setAskInput(event.target.value)} placeholder="e.g. What's my first task this week?" aria-label="Ask a question" />
                    <button className="btn btn-teal" onClick={() => { if (askInput.trim()) { runAsk(askInput); } }}>Ask</button>
                  </div>
                  <div className="chip-row" aria-label="Suggested questions">
                    {askChips.map((item) => (
                      <button key={item.id} className="chip" onClick={() => { setAskInput(item.question); runAsk(item.question); }}>{item.question}</button>
                    ))}
                  </div>
                  <div id="askResultArea">{askResult}</div>
                  <button className="demo-link" onClick={() => {
                    const area = document.getElementById("askResultArea");
                    if (!area) return;
                    area.innerHTML = `
                      <div class="answer-block state-error">
                        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" style="color:var(--coral)"><circle cx="10" cy="10" r="7"/><path d="M7 7l6 6M13 7l-6 6"/></svg>
                        <div><strong style="color:var(--coral)">Retrieval failed.</strong> The knowledge index didn't respond in time. Try again — if it keeps happening, tell an admin.</div>
                        <button class="btn btn-sm" style="margin-top:6px;">Retry</button>
                      </div>
                    `;
                  }}>Preview the error state →</button>
                </div>
              </section>
            )}

            {currentView === "verify" && (
              <section className="view active">
                <div className="card accent-amber">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Pending drafts</h2>
                    <span className="info-tip" tabIndex={0} data-tip="Verifying stamps an answer as canonical. It's then served instantly to everyone, skipping AI generation entirely, until its source changes.">i</span>
                    <span className="count">{pendingDrafts.length} waiting</span>
                  </div>
                  <p className="muted small" style={{ marginBottom: "4px" }}>Verifying stamps an answer as canonical — it will be served instantly, ahead of any new AI draft, from now on.</p>
                  {pendingDrafts.length === 0 ? (
                    <div className="state-empty" style={{ display: "flex" }}>
                      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 10l4 4 8-9" /></svg>
                      <div><strong style={{ color: "var(--ink)" }}>Queue clear.</strong> Nothing waiting on your review right now.</div>
                    </div>
                  ) : (
                    pendingDrafts.map((item) => (
                      <div key={item.id} className="queue-row">
                        <div>
                          <div className="queue-q">{item.question}</div>
                          <div className="queue-a">{item.answer}</div>
                          <div className="queue-meta"><span className="mono">{item.domain}</span><span>Draft · unverified</span></div>
                        </div>
                        <div className="queue-actions">
                          <button className="btn btn-sm" onClick={() => {
                            const revised = window.prompt("Edit this draft before verifying:", item.answer);
                            if (revised && revised.trim()) {
                              setQa((prev) => prev.map((entry) => (entry.id === item.id ? { ...entry, answer: revised.trim() } : entry)));
                              showToast("Draft updated. Review it once more before verifying.", "var(--amber)");
                            }
                          }}>Edit</button>
                          <button className="btn btn-sm btn-amber" onClick={() => verifyItem(item)}>Verify</button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </section>
            )}

            {currentView === "reverify" && (
              <section className="view active">
                <div className="card accent-coral">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Needs re-verification</h2>
                    <span className="info-tip" tabIndex={0} data-tip="When a source document changes, every verified answer that depended on it is automatically dropped back here for a fresh check.">i</span>
                    <span className="count">{flagged.length} flagged</span>
                  </div>
                  <p className="muted small" style={{ marginBottom: "12px" }}>A verified answer lands here automatically when the source chunk it depends on changes.</p>
                  <div className="filter-row">
                    <label style={{ margin: 0 }} htmlFor="domainFilter">Domain</label>
                    <select id="domainFilter" value={domainFilter} onChange={(event) => setDomainFilter(event.target.value)}>
                      <option value="all">All domains</option>
                      {Array.from(new Set(flagged.map((item) => item.domain))).map((domain) => (
                        <option key={domain} value={domain}>{domain}</option>
                      ))}
                    </select>
                    <label style={{ margin: 0 }} htmlFor="ownerFilter">Owner</label>
                    <select id="ownerFilter" value={ownerFilter} onChange={(event) => setOwnerFilter(event.target.value)}>
                      <option value="all">All owners</option>
                      {Array.from(new Set(flagged.map((item) => item.owner).filter(Boolean))).map((owner) => (
                        <option key={owner} value={owner}>{owner}</option>
                      ))}
                    </select>
                  </div>
                  {flagged.filter((item) => (domainFilter === "all" || item.domain === domainFilter) && (ownerFilter === "all" || item.owner === ownerFilter)).length === 0 ? (
                    <div className="state-empty" style={{ display: "flex" }}>
                      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 10l4 4 8-9" /></svg>
                      <div><strong style={{ color: "var(--ink)" }}>Nothing stale.</strong> No verified answers are currently flagged.</div>
                    </div>
                  ) : (
                    flagged
                      .filter((item) => (domainFilter === "all" || item.domain === domainFilter) && (ownerFilter === "all" || item.owner === ownerFilter))
                      .map((item) => (
                        <div key={item.id} className="queue-row">
                          <div>
                            <div className="queue-q">{item.question}</div>
                            <div className="queue-a">{item.answer}</div>
                            <div className="queue-meta">
                              <span className="mono">{item.domain}</span>
                              <span>Owner: {item.owner}</span>
                              <span style={{ color: "#9C3A24" }}>{item.staleReason}</span>
                            </div>
                          </div>
                          <div className="queue-actions">
                            <button className="btn btn-sm btn-coral" onClick={() => reconfirmItem(item)}>Re-confirm</button>
                          </div>
                        </div>
                      ))
                  )}
                </div>
              </section>
            )}

            {currentView === "dashboard" && (
              <section className="view active">
                <div className="kpi-grid">
                  <div className="card accent-teal kpi">
                    <div className="accent-bar" />
                    <div className="kpi-label">Recurring unanswered questions <span className="info-tip" tabIndex={0} data-tip="Questions asked repeatedly that returned no grounded answer — a signal of missing documentation.">i</span></div>
                    <div className="kpi-value" style={{ color: "var(--teal)" }}>29</div>
                    <div className="kpi-sub">across 3 roles, last 14 days</div>
                  </div>
                  <div className="card accent-amber kpi">
                    <div className="accent-bar" />
                    <div className="kpi-label">Verified coverage (live) <span className="info-tip" tabIndex={0} data-tip="Share of tracked answers that a human has confirmed. Updates instantly as drafts get verified.">i</span></div>
                    <div className="kpi-value" style={{ color: "var(--amber)" }}>{Math.round((qa.filter((item) => item.status === "verified").length / qa.length) * 100)}%</div>
                    <div className="kpi-sub">{qa.filter((item) => item.status === "verified").length} of {qa.length} tracked answers</div>
                  </div>
                  <div className="card accent-coral kpi">
                    <div className="accent-bar" />
                    <div className="kpi-label">Re-verification backlog <span className="info-tip" tabIndex={0} data-tip="Verified answers currently flagged because a source they depend on was edited.">i</span></div>
                    <div className="kpi-value" style={{ color: "var(--coral)" }}>{flagged.length}</div>
                    <div className="kpi-sub">flagged by source changes</div>
                  </div>
                </div>

                <div className="roadmap-grid">
                  <div className="card accent-teal">
                    <div className="accent-bar" />
                    <div className="section-heading"><h2>Top unanswered, by role</h2></div>
                    <ol className="rank-list" style={{ listStyle: "none" }}>
                      <li><span><span className="n">01</span>How do I request access to the CRM data source?</span><span className="count">14 asks</span></li>
                      <li><span><span className="n">02</span>Who owns the payroll integration docs?</span><span className="count">9 asks</span></li>
                      <li><span><span className="n">03</span>What's the escalation path for a P1 outside business hours?</span><span className="count">6 asks</span></li>
                    </ol>
                  </div>
                  <div className="card accent-amber">
                    <div className="accent-bar" />
                    <div className="section-heading"><h2>Verified coverage per role</h2></div>
                    <div className="bar-row"><span className="bar-label">Engineering</span><div className="bar-track"><div className="bar-fill" style={{ width: "71%", background: "var(--amber)" }} /></div><span className="bar-value">71%</span></div>
                    <div className="bar-row"><span className="bar-label">Sales</span><div className="bar-track"><div className="bar-fill" style={{ width: "54%", background: "var(--amber)" }} /></div><span className="bar-value">54%</span></div>
                    <div className="bar-row"><span className="bar-label">HR</span><div className="bar-track"><div className="bar-fill" style={{ width: "88%", background: "var(--amber)" }} /></div><span className="bar-value">88%</span></div>
                  </div>
                </div>
              </section>
            )}

            {currentView === "sources" && (
              <section className="view active">
                <div className="card accent-violet">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Connect a knowledge source</h2>
                    <span className="info-tip" tabIndex={0} data-tip="Indexing only reads content in. Welcome Wave never writes back to anything it connects to.">i</span>
                  </div>
                  <div className="dropzone" onClick={() => {
                    setSources((prev) => [...prev, { name: "onboarding-checklist.md", role: "Engineering", status: "syncing", synced: "now" }]);
                    setTimeout(() => {
                      setSources((prev) => {
                        const next = [...prev];
                        const last = next[next.length - 1];
                        if (last) {
                          last.status = "indexed";
                          last.synced = "just now";
                        }
                        return next;
                      });
                    }, 1200);
                  }}>Drop a PDF, Markdown or DOCX file here, or click to browse</div>

                  <div className="form-grid">
                    <div className="form-field">
                      <label htmlFor="ghRepo">GitHub repository (optional)</label>
                      <input type="text" id="ghRepo" placeholder="org/repo-name" />
                    </div>
                    <div className="form-field">
                      <label htmlFor="assignRole">Assign to role</label>
                      <select id="assignRole" defaultValue="Engineering">
                        <option>Engineering</option>
                        <option>Sales</option>
                        <option>HR</option>
                        <option>Support</option>
                      </select>
                    </div>
                  </div>

                  <div className="form-field">
                    <label>Scope — exclude paths from indexing</label>
                    <div className="scope-row">
                      <span className="scope-chip">/secrets<button aria-label="Remove exclusion" onClick={(event) => event.currentTarget.parentElement?.remove()}>✕</button></span>
                      <span className="scope-chip">/legal/contracts<button aria-label="Remove exclusion" onClick={(event) => event.currentTarget.parentElement?.remove()}>✕</button></span>
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                    <button className="btn btn-violet" onClick={() => {
                      const repo = (document.getElementById("ghRepo") as HTMLInputElement | null)?.value.trim();
                      const role = (document.getElementById("assignRole") as HTMLSelectElement | null)?.value ?? "Engineering";
                      const name = repo ? `${repo} (GitHub repo)` : "new-upload.pdf";
                      setSources((prev) => [...prev, { name, role, status: "syncing", synced: "now" }]);
                      setTimeout(() => {
                        setSources((prev) => {
                          const next = [...prev];
                          const last = next[next.length - 1];
                          if (last) {
                            last.status = "indexed";
                            last.synced = "just now";
                          }
                          return next;
                        });
                      }, 1400);
                    }}>Connect source</button>
                    <button className="btn btn-sm" onClick={() => showToast("INGESTION_ERROR: file could not be parsed. The console remains available.", "var(--coral)")}>Preview malformed upload</button>
                  </div>

                  <div className="lock-note">
                    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="4" y="9" width="12" height="8" rx="1.5" /><path d="M7 9V6a3 3 0 016 0v3" /></svg>
                    Read-only, always. Welcome Wave never writes back to a connected source — indexing only reads content in.
                  </div>
                </div>

                <div className="card accent-violet" style={{ marginTop: "14px" }}>
                  <div className="accent-bar" />
                  <div className="section-heading"><h2>Connected sources</h2></div>
                  {renderSourcesList()}
                </div>
              </section>
            )}

            {currentView === "team" && (
              <section className="view active">
                <div className="card accent-teal">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>My team</h2>
                    <span className="info-tip" tabIndex={0} data-tip="Team visibility is derived from the department attached to the active identity.">i</span>
                  </div>
                  <p className="muted small">New hires in {identity.department} appear here automatically.</p>
                  {renderTeamTable()}
                </div>
              </section>
            )}

            {currentView === "users" && (
              <section className="view active">
                <div className="card accent-coral">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Users &amp; departments</h2>
                    <span className="info-tip" tabIndex={0} data-tip="Functional role controls actions; department controls the knowledge scope each person can see.">i</span>
                  </div>
                  <div className="form-grid">
                    <div className="form-field">
                      <label htmlFor="newDepartment">Add department</label>
                      <input type="text" id="newDepartment" value={newDepartment} onChange={(event) => setNewDepartment(event.target.value)} placeholder="e.g. Support" />
                    </div>
                    <div className="form-field" style={{ alignSelf: "end" }}>
                      <button className="btn btn-coral" onClick={addDepartment}>Add department</button>
                    </div>
                  </div>

                  <div className="form-grid">
                    <div className="form-field"><label htmlFor="newUserName">Name</label><input type="text" id="newUserName" value={newUserName} onChange={(event) => setNewUserName(event.target.value)} placeholder="Full name" /></div>
                    <div className="form-field"><label htmlFor="newUserEmail">Email</label><input type="email" id="newUserEmail" value={newUserEmail} onChange={(event) => setNewUserEmail(event.target.value)} placeholder="name@company.com" /></div>
                    <div className="form-field"><label htmlFor="newUserDepartment">Department</label><select id="newUserDepartment" value={newUserDepartment} onChange={(event) => setNewUserDepartment(event.target.value)}>{departments.map((department) => <option key={department} value={department}>{department}</option>)}</select></div>
                    <div className="form-field"><label htmlFor="newUserRole">Functional role</label><select id="newUserRole" value={newUserRole} onChange={(event) => setNewUserRole(event.target.value as Role)}><option value="newhire">New Hire</option><option value="expert">Domain Expert</option><option value="manager">Manager</option><option value="admin">Admin</option></select></div>
                  </div>
                  <button className="btn btn-primary" onClick={addUser}>Create user</button>
                  <div className="small muted" style={{ marginTop: "10px" }}>{usersMessage}</div>
                </div>

                <div className="card">
                  <div className="section-heading"><h2>All users</h2><span className="count">{users.length} identities</span></div>
                  {renderUsersTable()}
                </div>
              </section>
            )}

            {currentView === "profile" && (
              <section className="view active">
                {renderProfile()}
              </section>
            )}
          </main>
        </div>
      )}
    </>
  );
}
