import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactElement } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import TrustBadge, { trustStateOf } from "./shared/TrustBadge";

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

type Toast = { id: number; message: string; color: string };

/**
 * The answer panel is real state rather than an innerHTML blob, so the icons
 * render as elements instead of stringifying to "[object Object]".
 */
type AskState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "answer"; item: QAItem; ms: number }
  | { phase: "empty"; question: string }
  | { phase: "error"; question: string };

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
    desc: "Built the moment you were added, from content tagged for your role. Nothing to prompt for.",
  },
  ask: {
    eyebrow: "Grounded Q&A",
    title: "Ask & Evidence",
    desc: "Answers come only from sources you're permitted to see, and arrive labelled with who stands behind them.",
  },
  verify: {
    eyebrow: "Verification & trust",
    title: "Verify Queue",
    desc: "Turn a correct draft into the canonical answer everyone gets from now on.",
  },
  reverify: {
    eyebrow: "Staleness & freshness",
    title: "Re-verification Queue",
    desc: "Verified answers whose source material changed since you signed off.",
  },
  dashboard: {
    eyebrow: "Manager analytics",
    title: "Coverage & gaps",
    desc: "Where documentation runs thin, and how much of the knowledge base a human has actually confirmed.",
  },
  sources: {
    eyebrow: "Ingestion",
    title: "Source Connect",
    desc: "Read-only connections. Welcome Wave never writes back to anything it indexes.",
  },
  team: {
    eyebrow: "Department visibility",
    title: "My Team",
    desc: "New hires in your department, with onboarding progress and question activity.",
  },
  users: {
    eyebrow: "Role & permission scoping",
    title: "Users & Departments",
    desc: "Manage functional roles and department scope for every Welcome Wave identity.",
  },
  profile: {
    eyebrow: "Personal workspace",
    title: "My Profile",
    desc: "Your identity, department scope, onboarding progress, and activity.",
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
    citations: [{ label: "onboarding-checklist.md", ref: "Week 1" }],
    status: "verified",
    verifiedBy: "Aman Nautiyal",
    verifiedAt: "Monday",
    roles: ["newhire"],
  },
  {
    id: "ask-2",
    domain: "Access & Permissions",
    owner: "Priya N.",
    question: "How do I request access to the CRM data source?",
    answer:
      "Ask your manager to add you as a viewer role in the CRM connector config — there is currently no self-service request flow.",
    citations: [{ label: "source-access.md", ref: "Access requests" }],
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
    citations: [{ label: "finance-systems-wiki export", ref: "Payroll" }],
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
    citations: [{ label: "staleness-flow.md", ref: "Dependency graph" }],
    status: "verified",
    verifiedBy: "Priya N.",
    verifiedAt: "Wednesday",
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
    citations: [{ label: "sales-playbook.docx", ref: "Discount policy" }],
    status: "verified",
    verifiedBy: "T. Alvarez",
    verifiedAt: "Friday",
    roles: ["newhire", "manager"],
    stale: true,
    staleReason: "sales-playbook.docx was updated yesterday",
  },
];

const ROLE_DATA: Record<
  Role,
  { title: string; overview: string; readFirst: { t: string; meta: string }[]; task: string }
