import { CSSProperties, ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, Config, isManager, Me, Project, ROLE_LABEL } from "./api";
import Notifications from "./Notifications";
import { SessionContext } from "./session";
import { hideBoot } from "./boot";
import { ErrorBoundary, Icon, IconName, Logo, Msg, Note, Toaster, toast } from "./ui";
import { Kbd, modKey } from "./fx";
import { ThemePref, useTheme } from "./theme";
import CommandPalette, { Command, openPalette } from "./CommandPalette";
import Chat from "./pages/Chat";
import Code from "./pages/Code";
import Tasks from "./pages/Tasks";
import Meetings from "./pages/Meetings";
import Approvals from "./pages/Approvals";
import Dashboard from "./pages/Dashboard";
import Settings from "./pages/Settings";
import { AccountPage } from "./pages/settings/Account";
import Inbox from "./pages/Inbox";
import Review from "./pages/Review";
import GitHubCallback from "./pages/GitHubCallback";
import NewProject, { NoProjects } from "./pages/NewProject";
import KnowledgeRisk from "./pages/KnowledgeRisk";
import KtPlanner from "./pages/KtPlanner";
import CodeTour from "./pages/CodeTour";
import DocumentView from "./pages/Document";
import Home from "./lifecycle/Home";
import Program from "./lifecycle/Program";
import Team from "./lifecycle/Team";
import Enrol from "./lifecycle/Enrol";
import Setup from "./lifecycle/Setup";
import Templates from "./lifecycle/Templates";
import Reports, { exportReport } from "./lifecycle/Reports";
import Handovers from "./lifecycle/Handovers";
import PeoplePage from "./lifecycle/People";
import Audit from "./lifecycle/Audit";
import Visibility from "./lifecycle/Visibility";
import FeedbackQueue from "./lifecycle/Feedback";
import Agents from "./lifecycle/Agents";
import { KIND_LABEL, agentName } from "./lifecycle/agentModel";
import DemoClock, { useDemoClock } from "./lifecycle/DemoClock";
import { NavIcon, NavIconName } from "./lifecycle/icons";
import { CrumbContext, LoadError, LcSkeleton, useApi } from "./lifecycle/kit";
import type { HireHome } from "./lifecycle/types";
import { replaceRoute, ROUTE_EVENT } from "./route";
import { startLive, stopLive, useLive, useLiveStatus } from "./live";

function useHash() {
  const [hash, setHash] = useState(location.hash || "#/home");
  useEffect(() => {
    const on = () => setHash(location.hash || "#/home");
    addEventListener("hashchange", on);
    addEventListener(ROUTE_EVENT, on); // a page's replaceRoute: the URL changed without a hashchange
    return () => { removeEventListener("hashchange", on); removeEventListener(ROUTE_EVENT, on); };
  }, []);
  const [path, query = ""] = hash.slice(1).split("?");
  // setting both in one event keeps a project switch and its new route in the same render
  const go = (to: string) => { location.hash = to; setHash(to); };
  const replace = (to: string) => { replaceRoute(to); setHash(to); }; // no history entry, and our state follows the URL
  return { path, params: new URLSearchParams(query), go, replace };
}

function useMedia(query: string) {
  const [match, setMatch] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const m = matchMedia(query), on = () => setMatch(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return match;
}

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

/** Linear-style two-key navigation: "g" then a letter. Ignored while typing or with a modifier held. */
function useGoKeys(keys: Record<string, string>, go: (to: string) => void) {
  const [armed, setArmed] = useState(false);
  const latest = useRef({ keys, go });
  latest.current = { keys, go };
  useEffect(() => {
    let pending = false, timer = 0;
    const disarm = () => { pending = false; setArmed(false); clearTimeout(timer); };
    const on = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || document.querySelector("[aria-modal='true']")) return;
      const k = e.key.toLowerCase();
      if (pending) {
        const to = latest.current.keys[k];
        disarm();
        if (to) { e.preventDefault(); latest.current.go(to); }
        return;
      }
      if (k === "g" && !e.shiftKey) {
        pending = true; setArmed(true);
        timer = window.setTimeout(disarm, 1400);
      }
    };
    addEventListener("keydown", on);
    return () => { removeEventListener("keydown", on); clearTimeout(timer); };
  }, []);
  return armed;
}

const THEMES: { value: ThemePref; label: string; icon: IconName }[] = [
  { value: "light", label: "Light", icon: "sun" },
  { value: "system", label: "System", icon: "monitor" },
  { value: "dark", label: "Dark", icon: "moon" },
];

