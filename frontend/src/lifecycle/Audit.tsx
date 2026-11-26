/** #/audit (admins): who did what, and when, in this organisation.
 *
 *  Newest first by default. Every column header sorts (a button with aria-sort; select it again to reverse). Filters: a time
 *  range (Today, 7 days, 30 days, or From/To; both days count, in the viewer's own time zone: the API gets the exact
 *  instants), the action, the person, the exact target, and whether views of this log itself are shown. Pages of 25, 30 or
 *  50 rows (the choice is remembered); the count is of every matching entry. The CSV export has the same filters and order.
 *  The view lives in the address (#/audit?from=…&sort=…&page=…), so it survives a reload and can be shared.
 *  Reading and exporting the log are themselves audited. GET /api/audit, /api/audit/facets, /api/audit.csv. Styles: audit.css. */
import { FormEvent, MouseEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { download, errorMessage } from "../api";
import { toast } from "../ui";
import { EmptyState } from "../fx";
import { Avatar, Glyph } from "../dataui";
import { replaceRoute } from "../route";
import { clean, hashOf, LcHead, LoadError, nameOf, titleCase, useApi } from "./kit";
import { actionGroup, actionLabel, actionMatches } from "./auditActions";
import type { AuditFacets, AuditOrder, AuditPage, AuditRow, AuditSort } from "./types";
import "./audit.css";

/* ---------------------------------------------------------------- the view: filters, order, page */

/** asOf: the snapshot later pages are read at (the API's as_of from the first page read), so entries written meanwhile (each
 *  read of this log writes one) never shift a page. Not in the address; a filter, a new order or Refresh drops it. */
type View = { from: string; to: string; action: string; actor: string; target: string; views: boolean; sort: AuditSort; order: AuditOrder; page: number;
  asOf: number | null };
const SORTS: AuditSort[] = ["time", "actor", "action", "target"];
/** A column's first direction: newest first for time, A to Z for the rest. */
const FIRST_ORDER: Record<AuditSort, AuditOrder> = { time: "desc", action: "asc", actor: "asc", target: "asc" };
const VIEW_KEYS = ["from", "to", "action", "actor", "target", "views", "sort", "order", "page"];
const SIZES = [25, 30, 50];
const SIZE_KEY = "ww-audit-rows";
const VIEWED = "audit.viewed";

const pad = (n: number) => String(n).padStart(2, "0");
const isoDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
/** Local midnight of a YYYY-MM-DD day, `add` days later. */
const dayStart = (day: string, add = 0) => { const [y, m, d] = day.split("-").map(Number); return new Date(y, m - 1, d + add); };
const isDay = (v: string | null): v is string => !!v && /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(dayStart(v).getTime());
const today = () => isoDay(new Date());
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return isoDay(d); };

function readView(): View {
  const q = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const sort = SORTS.find((s) => s === q.get("sort")) ?? "time";
  const order = q.get("order");
  let from = isDay(q.get("from")) ? q.get("from")! : "", to = isDay(q.get("to")) ? q.get("to")! : "";
  if (from && to && from > to) [from, to] = [to, from];
  return {
    from, to, action: (q.get("action") ?? "").slice(0, 120), actor: (q.get("actor") ?? "").slice(0, 320), target: (q.get("target") ?? "").slice(0, 500),
    views: q.get("views") === "1", sort, order: order === "asc" || order === "desc" ? order : FIRST_ORDER[sort],
    page: Math.max(1, Math.floor(Number(q.get("page"))) || 1), asOf: null,
  };
}

function writeView(v: View) {
  const q = new URLSearchParams(location.hash.split("?")[1] ?? ""); // keep what other parts of the app put there
  VIEW_KEYS.forEach((k) => q.delete(k));
  if (v.from) q.set("from", v.from);
  if (v.to) q.set("to", v.to);
  if (v.action) q.set("action", v.action);
  if (v.actor) q.set("actor", v.actor);
  if (v.target) q.set("target", v.target);
  if (v.views) q.set("views", "1");
  if (v.sort !== "time" || v.order !== "desc") { q.set("sort", v.sort); q.set("order", v.order); }
  if (v.page > 1) q.set("page", String(v.page));
  const s = q.toString();
  replaceRoute(`#/audit${s ? `?${s}` : ""}`);
}