> = {
  newhire: {
    title: "Engineering · New Hire",
    overview: "Built from content tagged for Engineering only — not a generic template.",
    readFirst: [
      { t: "Local environment setup guide", meta: "platform-repo / README.md" },
      { t: "How retrieval and verification fit together", meta: "architecture.md / End-to-end flow" },
      { t: "Who to ask in each domain", meta: "team-directory.md" },
    ],
    task: "Set up your local environment and shadow one live retrieval request.",
  },
  expert: {
    title: "Platform · Domain Expert",
    overview: "Your roadmap surfaces the review duties tied to your domain, not general reading.",
    readFirst: [
      { t: "What verifying an answer commits you to", meta: "verification-workflow.md" },
      { t: "How staleness dependency links work", meta: "staleness-flow.md" },
      { t: "Your current pending drafts", meta: "waiting in Verify Queue" },
    ],
    task: "Clear the drafts currently waiting in your Verify Queue.",
  },
  manager: {
    title: "Platform Team · Manager",
    overview: "Your roadmap points at gaps in your team's documentation, not onboarding steps.",
    readFirst: [
      { t: "Reading the coverage dashboard", meta: "manager-guide.md" },
      { t: "This week's top unanswered question", meta: "CRM data source access" },
      { t: "Re-verification backlog owners", meta: "flagged items by owner" },
    ],
    task: "Review this week's verified-coverage gaps on the Dashboard.",
  },
  admin: {
    title: "Platform Ops · Admin",
    overview: "Your roadmap starts at the ingestion layer — nothing is indexed until you connect a source.",
    readFirst: [
      { t: "Read-only ingestion guarantee", meta: "security-design.md / 9.4" },
      { t: "Scoping include and exclude rules before indexing", meta: "source-connect.md" },
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

const ROLE_CARDS: Array<{ role: Role; title: string; desc: string; accent: string; tint: string }> = [
  {
    role: "newhire",
    title: "New Hire",
    desc: "Land on a role-specific roadmap and ask grounded questions from day one.",
    accent: "var(--violet)",
    tint: "var(--violet-tint)",
  },
  {
    role: "expert",
    title: "Domain Expert",
    desc: "Review AI drafts and confirm the answers your domain is responsible for.",
    accent: "var(--amber)",
    tint: "var(--amber-tint)",
  },
  {
    role: "manager",
    title: "Manager",
    desc: "See where documentation is thin and how much of it is actually trusted.",
    accent: "var(--teal)",
    tint: "var(--teal-tint)",
  },
  {
    role: "admin",
    title: "Admin",
    desc: "Connect knowledge sources and assign them to roles across the console.",
    accent: "var(--coral)",
    tint: "var(--coral-tint)",
  },
];

/* -------------------------------------------------------------------------- */
/* Icons                                                                       */
/* -------------------------------------------------------------------------- */

const S = { fill: "none", stroke: "currentColor", strokeLinecap: "round", strokeLinejoin: "round" } as const;

const NAV_ICONS: Record<ViewName, ReactElement> = {
  roadmap: (
    <svg className="nav-icon" viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <path d="M3 10c2-4 5-6 7-6s5 2 7 6c-2 4-5 6-7 6s-5-2-7-6z" />
      <circle cx="10" cy="10" r="2" />
    </svg>
  ),
  ask: (
    <svg className="nav-icon" viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <path d="M4 4h12v9H8l-4 3z" />
    </svg>
  ),
  verify: (
    <svg className="nav-icon" viewBox="0 0 20 20" strokeWidth="1.8" {...S}>
      <path d="M4 10.5l4 4 8-9" />
    </svg>
  ),
  reverify: (
    <svg className="nav-icon" viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 6v4.2l2.8 1.7" />
    </svg>
  ),
  dashboard: (
    <svg className="nav-icon" viewBox="0 0 20 20" strokeWidth="1.7" {...S}>
      <path d="M4 15V9M10 15V5m6 10v-6" />
    </svg>
  ),
  sources: (
    <svg className="nav-icon" viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <rect x="3" y="4" width="14" height="12" rx="1.5" />
      <path d="M3 8h14" />
    </svg>
  ),
  team: (
    <svg className="nav-icon" viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <circle cx="9" cy="6" r="3" />
      <path d="M3 17c0-3 2.7-5 6-5s6 2 6 5" />
      <path d="M15 4.6a2.5 2.5 0 010 4.8" />
    </svg>
  ),
  users: (
    <svg className="nav-icon" viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <circle cx="7" cy="7" r="3" />
      <path d="M2 17c0-3 2.2-5 5-5s5 2 5 5M14 6h5M16.5 3.5v5" />
    </svg>
  ),
  profile: (
    <svg className="nav-icon" viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <circle cx="10" cy="6" r="3" />
      <path d="M3.5 17c.8-3 3-4.5 6.5-4.5s5.7 1.5 6.5 4.5" />
    </svg>
  ),
};

const DocIcon = () => (
  <svg viewBox="0 0 20 20" strokeWidth="1.7" {...S}>
    <path d="M6 3h6l3 3v11H6z" />
    <path d="M12 3v3h3" />
  </svg>
);

const ArrowIcon = () => (
  <svg viewBox="0 0 20 20" strokeWidth="1.9" {...S}>
    <path d="M4 10h11M11 6l4 4-4 4" />
  </svg>
);

const ROLE_GLYPH: Record<Role, ReactElement> = {
  newhire: (
    <svg viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <path d="M10 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" />
    </svg>
  ),
  expert: (
    <svg viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <path d="M10 3l6 2.5v4c0 4-2.5 6.8-6 7.5-3.5-.7-6-3.5-6-7.5v-4L10 3z" />
      <path d="M7.5 10l1.8 1.8L13 8" />
    </svg>
  ),
  manager: (
    <svg viewBox="0 0 20 20" strokeWidth="1.7" {...S}>
      <path d="M4 15V9M10 15V5m6 10v-6" />
    </svg>
  ),
  admin: (
    <svg viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
      <rect x="3" y="4" width="14" height="12" rx="1.5" />
      <path d="M3 8h14" />
    </svg>
  ),
};

/* -------------------------------------------------------------------------- */

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();

  const [authStep, setAuthStep] = useState<"landing" | "login">("login");
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
  const [excludedPaths, setExcludedPaths] = useState<string[]>(["/secrets", "/legal/contracts"]);
  const [roadmapProgress, setRoadmapProgress] = useState<Record<string, boolean>>({});
  const [roadmapChecks, setRoadmapChecks] = useState<Record<string, Record<number, boolean>>>({});
  const [domainFilter, setDomainFilter] = useState("all");
  const [ownerFilter, setOwnerFilter] = useState("all");
  const [askInput, setAskInput] = useState("");
  const [askState, setAskState] = useState<AskState>({ phase: "idle" });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [newDepartment, setNewDepartment] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserDepartment, setNewUserDepartment] = useState(DEPARTMENTS[0]);
  const [newUserRole, setNewUserRole] = useState<Role>("newhire");
  const [usersMessage, setUsersMessage] = useState("");
  const [repoInput, setRepoInput] = useState("");
  const [assignRole, setAssignRole] = useState("Engineering");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");
  const [toasts, setToasts] = useState<Toast[]>([]);

  const profileMenuRef = useRef<HTMLDivElement | null>(null);
  const askTimer = useRef<number | null>(null);
  const toastTimers = useRef<number[]>([]);
  const toastSeq = useRef(0);

  const activeRole = selectedRole;

  /* --- toasts ----------------------------------------------------------- */

  const showToast = useCallback((message: string, color = "var(--teal)") => {
    const id = ++toastSeq.current;
    setToasts((prev) => [...prev, { id, message, color }]);
    const timer = window.setTimeout(() => {
      setToasts((prev) => prev.filter((toast) => toast.id !== id));
    }, 3200);
    toastTimers.current.push(timer);
  }, []);

  useEffect(
    () => () => {
      toastTimers.current.forEach(window.clearTimeout);
      if (askTimer.current) window.clearTimeout(askTimer.current);
    },
    [],
  );

  /* --- routing ---------------------------------------------------------- */

  useEffect(() => {
    if (!signedIn && location.pathname !== "/login") {
      navigate("/login", { replace: true });
      return;
    }
    if (signedIn && !location.pathname.startsWith("/console")) {
      navigate("/console/roadmap", { replace: true });
      return;
    }
    if (signedIn && location.pathname.startsWith("/console/")) {
      const routeView = location.pathname.replace("/console/", "") as ViewName;
      if (NAV_ITEMS.some((item) => item.view === routeView)) {
        setCurrentView(routeView);
      } else {
        navigate("/console/roadmap", { replace: true });
      }
    }
  }, [location.pathname, navigate, signedIn]);

  /* --- dismiss the profile menu on outside click and Escape ------------- */

  useEffect(() => {
    if (!profileMenuOpen) return;

    function onPointerDown(event: MouseEvent) {
      if (!profileMenuRef.current?.contains(event.target as Node)) setProfileMenuOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setProfileMenuOpen(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [profileMenuOpen]);

  /* --- close the mobile drawer on Escape -------------------------------- */

  useEffect(() => {
    if (!sidebarOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setSidebarOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [sidebarOpen]);

  /* --- derived ---------------------------------------------------------- */

  const visibleNav = useMemo(() => NAV_ITEMS.filter((item) => item.roles.includes(activeRole)), [activeRole]);

  const pendingDrafts = useMemo(() => qa.filter((item) => item.status === "draft"), [qa]);
  const flagged = useMemo(() => qa.filter((item) => item.stale), [qa]);

  const verifiedCount = qa.filter((item) => item.status === "verified").length;
  // Guarded: an empty list used to render "NaN%" in the sidebar and the KPI.
  const coverage = qa.length ? Math.round((verifiedCount / qa.length) * 100) : 0;

  const topBarName = identity.name || "Your name";
  const topBarRole = `${ROLE_LABEL[activeRole]} · ${identity.department}`;
  const topBarInitials = identity.name
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  /* --- auth ------------------------------------------------------------- */

  function goToView(view: ViewName) {
    setCurrentView(view);
    navigate(`/console/${view}`);
  }

  function detectIdentity(email: string, role: Role): User {
    const normalized = email.trim().toLowerCase();
    const known = users.find((user) => user.email.toLowerCase() === normalized);
    if (known) return known;

    const name = email
      .split("@")[0]
      .replace(/[._-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());

    return { name, email: email.trim(), department: role === "admin" ? "All" : "Engineering", role };
  }

  function signInWithRole(role: Role, maybeEmail?: string) {
    const email = (maybeEmail ?? loginEmail).trim();
    if (!email) {
      showToast("Enter your work email to continue.", "var(--coral)");
      return;
    }
    const value = detectIdentity(email, role);
    setIdentity(value);
    setSelectedRole(role);
    setSignedIn(true);
    setAuthStep("login");
    setAskState({ phase: "idle" });
    setAskInput("");
    goToView("roadmap");
    setProfileMenuOpen(false);
    showToast(`Signed in as ${value.name}, ${ROLE_LABEL[role]}.`);
  }

  function switchRole() {
    // Sends you to the role picker rather than straight back to the sign-in
    // form — "switch role" should actually let you compare roles.
    setSignedIn(false);
    setAuthStep("landing");
    navigate("/login");
    setProfileMenuOpen(false);
  }

  function signOut() {
    setSignedIn(false);
    setAuthStep("login");
    navigate("/login");
    setProfileMenuOpen(false);
    setLoginEmail("");
    showToast("Signed out.");
  }

  /* --- roadmap ---------------------------------------------------------- */

  function toggleTaskComplete() {
    setRoadmapProgress((prev) => ({ ...prev, [identity.email]: true }));
    showToast("Task complete. Your team can see the progress.");
  }

  function handleChecklistToggle(index: number, checked: boolean) {
    const key = `${identity.email}:${activeRole}`;
    setRoadmapChecks((prev) => ({ ...prev, [key]: { ...(prev[key] ?? {}), [index]: checked } }));
  }

  /* --- ask -------------------------------------------------------------- */

  function runAsk(question: string) {
    const normalized = question.trim();
    if (!normalized) return;
    if (askTimer.current) window.clearTimeout(askTimer.current);

    const match = qa.find((item) => item.question.toLowerCase() === normalized.toLowerCase());

    // A verified answer is served from the store, so it lands noticeably
    // faster than one that has to be generated. The timing is part of the
    // product's argument, not decoration.
    const ms = match?.status === "verified" && !match.stale ? 240 : 780;

    setAskState({ phase: "loading" });
    askTimer.current = window.setTimeout(() => {
      setAskState(match ? { phase: "answer", item: match, ms } : { phase: "empty", question: normalized });
    }, ms);
  }

  /* --- verification ----------------------------------------------------- */

  function verifyItem(item: QAItem) {
    setQa((prev) =>
      prev.map((entry) =>
        entry.id === item.id
          ? { ...entry, status: "verified", verifiedBy: identity.name, verifiedAt: "just now", stale: false, staleReason: undefined }
          : entry,
      ),
    );
    setEditingId(null);
    showToast("Verified. This is the answer everyone gets now.", "var(--amber)");
  }

  function reconfirmItem(item: QAItem) {
    setQa((prev) =>
      prev.map((entry) =>
        entry.id === item.id
          ? { ...entry, stale: false, staleReason: undefined, verifiedBy: identity.name, verifiedAt: "just now" }
          : entry,
      ),
    );
    showToast("Re-confirmed. The staleness flag is cleared.", "var(--coral)");
  }

  function startEditing(item: QAItem) {
    setEditingId(item.id);
    setEditingText(item.answer);
  }

  function saveEdit(id: string) {
    const next = editingText.trim();
    if (!next) {
      showToast("An answer can't be empty.", "var(--coral)");
      return;
    }
    setQa((prev) => prev.map((entry) => (entry.id === id ? { ...entry, answer: next } : entry)));
    setEditingId(null);
    showToast("Draft saved. Review it once more before verifying.", "var(--amber)");
  }

  /* --- sources ---------------------------------------------------------- */

  function addSource(name: string, role: string) {
    setSources((prev) => [...prev, { name, role, status: "syncing", synced: "now" }]);
    window.setTimeout(() => {
      setSources((prev) =>
        prev.map((source) =>
          source.name === name && source.status === "syncing"
            ? { ...source, status: "indexed", synced: "just now" }
            : source,
        ),
      );
      showToast(`${name} indexed.`);
    }, 1300);
  }

  function simulateSourceUpdate(source: SourceItem) {
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
      affected.length
        ? `${affected.length} verified ${affected.length === 1 ? "answer needs" : "answers need"} re-verification.`
        : "Re-indexed. No verified answers depend on this source.",
      affected.length ? "var(--amber)" : "var(--teal)",
    );
  }

  /* --- users ------------------------------------------------------------ */

  function addDepartment() {
    const trimmed = newDepartment.trim();
    if (!trimmed) {
      setUsersMessage("Enter a department name first.");
      return;
    }
    if (departments.some((item) => item.toLowerCase() === trimmed.toLowerCase())) {
      setUsersMessage(`${trimmed} already exists.`);
      return;
    }
    setDepartments((prev) => [...prev, trimmed]);
    setNewDepartment("");
    setNewUserDepartment(trimmed);
    setUsersMessage(`${trimmed} added.`);
    showToast(`Department ${trimmed} added.`);
  }

  function addUser() {
    const name = newUserName.trim();
    const email = newUserEmail.trim();
    if (!name || !email) {
      setUsersMessage("Enter both a name and an email.");
      return;
    }
    if (users.some((user) => user.email.toLowerCase() === email.toLowerCase())) {
      setUsersMessage(`${email} already has an account.`);
      return;
    }
    setUsers((prev) => [...prev, { name, email, department: newUserDepartment, role: newUserRole }]);
    setNewUserName("");
    setNewUserEmail("");
    setUsersMessage(`${name} added as ${ROLE_LABEL[newUserRole]}.`);
    showToast(`${name} added to ${newUserDepartment}.`);
  }

  /* --- fragments -------------------------------------------------------- */

  function renderRoadmapList() {
    const key = `${identity.email}:${activeRole}`;
    const saved = roadmapChecks[key] ?? {};
    const items = ROLE_DATA[activeRole].readFirst;
    const complete = items.filter((_, index) => saved[index]).length;
    const percent = items.length ? Math.round((complete / items.length) * 100) : 0;

    return (
      <>
        <div className="roadmap-progress">
          <div className="roadmap-progress-head">
            <span>Reading progress</span>
            <strong aria-live="polite">
              {complete} of {items.length} done
            </strong>
          </div>
          <div
            className="roadmap-progress-track"
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Reading progress"
          >
            <div className="roadmap-progress-fill" style={{ width: `${percent}%` }} />
          </div>
        </div>
        <ul className="checklist">
          {items.map((item, index) => (
            <li key={item.t} className={saved[index] ? "done" : ""}>
              <input
                type="checkbox"
                id={`rf-${index}`}
                checked={Boolean(saved[index])}
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
  }

  function renderAskResult() {
    if (askState.phase === "idle") return null;

    if (askState.phase === "loading") {
      return (
        <div className="answer-block state-loading" aria-live="polite" aria-busy="true">
          <span className="small">Searching sources you're permitted to see…</span>
          <div className="skeleton" style={{ width: "72%" }} />
          <div className="skeleton" style={{ width: "94%" }} />
          <div className="skeleton" style={{ width: "44%" }} />
        </div>
      );
    }

    if (askState.phase === "empty") {
      return (
        <div className="answer-block state-empty" aria-live="polite">
          <svg viewBox="0 0 20 20" strokeWidth="1.5" {...S}>
            <circle cx="10" cy="10" r="7" />
            <path d="M10 7v4M10 13.5h.01" />
          </svg>
          <div>
            <strong>Nothing in your sources covers this yet.</strong> Rephrase the question, or send it to a domain
            expert to answer and verify.
          </div>
          <button className="btn btn-sm" onClick={() => showToast("Sent to a domain expert.", "var(--amber)")}>
            Send to an expert
          </button>
        </div>
      );
    }

    if (askState.phase === "error") {
      return (
        <div className="answer-block state-error" aria-live="assertive">
          <svg viewBox="0 0 20 20" strokeWidth="1.5" {...S}>
            <circle cx="10" cy="10" r="7" />
            <path d="M7 7l6 6M13 7l-6 6" />
          </svg>
          <div>
            <strong>The knowledge index didn't respond.</strong> Your permissions and saved answers are unaffected. Try
            again, and tell an admin if it keeps happening.
          </div>
          <button className="btn btn-sm" onClick={() => runAsk(askState.question)}>
            Try again
          </button>
        </div>
      );
    }

    const { item, ms } = askState;
    const state = trustStateOf(item);

    return (
      <div className="answer-block" aria-live="polite">
        <div className="answer-head">
          <TrustBadge state={state} verifiedBy={item.verifiedBy} />
          <span className="answer-question">for “{item.question}”</span>
        </div>
        <p className="answer-text">{item.answer}</p>
        {item.stale && <p className="answer-note">Flagged because {item.staleReason}.</p>}
        <div className="citation-list">
          <span className="citation-label">
            {state === "verified" ? "Served from a verified answer" : "Generated from"} in {(ms / 1000).toFixed(2)}s
          </span>
          {item.citations.map((citation) => (
            <button
              key={citation.label}
              className="citation"
              onClick={() => showToast(`Opening ${citation.label}.`)}
            >
              <DocIcon />
              {citation.label} — {citation.ref}
            </button>
          ))}
        </div>
      </div>
    );
  }

  function renderSourcesList() {
    return (
      <div className="table-scroll">
        <table className="sources-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Role</th>
              <th>Status</th>
              <th>Last synced</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sources.map((source) => (
              <tr key={source.name}>
                <td>{source.name}</td>
                <td>{source.role}</td>
                <td>
                  <span className={`status-pill status-${source.status}`}>
                    <span className="dot" />
                    {source.status === "indexed" ? "Indexed" : source.status === "syncing" ? "Syncing" : "Error"}
                  </span>
                </td>
                <td className="mono">{source.synced}</td>
                <td>
                  <button className="btn btn-sm" onClick={() => simulateSourceUpdate(source)}>
                    Simulate a change
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  function renderTeamTable() {
    const team = users.filter((user) => user.role === "newhire" && user.department === identity.department);
    if (!team.length) {
      return (
        <div className="state-empty">
          <div>
            <strong>No new hires in {identity.department} yet.</strong> They'll appear here as soon as an admin adds
            them.
          </div>
        </div>
      );
    }
    return (
      <div className="table-scroll">
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
                      {done ? "Done" : "In progress"}
                    </span>
                  </td>
                  <td className="mono">{asks}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
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
            <h2>{identity.name}</h2>
            <p className="muted small" style={{ marginTop: 4 }}>
              {identity.email}
            </p>
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
              <div>
                <dt>Work email</dt>
                <dd>{identity.email}</dd>
              </div>
              <div>
                <dt>Functional role</dt>
                <dd>{roleLabel}</dd>
              </div>
              <div>
                <dt>Department scope</dt>
                <dd>{identity.department}</dd>
              </div>
              <div>
                <dt>Access level</dt>
                <dd>{accessLevel}</dd>
              </div>
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
              <span className="profile-metric-value">{completed ? "Done" : "In progress"}</span>
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
  const brandCluster = (
    <span className="brand-cluster" aria-hidden="true">
      <span style={{ background: "var(--violet)" }} />
      <span style={{ background: "var(--teal)" }} />
      <span style={{ background: "var(--amber)" }} />
      <span style={{ background: "var(--coral)" }} />
    </span>
  );

  /* ------------------------------------------------------------------ */

  return (
    <>
      <div className="toast-stack" aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => (
          <div key={toast.id} className="toast">
            <span className="dot" style={{ background: toast.color, color: toast.color }} />
            {toast.message}
          </div>
        ))}
      </div>

      {/* ---------- role picker ---------- */}
      {authStep === "landing" && !signedIn && (
        <section className="landing">
          <div className="landing-inner">
            <div className="landing-brand">
              {brandCluster}
              <span className="brand-name">Welcome Wave</span>
            </div>
            <p className="landing-tagline">Grounded answers. Verified trust. No guesswork onboarding.</p>

            <div className="how-strip">
              <div className="how-step">
                <span className="num" style={{ background: "var(--violet-tint)", color: "var(--violet-text)" }}>1</span>
                <p>
                  <strong>Pick a role</strong>Your roadmap and permissions are scoped to it immediately.
                </p>
              </div>
              <div className="how-step">
                <span className="num" style={{ background: "var(--teal-tint)", color: "var(--teal-text)" }}>2</span>
                <p>
                  <strong>Ask anything in scope</strong>Answers are built only from content you're permitted to see.
                </p>
              </div>
              <div className="how-step">
                <span className="num" style={{ background: "var(--amber-tint)", color: "var(--amber-text)" }}>3</span>
                <p>
                  <strong>An expert signs off</strong>A confirmed answer is served instantly, and re-flagged if its
                  source changes.
                </p>
              </div>
            </div>

            <p className="landing-heading">Choose a role to explore</p>

            <div className="role-grid" role="group" aria-label="Choose a role">
              {ROLE_CARDS.map((card) => (
                <button
                  key={card.role}
                  className="role-card"
                  data-role={card.role}
                  onClick={() => {
                    setPendingRole(card.role);
                    setAuthStep("login");
                  }}
                >
                  <div className="card-top" style={{ background: card.accent }} />
                  <div className="card-body">
                    {/* This used to be `${card.accent}-tint`, which produced the
                        invalid value "var(--violet)-tint" and no background. */}
                    <div className="role-icon" style={{ background: card.tint, color: card.accent }}>
                      {ROLE_GLYPH[card.role]}
                    </div>
                    <h3>{card.title}</h3>
                    <p className="role-desc">{card.desc}</p>
                    <div className="access-tags">
                      <span className="access-tag">
                        <span className="dot" style={{ background: "var(--violet)" }} />
                        Roadmap
                      </span>
                      <span className="access-tag">
                        <span className="dot" style={{ background: "var(--teal)" }} />
                        Ask &amp; Evidence
                      </span>
                      {card.role === "expert" && (
                        <span className="access-tag">
                          <span className="dot" style={{ background: "var(--amber)" }} />
                          Verify Queue
                        </span>
                      )}
                      {card.role === "manager" && (
                        <span className="access-tag">
                          <span className="dot" style={{ background: "var(--teal)" }} />
                          Dashboard
                        </span>
                      )}
                      {card.role === "admin" && (
                        <span className="access-tag">
                          <span className="dot" style={{ background: "var(--coral)" }} />
                          Sources, users and analytics
                        </span>
                      )}
                    </div>
                    <span className="continue">
                      Continue as {card.title}
                      <ArrowIcon />
                    </span>
                  </div>
                </button>
              ))}
            </div>

            <p className="landing-footnote">
              In production your role comes from the company directory. This preview lets you pick one so you can see
              what each role is allowed to do.
            </p>
          </div>
        </section>
      )}

      {/* ---------- sign in ---------- */}
      {authStep === "login" && !signedIn && (
        <section className="landing">
          <div className="login-page">
            <div className="login-visual">
              <div className="landing-brand">
                {brandCluster}
                <span className="brand-name">Welcome Wave</span>
              </div>
              <div className="login-visual-copy">
                <h1>Every answer traced back to someone who knows.</h1>
                <p>
                  New hires get a roadmap before they think to ask. When they do ask, the answer cites its sources,
                  says whether a human has confirmed it, and flags itself the moment that source changes.
                </p>
              </div>
              <div className="login-stats">
                <div className="login-stat">
                  <strong>Four roles</strong>
                  <span>separate access paths</span>
                </div>
                <div className="login-stat">
                  <strong>Every answer</strong>
                  <span>cited and labelled</span>
                </div>
                <div className="login-stat">
                  <strong>Read-only</strong>
                  <span>sources are never written to</span>
                </div>
              </div>
            </div>

            <div className="login-panel">
              <form
                className="login-card"
                onSubmit={(event) => {
                  event.preventDefault();
                  signInWithRole(pendingRole, loginEmail);
                }}
              >
                <div className="eyebrow">
                  <span className="dot" style={{ background: "var(--teal)" }} />
                  Onboarding console
                </div>
                <h2>Sign in</h2>
                <p className="login-sub">Your role decides what you can see and do.</p>

                <div className="login-detection">
                  <span>Signing in as</span>
                  <select
                    aria-label="Your role"
                    value={pendingRole}
                    onChange={(event) => setPendingRole(event.target.value as Role)}
                  >
                    <option value="newhire">New Hire</option>
                    <option value="expert">Domain Expert</option>
                    <option value="manager">Manager</option>
                    <option value="admin">Admin</option>
                  </select>
                </div>

                <div className="login-field">
                  <label htmlFor="loginEmail">Work email</label>
                  <input
                    type="email"
                    id="loginEmail"
                    value={loginEmail}
                    onChange={(event) => setLoginEmail(event.target.value)}
                    placeholder="you@company.com"
                    autoComplete="email"
                  />
                </div>

                <button type="submit" className="btn btn-primary sso-btn">
                  Continue
                </button>

                <div className="login-divider">or</div>

                <button
                  type="button"
                  className="btn sso-btn"
                  onClick={() => {
                    const fallback = users.find((user) => user.role === pendingRole);
                    const nextEmail = fallback ? fallback.email : "you@company.com";
                    setLoginEmail(nextEmail);
                    signInWithRole(pendingRole, nextEmail);
                  }}
                >
                  <svg viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
                    <rect x="3" y="3" width="14" height="14" rx="2" />
                    <path d="M3 8h14" />
                  </svg>
                  Continue with company SSO
                </button>

                <button type="button" className="demo-link" onClick={() => setAuthStep("landing")}>
                  Not sure which role? Compare all four
                </button>

                <p className="login-footnote">This preview skips real authentication — any email will get you in.</p>
              </form>
            </div>
          </div>
        </section>
      )}

      {/* ---------- console ---------- */}
      {signedIn && (
        <div className="app">
          <a className="skip-link" href="#main">
            Skip to content
          </a>

          <aside className={`sidebar ${sidebarOpen ? "open" : ""}`} aria-label="Primary">
            <div className="brand">
              <span className="brand-mark" aria-hidden="true" />
              <span className="brand-name">Welcome Wave</span>
            </div>
            <div className="brand-sub">Onboarding console</div>
            <div className="role-pill">
              <span className="dot" />
              Viewing as <strong>{ROLE_LABEL[activeRole]}</strong>
            </div>

            <nav className="nav-group" aria-label="Console sections">
              {visibleNav.map((item) => {
                const count =
                  item.view === "verify" ? pendingDrafts.length : item.view === "reverify" ? flagged.length : null;
                return (
                  <button
                    key={item.view}
                    className={`nav-item ${currentView === item.view ? "active" : ""}`}
                    data-view={item.view}
                    aria-current={currentView === item.view ? "page" : undefined}
                    onClick={() => {
                      goToView(item.view);
                      setSidebarOpen(false);
                    }}
                  >
                    {NAV_ICONS[item.view]}
                    {item.label}
                    {count !== null && (
                      <span className="nav-count" data-has-items={count > 0}>
                        {count}
                      </span>
                    )}
                  </button>
                );
              })}
            </nav>

            <div className="sidebar-footer">
              <div className="row">
                <span>Active sources</span>
                <span>{sources.filter((source) => source.status !== "error").length}</span>
              </div>
              <div className="row">
                <span>Verified coverage</span>
                <span>{coverage}%</span>
              </div>
              <div className="row">
                <span>Avg. answer latency</span>
                <span>1.8s</span>
              </div>
            </div>
          </aside>

          <div className={`scrim ${sidebarOpen ? "show" : ""}`} onClick={() => setSidebarOpen(false)} />

          <main id="main">
            <button className="menu-toggle" onClick={() => setSidebarOpen(true)} aria-label="Open navigation">
              <svg width="16" height="16" viewBox="0 0 20 20" strokeWidth="1.7" {...S}>
                <path d="M3 5h14M3 10h14M3 15h14" />
              </svg>
              Menu
            </button>

            <div className="topbar">
              <div className="topbar-title">
                <div className="eyebrow">{viewMeta.eyebrow}</div>
                <h1>{viewMeta.title}</h1>
                <p className="desc">{viewMeta.desc}</p>
              </div>

              <div className="topbar-actions" ref={profileMenuRef}>
                <button
                  className="profile-menu-trigger"
                  aria-expanded={profileMenuOpen}
                  aria-haspopup="menu"
                  onClick={() => setProfileMenuOpen((prev) => !prev)}
                >
                  <span className="profile-menu-avatar">{topBarInitials}</span>
                  <span className="profile-menu-copy">
                    <span className="profile-menu-name">{topBarName}</span>
                    <span className="profile-menu-role">{topBarRole}</span>
                  </span>
                  <span className="profile-menu-chevron">
                    <svg width="13" height="13" viewBox="0 0 20 20" strokeWidth="1.8" {...S}>
                      <path d="M6 8l4 4 4-4" />
                    </svg>
                  </span>
                </button>
                <div className="profile-menu" role="menu" hidden={!profileMenuOpen}>
                  <button
                    role="menuitem"
                    onClick={() => {
                      setProfileMenuOpen(false);
                      goToView("profile");
                    }}
                  >
                    View profile
                  </button>
                  <button role="menuitem" onClick={switchRole}>
                    Switch role
                  </button>
                  <div className="menu-sep" />
                  <button role="menuitem" className="menu-danger" onClick={signOut}>
                    Sign out
                  </button>
                </div>
              </div>
            </div>

            {/* ---------- roadmap ---------- */}
            {currentView === "roadmap" && (
              <section className="view">
                <div className="roadmap-grid">
                  <div className="card accent-violet">
                    <div className="accent-bar" />
                    <div className="section-heading">
                      <h2>{ROLE_DATA[activeRole].title}</h2>
                    </div>
                    <p className="muted small">{ROLE_DATA[activeRole].overview}</p>
                    {renderRoadmapList()}
                  </div>

                  <div>
                    <div className="card accent-violet">
                      <div className="accent-bar" />
                      <div className="section-heading">
                        <h2>Start here</h2>
                      </div>
                      <div className="task-callout">
                        <p className="label">TODAY</p>
                        <p>{ROLE_DATA[activeRole].task}</p>
                        <button
                          className="btn btn-primary btn-sm"
                          onClick={toggleTaskComplete}
                          disabled={Boolean(roadmapProgress[identity.email])}
                          style={{ marginTop: 14 }}
                        >
                          {roadmapProgress[identity.email] ? "Completed" : "Mark complete"}
                        </button>
                      </div>
                    </div>

                    <div className="card accent-teal">
                      <div className="accent-bar" />
                      <div className="section-heading">
                        <h2>Already have a question?</h2>
                      </div>
                      <p className="muted small">
                        Skip ahead. Every answer is grounded in your sources and labelled with who confirmed it.
                      </p>
                      <button className="btn btn-teal" style={{ marginTop: 14 }} onClick={() => goToView("ask")}>
                        Ask a question
                        <ArrowIcon />
                      </button>
                    </div>
                  </div>
                </div>
              </section>
            )}

            {/* ---------- ask ---------- */}
            {currentView === "ask" && (
              <section className="view">
                <div className="card accent-teal">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Ask anything in your scope</h2>
                    <span
                      className="info-tip"
                      tabIndex={0}
                      role="note"
                      aria-label="Answers are built only from content retrieved for your role. Nothing outside your permitted scope is used."
                      data-tip="Answers are built only from content retrieved for your role. Nothing outside your permitted scope is used."
                    >
                      i
                    </span>
                  </div>

                  <div className="legend-row">
                    <span className="legend-item">
                      <TrustBadge state="verified" />
                      confirmed by a person
                    </span>
                    <span className="legend-item">
                      <TrustBadge state="draft" />
                      not reviewed yet
                    </span>
                    <span className="legend-item">
                      <TrustBadge state="stale" />
                      its source changed
                    </span>
                  </div>

                  <form
                    className="ask-box"
                    onSubmit={(event) => {
                      event.preventDefault();
                      runAsk(askInput);
                    }}
                  >
                    <input
                      type="text"
                      value={askInput}
                      onChange={(event) => setAskInput(event.target.value)}
                      placeholder="What's my first task this week?"
                      aria-label="Ask a question"
                    />
                    <button type="submit" className="btn btn-teal" disabled={!askInput.trim()}>
                      Ask
                    </button>
                  </form>

                  <div className="chip-row">
                    {qa
                      .filter((item) => item.roles.includes(activeRole))
                      .map((item) => (
                        <button
                          key={item.id}
                          className="chip"
                          onClick={() => {
                            setAskInput(item.question);
                            runAsk(item.question);
                          }}
                        >
                          {item.question}
                        </button>
                      ))}
                  </div>

                  {renderAskResult()}

                  <button
                    className="demo-link"
                    onClick={() => setAskState({ phase: "error", question: askInput || "What's my first task this week?" })}
                  >
                    Preview the retrieval-failure state
                  </button>
                </div>
              </section>
            )}

            {/* ---------- verify ---------- */}
            {currentView === "verify" && (
              <section className="view">
                <div className="card accent-amber">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Waiting on you</h2>
                    <span
                      className="info-tip"
                      tabIndex={0}
                      role="note"
                      aria-label="Verifying makes an answer canonical. It is then served instantly to everyone, skipping generation, until its source changes."
                      data-tip="Verifying makes an answer canonical. It is then served instantly to everyone, skipping generation, until its source changes."
                    >
                      i
                    </span>
                    <span className="count">{pendingDrafts.length} pending</span>
                  </div>
                  <p className="muted small">
                    Verifying makes an answer canonical — it's served ahead of any new draft from then on.
                  </p>

                  {pendingDrafts.length === 0 ? (
                    <div className="state-empty">
                      <svg viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
                        <path d="M4 10.5l4 4 8-9" />
                      </svg>
                      <div>
                        <strong>Queue clear.</strong> Nothing is waiting on your review.
                      </div>
                    </div>
                  ) : (
                    pendingDrafts.map((item) => (
                      <div key={item.id} className="queue-row">
                        <div>
                          <div className="queue-q">{item.question}</div>
                          {editingId === item.id ? (
                            <div className="queue-edit">
                              <textarea
                                value={editingText}
                                onChange={(event) => setEditingText(event.target.value)}
                                aria-label={`Edit the answer to: ${item.question}`}
                                autoFocus
                              />
                              <div className="queue-edit-actions">
                                <button className="btn btn-sm btn-amber" onClick={() => saveEdit(item.id)}>
                                  Save draft
                                </button>
                                <button className="btn btn-sm btn-ghost" onClick={() => setEditingId(null)}>
                                  Cancel
                                </button>
                              </div>
                            </div>
                          ) : (
                            <>
                              <div className="queue-a">{item.answer}</div>
                              <div className="queue-meta">
                                <span className="mono">{item.domain}</span>
                                <TrustBadge state="draft" />
                              </div>
                            </>
                          )}
                        </div>
                        {editingId !== item.id && (
                          <div className="queue-actions">
                            <button className="btn btn-sm" onClick={() => startEditing(item)}>
                              Edit
                            </button>
                            <button className="btn btn-sm btn-amber" onClick={() => verifyItem(item)}>
                              Verify
                            </button>
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </section>
            )}

            {/* ---------- re-verify ---------- */}
            {currentView === "reverify" && (
              <section className="view">
                <div className="card accent-coral">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Sources moved underneath these</h2>
                    <span
                      className="info-tip"
                      tabIndex={0}
                      role="note"
                      aria-label="When a source document changes, every verified answer that depended on it drops back here for a fresh check."
                      data-tip="When a source document changes, every verified answer that depended on it drops back here for a fresh check."
                    >
                      i
                    </span>
                    <span className="count">{flagged.length} flagged</span>
                  </div>
                  <p className="muted small" style={{ marginBottom: 14 }}>
                    A verified answer lands here automatically when the source chunk it depends on changes.
                  </p>

                  <div className="filter-row">
                    <label htmlFor="domainFilter">Domain</label>
                    <select id="domainFilter" value={domainFilter} onChange={(event) => setDomainFilter(event.target.value)}>
                      <option value="all">All domains</option>
                      {Array.from(new Set(flagged.map((item) => item.domain))).map((domain) => (
                        <option key={domain} value={domain}>
                          {domain}
                        </option>
                      ))}
                    </select>
                    <label htmlFor="ownerFilter">Owner</label>
                    <select id="ownerFilter" value={ownerFilter} onChange={(event) => setOwnerFilter(event.target.value)}>
                      <option value="all">All owners</option>
                      {Array.from(new Set(flagged.map((item) => item.owner).filter(Boolean))).map((owner) => (
                        <option key={owner} value={owner}>
                          {owner}
                        </option>
                      ))}
                    </select>
                  </div>

                  {(() => {
                    const rows = flagged.filter(
                      (item) =>
                        (domainFilter === "all" || item.domain === domainFilter) &&
                        (ownerFilter === "all" || item.owner === ownerFilter),
                    );
                    if (!rows.length) {
                      return (
                        <div className="state-empty">
                          <svg viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
                            <path d="M4 10.5l4 4 8-9" />
                          </svg>
                          <div>
                            <strong>Nothing stale.</strong>{" "}
                            {flagged.length
                              ? "No flagged answers match these filters."
                              : "Every verified answer still matches its source."}
                          </div>
                        </div>
                      );
                    }
                    return rows.map((item) => (
                      <div key={item.id} className="queue-row">
                        <div>
                          <div className="queue-q">{item.question}</div>
                          <div className="queue-a">{item.answer}</div>
                          <div className="queue-meta">
                            <span className="mono">{item.domain}</span>
                            <span>Owner: {item.owner}</span>
                            <span className="warn">{item.staleReason}</span>
                          </div>
                        </div>
                        <div className="queue-actions">
                          <button className="btn btn-sm btn-coral" onClick={() => reconfirmItem(item)}>
                            Still correct
                          </button>
                        </div>
                      </div>
                    ));
                  })()}
                </div>
              </section>
            )}

            {/* ---------- dashboard ---------- */}
            {currentView === "dashboard" && (
              <section className="view">
                <div className="kpi-grid">
                  <div className="card accent-teal kpi">
                    <div className="accent-bar" />
                    <div className="kpi-label">
                      Recurring unanswered questions
                      <span
                        className="info-tip"
                        tabIndex={0}
                        role="note"
                        aria-label="Questions asked repeatedly that returned no grounded answer — a signal of missing documentation."
                        data-tip="Questions asked repeatedly that returned no grounded answer — a signal of missing documentation."
                      >
                        i
                      </span>
                    </div>
                    <div className="kpi-value" style={{ color: "var(--teal-text)" }}>
                      29
                    </div>
                    <div className="kpi-sub">across 3 roles, last 14 days</div>
                  </div>

                  <div className="card accent-amber kpi">
                    <div className="accent-bar" />
                    <div className="kpi-label">
                      Verified coverage
                      <span
                        className="info-tip"
                        tabIndex={0}
                        role="note"
                        aria-label="Share of tracked answers a person has confirmed. Updates as drafts get verified."
                        data-tip="Share of tracked answers a person has confirmed. Updates as drafts get verified."
                      >
                        i
                      </span>
                    </div>
                    <div className="kpi-value" style={{ color: "var(--amber-text)" }}>
                      {coverage}%
                    </div>
                    <div className="kpi-sub">
                      {verifiedCount} of {qa.length} tracked answers
                    </div>
                  </div>

                  <div className="card accent-coral kpi">
                    <div className="accent-bar" />
                    <div className="kpi-label">
                      Re-verification backlog
                      <span
                        className="info-tip"
                        tabIndex={0}
                        role="note"
                        aria-label="Verified answers currently flagged because a source they depend on was edited."
                        data-tip="Verified answers currently flagged because a source they depend on was edited."
                      >
                        i
                      </span>
                    </div>
                    <div className="kpi-value" style={{ color: "var(--coral-text)" }}>
                      {flagged.length}
                    </div>
                    <div className="kpi-sub">flagged by source changes</div>
                  </div>
                </div>

                <div className="roadmap-grid">
                  <div className="card accent-teal">
                    <div className="accent-bar" />
                    <div className="section-heading">
                      <h2>Asked most, answered least</h2>
                    </div>
                    <ul className="rank-list">
                      <li>
                        <span>How do I request access to the CRM data source?</span>
                        <span className="count">14 asks</span>
                      </li>
                      <li>
                        <span>Who owns the payroll integration docs?</span>
                        <span className="count">9 asks</span>
                      </li>
                      <li>
                        <span>What's the escalation path for a P1 outside business hours?</span>
                        <span className="count">6 asks</span>
                      </li>
                    </ul>
                  </div>

                  <div className="card accent-amber">
                    <div className="accent-bar" />
                    <div className="section-heading">
                      <h2>Verified coverage per role</h2>
                    </div>
                    {[
                      { label: "Engineering", value: 71 },
                      { label: "Sales", value: 54 },
                      { label: "HR", value: 88 },
                    ].map((bar) => (
                      <div className="bar-row" key={bar.label}>
                        <span className="bar-label">{bar.label}</span>
                        <div
                          className="bar-track"
                          role="progressbar"
                          aria-valuenow={bar.value}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-label={`${bar.label} verified coverage`}
                        >
                          <div className="bar-fill" style={{ width: `${bar.value}%`, background: "var(--amber)" }} />
                        </div>
                        <span className="bar-value">{bar.value}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              </section>
            )}

            {/* ---------- sources ---------- */}
            {currentView === "sources" && (
              <section className="view">
                <div className="card accent-violet">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Connect a knowledge source</h2>
                    <span
                      className="info-tip"
                      tabIndex={0}
                      role="note"
                      aria-label="Indexing only reads content in. Welcome Wave never writes back to anything it connects to."
                      data-tip="Indexing only reads content in. Welcome Wave never writes back to anything it connects to."
                    >
                      i
                    </span>
                  </div>

                  <button className="dropzone" onClick={() => addSource("onboarding-checklist.md", "Engineering")}>
                    <svg viewBox="0 0 24 24" strokeWidth="1.6" {...S}>
                      <path d="M12 16V4M8 8l4-4 4 4" />
                      <path d="M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3" />
                    </svg>
                    <span>
                      <strong>Drop a PDF, Markdown or Word file</strong>, or click to browse
                    </span>
                  </button>

                  <div className="form-grid">
                    <div className="form-field">
                      <label htmlFor="ghRepo">GitHub repository</label>
                      <input
                        type="text"
                        id="ghRepo"
                        value={repoInput}
                        onChange={(event) => setRepoInput(event.target.value)}
                        placeholder="org/repo-name"
                      />
                      <p className="field-hint">Optional. Cloned read-only, never written to.</p>
                    </div>
                    <div className="form-field">
                      <label htmlFor="assignRole">Who can see it</label>
                      <select id="assignRole" value={assignRole} onChange={(event) => setAssignRole(event.target.value)}>
                        <option>Engineering</option>
                        <option>Sales</option>
                        <option>HR</option>
                        <option>Support</option>
                      </select>
                      <p className="field-hint">Enforced at retrieval, not just hidden in the interface.</p>
                    </div>
                  </div>

                  <div className="form-field">
                    <label>Paths to leave out of the index</label>
                    <div className="scope-row">
                      {excludedPaths.map((path) => (
                        <span className="scope-chip" key={path}>
                          {path}
                          {/* Removing used to call node.remove() directly, so the
                              chip came back on the next React render. */}
                          <button
                            aria-label={`Stop excluding ${path}`}
                            onClick={() => setExcludedPaths((prev) => prev.filter((value) => value !== path))}
                          >
                            <svg width="12" height="12" viewBox="0 0 20 20" strokeWidth="2" {...S}>
                              <path d="M5 5l10 10M15 5L5 15" />
                            </svg>
                          </button>
                        </span>
                      ))}
                      {!excludedPaths.length && <span className="small muted">Everything in this source is indexed.</span>}
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button
                      className="btn btn-violet"
                      onClick={() => {
                        const name = repoInput.trim() ? `${repoInput.trim()} (GitHub repo)` : "new-upload.pdf";
                        addSource(name, assignRole);
                        setRepoInput("");
                      }}
                    >
                      Connect source
                    </button>
                    <button
                      className="btn"
                      onClick={() => showToast("That file couldn't be parsed. Nothing else was affected.", "var(--coral)")}
                    >
                      Preview a failed upload
                    </button>
                  </div>

                  <div className="lock-note">
                    <svg viewBox="0 0 20 20" strokeWidth="1.6" {...S}>
                      <rect x="4" y="9" width="12" height="8" rx="1.5" />
                      <path d="M7 9V6a3 3 0 016 0v3" />
                    </svg>
                    Read-only, always. Indexing reads content in; Welcome Wave never writes back to a connected source.
                  </div>
                </div>

                <div className="card accent-violet">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Connected sources</h2>
                    <span className="count">{sources.length} connected</span>
                  </div>
                  {renderSourcesList()}
                </div>
              </section>
            )}

            {/* ---------- team ---------- */}
            {currentView === "team" && (
              <section className="view">
                <div className="card accent-teal">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>My team</h2>
                    <span
                      className="info-tip"
                      tabIndex={0}
                      role="note"
                      aria-label="Team visibility comes from the department attached to your identity."
                      data-tip="Team visibility comes from the department attached to your identity."
                    >
                      i
                    </span>
                  </div>
                  <p className="muted small">New hires in {identity.department} appear here automatically.</p>
                  {renderTeamTable()}
                </div>
              </section>
            )}

            {/* ---------- users ---------- */}
            {currentView === "users" && (
              <section className="view">
                <div className="card accent-coral">
                  <div className="accent-bar" />
                  <div className="section-heading">
                    <h2>Add people and departments</h2>
                    <span
                      className="info-tip"
                      tabIndex={0}
                      role="note"
                      aria-label="Functional role controls what someone can do. Department controls which knowledge they can see."
                      data-tip="Functional role controls what someone can do. Department controls which knowledge they can see."
                    >
                      i
                    </span>
                  </div>

                  <div className="form-grid">
                    <div className="form-field">
                      <label htmlFor="newDepartment">New department</label>
                      <input
                        type="text"
                        id="newDepartment"
                        value={newDepartment}
                        onChange={(event) => setNewDepartment(event.target.value)}
                        placeholder="Support"
                      />
                    </div>
                    <div className="form-field" style={{ alignSelf: "end" }}>
                      <button className="btn btn-coral" onClick={addDepartment}>
                        Add department
                      </button>
                    </div>
                  </div>

                  <div className="form-grid">
                    <div className="form-field">
                      <label htmlFor="newUserName">Name</label>
                      <input
                        type="text"
                        id="newUserName"
                        value={newUserName}
                        onChange={(event) => setNewUserName(event.target.value)}
                        placeholder="Full name"
                      />
                    </div>
                    <div className="form-field">
                      <label htmlFor="newUserEmail">Email</label>
                      <input
                        type="email"
                        id="newUserEmail"
                        value={newUserEmail}
                        onChange={(event) => setNewUserEmail(event.target.value)}
                        placeholder="name@company.com"
                      />
                    </div>
                    <div className="form-field">
                      <label htmlFor="newUserDepartment">Department</label>
                      <select
                        id="newUserDepartment"
                        value={newUserDepartment}
                        onChange={(event) => setNewUserDepartment(event.target.value)}
                      >
                        {departments.map((department) => (
                          <option key={department} value={department}>
                            {department}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="form-field">
                      <label htmlFor="newUserRole">Functional role</label>
                      <select
                        id="newUserRole"
                        value={newUserRole}
                        onChange={(event) => setNewUserRole(event.target.value as Role)}
                      >
                        <option value="newhire">New Hire</option>
                        <option value="expert">Domain Expert</option>
                        <option value="manager">Manager</option>
                        <option value="admin">Admin</option>
                      </select>
                    </div>
                  </div>

                  <button className="btn btn-primary" onClick={addUser}>
                    Create user
                  </button>
                  {usersMessage && (
                    <p className="small muted" style={{ marginTop: 12 }} aria-live="polite">
                      {usersMessage}
                    </p>
                  )}
                </div>

                <div className="card">
                  <div className="section-heading">
                    <h2>Everyone</h2>
                    <span className="count">{users.length} people</span>
                  </div>
                  <div className="table-scroll">
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
                            <td className="mono">{user.email}</td>
                            <td>
                              <span className="badge badge-teal">{user.department}</span>
                            </td>
                            <td>{ROLE_LABEL[user.role]}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </section>
            )}

            {/* ---------- profile ---------- */}
            {currentView === "profile" && <section className="view">{renderProfile()}</section>}
          </main>
        </div>
      )}
    </>
  );
}
