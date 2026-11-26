import { CSSProperties, KeyboardEvent as ReactKeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { useLive } from "./live";
import { api, post } from "./api";
import { Icon, IconName } from "./ui";

type Notification = { id: string; kind: string; text: string; link: string; read_at: string | null; at: string };

/** Notification links are server paths (/reviews/<id>?project=<pid>, /experts?message=..&project=..); the app uses hash routes. */
export function linkTarget(link: string): { hash: string | null; project: string | null } {
  let url: URL;
  try { url = new URL(link || "/", location.origin); } catch { return { hash: null, project: null }; }
  return { hash: toHash(url), project: url.searchParams.get("project") };
}

function toHash(url: URL): string | null {
  const [, section, id] = url.pathname.split("/");
  const message = url.searchParams.get("message");
  switch (section) {
    case "reviews": return id ? `#/reviews/${encodeURIComponent(id)}` : "#/tasks";
    case "meetings": {
      const call = url.searchParams.get("call"); // a booked KT session: the list, with that call picked out
      return id ? `#/meetings/${encodeURIComponent(id)}` : call ? `#/meetings?call=${encodeURIComponent(call)}` : "#/meetings";
    }
    case "approvals": return id ? `#/approvals/${encodeURIComponent(id)}` : "#/approvals"; // that one, picked out
    case "tasks": return "#/tasks";
    case "experts": return message ? `#/inbox?message=${encodeURIComponent(message)}` : "#/inbox";
    case "chat": return message ? `#/chat?message=${encodeURIComponent(message)}` : "#/chat"; // the turn an expert answered
    case "runs": return "#/agents"; // an incident: the agents page, where the runs and their failures are
    // lifecycle links (/program/<id>?tab=…, /feedback, /handovers, /setup, /reports): the same path as a hash route
    case "program": case "feedback": case "handovers": case "setup": case "reports": case "home": case "team": {
      const q = new URLSearchParams(url.search);
      q.delete("project");
      const qs = q.toString();
      return `#${url.pathname}${qs ? `?${qs}` : ""}`;
    }
    default: return null;
  }
}

const KIND_ICON: Record<string, IconName> = {
  approval: "approve", expert_request: "inbox", expert_answer: "chat", incident: "video", review: "code", task: "tasks", meeting: "video",
  kt_request: "calendar", // a KT session booked from the planner
};

/** Compact relative time: "now", "5m", "3h", "2d", then the date. */
function ago(iso: string) {
  const d = new Date(iso), s = (Date.now() - d.getTime()) / 1000;
  if (Number.isNaN(s)) return "";
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export default function Notifications({ onOpen }: { onOpen: (hash: string | null, project: string | null) => void }) {
  const [items, setItems] = useState<Notification[]>([]);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);

  const load = useCallback(() => {
    api<Notification[]>("/notifications").then(setItems).catch(() => { /* the next change or focus tries again */ });
  }, []);
  useEffect(() => {
    load();
    addEventListener("focus", load);
    return () => removeEventListener("focus", load);
  }, [load]);
  useLive("notifications", load); // a new notification rings the bell the moment it is written

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); toggle.current?.focus(); } };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", escape); };
  }, [open]);

  const unread = items.filter((n) => !n.read_at).length;

  function markRead(ids: string[] | null) {
    const now = new Date().toISOString();
    setItems((xs) => xs.map((n) => (ids === null || ids.includes(n.id) ? { ...n, read_at: n.read_at ?? now } : n)));
    post("/notifications/read", { ids }).catch(() => {});
  }
  function openItem(n: Notification) {
    if (!n.read_at) markRead([n.id]);
    setOpen(false);
    const { hash, project } = linkTarget(n.link);
    onOpen(hash, project);
  }

  /** Up and down move between notifications, like a menu. */
  function arrows(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const all = Array.from(box.current?.querySelectorAll<HTMLButtonElement>(".notif-item") ?? []);
    if (!all.length) return;
    e.preventDefault();
    const i = all.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.key === "ArrowDown" ? (i + 1) % all.length : i <= 0 ? all.length - 1 : i - 1;
    all[next].focus();
  }

  const fresh = items.filter((n) => !n.read_at), earlier = items.filter((n) => n.read_at);
  const row = (n: Notification, i: number) => (
    <li key={n.id} style={{ "--i": i } as CSSProperties}>
      <button className={`notif-item k-${n.kind}${n.read_at ? "" : " unread"}`} onClick={() => openItem(n)}>
        <span className="notif-icon" aria-hidden="true"><Icon name={KIND_ICON[n.kind] ?? "bell"} size={15} /></span>
        <span className="notif-text">{n.text}{!n.read_at && <span className="sr-only"> (unread)</span>}</span>
        <time className="when" dateTime={n.at} title={new Date(n.at).toLocaleString()}>{ago(n.at)}</time>
        {!n.read_at && <span className="notif-dot" aria-hidden="true" />}
      </button>
    </li>
  );

  return (
    <div className="notif" ref={box} onKeyDown={arrows}>
      <button ref={toggle} className={`bell${unread ? " has-unread" : ""}`} aria-expanded={open} aria-controls="notif-panel" onClick={() => setOpen(!open)}
              aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} title="Notifications">
        {/* the nudge moves this wrapper, not the svg: Chrome animates an svg's transform on the main thread, every frame */}
        <span className="bell-icon"><Icon name="bell" /></span>
        {/* keyed by the count: a new unread item restarts the badge's (finite) ping */}
        {unread > 0 && <span key={unread} className="badge" aria-hidden="true">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div id="notif-panel" className="notif-panel" role="region" aria-label="Notifications">
          <div className="notif-head">
            <h2>Notifications{unread > 0 && <span className="notif-count">{unread} new</span>}</h2>
            {unread > 0 && <button className="link" onClick={() => { markRead(null); toggle.current?.focus(); }}>Mark all read</button>}
          </div>
          {items.length === 0 ? (
            <div className="notif-empty">
              <span className="notif-empty-mark" aria-hidden="true"><Icon name="check" size={18} /></span>
              <p className="muted">You are all caught up.</p>
            </div>
          ) : (
            <>
              {fresh.length > 0 && <><p className="notif-group">New</p><ul>{fresh.map((n, i) => row(n, i))}</ul></>}
              {earlier.length > 0 && <><p className="notif-group">Earlier</p><ul>{earlier.map((n, i) => row(n, fresh.length + i))}</ul></>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
