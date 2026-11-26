/** #/audit (admins): the audit log with filters (action, actor, target, dates), paging and a CSV export. Reading and
 *  exporting the log are themselves audited. GET /api/audit, /api/audit.csv (Track F). */
import { FormEvent, useState } from "react";
import { download, errorMessage } from "../api";
import { toast } from "../ui";
import { EmptyState } from "../fx";
import { Glyph } from "../dataui";
import { How, LcHead, LcSkeleton, LoadError, nameOf, NextAction, useApi } from "./kit";
import type { AuditPage } from "./types";

type Filters = { action: string; actor: string; target: string; since: string; until: string };
const EMPTY: Filters = { action: "", actor: "", target: "", since: "", until: "" };
const LIMIT = 50;

const qs = (f: Filters, extra: Record<string, string | number> = {}) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v.trim()) q.set(k, v.trim());
  for (const [k, v] of Object.entries(extra)) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
};
const when = (iso: string) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export default function Audit() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [offset, setOffset] = useState(0);
  const page = useApi<AuditPage>(`/audit${qs(applied, { limit: LIMIT, offset })}`, ["audit"]);
  const set = (k: keyof Filters) => (v: string) => setDraft((d) => ({ ...d, [k]: v }));
  const used = Object.values(applied).some((v) => v.trim());

  function apply(e: FormEvent) { e.preventDefault(); setApplied(draft); setOffset(0); }
  function clear() { setDraft(EMPTY); setApplied(EMPTY); setOffset(0); }
  async function exportCsv() {
    try { await download(`/audit.csv${qs(applied)}`, "audit-log.csv"); toast("Audit log exported"); }
    catch (e) { toast(errorMessage(e), "error"); }
  }

  const d = page.data;
  return (
    <section className="lc">
      <LcHead title="Audit log" sub={d ? `Kept ${d.retention_days} days · viewing it is audited too` : " "}
              actions={<button type="button" className="dp-btn" onClick={exportCsv}><Glyph name="file" size={14} />Export CSV</button>} />
      <form className="lc-filters lc-audit-filters" onSubmit={apply}>
        <label className="lc-filter"><span>Action</span><input value={draft.action} placeholder="e.g. onboarding.*" onChange={(e) => set("action")(e.target.value)} maxLength={120} /></label>
        <label className="lc-filter"><span>Actor</span><input value={draft.actor} placeholder="Email or id" onChange={(e) => set("actor")(e.target.value)} maxLength={320} /></label>
        <label className="lc-filter"><span>Target</span><input value={draft.target} onChange={(e) => set("target")(e.target.value)} maxLength={500} /></label>
        <label className="lc-filter"><span>From</span><input type="date" value={draft.since} onChange={(e) => set("since")(e.target.value)} /></label>
        <label className="lc-filter"><span>To</span><input type="date" value={draft.until} onChange={(e) => set("until")(e.target.value)} /></label>
        <div className="lc-filter-actions">
          <button type="submit" className="dp-btn primary small">Apply</button>
          {used && <button type="button" className="dp-btn ghost small" onClick={clear}>Clear</button>}
          <How text="Action matches exactly, or by prefix with a trailing *. Dates are inclusive." align="end" />
        </div>
      </form>
      {page.error && !d ? <LoadError error={page.error} retry={page.reload} />
        : !d ? <LcSkeleton rows={6} head={false} />
        : d.items.length === 0 ? <EmptyState title={used ? "Nothing matches these filters" : "Nothing recorded yet"} action={used ? <button type="button" className="dp-btn" onClick={clear}>Clear filters</button> : undefined} />
        : (
          <>
            <div className="dp-table-wrap">
              <table className="dp-table lc-audit">
                <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">Action</th><th scope="col">Target</th><th scope="col"><span className="sr-only">Detail</span></th></tr></thead>
                <tbody>
                  {d.items.map((r) => (
                    <tr key={r.id}>
                      <td className="lc-mono">{when(r.created_at)}</td>
                      <td>{r.actor_name ? nameOf(r.actor_name) : <span className="lc-dim">System</span>}</td>
                      <td><code className="lc-code">{r.action}</code></td>
                      <td className="lc-trunc"><code className="lc-code" title={r.target}>{r.target}</code></td>
                      <td className="lc-right">{r.detail && Object.keys(r.detail as object).length > 0
                        ? <How label="Detail" title="Recorded detail" text={<code className="lc-json">{JSON.stringify(r.detail, null, 2)}</code>} align="end" /> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="lc-pager">
              <span className="lc-dim">{d.offset + 1}–{d.offset + d.items.length} of {d.total}</span>
              <button type="button" className="dp-btn small" disabled={d.offset === 0} onClick={() => setOffset(Math.max(0, d.offset - LIMIT))}>Previous</button>
              <button type="button" className="dp-btn small" disabled={d.next_offset === null} onClick={() => d.next_offset !== null && setOffset(d.next_offset)}>Next</button>
            </div>
          </>
        )}
      <NextAction><button type="button" className="dp-btn" onClick={exportCsv}><Glyph name="file" size={14} />Export {used ? "these entries" : "the log"} as CSV</button></NextAction>
    </section>
  );
}