/** Light / system / dark, as one segmented control with a sliding thumb. */
function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  const at = Math.max(0, THEMES.findIndex((t) => t.value === theme));
  return (
    <div className="theme-switch" role="group" aria-label="Theme" style={{ "--at": at } as CSSProperties}>
      <span className="theme-thumb" aria-hidden="true" />
      {THEMES.map((t) => (
        <button key={t.value} type="button" aria-pressed={theme === t.value} aria-label={`${t.label} theme`} title={`${t.label} theme`}
                onClick={() => setTheme(t.value)}>
          <Icon name={t.icon} size={15} />
        </button>
      ))}
    </div>
  );
}

type Item = { href: string; label: string; icon: NavIconName; key: string };
type Group = { label: string; items: Item[] };

/** Every page's name, for the rail, the breadcrumb and the palette. */
const PAGE: Record<string, string> = {
  "/home": "Home", "/team": "Team", "/enrol": "Enrol a new hire", "/program": "Program", "/setup": "Setup", "/templates": "Templates",
  "/reports": "Reports", "/handovers": "Handovers", "/people": "People", "/audit": "Audit log", "/me": "What your manager sees",
  "/feedback": "Feedback", "/chat": "Ask the project", "/code-tour": "Code tour", "/code": "Code and KT", "/meetings": "KT sessions",
  "/tasks": "Tasks", "/inbox": "Expert inbox", "/experts": "Expert inbox", "/approvals": "Approvals", "/dashboard": "KT coverage",
  "/knowledge-risk": "Knowledge risk", "/kt-planner": "KT planner", "/settings": "Settings", "/reviews": "Pull request review",
  "/projects": "New project", "/documents": "Document", "/agents": "Agents",
};
const KNOWN = new Set(Object.keys(PAGE));
const SETTINGS_TAB: Record<string, string> = {
  people: "People", repository: "Repository", documents: "Documents", onboarding: "Onboarding", integrations: "Integrations", organisation: "Organisation", account: "Account",
};

/** #/program with no id: the signed-in hire's own program. */
function OwnProgram({ params }: { params: URLSearchParams }) {
  const home = useApi<HireHome>("/home");
  const id = home.data?.role === "new_hire" ? home.data.sections.program?.profile.onboarding_id : null;
  if (home.error) return <section className="lc"><LoadError error={home.error} retry={home.reload} /></section>;
  if (!home.data) return <section className="lc"><LcSkeleton rows={5} /></section>;
  if (!id) return <Home />;
  return <Program id={id} params={params} />;
}

const projectKey = (userId: string) => `ww-project:${userId}`;

function initialProject(me: Me, demo: boolean) {
  let saved: string | null = null;
  try { saved = localStorage.getItem(projectKey(me.id)); } catch { /* storage blocked */ }
  const preferred = [saved, demo ? (import.meta.env.VITE_PROJECT_ID ?? "proj-demo") : null];
  return preferred.find((id) => id && me.projects.some((p) => p.id === id)) ?? me.projects[0]?.id ?? null;
}

type Props = {
  config: Config; me: Me; setMe: (me: Me) => void; signOut?: () => void;
  reloadAccount?: () => void; // production: resolve the login again after it left the organisation
};