/** The API's query for a view: exact instants for the local days (To is included: it ends at the next local midnight). */
function apiQuery(v: View, size: number | null): string {
  const q = new URLSearchParams();
  if (v.from) q.set("since", dayStart(v.from).toISOString());
  if (v.to) q.set("until", dayStart(v.to, 1).toISOString());
  if (v.action) q.set("action", v.action);
  if (v.actor) q.set("actor", v.actor);
  if (v.target) q.set("target", v.target);
  if (!v.views && !(v.action && actionMatches(v.action, VIEWED))) q.set("exclude_action", VIEWED);
  q.set("sort", v.sort);
  q.set("order", v.order);
  if (v.asOf !== null) q.set("as_of", String(v.asOf));
  if (size) { q.set("limit", String(size)); q.set("offset", String((v.page - 1) * size)); }
  return q.toString();
}

function readSize(): number {
  try { const n = Number(localStorage.getItem(SIZE_KEY)); return SIZES.includes(n) ? n : SIZES[0]; } catch { return SIZES[0]; }
}

/* ---------------------------------------------------------------- time, in words */

const rtf = typeof Intl !== "undefined" && "RelativeTimeFormat" in Intl ? new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }) : null;
function relative(iso: string, now: number): string {
  const s = (new Date(iso).getTime() - now) / 1000;
  const a = Math.abs(s);
  if (!rtf) return "";
  if (a < 45) return "just now";
  if (a < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (a < 86400) return rtf.format(Math.round(s / 3600), "hour");
  if (a < 86400 * 30) return rtf.format(Math.round(s / 86400), "day");
  if (a < 86400 * 365) return rtf.format(Math.round(s / (86400 * 30.4)), "month");
  return rtf.format(Math.round(s / (86400 * 365)), "year");
}
function absolute(iso: string): string {
  const d = new Date(iso);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleString(undefined, { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }), hour: "2-digit", minute: "2-digit" });
}
const exact = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: "full", timeStyle: "long" });

/* ---------------------------------------------------------------- the page */

