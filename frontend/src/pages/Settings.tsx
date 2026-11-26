import { KeyboardEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, errorMessage, isManager, Project, User } from "../api";
import { useSession } from "../session";
import { Skeleton } from "../fx";
import "../styles/entry.css";
import People from "./settings/People";
import Repository from "./settings/Repository";
import Documents from "./settings/Documents";
import Onboarding from "./settings/Onboarding";
import Account from "./settings/Account";
import Integrations from "./settings/Integrations";
import Organisation from "./settings/Organisation";
import Preferences from "./settings/Preferences";
import { useLive } from "../live";

export type Repo = {
  id: string; full_name: string; installation_id: string | null; default_branch: string; status: "pending" | "indexing" | "ready" | "failed";
  last_indexed_sha?: string | null; last_indexed_at?: string | null; // shown when the API sends them
};
export type Invitation = { id: string; email: string; role: string; name: string | null; github_login: string | null; expires_at: string };
export type Doc = {
  id: string; title: string; status: "pending" | "ready" | "failed"; created_at: string;
  mime?: string | null; uri?: string | null; page_count?: number | null; // shown when the API sends them
};
export type OnboardingRow = { id: string; new_hire_id: string; start_date: string; role_title: string; status: string };
export type ProjectDetail = {
  project: Project; repositories: Repo[]; members: User[]; onboardings: OnboardingRow[]; documents: Doc[]; invitations?: Invitation[];
};
export type SectionProps = { d: ProjectDetail; reload: () => void };

export default function Settings() {
  const { project, me } = useSession();
  const manager = isManager(me.role);
  const [d, setD] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<ProjectDetail>(`/projects/${project.id}`).then((x) => { setD(x); setError(null); }).catch((e) => setError(errorMessage(e)));
  }, [project.id]);
  useEffect(load, [load]);
  useLive(["sources", "people", "tasks", "projects"], load, project.id); // indexing finishes, someone accepts an invitation

  // indexing runs in the background: check again while anything is still in progress
  const working = !!d && (d.repositories.some((r) => r.status === "pending" || r.status === "indexing") || d.documents.some((x) => x.status === "pending"));
  useEffect(() => {
    if (!working) return;
    const t = setTimeout(load, 10_000);
    return () => clearTimeout(t);
  }, [working, d, load]);

  const all: Tab[] = [
    { id: "people", label: "People", count: d ? d.members.length + (d.invitations?.length ?? 0) : undefined },
    { id: "repository", label: "Repository", dot: d ? (d.repositories[0]?.status ?? "none") : undefined },
    { id: "documents", label: "Documents", count: d?.documents.length },
    { id: "onboarding", label: "Onboarding", count: d?.onboardings.length },
    { id: "integrations", label: "Integrations" }, // Google Calendar, Calendly (docs/specs/scheduling.md)
    { id: "preferences", label: "Preferences" }, // when routed questions may interrupt (experts and managers)
    ...(me.role === "admin" ? [{ id: "organisation" as const, label: "Organisation" }] : []), // export and delete the organisation
    { id: "account", label: "Account" }, // profile and theme for everyone; leaving the organisation only with sign-in
  ];
  // experts add and remove the documents the chat answers from, keep their own Calendly link and set when questions may reach
  // them; people, the repository and onboarding are the managers'
  const expertTabs = ["documents", "integrations", "preferences", "account"];
  const tabs = manager ? all : all.filter((t) => expertTabs.includes(t.id) && (t.id !== "integrations" && t.id !== "preferences" || me.role === "expert"));
  const [tab, setTab] = useTab(tabs.map((t) => t.id));
  // a project with no repository yet opens on Repository, unless the link names a section
  const routed = useRef(!!tabFromHash());
  useEffect(() => {
    if (!d || routed.current) return;
    routed.current = true;
    if (manager && d.repositories.length === 0) setTab("repository", false);
  }, [d, setTab, manager]);

  const body = !d ? null
    : tab === "people" ? <People d={d} reload={load} />
    : tab === "repository" ? <Repository d={d} reload={load} />
    : tab === "documents" ? <Documents d={d} reload={load} />
    : tab === "onboarding" ? <Onboarding d={d} reload={load} />
    : tab === "integrations" ? <Integrations />
    : tab === "preferences" ? <Preferences />
    : tab === "organisation" ? <Organisation />
    : <Account />;

  return (
    <section className="set-page">
      <header className="page-head set-head">
        <h1>Settings</h1>
      </header>
      <Tabs tabs={tabs} active={tab} onChange={(t) => { routed.current = true; setTab(t, true); }} />
      {error && <p className="error" role="alert">{error}</p>}
      <div className="set-panel" role="tabpanel" id={`set-panel-${tab}`} aria-labelledby={`set-tab-${tab}`} key={tab} tabIndex={-1}>
        {!d ? (!error && <div className="set-card"><Skeleton lines={4} height={16} /></div>) : body}
      </div>
    </section>
  );
}

