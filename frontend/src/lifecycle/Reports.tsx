/** #/reports (managers, admins, executives): the onboarding report. Each figure compares the current cohort with the
 *  previous one, says "not enough data" below its floor instead of 0, and keeps how it is worked out behind a
 *  disclosure. Then the survey aggregates (groups of five or more only), the assumptions, and a CSV export.
 *  Managers read their project; admins and executives the organisation. GET /api/reports/onboarding(.csv). */
import { download, errorMessage } from "../api";
import { useSession } from "../session";
import { toast } from "../ui";
import { Glyph } from "../dataui";
import { FigureTile } from "./ReportParts";
import { How, LcHead, LcSkeleton, LoadError, NextAction, Section, shortDate, useApi, Why } from "./kit";
import type { Report, ReportFigure } from "./types";

const GROUPS: { title: string; keys: string[] }[] = [
  { title: "Pace", keys: ["hires", "days_to_first_pr", "days_to_first_merge", "days_to_complete"] },
  { title: "Follow-through", keys: ["milestones_on_time", "checkins_on_time", "blocker_days", "open_blockers"] },
  { title: "Knowledge", keys: ["answered_from_sources", "senior_hours_saved", "knowledge_gaps"] },
];
const SHORT: Record<string, string> = { clarity: "Clarity", support: "Support", docs: "Docs", tools: "Tools", belonging: "Belonging" };

/** The report's query: a manager's project, or the whole organisation for admins and executives. */
export function reportQuery(role: string, projectId: string) {
  return role === "manager" ? `?project_id=${encodeURIComponent(projectId)}` : "";
}

export async function exportReport(role: string, projectId: string) {
  try { await download(`/reports/onboarding.csv${reportQuery(role, projectId)}`, "onboarding-report.csv"); toast("Report exported"); }
  catch (e) { toast(errorMessage(e), "error"); }
}

export default function Reports() {
  const { me, project } = useSession();
  const q = reportQuery(me.role, project.id);
  const r = useApi<Report>(`/reports/onboarding${q}`);
  const exportBtn = <button type="button" className="dp-btn" onClick={() => exportReport(me.role, project.id)}><Glyph name="file" size={14} />Export CSV</button>;
  if (r.error && !r.data) return <section className="lc"><LcHead title="Reports" /><LoadError error={r.error} retry={r.reload} /></section>;
  if (!r.data) return <section className="lc"><LcSkeleton rows={4} /></section>;
  const d = r.data;
  const byKey = new Map(d.figures.map((f) => [f.key, f]));
  const placed = new Set(GROUPS.flatMap((g) => g.keys));
  const rest = d.figures.filter((f) => !placed.has(f.key));
  const a = d.assumptions;
  return (
    <section className="lc">
      <LcHead title="Reports"
              sub={<>{me.role === "manager" ? project.name : "All projects"} · last {d.window_days} days against the {d.window_days} before · as of {shortDate(d.as_of)} <Why why={d.why} label="About" /></>}
              actions={exportBtn} />
      {GROUPS.map((g) => {
        const figs = g.keys.map((k) => byKey.get(k)).filter((f): f is ReportFigure => !!f);
        return figs.length ? (
          <Section key={g.title} title={g.title} id={`rep-${g.title}`}>
            <div className="lc-tiles">{figs.map((f) => <FigureTile key={f.key} f={f} />)}</div>
          </Section>
        ) : null;
      })}
      {rest.length > 0 && <Section title="Other" id="rep-other"><div className="lc-tiles">{rest.map((f) => <FigureTile key={f.key} f={f} />)}</div></Section>}

      <Section title="Onboarding survey" id="rep-survey"
               action={<How text={`Anonymous answers on a 1–5 scale. A team's averages appear only with ${d.surveys.min_group} or more answers, so nobody can be identified.`} align="end" />}>
        {d.surveys.teams.length === 0 ? <p className="lc-quiet">No answers yet.</p> : (
          <div className="dp-table-wrap">
            <table className="dp-table lc-survey">
              <thead>
                <tr>
                  <th scope="col">Team</th>
                  {d.surveys.questions.map((q) => <th key={q.id} scope="col" title={q.text}>{SHORT[q.id] ?? q.id}</th>)}
                  <th scope="col" className="num">Answers</th>
                </tr>
              </thead>
              <tbody>
                {d.surveys.teams.map((t) => (
                  <tr key={t.team_id}>
                    <th scope="row">{t.team}</th>
                    {t.status === "ok" && t.scores
                      ? d.surveys.questions.map((q) => <td key={q.id}><Score v={t.scores![q.id]} /></td>)
                      : <td colSpan={d.surveys.questions.length} className="lc-thin">Not enough data</td>}
                    <td className="num">{t.n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Assumptions" id="rep-assumptions">
        <dl className="lc-assume">
          <div><dt>Minutes per avoided interruption</dt><dd>{a.minutes_per_interruption ?? "–"}{me.role === "admin" && <a className="lc-link" href="#/setup">Change</a>}</dd></div>
          <div><dt>Medians need</dt><dd>{a.min_median ?? 3} values</dd></div>
          <div><dt>Rates need</dt><dd>{a.min_rate ?? 5} cases</dd></div>
          <div><dt>Survey groups need</dt><dd>{a.survey_min_group ?? d.surveys.min_group} answers</dd></div>
        </dl>
      </Section>

      <NextAction>{exportBtn}<a className="dp-btn ghost" href="#/home">Back to Home</a></NextAction>
    </section>
  );
}

function Score({ v }: { v: number | undefined }) {
  if (v === undefined || v === null) return <span className="lc-dim">–</span>;
  return <span className="lc-score-cell"><span className="lc-score-bar"><i style={{ width: `${(v / 5) * 100}%` }} /></span><span className="lc-mono">{v.toFixed(1)}</span></span>;
}