export default function Audit() {
  const [view, setViewState] = useState<View>(readView);
  const [size, setSize] = useState(readSize);
  const [open, setOpen] = useState<number | null>(null);
  const [exporting, setExporting] = useState(false);
  const tableRef = useRef<HTMLDivElement>(null);

  const setView = setViewState;
  useEffect(() => writeView(view), [view]); // the address follows the view (no history entry per click)
  const filter = (patch: Partial<View>) => { setOpen(null); setView((v) => ({ ...v, ...patch, page: 1, asOf: null })); };
  const [stale, setStale] = useState(false); // the From/To fields hold a range not applied yet (being typed, or From after To)

  const res = useApi<AuditPage>(`/audit?${apiQuery(view, size)}`, ["audit"]);
  const facets = useApi<AuditFacets>("/audit/facets", ["audit"]);
  const last = useRef<AuditPage | null>(null);
  if (res.data) last.current = res.data;
  const d = res.data ?? last.current; // the last page stays on screen, dimmed, while the next one loads
  const busy = !res.data && !res.error && !!last.current;

  // a page past the end (the log shrank, or an old link): go to the last page there is
  useEffect(() => {
    const p = res.data;
    if (p && p.items.length === 0 && p.total > 0 && p.offset > 0) setView((v) => ({ ...v, page: Math.max(1, Math.ceil(p.total / p.limit)) }));
  }, [res.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const used = !!(view.from || view.to || view.action || view.actor || view.target);
  const clear = () => filter({ from: "", to: "", action: "", actor: "", target: "" });

  function sortBy(col: AuditSort) {
    setOpen(null);
    setView((v) => ({ ...v, sort: col, order: v.sort === col ? (v.order === "desc" ? "asc" : "desc") : FIRST_ORDER[col], page: 1, asOf: null }));
  }
  function goTo(page: number) {
    setOpen(null);
    setView((v) => ({ ...v, page, asOf: v.asOf ?? d?.as_of ?? null })); // the page read so far is the snapshot
    const top = tableRef.current?.getBoundingClientRect().top ?? 0;
    if (top < 0) tableRef.current?.scrollIntoView({ block: "start" });
  }
  function resize(next: number) {
    try { localStorage.setItem(SIZE_KEY, String(next)); } catch { /* storage blocked: this visit only */ }
    setOpen(null);
    setView((v) => ({ ...v, page: Math.floor(((v.page - 1) * size) / next) + 1, asOf: v.asOf ?? d?.as_of ?? null })); // the first row stays
    setSize(next);
  }
  async function exportCsv() {
    setExporting(true);
    try {
      await download(`/audit.csv?${apiQuery(view, null)}`, "audit-log.csv");
      toast(d ? `Exported ${d.total.toLocaleString()} ${d.total === 1 ? "entry" : "entries"}` : "Audit log exported");
    } catch (e) { toast(errorMessage(e), "error"); }
    finally { setExporting(false); }
  }
  const refresh = () => { setView((v) => ({ ...v, asOf: null })); res.reload(); facets.reload(); };

  const retention = d?.retention_days ?? facets.data?.retention_days;
  return (
    <section className="lc au">
      <LcHead title="Audit log"
              sub={`Who did what, and when. ${retention ? `Kept ${retention} days; opening` : "Opening"} or exporting the log is recorded too.`}
              actions={<>
                <button type="button" className="dp-btn ghost small au-icon-btn" onClick={refresh} aria-label="Refresh the log" title="Refresh">
                  <RefreshIcon /></button>
                <button type="button" className="dp-btn small" onClick={exportCsv} disabled={exporting || !d || d.total === 0}
                        title="Downloads every entry that matches the filters, in the order shown">
                  <Glyph name="file" size={14} />{exporting ? "Exporting…" : "Export CSV"}</button>
              </>} />

      <Toolbar view={view} facets={facets.data} onFilter={filter} onClear={clear} used={used} onStale={setStale} />

      {res.error && !busy && d ? <LoadError error={res.error} retry={res.reload} /> : null}
      {res.error && !d ? <LoadError error={res.error} retry={res.reload} />
        : !d ? <TableSkeleton />
        : d.items.length === 0 && d.total === 0 ? (
          <div className="au-empty">
            <EmptyState title={used ? "No entries match these filters" : "Nothing recorded yet"}
                        action={used ? <button type="button" className="dp-btn" onClick={clear}>Clear filters</button> : undefined}>
              {used ? "Try a wider time range, or another person or action."
                : "Invitations, role changes, approvals and sign-ins appear here as they happen."}
            </EmptyState>
          </div>
        ) : (
          <>
            <div ref={tableRef} className={`dp-table-wrap au-wrap${busy || stale ? " is-busy" : ""}`} aria-busy={busy || undefined}>
              <table className="dp-table au-table">
                <caption className="sr-only">
                  Audit log, sorted by {COLUMN[view.sort].toLowerCase()}, {orderWords(view.sort, view.order)}. Showing {d.offset + 1} to {d.offset + d.items.length} of {d.total}.
                </caption>
                <thead>
                  <tr>
                    {(["time", "actor", "action", "target"] as AuditSort[]).map((col) => (
                      <SortHeader key={col} col={col} view={view} onSort={sortBy} />
                    ))}
                    <th scope="col" className="au-more-col"><span className="sr-only">Details</span></th>
                  </tr>
                </thead>
                <tbody>
                  {d.items.map((r) => (
                    <Row key={r.id} r={r} open={open === r.id} now={Date.now()} toggle={() => setOpen((o) => (o === r.id ? null : r.id))}
                         onFilter={filter} />
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={view.page} size={size} total={d.total} shown={d.items.length} offset={d.offset} onPage={goTo} onSize={resize} />
          </>
        )}
    </section>
  );
}

/* ---------------------------------------------------------------- filters */

const QUICK = [
  { key: "today", label: "Today", from: () => today() },
  { key: "7", label: "7 days", from: () => daysAgo(6) },
  { key: "30", label: "30 days", from: () => daysAgo(29) },
] as const;

/** A day the API can take: four-digit years from 1970 to this year. A date field reports every partial year while one is
 *  typed (0202, 2026, …); those stay in the field and are not sent. */
const usableDay = (v: string) => isDay(v) && Number(v.slice(0, 4)) >= 1970 && Number(v.slice(0, 4)) <= new Date().getFullYear();

function Toolbar({ view, facets, onFilter, onClear, used, onStale }:
  { view: View; facets: AuditFacets | null; onFilter: (p: Partial<View>) => void; onClear: () => void; used: boolean; onStale: (s: boolean) => void }) {
  const [from, setFrom] = useState(view.from);
  const [to, setTo] = useState(view.to);
  const [target, setTarget] = useState(view.target);
  useEffect(() => { setFrom(view.from); setTo(view.to); }, [view.from, view.to]);
  useEffect(() => setTarget(view.target), [view.target]);

  const bad = !!(from && to && from > to);
  const pending = from !== view.from || to !== view.to; // the fields differ from the range the table shows
  useEffect(() => onStale(pending), [pending]); // eslint-disable-line react-hooks/exhaustive-deps
  const quick = !view.from && !view.to ? "all" : view.to === today() ? QUICK.find((q) => q.from() === view.from)?.key ?? null : null;
  function pickDay(which: "from" | "to", value: string) {
    const f = which === "from" ? value : from, t = which === "to" ? value : to;
    if (which === "from") setFrom(value); else setTo(value);
    if (value && !usableDay(value)) return;
    if ((!f || usableDay(f)) && (!t || usableDay(t)) && !(f && t && f > t)) onFilter({ from: f, to: t });
  }
  function submitTarget(e?: FormEvent) {
    e?.preventDefault();
    if (target.trim() !== view.target) onFilter({ target: target.trim() });
  }

  const actionGroups = useMemo(() => groupActions(facets?.actions ?? [], view.action), [facets, view.action]);
  const people = facets?.actors ?? [];
  const knownActor = !view.actor || view.actor === "system" || people.some((p) => p.id === view.actor);

  return (
    <div className="au-toolbar">
      <div className="au-toolbar-row">
        <div className="au-quick" role="group" aria-label="Time range">
          {QUICK.map((q) => (
            <button key={q.key} type="button" aria-pressed={quick === q.key} onClick={() => onFilter({ from: q.from(), to: today() })}>{q.label}</button>
          ))}
          <button type="button" aria-pressed={quick === "all"} onClick={() => onFilter({ from: "", to: "" })}>All time</button>
        </div>
        <div className="au-dates">
          <label className="au-field">
            <span>From</span>
            <input type="date" value={from} max={to || today()} aria-invalid={bad || undefined} aria-describedby={bad ? "au-range-error" : undefined}
                   onChange={(e) => pickDay("from", e.target.value)} />
          </label>
          <span className="au-dash" aria-hidden="true">–</span>
          <label className="au-field">
            <span>To <span className="au-hint">(included)</span></span>
            <input type="date" value={to} min={from || undefined} max={today()} aria-invalid={bad || undefined} aria-describedby={bad ? "au-range-error" : undefined}
                   onChange={(e) => pickDay("to", e.target.value)} />
          </label>
        </div>
      </div>
      <div className="au-toolbar-row">
        <label className="au-field">
          <span>Action</span>
          <select value={view.action} onChange={(e) => onFilter({ action: e.target.value })}>
            <option value="">All actions</option>
            {actionGroups.map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.prefix && <option value={`${g.prefix}.*`}>Any {g.label.toLowerCase()} action ({g.total})</option>}
                {g.items.map((a) => <option key={a.action} value={a.action}>{actionLabel(a.action)}{a.count ? ` (${a.count})` : ""}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
        <label className="au-field">
          <span>Person</span>
          <select value={view.actor} onChange={(e) => onFilter({ actor: e.target.value })}>
            <option value="">Everyone</option>
            {people.map((p) => <option key={p.id} value={p.id}>{nameOf(p.name, "Former member")} ({p.count})</option>)}
            {!!facets?.system && <option value="system">System, no person ({facets.system})</option>}
            {!knownActor && <option value={view.actor}>{view.actor}</option>}
          </select>
        </label>
        <form className="au-field au-grow" role="search" onSubmit={submitTarget}>
          <label htmlFor="au-target">Target</label>
          <input id="au-target" type="search" value={target} maxLength={500} placeholder="An exact id or email, then Enter" enterKeyHint="search"
                 onChange={(e) => { setTarget(e.target.value); if (!e.target.value && view.target) onFilter({ target: "" }); }}
                 onBlur={() => submitTarget()} />
        </form>
        <label className="au-check" title="Each time an admin opens or pages through this log, an entry is recorded. They are hidden unless you show them.">
          <input type="checkbox" checked={view.views} onChange={(e) => onFilter({ views: e.target.checked })} />
          Show views of this log
        </label>
        {used && <button type="button" className="dp-btn ghost small au-clear" onClick={onClear}>Clear filters</button>}
      </div>
      {bad && <p id="au-range-error" className="au-error" role="alert">The From day is after the To day. Pick a From day on or before {dayLabel(to)}.
        The table still shows the last range that worked.</p>}
    </div>
  );
}

const dayLabel = (day: string) => dayStart(day).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

type ActionGroup = { label: string; prefix: string | null; total: number; items: { action: string; count: number }[] };
/** The actions in the log, grouped for the menu ("Members": added, role changed, …). A group with one kind of code prefix
 *  and more than one action also offers "Any … action" (a prefix filter). The current filter is always an option. */
function groupActions(actions: { action: string; count: number }[], current: string): ActionGroup[] {
  const list = [...actions];
  if (current && !current.endsWith("*") && !list.some((a) => a.action === current)) list.push({ action: current, count: 0 });
  const groups = new Map<string, ActionGroup & { heads: Set<string> }>();
  for (const a of list) {
    const label = actionGroup(a.action);
    const g = groups.get(label) ?? { label, prefix: null, total: 0, items: [], heads: new Set<string>() };
    g.items.push(a); g.total += a.count; g.heads.add(a.action.split(".")[0]);
    groups.set(label, g);
  }
  const out = [...groups.values()].map(({ heads, ...g }) => ({
    ...g, prefix: heads.size === 1 && g.items.length > 1 ? [...heads][0] : null,
    items: g.items.sort((x, y) => actionLabel(x.action).localeCompare(actionLabel(y.action))),
  })).sort((x, y) => x.label.localeCompare(y.label));
  if (current.endsWith("*") && !out.some((g) => g.prefix && `${g.prefix}.*` === current)) {
    out.unshift({ label: "Filter", prefix: null, total: 0, items: [{ action: current, count: 0 }] });
  }
  return out;
}

/* ---------------------------------------------------------------- table */

const COLUMN: Record<AuditSort, string> = { time: "When", actor: "Person", action: "Action", target: "Target" };
function orderWords(col: AuditSort, order: AuditOrder) {
  if (col === "time") return order === "desc" ? "newest first" : "oldest first";
  return order === "asc" ? "A to Z" : "Z to A";
}

function SortHeader({ col, view, onSort }: { col: AuditSort; view: View; onSort: (c: AuditSort) => void }) {
  const on = view.sort === col;
  const next = on ? (view.order === "desc" ? "asc" : "desc") : FIRST_ORDER[col];
  return (
    <th scope="col" className={`au-col-${col}`} aria-sort={on ? (view.order === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className={`au-sort${on ? " on" : ""}`} onClick={() => onSort(col)} title={`Sort ${orderWords(col, next)}`}>
        {COLUMN[col]}
        <svg className={`au-sort-icon${on ? ` is-${view.order}` : ""}`} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {on ? <path d="M12 5v14M6 13l6 6 6-6" /> : <path d="M8 9l4-4 4 4M8 15l4 4 4-4" />}
        </svg>
      </button>
    </th>
  );
}

function Row({ r, open, now, toggle, onFilter }:
  { r: AuditRow; open: boolean; now: number; toggle: () => void; onFilter: (p: Partial<View>) => void }) {
  const label = actionLabel(r.action, r.detail);
  const when = absolute(r.created_at);
  const id = `au-detail-${r.id}`;
  // a click anywhere on the row opens it, except on its links and buttons (the chevron is the keyboard's way in)
  const onRow = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest("a, button, input, select")) toggle(); };
  return (
    <>
      <tr className={`au-row${open ? " is-open" : ""}`} onClick={onRow}>
        <td className="au-when">
          <time dateTime={r.created_at} title={exact(r.created_at)}>{when}</time>
          <span className="au-rel">{relative(r.created_at, now)}</span>
        </td>
        <td className="au-who"><Person r={r} /></td>
        <td className="au-what">
          <span className="au-action">{label}</span>
          <code className="au-code">{r.action}</code>
        </td>
        <td className="au-target"><Target r={r} /></td>
        <td className="au-more">
          <button type="button" className="au-toggle" aria-expanded={open} aria-controls={open ? id : undefined}
                  aria-label={`${open ? "Hide" : "Show"} details: ${label}, ${when}`} onClick={toggle}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </td>
      </tr>
      {open && (
        <tr className="au-detail-row" id={id}>
          <td colSpan={5}><Detail r={r} label={label} onFilter={onFilter} /></td>
        </tr>
      )}
    </>
  );
}

function Person({ r }: { r: AuditRow }) {
  if (!r.actor_id) return <span className="au-person is-system"><span className="au-sys" aria-hidden="true" />System</span>;
  const name = nameOf(r.actor_name, "Former member");
  return <span className="au-person" title={r.actor_email ?? undefined}><Avatar name={name} size={22} /><span className="au-person-name">{name}</span></span>;
}

/** A title the entry recorded (a task's, a document's), for targets the API could not name. */
function detailTitle(detail: unknown): string | null {
  if (!detail || typeof detail !== "object") return null;
  const d = detail as Record<string, unknown>;
  const t = d.title ?? d.name;
  return typeof t === "string" && t.trim() ? t.trim().slice(0, 140) : null;
}

const FEEDBACK_ON: Record<string, string> = { doc: "a document", code: "code", meeting: "a KT call", answer: "an answer" };

/** The target in words, linked to where it lives when the API says where; the raw id otherwise (it is in the details too). */
function Target({ r }: { r: AuditRow }) {
  const sub = r.target_kind === "onboarding" && r.target.includes(":") ? titleCase(r.target.slice(r.target.indexOf(":") + 1)) : null;
  const name = r.target_name ? nameOf(r.target_name) : null;
  const board = r.action.startsWith("board.");
  const text = r.target_kind === "person" ? name
    : r.target_kind === "task" && name ? `Task · ${clean(r.target_name!)}`
    : r.target_kind === "onboarding" && name ? `${name}'s ${board ? "board" : "onboarding"}`
    : r.target_kind === "handover" && name ? `${name}'s handover`
    : r.target_kind === "feedback" ? `Feedback on ${FEEDBACK_ON[r.target_name ?? ""] ?? "documentation"}`
    : r.target_kind === "access" && r.target_name ? clean(r.target_name)
    : r.target_kind === "organisation" ? "This organisation"
    : r.target === "audit_log" ? "This log"
    : r.target_name ?? detailTitle(r.detail);
  if (!text) return <code className="au-raw" title={r.target}>{r.target}</code>;
  return (
    <span className="au-target-name" title={r.target}>
      {r.target_link ? <a className="au-link" href={hashOf(r.target_link)}>{text}</a> : text}
      {sub && <span className="au-sub"> · {sub}</span>}
    </span>
  );
}

/** A task's status in the board's words (the log keeps the code). */
const BOARD_STATUS: Record<string, string> = {
  approved: "To do", in_progress: "In progress", changes_requested: "In progress, changes requested", submitted: "Done, waiting for sign-off",
  done: "Done", rejected: "Removed", proposed: "Proposed",
};
const STATUS_ACTIONS = new Set(["board.task.moved", "board.task.removed", "task.moved"]);
const HIDDEN = new Set(["from_title", "to_title", "from_day", "to_day"]); // folded into From / To / Milestone below

/** What an entry recorded, as label and value pairs, in the board's words where the entry is a board action: statuses as
 *  the columns read, milestones as "Day 30 · First change shipped". */
function detailEntries(r: AuditRow): [string, unknown][] {
  if (!r.detail || typeof r.detail !== "object" || Array.isArray(r.detail)) return [];
  const d = r.detail as Record<string, unknown>;
  const milestone = (key: unknown, day: unknown, title: unknown) =>
    title ? `${day ? `Day ${day} · ` : ""}${title}` : typeof key === "string" ? titleCase(key) : key;
  return Object.entries(d).filter(([k]) => !HIDDEN.has(k)).map(([k, v]): [string, unknown] => {
    if (STATUS_ACTIONS.has(r.action) && (k === "from" || k === "to") && typeof v === "string") return [titleCase(k), BOARD_STATUS[v] ?? titleCase(v)];
    if (r.action === "board.task.rescheduled" && (k === "from" || k === "to")) return [titleCase(k), milestone(v, d[`${k}_day`], d[`${k}_title`])];
    if (r.action === "board.task.added" && k === "milestone") return ["Milestone", milestone(v, d.to_day, d.to_title)];
    return [titleCase(k), v];
  });
}

function Detail({ r, label, onFilter }: { r: AuditRow; label: string; onFilter: (p: Partial<View>) => void }) {
  const detail = detailEntries(r);
  const who = r.actor_id ? nameOf(r.actor_name, "Former member") : null;
  return (
    <div className="au-detail">
      <dl className="au-facts">
        <Fact term="Exact time"><time dateTime={r.created_at}>{exact(r.created_at)}</time></Fact>
        <Fact term="Person">{who ? <>{who}{r.actor_email && <span className="au-dim"> · {r.actor_email}</span>}</> : "System: recorded by the app itself, not a person"}</Fact>
        <Fact term="Action"><code>{r.action}</code></Fact>
        <Fact term="Target"><code className="au-break">{r.target}</code></Fact>
        {detail.map(([k, v]) => <Fact key={k} term={k}><Value v={v} /></Fact>)}
        <Fact term="Entry"><span className="au-mono">#{r.id}</span></Fact>
      </dl>
      <div className="au-detail-actions">
        <span className="au-dim">Show only</span>
        <button type="button" className="dp-btn small" onClick={() => onFilter({ action: r.action })}>{label}</button>
        {r.actor_id && <button type="button" className="dp-btn small" onClick={() => onFilter({ actor: r.actor_id! })}>{who}</button>}
        <button type="button" className="dp-btn small" onClick={() => onFilter({ target: r.target })}>This target</button>
      </div>
    </div>
  );
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return <div className="au-fact"><dt>{term}</dt><dd>{children}</dd></div>;
}

function Value({ v }: { v: unknown }) {
  if (v === null || v === undefined || v === "") return <span className="au-dim">None</span>;
  if (typeof v === "boolean") return <>{v ? "Yes" : "No"}</>;
  if (typeof v === "number") return <span className="au-mono">{v}</span>;
  if (typeof v === "string") return <span className="au-break">{v}</span>;
  return <code className="au-json">{JSON.stringify(v, null, 2)}</code>;
}

/* ---------------------------------------------------------------- paging */

/** 1 … 4 5 6 … 12: the first and last page, the current one and its neighbours. */
function pageList(page: number, pages: number): (number | "gap")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const keep = new Set([1, pages, page - 1, page, page + 1].filter((p) => p >= 1 && p <= pages));
  if (page <= 3) [2, 3, 4].forEach((p) => keep.add(p));
  if (page >= pages - 2) [pages - 1, pages - 2, pages - 3].forEach((p) => keep.add(p));
  const sorted = [...keep].sort((a, b) => a - b);
  const out: (number | "gap")[] = [];
  sorted.forEach((p, i) => { if (i && p - sorted[i - 1] > 1) out.push("gap"); out.push(p); });
  return out;
}

function Pager({ page, size, total, shown, offset, onPage, onSize }:
  { page: number; size: number; total: number; shown: number; offset: number; onPage: (p: number) => void; onSize: (n: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  const current = Math.min(page, pages);
  const first = shown ? offset + 1 : 0;
  return (
    <div className="au-foot">
      <p className="au-count" aria-live="polite">
        Showing <strong>{first.toLocaleString()}–{(offset + shown).toLocaleString()}</strong> of <strong>{total.toLocaleString()}</strong>
      </p>
      <label className="au-size">
        <span>Rows per page</span>
        <select value={size} onChange={(e) => onSize(Number(e.target.value))}>
          {SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      {pages > 1 && (
        <nav className="au-pages" aria-label="Audit log pages">
          <PageButton label="First page" disabled={current === 1} onClick={() => onPage(1)}><Chevrons double dir="left" /></PageButton>
          <PageButton label="Previous page" disabled={current === 1} onClick={() => onPage(current - 1)}><Chevrons dir="left" /></PageButton>
          <span className="au-page-nums">
            {pageList(current, pages).map((p, i) => p === "gap"
              ? <span key={`gap-${i}`} className="au-gap" aria-hidden="true">…</span>
              : <button key={p} type="button" className="au-page" aria-current={p === current ? "page" : undefined} aria-label={`Page ${p}`}
                        onClick={() => p !== current && onPage(p)}>{p}</button>)}
          </span>
          <span className="au-page-of">Page {current} of {pages}</span>
          <PageButton label="Next page" disabled={current === pages} onClick={() => onPage(current + 1)}><Chevrons dir="right" /></PageButton>
          <PageButton label="Last page" disabled={current === pages} onClick={() => onPage(pages)}><Chevrons double dir="right" /></PageButton>
        </nav>
      )}
    </div>
  );
}

function PageButton({ label, disabled, onClick, children }: { label: string; disabled: boolean; onClick: () => void; children: ReactNode }) {
  return <button type="button" className="au-page au-step" aria-label={label} title={label} disabled={disabled} onClick={onClick}>{children}</button>;
}

function Chevrons({ dir, double }: { dir: "left" | "right"; double?: boolean }) {
  const d = dir === "left" ? (double ? "M11 17l-5-5 5-5M18 17l-5-5 5-5" : "M15 18l-6-6 6-6") : (double ? "M13 17l5-5-5-5M6 17l5-5-5-5" : "M9 18l6-6-6-6");
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;
}

function RefreshIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 4v5h-5" />
    </svg>
  );
}

function TableSkeleton() {
  return (
    <div className="dp-table-wrap au-wrap au-skel" aria-busy="true">
      <span className="sr-only" role="status">Loading the audit log…</span>
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="au-skel-row" aria-hidden="true">
          <i style={{ width: "9rem" }} /><i style={{ width: "7rem" }} /><i style={{ width: `${10 + ((i * 37) % 7)}rem` }} /><i style={{ width: "6rem" }} />
        </div>
      ))}
    </div>
  );
}