type TabId = "people" | "repository" | "documents" | "onboarding" | "integrations" | "preferences" | "organisation" | "account";
type Tab = { id: TabId; label: string; count?: number; dot?: string };

function tabFromHash(): TabId | null {
  const q = location.hash.split("?")[1];
  return (q ? new URLSearchParams(q).get("tab") : null) as TabId | null;
}

/** The open section lives in the hash (#/settings?tab=documents) so it survives a reload and can be linked to. */
function useTab(ids: TabId[]): [TabId, (t: TabId, remember: boolean) => void] {
  const [tab, set] = useState<TabId>(() => tabFromHash() ?? "people");
  const valid = ids.includes(tab) ? tab : ids[0];
  const change = useCallback((t: TabId, remember: boolean) => {
    set(t);
    // replaceState: no hashchange, so the workspace does not re-route; the menu's own #/settings link still works
    if (remember && location.hash.startsWith("#/settings")) history.replaceState(null, "", `#/settings?tab=${t}`);
  }, []);
  useEffect(() => { // a link such as #/settings?tab=people while Settings is already open
    const on = () => { const t = tabFromHash(); if (t) set(t); };
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return [valid, change];
}

/** A segmented control with an indicator that slides to the selected section (ARIA tabs, arrow keys move). */
function Tabs({ tabs, active, onChange }: { tabs: Tab[]; active: TabId; onChange: (t: TabId) => void }) {
  const list = useRef<HTMLDivElement>(null);
  const [bar, setBar] = useState<{ x: number; w: number } | null>(null);
  const [more, setMore] = useState<"" | "start" | "end" | "both">("");

  useLayoutEffect(() => {
    const el = list.current;
    if (!el) return;
    const place = () => {
      const b = el.querySelector<HTMLElement>(`[data-tab="${active}"]`);
      if (b) setBar({ x: b.offsetLeft, w: b.offsetWidth });
      // keep the selected tab visible when the row scrolls sideways (phones); never scroll the page itself
      if (b && b.offsetLeft < el.scrollLeft) el.scrollLeft = b.offsetLeft - 12;
      else if (b && b.offsetLeft + b.offsetWidth > el.scrollLeft + el.clientWidth) el.scrollLeft = b.offsetLeft + b.offsetWidth - el.clientWidth + 12;
    };
    // which edges have tabs scrolled out of view: CSS fades those edges so the row reads as scrollable
    const edges = () => {
      const max = el.scrollWidth - el.clientWidth;
      const s = max > 2 && el.scrollLeft > 2, e = max > 2 && el.scrollLeft < max - 2;
      setMore(s && e ? "both" : s ? "start" : e ? "end" : "");
    };
    place(); edges();
    const ro = new ResizeObserver(() => { place(); edges(); });
    ro.observe(el);
    el.addEventListener("scroll", edges, { passive: true });
    return () => { ro.disconnect(); el.removeEventListener("scroll", edges); };
  }, [active, tabs.length]);

  function key(e: KeyboardEvent) {
    const i = tabs.findIndex((t) => t.id === active);
    const next = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length
      : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    onChange(tabs[next].id);
    list.current?.querySelector<HTMLElement>(`[data-tab="${tabs[next].id}"]`)?.focus();
  }

  return (
    <div className="set-tabs-wrap">
      <div className="set-tabs" role="tablist" aria-label="Settings sections" ref={list} onKeyDown={key} data-more={more || undefined}>
        {bar && <span className="set-tabs-bar" style={{ transform: `translateX(${bar.x}px)`, width: bar.w }} aria-hidden="true" />}
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" id={`set-tab-${t.id}`} data-tab={t.id} aria-selected={t.id === active}
                  aria-controls={`set-panel-${t.id}`} tabIndex={t.id === active ? 0 : -1} onClick={() => onChange(t.id)}>
            {t.label}
            {t.count !== undefined && <span className="set-tab-count">{t.count}</span>}
            {t.dot !== undefined && <span className={`set-tab-dot d-${t.dot}`} aria-label={DOT_LABEL[t.dot] ?? t.dot} role="img" />}
          </button>
        ))}
      </div>
    </div>
  );
}

const DOT_LABEL: Record<string, string> = { none: "not connected", pending: "waiting", indexing: "reading", ready: "ready", failed: "could not be read" };
