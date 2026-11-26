/** #/dashboard, shown as "KT coverage": what the KT sessions have explained, module by module. It is about the code, not about
 *  people: no per-hire rings, question counts or task percentages (GET /api/dashboard may still send them; they are ignored). */
import { ReactNode, useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../api";
import { useSession } from "../session";
import { CountUp, EmptyState, Reveal, Skeleton } from "../fx";
import { Glyph, SectionHead } from "../dataui";
import { useLive } from "../live";

type Dash = {
  kt_gaps?: string[];
  coverage?: { module: string; files: number; files_explained: number; kt_minutes: number }[];
  repeated_questions?: { topic: string; count: number }[];
};

const pct = (n: number) => `${Math.round(n)}%`;
const minutes = (m: number) => (m >= 10 || Number.isInteger(m) ? String(Math.round(m)) : m.toFixed(1));

/** A thin bar split into parts: teal for what a KT session explained, a neutral line for the rest. */
function Seg({ parts, label }: { parts: { n: number; tone?: "kt" | "on" }[]; label: string }) {
  const shown = parts.filter((p) => p.n > 0);
  return (
    <div className={`dp-seg${shown.length ? "" : " is-empty"}`} role="img" aria-label={label}>
      {shown.map((p, i) => <span key={i} className={p.tone ?? ""} style={{ flexGrow: p.n, animationDelay: `${150 + i * 80}ms` }} />)}
    </div>
  );
}

export default function Dashboard() {
  const { project } = useSession();
  const [d, setD] = useState<Dash | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { api<Dash>(`/dashboard?project_id=${project.id}`).then((x) => { setD(x); setError(null); }).catch((e) => setError(errorMessage(e))); }, [project.id]);
  useEffect(load, [load]);
  useLive(["meetings", "sources", "knowledge", "inbox"], load, project.id); // a session is processed, the code is read again
  if (error) return <Frame><p className="error" role="alert">{error}</p></Frame>;
  if (!d) return <CoverageSkeleton />;

  const coverage = d.coverage ?? [], gaps = d.kt_gaps ?? [], repeats = d.repeated_questions ?? [];
  if (coverage.length === 0) {
    return (
      <Frame>
        <EmptyState title="No coverage yet" action={<a className="dp-btn" href="#/settings?tab=repository">Repository settings</a>}>
          It appears once the repository has been read.
        </EmptyState>
      </Frame>
    );
  }

  const files = coverage.reduce((s, c) => s + c.files, 0), explained = coverage.reduce((s, c) => s + c.files_explained, 0);
  const ktTotal = coverage.reduce((s, c) => s + c.kt_minutes, 0);
  const maxMin = Math.max(1, ...coverage.map((c) => c.kt_minutes));
  const maxQ = Math.max(1, ...repeats.map((q) => q.count));
  const isGap = (m: string) => gaps.includes(m); // code modules never explained: they head the next KT agenda

  return (
    <Frame>
      <div className="dp-kpis dp-kpis-3">
        <Reveal className="dp-kpi spot">
          <p className="dp-kpi-label"><Glyph name="video" size={14} />Code explained</p>
          <p className="dp-kpi-value"><CountUp value={files ? (explained / files) * 100 : 0} format={pct} /></p>
          <Seg label={`${explained} of ${files} files explained in a KT session`} parts={[{ n: explained, tone: "kt" }, { n: files - explained }]} />
          <p className="dp-kpi-sub">{explained} of {files} files</p>
        </Reveal>
        <Reveal className="dp-kpi spot" delay={60}>
          <p className="dp-kpi-label"><Glyph name="clock" size={14} />KT recorded</p>
          <p className="dp-kpi-value"><CountUp value={ktTotal} format={minutes} /><span className="dp-kpi-of">min</span></p>
          <p className="dp-kpi-sub">across {coverage.filter((c) => c.kt_minutes > 0).length} of {coverage.length} modules</p>
        </Reveal>
        <Reveal className="dp-kpi spot" delay={120}>
          <p className="dp-kpi-label"><Glyph name="layers" size={14} />Never explained</p>
          <p className="dp-kpi-value"><CountUp value={gaps.length} /><span className="dp-kpi-of">/ {coverage.length}</span></p>
          <Seg label={`${gaps.length} of ${coverage.length} modules never explained`} parts={[{ n: gaps.length, tone: "on" }, { n: coverage.length - gaps.length }]} />
          <p className="dp-kpi-sub">{gaps.length ? <>Next agenda: {gaps.map((g, i) => <span key={g}>{i > 0 && ", "}<code>{g}</code></span>)}</> : "Every module has a session"}</p>
        </Reveal>
      </div>

      <Reveal delay={120}>
        <SectionHead title="Modules" count={coverage.length}>
          <span className="dp-section-links">
            {gaps.length > 0 && <a className="dp-section-link" href="#/kt-planner">Plan a session<Glyph name="arrow" size={14} /></a>}
            <a className="dp-section-link" href="#/knowledge-risk">Knowledge risk<Glyph name="arrow" size={14} /></a>
          </span>
        </SectionHead>
        <div className="dp-table-wrap">
          <table className="dp-table">
            <thead><tr><th scope="col">Module</th><th scope="col">Explained</th><th scope="col">KT minutes</th></tr></thead>
            <tbody>
              {coverage.map((c) => (
                <tr key={c.module} className={isGap(c.module) ? "gap" : ""}>
                  <th scope="row">
                    <a href="#/code">{c.module}</a>
                    {isGap(c.module) && <span className="dp-tag t-warn"><span className="dp-gap-mark" aria-hidden="true" />Next agenda</span>}
                  </th>
                  <td>
                    <span className="dp-cells" aria-hidden="true">
                      {Array.from({ length: Math.min(c.files, 12) }, (_, i) => (
                        <i key={i} className={i < Math.round((c.files_explained / Math.max(1, c.files)) * Math.min(c.files, 12)) ? "on" : ""} />
                      ))}
                    </span>
                    <span className="dp-num">{c.files_explained} of {c.files}</span>
                  </td>
                  <td>
                    <span className="dp-hbar" aria-hidden="true"><span style={{ width: `${(c.kt_minutes / maxMin) * 100}%` }} /></span>
                    {c.kt_minutes === 0
                      ? <span className="dp-num dp-none"><span aria-hidden="true">—</span><span className="sr-only">none</span></span>
                      : <span className="dp-num">{minutes(c.kt_minutes)}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Reveal>

      {repeats.length > 0 && (
        <Reveal delay={160}>
          <SectionHead title="Asked more than once" count={repeats.length} />
          <p className="dp-lede">Worth a document or the next KT session.</p>
          <ul className="dp-repeats">
            {repeats.map((q) => (
              <li key={q.topic}>
                <span className="dp-repeat-bar" style={{ width: `${(q.count / maxQ) * 100}%` }} aria-hidden="true" />
                <span className="dp-repeat-topic">{q.topic}</span>
                <span className="dp-num">{q.count} times</span>
              </li>
            ))}
          </ul>
        </Reveal>
      )}
    </Frame>
  );
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <section className="dp">
      <header className="dp-head">
        <div>
          <h1>KT coverage</h1>
          <p className="dp-sub">What KT sessions have explained, module by module.</p>
        </div>
      </header>
      {children}
    </section>
  );
}

function CoverageSkeleton() {
  return (
    <section className="dp" aria-busy="true">
      <span className="sr-only" role="status">Loading…</span>
      <header className="dp-head"><div><h1>KT coverage</h1></div></header>
      <div className="dp-kpis dp-kpis-3">{[0, 1, 2].map((i) => <div key={i} className="dp-kpi"><Skeleton lines={3} height={14} /></div>)}</div>
    </section>
  );
}