export default function Workspace({ config, me, setMe, signOut, reloadAccount }: Props) {
  const { path, params, go } = useHash();
  const demo = config.mode === "demo";
  const [projectId, setProjectId] = useState(() => initialProject(me, demo));
  // GitHub sends the browser to the real path /github/callback after the App is installed
  const [callback, setCallback] = useState(() => (location.pathname === "/github/callback" && isManager(me.role) ? location.search : null));
  const [notice, setNotice] = useState<Msg>(null);
  const project = me.projects.find((p) => p.id === projectId) ?? me.projects[0] ?? null;
  const manager = isManager(me.role), admin = me.role === "admin", exec = me.role === "executive", hire = me.role === "new_hire";
  const expert = me.role === "expert" || manager; // experts and managers: the expert inbox, KT queue, documents
  const [crumbDetail, setCrumbDetail] = useState<string | null>(null);
  const setCrumb = useCallback((label: string | null) => setCrumbDetail(label), []);
  const [tick, setTick] = useState(0); // the demo clock moved: every page reloads its data
  const clock = useDemoClock(projectId, manager, demo, () => setTick((n) => n + 1));
  const section = "/" + path.split("/")[1];
  const id = path.split("/")[2] || null;
  // the rail item a page belongs to: the callback view to Settings, a program and enrolment to Team (or a hire's Home)
  const owner: Record<string, string> = {
    "/program": manager ? "/team" : "/home", "/me": manager ? "/team" : "/home", "/enrol": "/team", "/dashboard": "/team", "/experts": "/inbox",
  };
  const current = callback !== null ? "/settings" : owner[section] ?? section;

  const narrow = useMedia("(max-width: 899px)");
  const [drawer, setDrawer] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const drawerClose = useRef<HTMLButtonElement>(null);
  const stage = useRef<HTMLDivElement>(null), topbar = useRef<HTMLElement>(null);
  const refocus = useRef(false);
  const closeDrawer = () => { refocus.current = true; setDrawer(false); };
  const mainRef = useRef<HTMLElement>(null);
  const [announce, setAnnounce] = useState("");

  useEffect(hideBoot, []);
  useEffect(() => { startLive(); return stopLive; }, []);
  const liveStatus = useLiveStatus();
  // added to (or removed from) a project, a project renamed: the project list follows without a reload
  useLive(["projects", "people"], () => { api<Me>("/me").then((m) => { if (m.id === me.id) setMe(m); }).catch(() => { /* keep the list we have */ }); });
  useEffect(() => setNotice(null), [path, projectId]);
  // links from the API name their project (?project=): follow it when it is one of ours
  const linkedProject = params.get("project");
  useEffect(() => {
    if (linkedProject && linkedProject !== projectId && me.projects.some((p) => p.id === linkedProject)) choose(linkedProject);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkedProject]);
  useEffect(() => setDrawer(false), [path, projectId, narrow]);
  useEffect(() => { // the drawer is modal: Escape closes it, the page behind does not scroll, focus moves in and back out
    if (!drawer) return;
    drawerClose.current?.focus();
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") closeDrawer(); };
    document.addEventListener("keydown", escape);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const behind = [stage.current, topbar.current];
    behind.forEach((el) => el && (el.inert = true)); // keyboard and screen-reader focus stays in the drawer
    return () => {
      document.removeEventListener("keydown", escape); document.body.style.overflow = overflow;
      behind.forEach((el) => el && (el.inert = false));
      if (refocus.current) { refocus.current = false; menuButton.current?.focus(); } // only after the page behind is focusable again
    };
  }, [drawer]);
  useEffect(() => { document.title = project ? `${project.name} · Welcome Wave` : "Welcome Wave"; }, [project]);
  useEffect(() => () => { document.title = "Welcome Wave"; }, []); // signed out: the project's name leaves the tab with it
  useEffect(() => { // navigating anywhere leaves the GitHub callback view (its one-time code is never posted again)
    if (callback === null) return;
    const leave = () => setCallback(null);
    addEventListener("hashchange", leave);
    return () => removeEventListener("hashchange", leave);
  }, [callback]);

  function choose(pid: string) {
    setProjectId(pid);
    try { localStorage.setItem(projectKey(me.id), pid); } catch { /* storage blocked */ }
  }
  function switchProject(pid: string) {
    choose(pid);
    if (id || params.toString()) go(`#${section}`); // a meeting, review or file belongs to the old project
  }
  /** Notification links name their project: switch to it first. A project we do not know yet may have been added since sign-in. */
  async function openLink(hash: string | null, pid: string | null) {
    if (pid && pid !== project?.id) {
      let mine = me;
      if (!mine.projects.some((p) => p.id === pid)) {
        try { mine = await api<Me>("/me"); setMe(mine); } catch { /* keep the list we have */ }
      }
      if (!mine.projects.some((p) => p.id === pid)) {
        return setNotice({ ok: false, text: "That notification is about a project you are no longer a member of." });
      }
      choose(pid);
    }
    if (hash) go(hash);
  }
  function created(p: Project) {
    setMe({ ...me, projects: [...me.projects, p].sort((a, b) => a.name.localeCompare(b.name)) });
    choose(p.id);
    location.hash = "#/settings";
  }
  function leaveCallback(pid: string | null) {
    if (pid && me.projects.some((p) => p.id === pid)) choose(pid);
    setCallback(null);
    history.replaceState(null, "", "/app#/settings?tab=repository"); // land on the repository that was just connected (off /github/callback)
    dispatchEvent(new HashChangeEvent("hashchange"));
  }

  const deny = <section className="page-head"><h1>Not available</h1><p>Your role does not include this page.</p></section>;
  let page: ReactNode;
  if (callback !== null) page = manager ? <GitHubCallback search={callback} onDone={leaveCallback} /> : deny;
  else if (section === "/projects" && manager) page = <NewProject onCreated={created} />;
  else if (!project) page = <NoProjects me={me} onCreated={created} />;
  // executives are read-only and organisation-level: their home, the report, and their account
  else if (exec) page = section === "/reports" ? <Reports /> : section === "/agents" ? <Agents params={params} /> : section === "/settings" ? <AccountPage />
    : section === "/home" || !KNOWN.has(section) ? <Home /> : deny;
  else if (section === "/home") page = <Home />;
  else if (section === "/program") page = id ? <Program key={id} id={id} params={params} /> : hire ? <OwnProgram params={params} /> : manager ? <Team /> : <Home />;
  else if (section === "/team") page = manager ? <Team /> : deny;
  else if (section === "/enrol") page = manager ? <Enrol /> : deny;
  else if (section === "/setup") page = admin ? <Setup /> : deny;
  else if (section === "/templates") page = manager ? <Templates params={params} /> : deny;
  else if (section === "/reports") page = manager ? <Reports /> : deny;
  else if (section === "/handovers") page = manager ? <Handovers /> : deny;
  else if (section === "/people") page = admin ? <PeoplePage /> : deny;
  else if (section === "/audit") page = admin ? <Audit /> : deny;
  else if (section === "/me") page = hire || (manager && id) ? <Visibility key={id ?? "me"} id={id} /> : deny;
  else if (section === "/feedback") page = expert ? <FeedbackQueue /> : deny;
  else if (section === "/code") page = <Code path={params.get("path")} line={Number(params.get("line")) || null} />;
  else if (section === "/meetings") page = <Meetings id={id} at={Number(params.get("t")) || 0} focus={params.get("call")} />;
  else if (section === "/documents" && id) page = <DocumentView id={id} page={Number(params.get("page")) || null} />;
  else if (section === "/tasks") page = <Tasks />;
  else if (section === "/reviews" && id) page = <Review id={id} />;
  else if (section === "/inbox" || section === "/experts") page = expert ? <Inbox focus={params.get("message")} /> : deny;
  else if (section === "/approvals") page = manager ? <Approvals focus={id} kind={params.get("kind")} agent={params.get("agent")} /> : deny;
  else if (section === "/agents") page = manager ? <Agents params={params} /> : deny;
  else if (section === "/dashboard") page = manager ? <Dashboard /> : deny;
  else if (section === "/code-tour") page = <CodeTour params={params} />;
  else if (section === "/knowledge-risk") page = manager ? <KnowledgeRisk params={params} /> : deny;
  else if (section === "/kt-planner") page = expert ? <KtPlanner params={params} /> : deny;
  // experts add the documents the chat answers from, so they get Settings too (documents and account only)
  else if (section === "/settings") page = expert ? <Settings /> : reloadAccount ? <AccountPage /> : deny;
  else if (section === "/chat") page = <Chat q={params.get("q")} focus={params.get("message")} />;
  else page = <Home />;

  /* The rail, per role, in a few short groups (docs/PLAN.md, Track D). Keys: "g" then the letter. Pages a role may open
     but that are not in its rail (a manager's code tour, a hire's visibility page) are in the palette. */
  const A = (href: string, key: string, icon: NavIconName, label = PAGE[href.slice(1)] ?? href): Item => ({ href, label, icon, key });
  const groups: Group[] = !project ? [] : hire ? [
    { label: "Workspace", items: [A("#/home", "h", "home"), A("#/chat", "c", "chat", "Ask"), A("#/code-tour", "o", "route"), A("#/code", "k", "code"),
      A("#/meetings", "m", "video"), A("#/tasks", "t", "tasks", "My tasks")] },
    ...(reloadAccount ? [{ label: "Account", items: [A("#/settings", "s", "settings")] }] : []),
  ] : exec ? [
    { label: "Workspace", items: [A("#/home", "h", "home"), A("#/reports", "r", "chart"), A("#/agents", "n", "agents")] },
    { label: "Account", items: [A("#/settings", "s", "settings")] },
  ] : !manager ? [
    { label: "Workspace", items: [A("#/home", "h", "home"), A("#/chat", "c", "chat", "Ask"), A("#/inbox", "i", "inbox"), A("#/kt-planner", "p", "calendar", "My KT queue"),
      A("#/feedback", "f", "feedback")] },
    { label: "Project", items: [A("#/settings", "s", "settings")] },
  ] : [
    { label: "Workspace", items: [A("#/home", "h", "home"), A("#/team", "t", "team"), A("#/approvals", "a", "approve")] },
    ...(admin ? [{ label: "People", items: [A("#/people", "e", "people"), A("#/handovers", "v", "handover"), A("#/templates", "l", "layers")] }] : []),
    { label: "Insight", items: [A("#/kt-planner", "p", "calendar", "KT planner"), A("#/knowledge-risk", "k", "radar"), A("#/reports", "r", "chart"),
      A("#/agents", "n", "agents")] },
    { label: admin ? "Admin" : "Project", items: [...(admin ? [A("#/setup", "u", "setup"), A("#/audit", "d", "list")] : []), A("#/settings", "s", "settings")] },
  ];
  const visible = groups.flatMap((g) => g.items);
  const goKeys = Object.fromEntries(visible.map((n) => [n.key, n.href]));
  const armed = useGoKeys(goKeys, go);

  // After a route change by keyboard (g-keys, the palette, a drawer link) focus would be left on <body> or inside the closed drawer:
  // move it to the new page's title, and tell screen readers which page this is. A focused field (autofocus) keeps its focus.
  const firstRoute = useRef(true);
  useEffect(() => {
    if (firstRoute.current) { firstRoute.current = false; return; }
    let raf2 = 0;
    const raf = requestAnimationFrame(() => { raf2 = requestAnimationFrame(() => {
      const a = document.activeElement as HTMLElement | null;
      const lost = !a || a === document.body || !a.isConnected || (narrow && !!a.closest("#rail"));
      const h = mainRef.current?.querySelector<HTMLElement>("h1");
      if (lost) {
        if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); } else mainRef.current?.focus({ preventScroll: true });
      }
      const label = h?.textContent || PAGE[section] || "";
      setAnnounce(label ? `${label} page` : "");
    }); });
    return () => { cancelAnimationFrame(raf); cancelAnimationFrame(raf2); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  // one indicator slides between nav items instead of each item lighting up on its own
  const nav = useRef<HTMLDivElement>(null);
  const [mark, setMark] = useState<{ y: number; h: number } | null>(null);
  const [markReady, setMarkReady] = useState(false);
  useLayoutEffect(() => {
    const box = nav.current;
    if (!box) return;
    const measure = () => {
      const a = box.querySelector<HTMLElement>('a[aria-current="page"]');
      setMark(a ? { y: a.offsetTop, h: a.offsetHeight } : null);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(box);
    return () => ro.disconnect();
  }, [current, section, project?.id, visible.length]);
  useEffect(() => { // place it without animating the first time, then let it glide
    if (!mark || markReady) return;
    const raf = requestAnimationFrame(() => setMarkReady(true));
    return () => cancelAnimationFrame(raf);
  }, [mark, markReady]);

  /** In the drawer, a tap on any link closes it. A link to the page already open changes no route, so the route effect
   *  would leave the drawer open (and focus inside it): close it here and give focus back to the menu button. */
  const tapped = (href: string) => {
    if (!narrow) return;
    if (current === href.slice(1)) closeDrawer();
    else setDrawer(false);
  };
  const links = (items: Item[]) => (
    <ul>
      {items.map((n) => (
        <li key={n.href}>
          <a href={n.href} aria-current={current === n.href.slice(1) ? "page" : undefined} aria-keyshortcuts={`g ${n.key}`}
             onClick={() => tapped(n.href)}>
            <NavIcon name={n.icon} /><span className="rail-text">{n.label}</span>
            <span className="rail-key" aria-hidden="true"><kbd>G</kbd><kbd>{n.key.toUpperCase()}</kbd></span>
          </a>
        </li>
      ))}
    </ul>
  );
  const initials = me.name.replace(/\(.*?\)/g, "").trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  const [, setThemePref] = useTheme();
  const commands = useMemo<Command[]>(() => {
    const list: Command[] = visible.filter((n) => n.href !== "#/agents").map((n) => ({
      id: `go-${n.key}`, label: n.label, group: "Go to", glyph: <NavIcon name={n.icon} size={16} />, hint: `G ${n.key.toUpperCase()}`,
      keywords: n.href.slice(2), run: () => go(n.href),
    }));
    // pages this role may open that are not in its rail
    const more: [string, string, NavIconName, boolean][] = [
      ["#/chat", "Ask the project", "chat", manager],
      ["#/code-tour", "Code tour", "route", !exec && !hire],
      ["#/code", "Code and KT", "code", !exec && !hire],
      ["#/meetings", "KT sessions", "video", !exec && !hire],
      ["#/tasks", "Team tasks", "tasks", expert],
      ["#/inbox", "Expert inbox", "inbox", manager],
      ["#/feedback", "Feedback", "feedback", manager],
      ["#/handovers", "Handovers", "handover", manager && !admin],
      ["#/templates", "Templates", "layers", manager && !admin],
      ["#/dashboard", "KT coverage", "pulse", manager],
      ["#/program", "My program", "program", hire],
      ["#/me", "What your manager sees", "eye", hire],
    ];
    for (const [href, label, icon, ok] of more) {
      if (ok && !visible.some((n) => n.href === href)) list.push({ id: `go${href.slice(1).replace(/\//g, "-")}`, label, group: "Go to", glyph: <NavIcon name={icon} size={16} />, keywords: href.slice(2), run: () => go(href) });
    }
    if (manager) {
      list.push({ id: "enrol", label: "Enrol a new hire", group: "Actions", glyph: <NavIcon name="enrol" size={16} />, keywords: "new hire onboard start invite program", run: () => go("#/enrol") });
      for (const h of clock.hires.filter((x) => x.status === "active")) {
        list.push({ id: `pre-${h.id}`, label: `Create pre-boarding link for ${h.name}`, group: "Actions", glyph: <NavIcon name="link" size={16} />,
          keywords: "preboarding pre-boarding welcome link portal", run: () => go(`#/program/${encodeURIComponent(h.id)}?tab=preboarding`) });
      }
    }
    if (visible.some((n) => n.href === "#/agents")) {
      list.push({ id: "agents-open", label: "Open Agents", group: "Agents", glyph: <NavIcon name="agents" size={16} />, hint: "G N",
        keywords: "agents supervisor librarian note-taker guide planner scout reviewer coach keeper ai rules", run: () => go("#/agents") });
      if (manager && config.mode !== "gcp") list.push({ id: "agents-tick", label: "Run daily check now", group: "Agents", glyph: <NavIcon name="clock" size={16} />,
        keywords: "agents tick daily check blockers nudges coach", run: () => go("#/agents?tick=1") });
    }
    if (manager || exec) list.push({ id: "export-report", label: "Export report (CSV)", group: "Actions", glyph: <NavIcon name="download" size={16} />,
      keywords: "report csv download onboarding figures", run: () => { void exportReport(me.role, project?.id ?? ""); } });
    if (clock.ready && clock.day !== null) {
      const d = clock.day, who = clock.focus?.name ?? "the hire";
      const step = (days: number, label: string) => list.push({ id: `clock-${label}`, label, group: "Demo clock", glyph: <NavIcon name="clock" size={16} />,
        keywords: "demo clock advance time day week forward", run: () => { void clock.advance(days); } });
      step(1, "Advance the demo clock one day");
      step(7, "Advance the demo clock one week");
      for (const m of [30, 60, 90].map((x) => Math.round((x * clock.duration) / 90))) {
        if (m > d) step(m - d, `Jump to day ${m} for ${who}`);
      }
      if (clock.offset) list.push({ id: "clock-reset", label: "Reset the demo clock to day 0", group: "Demo clock", glyph: <NavIcon name="clock" size={16} />,
        keywords: "demo clock reset start back undo", run: () => { void clock.reset(); } });
    }
    for (const p of me.projects) {
      if (p.id === project?.id) continue;
      list.push({ id: `project-${p.id}`, label: `Switch to ${p.name}`, group: "Projects", icon: "folder", keywords: `project ${p.description ?? ""}`, run: () => switchProject(p.id) });
    }
    if (manager) {
      const tabs: [string, string, string][] = [
        ["people", "People and invitations", "members invite team roles"],
        ["repository", "Repository", "github connect index reindex code"],
        ["documents", "Documents", "upload pdf docs files"],
        ["onboarding", "Onboarding", "new hire plan start"],
        ["account", "Account and appearance", "profile theme leave organisation"],
        ...(admin ? [["organisation", "Organisation: export or delete", "export download data delete organization org"] as [string, string, string]] : []),
      ];
      for (const [tab, label, kw] of tabs) {
        // the group header already says Settings; the word stays in the keywords so "settings people" still matches
        list.push({ id: `settings-${tab}`, label, group: "Settings", icon: "settings", keywords: `settings ${kw}`, run: () => go(`#/settings?tab=${tab}`) });
      }
    }
    if (manager) list.push({ id: "project-new", label: "New project", group: "Projects", icon: "plus", keywords: "create add repository", run: () => go("#/projects/new") });
    for (const t of THEMES) {
      list.push({ id: `theme-${t.value}`, label: t.value === "system" ? "Match system theme" : `${t.label} theme`, group: "Appearance", icon: t.icon,
        keywords: "theme appearance mode colour color dark light", run: () => { setThemePref(t.value); toast(t.value === "system" ? "Following the system theme" : `${t.label} theme on`); } });
    }
    list.push({ id: "copy-link", label: "Copy link to this page", group: "General", icon: "link", keywords: "share url clipboard", run: () => {
      if (!navigator.clipboard) return toast("Copying needs a secure (https) page", "error");
      navigator.clipboard.writeText(location.href).then(() => toast("Link copied"), () => toast("Could not copy the link", "error"));
    } });
    if (signOut) list.push({ id: "sign-out", label: "Sign out", group: "Account", icon: "logout", keywords: "log out logout exit", run: signOut });
    if (signOut && demo) list.push({ id: "switch-account", label: "Switch account", group: "Account", icon: "logout", keywords: "change user role sign in as demo", run: signOut });
    return list;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible.map((n) => n.href + n.label).join(), me.projects, project?.id, manager, signOut, path, params.toString(),
      clock.hires.map((h) => h.id).join(), clock.ready, clock.day, clock.busy, clock.offset]);

  const bell = <Notifications onOpen={openLink} />;
  // the breadcrumb in the desktop top bar: project, the section (a link when a page or item sits under it), the page, the item
  const crumbs: { label: string; href?: string }[] = [];
  if (callback !== null) crumbs.push({ label: "Settings" });
  else if (section === "/program" || (section === "/me" && id)) {
    if (manager) crumbs.push({ label: "Team", href: "#/team" });
    else crumbs.push({ label: "Home", href: "#/home" });
    if (section === "/me") crumbs.push({ label: crumbDetail ?? "Hire", href: `#/program/${encodeURIComponent(id ?? "")}` }, { label: "What they see" });
    else crumbs.push({ label: crumbDetail ?? (hire ? "My program" : "Program") });
  } else if (section === "/enrol") crumbs.push({ label: "Team", href: "#/team" }, { label: "Enrol" });
  else if (section === "/dashboard" && manager) crumbs.push({ label: "Team", href: "#/team" }, { label: "KT coverage" });
  else if (section === "/settings" && params.get("tab") && SETTINGS_TAB[params.get("tab")!]) crumbs.push({ label: "Settings", href: "#/settings" }, { label: SETTINGS_TAB[params.get("tab")!] });
  else if (section === "/approvals" && !id && (params.get("kind") || params.get("agent"))) {
    const k = params.get("kind"), ag = params.get("agent");
    crumbs.push({ label: "Approvals", href: "#/approvals" }, { label: k ? KIND_LABEL[k] ?? k : agentName(ag) });
  } else if ((section === "/meetings" || section === "/approvals" || section === "/documents") && id) crumbs.push({ label: PAGE[section] ?? "", href: `#${section}` }, { label: crumbDetail ?? (section === "/meetings" ? "Session" : section === "/approvals" ? "Decision" : "Document") });
  else if (PAGE[section]) crumbs.push({ label: section === "/tasks" ? (hire ? "My tasks" : "Team tasks") : section === "/kt-planner" && !manager ? "My KT queue" : section === "/chat" ? "Ask" : PAGE[section] });
  const searchKeys = `${modKey === "⌘" ? "Meta" : "Control"}+K`;

  return (
    <>
      <div className={`shell${drawer ? " drawer-open" : ""}`}>
        <a className="skip-link" href="#main" onClick={(e) => { e.preventDefault(); mainRef.current?.focus({ preventScroll: false }); }}>Skip to content</a>
        <header className="topbar" ref={topbar}>
          <button ref={menuButton} className="icon topbar-btn" aria-label="Open menu" aria-expanded={drawer} aria-controls="rail"
                  onClick={() => setDrawer(true)}><Icon name="menu" size={20} /></button>
          <a className="brand" href="#/home"><Logo size={24} />Welcome Wave</a>
          <span className="topbar-end">
            {narrow && demo && manager && <DemoClock clock={clock} />}
            {project && <button className="icon topbar-btn" aria-label="Search or ask" onClick={() => openPalette()}><Icon name="search" size={19} /></button>}
            {narrow && bell}
          </span>
        </header>
        <div className="scrim" aria-hidden="true" onClick={() => setDrawer(false)} />
        <nav id="rail" className={`rail${armed ? " keys-armed" : ""}`} aria-label="Main">
          <div className="rail-top">
            <a className="brand" href="#/home" onClick={() => tapped("#/home")}><Logo size={22} />Welcome Wave</a>
            {narrow && <button ref={drawerClose} className="icon rail-icon" aria-label="Close menu" onClick={closeDrawer}><Icon name="close" size={18} /></button>}
          </div>
          {/* the current project; with more than one, a native select laid over the whole block switches it */}
          <div className={`rail-project${project && me.projects.length > 1 ? " can-switch" : ""}`}>
            <span className="rail-project-tile" aria-hidden="true">{(project?.name ?? me.org_name).trim().charAt(0).toUpperCase()}</span>
            <span className="rail-project-text">
              <span className="rail-project-name">{project?.name ?? me.org_name}</span>
              <span className="rail-project-org">{!project ? "No project yet" : me.org_name !== project.name ? me.org_name : `${me.projects.length} project${me.projects.length === 1 ? "" : "s"}`}</span>
            </span>
            {project && me.projects.length > 1 && (
              <>
                <Icon name="updown" size={14} />
                <label className="sr-only" htmlFor="project-pick">Project</label>
                <select id="project-pick" value={project.id} onChange={(e) => switchProject(e.target.value)}>
                  {me.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </>
            )}
          </div>
          {project && narrow && (
            <button type="button" className="rail-search" onClick={() => {
              // in the drawer: close it first so focus goes back to the menu button, and the palette returns focus there on Escape
              refocus.current = true; setDrawer(false); requestAnimationFrame(() => requestAnimationFrame(() => openPalette()));
            }} aria-keyshortcuts={searchKeys}>
              <Icon name="search" size={15} /><span>Search or ask…</span>
            </button>
          )}
          <div className="rail-nav" ref={nav}>
            <span className={`rail-mark${mark ? " on" : ""}${markReady ? " ready" : ""}`} aria-hidden="true"
                  style={mark ? { transform: `translateY(${mark.y}px)`, height: mark.h } : undefined} />
            {groups.map((g) => <div key={g.label} className="rail-group"><p className="rail-label">{g.label}</p>{links(g.items)}</div>)}
            {manager && (
              <a className="rail-link" href="#/projects/new" aria-current={section === "/projects" ? "page" : undefined}
                 onClick={() => tapped("#/projects")}><Icon name="plus" size={16} />New project</a>
            )}
          </div>
          <div className="rail-foot">
            <ThemeSwitch />
            <div className="me">
              <span className="avatar" aria-hidden="true">{initials || "?"}</span>
              {/* the demo's accounts are its roles, so it names the role; production names the login */}
              <span className="me-text"><span className="who-name">{me.name.replace(/\s*\(.*?\)/g, "")}</span>
                <span className="me-sub" title={me.email}>{demo ? ROLE_LABEL[me.role] ?? me.role : me.email}</span></span>
              {signOut && <button className="icon rail-icon" onClick={signOut} aria-label="Sign out" title="Sign out"><Icon name="logout" size={17} /></button>}
            </div>
            {demo && signOut && (
              <button type="button" className="rail-switch" onClick={signOut}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M16 3l4 4-4 4" /><path d="M20 7H4" /><path d="M8 21l-4-4 4-4" /><path d="M4 17h16" />
                </svg>
                Switch account
              </button>
            )}
          </div>
        </nav>
        <div className="stage" ref={stage}>
          {!narrow && (
            <header className="stage-bar">
              <nav className="crumbs" aria-label="Breadcrumb">
                <ol>
                  <li>{project?.name ?? me.org_name}</li>
                  {crumbs.map((c, i) => i < crumbs.length - 1 && c.href
                    ? <li key={i}><a href={c.href}>{c.label}</a></li>
                    : <li key={i} aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c.label}</li>)}
                </ol>
              </nav>
              <div className="stage-bar-end">
                {demo && <span className="demo-tag" title="In-memory data and an offline model; nothing here is saved">Demo</span>}
                {demo && manager && <DemoClock clock={clock} />}
                {liveStatus === "offline" && <span className="live-off" role="status" title="Live updates paused: reconnecting to the server"><span className="live-dot" aria-hidden="true" />Reconnecting…</span>}
                {project && (
                  <button type="button" className="bar-search" onClick={() => openPalette()} aria-keyshortcuts={searchKeys}>
                    <Icon name="search" size={14} /><span>Search or ask…</span>
                    <span className="bar-search-keys" aria-hidden="true"><Kbd>{modKey}</Kbd><Kbd>K</Kbd></span>
                  </button>
                )}
                {bell}
              </div>
            </header>
          )}
          <main key={project?.id ?? "none"} id="main" ref={mainRef} tabIndex={-1}>
            <SessionContext.Provider value={project ? { config, me, project, demo, reloadAccount: reloadAccount ?? null, signOut: signOut ?? null } : null}>
              <Note msg={notice} />
              {config.ai === false && manager && (
                <div className="ai-off" role="note">
                  <Icon name="alert" size={16} />
                  <p><strong>The AI is off on this server.</strong> Chat, onboarding plans, repository indexing and KT session notes need it.
                    Add a Gemini API key (<code>GEMINI_API_KEY</code> in <code>backend/selfhost.env</code>) and restart Welcome Wave.</p>
                </div>
              )}
              <CrumbContext.Provider value={setCrumb}>
                <div className="page-enter" key={`${path}:${tick}`}>
                  <ErrorBoundary key={path}>{page}</ErrorBoundary>
                </div>
              </CrumbContext.Provider>
            </SessionContext.Provider>
          </main>
        </div>
      </div>
      {project && <CommandPalette commands={commands} onAsk={exec ? undefined : (q) => go("#/chat?q=" + encodeURIComponent(q))} />}
      <p className="sr-only" role="status" aria-live="polite">{announce}</p>
      <Toaster />
    </>
  );
}
