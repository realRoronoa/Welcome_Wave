/** #/setup (admins): the organisation's setup as resumable steps (done, skipped, to do) that can be completed, skipped or
 *  reopened, and the one assumption the reports rely on: minutes per avoided interruption. GET/PATCH /api/setup.
 *  Teams are added and renamed here too (the step is done once the project has one). */
import { FormEvent, useEffect, useState } from "react";
import { patch } from "../api";
import { toast } from "../ui";
import { Glyph } from "../dataui";
import { reducedMotion } from "../fx";
import { useSession } from "../session";
import Teams from "../pages/settings/Teams";
import { How, LcHead, LcSkeleton, LoadError, NextAction, Progress, useAction, useApi } from "./kit";
import type { SetupStep } from "./types";

type SetupData = { steps: SetupStep[]; minutes_per_interruption: number };

/** Where each step is done in the app. */
const WHERE: Record<string, { href: string; label: string }> = {
  dpa: { href: "#/settings?tab=organisation", label: "Read and accept" }, // Settings, Organisation, Legal
  repository: { href: "#/settings?tab=repository", label: "Repository" },
  teams: { href: "#setup-teams", label: "Add teams" }, // on this page, below the steps
  templates: { href: "#/templates", label: "Templates" },
  people: { href: "#/settings?tab=people", label: "Invite" },
  first_enrolment: { href: "#/enrol", label: "Enrol" },
  estimate: { href: "#estimate", label: "Below" },
};

/** Bring the teams card into view (it is on this page, so not a route change). */
function toTeams(e: { preventDefault: () => void }) {
  e.preventDefault();
  const el = document.getElementById("setup-teams");
  el?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  el?.querySelector<HTMLInputElement>(".set-team-add input")?.focus({ preventScroll: true });
}

export default function Setup() {
  const { project } = useSession();
  const data = useApi<SetupData>("/setup");
  const act = useAction();
  const [minutes, setMinutes] = useState("");
  useEffect(() => { if (data.data) setMinutes(String(data.data.minutes_per_interruption)); }, [data.data?.minutes_per_interruption]); // eslint-disable-line react-hooks/exhaustive-deps

  if (data.error && !data.data) return <section className="lc"><LcHead title="Setup" /><LoadError error={data.error} retry={data.reload} /></section>;
  if (!data.data) return <section className="lc"><LcSkeleton rows={6} /></section>;
  const steps = data.data.steps;
  const settled = steps.filter((s) => s.status !== "todo").length;
  const next = steps.find((s) => s.status === "todo");

  const step = (s: SetupStep, action: "complete" | "skip" | "reopen") =>
    act.run(s.key + action, () => patch<SetupData>("/setup", { step: s.key, action })).then((r) => {
      if (r) { data.set(() => r); toast(action === "complete" ? "Marked done" : action === "skip" ? "Skipped" : "Reopened"); }
    });
  function saveMinutes(e: FormEvent) {
    e.preventDefault();
    const n = Math.round(Number(minutes));
    if (!(n >= 1 && n <= 240)) return act.setError("Use a whole number of minutes from 1 to 240.");
    act.run("minutes", () => patch<SetupData>("/setup", { minutes_per_interruption: n })).then((r) => { if (r) { data.set(() => r); toast("Estimate saved"); } });
  }

  return (
    <section className="lc lc-narrow">
      <LcHead title="Setup" sub={settled === steps.length ? "Everything is set up." : `${settled} of ${steps.length} steps settled. Pick up where you left off.`} />
      <div className="lc-setup-progress"><Progress done={settled} total={steps.length} label={`${settled} of ${steps.length} steps settled`} /></div>
      <ol className="lc-steps">
        {steps.map((s, i) => (
          <li key={s.key} className={`lc-step st-${s.status}${next?.key === s.key ? " is-next" : ""}`}>
            <span className="lc-step-n" aria-hidden="true">{s.status === "done" ? <Glyph name="check" size={13} /> : i + 1}</span>
            <div className="lc-step-text">
              <p className="lc-step-title">{s.title}</p>
              <p className="lc-step-why">{s.why}</p>
            </div>
            <div className="lc-step-side">
              <span className="lc-step-state">{s.status === "done" ? "Done" : s.status === "skipped" ? "Skipped" : "To do"}</span>
              <How text={s.how} align="end" />
              {s.status === "todo" ? <>
                {WHERE[s.key] && s.key !== "estimate" && <a className="dp-btn small" href={WHERE[s.key].href}
                  onClick={s.key === "teams" ? toTeams : undefined}>{WHERE[s.key].label}</a>}
                {/* the agreement is done when it is accepted, never by hand (the API refuses it too) */}
                {s.key !== "dpa" && <button type="button" className="dp-btn small" disabled={!!act.busy} onClick={() => step(s, "complete")}>Done</button>}
                <button type="button" className="dp-btn small ghost" disabled={!!act.busy} onClick={() => step(s, "skip")}>Skip</button>
              </> : s.how !== "Checked from your data." && (
                <button type="button" className="dp-btn small ghost" disabled={!!act.busy} onClick={() => step(s, "reopen")}>Reopen</button>
              )}
            </div>
          </li>
        ))}
      </ol>

      <section id="setup-teams" className="lc-card lc-setup-teams" aria-labelledby="setup-teams-h">
        <p className="lc-strong" id="setup-teams-h">Teams in {project.name}</p>
        <p className="lc-dim">Hires are enrolled into a team; reports and the anonymous survey are grouped by team.</p>
        <Teams projectId={project.id} onChange={(t) => { if (t.length && steps.find((x) => x.key === "teams")?.status === "todo") data.reload(); }} />
      </section>

      <form id="estimate" className="lc-card lc-estimate" onSubmit={saveMinutes}>
        <div>
          <p className="lc-strong">Minutes per avoided interruption</p>
          <p className="lc-dim">Used for the senior-time estimate in Reports.</p>
        </div>
        <div className="lc-inline">
          <label className="sr-only" htmlFor="setup-minutes">Minutes</label>
          <input id="setup-minutes" type="number" min={1} max={240} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
          <span className="lc-dim">min</span>
          <button type="submit" className="dp-btn" disabled={!!act.busy || String(data.data.minutes_per_interruption) === minutes}>Save</button>
        </div>
      </form>
      {act.error && <p className="error" role="alert">{act.error}</p>}

      <NextAction>
        {next && WHERE[next.key] && next.key !== "estimate"
          ? <a className="dp-btn primary" href={WHERE[next.key].href} onClick={next.key === "teams" ? toTeams : undefined}>Next: {next.title}<Glyph name="arrow" size={14} /></a>
          : <a className="dp-btn primary" href="#/home">Back to Home<Glyph name="arrow" size={14} /></a>}
      </NextAction>
    </section>
  );
}
