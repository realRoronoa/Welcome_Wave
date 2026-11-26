/** #/feedback (experts and managers): documentation feedback, grouped by what it is about. Owners work their queue;
 *  managers see everything. GET/PATCH /api/feedback (docs/specs/documentation-feedback.md). */
import { useMemo, useState } from "react";
import { patch } from "../api";
import { useSession } from "../session";
import { toast } from "../ui";
import { EmptyState } from "../fx";
import { Glyph, GlyphName } from "../dataui";
import { LcHead, LcSkeleton, LoadError, nameOf, NextAction, shortDate, useAction, useApi } from "./kit";
import type { Feedback as FeedbackT } from "./types";

const TARGET: Record<FeedbackT["target_kind"], { glyph: GlyphName; label: string; href: (ref: string) => string | null }> = {
  code: { glyph: "code", label: "Code", href: (r) => `#/code?path=${encodeURIComponent(r)}` },
  doc: { glyph: "file", label: "Document", href: (r) => `#/documents/${encodeURIComponent(r)}` },
  meeting: { glyph: "video", label: "KT session", href: (r) => `#/meetings/${encodeURIComponent(r)}` },
  answer: { glyph: "message", label: "Answer", href: () => null },
};
const FILTERS = [["open", "Open"], ["closed", "Closed"], ["all", "All"]] as const;

export default function Feedback() {
  const { project, me } = useSession();
  const list = useApi<FeedbackT[]>(`/feedback?project_id=${encodeURIComponent(project.id)}`);
  const detail = useApi<{ members: { id: string; name: string }[]; meetings?: unknown }>(`/projects/${encodeURIComponent(project.id)}`, ["people"]);
  const meetings = useApi<{ id: string; title: string }[]>(`/meetings?project_id=${encodeURIComponent(project.id)}`, ["meetings"]);
  const act = useAction();
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("open");
  const who = (id: string) => id === me.id ? "you" : nameOf(detail.data?.members.find((m) => m.id === id)?.name ?? null, "someone");
  const manager = me.role === "manager" || me.role === "admin";

  const groups = useMemo(() => {
    const rows = (list.data ?? []).filter((f) => filter === "all" || (filter === "open" ? f.status === "open" : f.status !== "open"));
    const m = new Map<string, FeedbackT[]>();
    for (const f of rows) {
      const k = `${f.target_kind}:${f.target_ref}`;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(f);
    }
    return [...m.entries()];
  }, [list.data, filter]);
  const openCount = (list.data ?? []).filter((f) => f.status === "open").length;
  const label = (f: FeedbackT) => f.target_kind === "meeting" ? (Array.isArray(meetings.data) ? meetings.data.find((m) => m.id === f.target_ref)?.title : null) ?? f.target_ref : f.target_ref;

  const set = (f: FeedbackT, status: FeedbackT["status"]) =>
    act.run(f.id + status, () => patch(`/feedback/${encodeURIComponent(f.id)}`, { status })).then((r) => {
      if (r) { toast(status === "resolved" ? "Resolved" : status === "dismissed" ? "Dismissed" : "Reopened"); list.reload(); }
    });

  if (list.error && !list.data) return <section className="lc"><LcHead title="Feedback" /><LoadError error={list.error} retry={list.reload} /></section>;
  if (!list.data) return <section className="lc"><LcSkeleton rows={3} /></section>;
  return (
    <section className="lc lc-narrow">
      <LcHead title="Feedback" sub={openCount ? `${openCount} open · marked unclear or outdated` : "Marked unclear or outdated by your team"} />
      <div className="lc-seg" role="tablist" aria-label="Show">
        {FILTERS.map(([id, l]) => (
          <button key={id} type="button" role="tab" aria-selected={filter === id} className={filter === id ? "on" : ""} onClick={() => setFilter(id)}>{l}</button>
        ))}
      </div>
      {groups.length === 0 ? (
        <EmptyState title={filter === "open" ? "Nothing open" : "Nothing here"}>
          {filter === "open" ? "When someone marks your docs, code or sessions unclear, it lands here." : undefined}
        </EmptyState>
      ) : (
        <div className="lc-stack-l">
          {groups.map(([k, items]) => {
            const t = TARGET[items[0].target_kind];
            const href = t.href(items[0].target_ref);
            return (
              <section key={k} className="lc-card lc-fb-group" aria-label={label(items[0])}>
                <header className="lc-fb-head">
                  <span className="lc-li-icon"><Glyph name={t.glyph} size={14} /></span>
                  <span className="lc-dim">{t.label}</span>
                  {href ? <a className="lc-fb-target" href={href}>{label(items[0])}</a> : <span className="lc-fb-target">{label(items[0])}</span>}
                </header>
                <ul className="lc-list">
                  {items.map((f) => {
                    const can = manager || f.owner_id === me.id;
                    return (
                      <li key={f.id} className={`lc-li${f.status !== "open" ? " is-done" : ""}`}>
                        <span className={`lc-kind k-${f.kind}`}>{f.kind === "unclear" ? "Unclear" : "Outdated"}</span>
                        <span className="lc-li-main">{f.note || <span className="lc-dim">No note</span>}
                          <span className="lc-li-sub">From {who(f.created_by)} · {shortDate(f.created_at)}{f.owner_id !== me.id ? ` · owner ${who(f.owner_id)}` : ""}{f.status !== "open" ? ` · ${f.status}` : ""}</span>
                        </span>
                        {can && (
                          <span className="lc-li-actions">
                            {f.status === "open" ? <>
                              <button type="button" className="dp-btn small" disabled={!!act.busy} onClick={() => set(f, "resolved")}>Resolve</button>
                              <button type="button" className="dp-btn small ghost" disabled={!!act.busy} onClick={() => set(f, "dismissed")}>Dismiss</button>
                            </> : <button type="button" className="dp-btn small ghost" disabled={!!act.busy} onClick={() => set(f, "open")}>Reopen</button>}
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </div>
      )}
      {act.error && <p className="error" role="alert">{act.error}</p>}
      <NextAction><a className="dp-btn" href="#/home">Back to Home</a></NextAction>
    </section>
  );
}
