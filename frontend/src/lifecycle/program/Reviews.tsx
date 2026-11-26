/** Program sections: the 30/60/90 check-ins (each side writes its own half), the anonymous survey, and completion. */
import { FormEvent, useState } from "react";
import { ApiError, errorMessage, post } from "../../api";
import { toast } from "../../ui";
import { Glyph } from "../../dataui";
import { How, plural, Quiet, shortDate, titleCase, useAction, useApi } from "../kit";
import { Actions, SectionProps } from "./shared";
import WhyThis from "../WhyThis";
import { explainOf } from "../agentModel";
import type { Checkins, CheckinReview, Completion, HireHome, Question } from "../types";

/* ---------------------------------------------------------------- check-ins */

function Answers({ r, qs }: { r: CheckinReview; qs: Question[] }) {
  const shown = qs.filter((q) => r.answers[q.id] !== undefined && r.answers[q.id] !== "");
  if (!shown.length) return <p className="lc-dim">Written, no answers.</p>;
  return (
    <dl className="lc-answers">
      {shown.map((q) => (
        <div key={q.id}><dt>{q.text}</dt><dd>{q.scale ? <ScaleShown v={Number(r.answers[q.id])} /> : String(r.answers[q.id])}</dd></div>
      ))}
    </dl>
  );
}

const ScaleShown = ({ v }: { v: number }) => (
  <span className="lc-scale-shown" aria-label={`${v} of 5`}>{[1, 2, 3, 4, 5].map((n) => <i key={n} className={n <= v ? "on" : ""} />)}<span>{v}/5</span></span>
);

function Scale({ name, value, onChange, label }: { name: string; value: number | undefined; onChange: (v: number) => void; label: string }) {
  return (
    <div className="lc-scale" role="radiogroup" aria-label={label}>
      {[1, 2, 3, 4, 5].map((n) => (
        <label key={n} className={value === n ? "on" : ""}>
          <input type="radio" name={name} value={n} checked={value === n} onChange={() => onChange(n)} />
          <span>{n}</span>
        </label>
      ))}
    </div>
  );
}

function CheckinForm({ id, day, qs, onDone, onCancel }: { id: string; day: number; qs: Question[]; onDone: () => void; onCancel: () => void }) {
  const [answers, setAnswers] = useState<Record<string, string | number>>({});
  const act = useAction();
  const ready = qs.some((q) => answers[q.id] !== undefined && answers[q.id] !== "");
  function submit(e: FormEvent) {
    e.preventDefault();
    act.run("save", () => post(`/onboardings/${encodeURIComponent(id)}/checkins`, { day, answers })).then((r) => { if (r) { toast(`Day-${day} check-in saved`); onDone(); } });
  }
  return (
    <form className="lc-form lc-checkin-form" onSubmit={submit}>
      {qs.map((q) => (
        <div key={q.id} className="field">
          <span>{q.text}</span>
          {q.scale ? <Scale name={`${id}-${day}-${q.id}`} label={q.text} value={answers[q.id] as number | undefined} onChange={(v) => setAnswers((a) => ({ ...a, [q.id]: v }))} />
            : <textarea rows={2} maxLength={2000} value={(answers[q.id] as string) ?? ""} onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))} />}
        </div>
      ))}
      <div className="lc-form-foot">
        <button type="button" className="dp-btn ghost" onClick={onCancel}>Cancel</button>
        <button type="submit" className="dp-btn primary" disabled={!ready || !!act.busy}>{act.busy ? "Saving…" : "Save my half"}</button>
      </div>
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </form>
  );
}

export function CheckinsSection({ id, p, rel }: SectionProps) {
  const data = useApi<{ checkins: Checkins }>(`/onboardings/${encodeURIComponent(id)}/checkins`);
  const [writing, setWriting] = useState<number | null>(null);
  if (data.error) return <p className="error" role="alert">Check-ins could not be loaded.</p>;
  if (!data.data) return <div className="lc-skel-line" aria-busy="true" />;
  const c = data.data.checkins;
  const day = p.profile.day;
  const mine = rel === "hire" ? "hire" : "manager";
  const dueOf = (d: number) => p.milestones.find((m) => m.key.startsWith("checkin_") && m.due_day === d)?.due_date
    ?? c.due.find((x) => x.day === d)?.due_date ?? null;
  return (
    <div className="lc-stack">
      {c.due.length > 0 && (
        <ul className="lc-due">
          {c.due.map((d) => (
            <li key={d.day + d.author_role} className={d.overdue ? "late" : ""}>
              <Glyph name={d.overdue ? "alert" : "clock"} size={13} />
              Day {d.day} · {d.author_role === "hire" ? `${rel === "hire" ? "your" : "the hire's"} half` : `${rel === "manager" ? "your" : "the manager's"} half`} {d.overdue ? "is overdue" : "is due"}
            </li>
          ))}
        </ul>
      )}
      <div className="lc-checkins">
        {c.days.map((d) => {
          const reached = d <= day;
          const halves = (["hire", "manager"] as const).map((role) => ({ role, r: c.reviews.find((x) => x.day === d && x.author_role === role) }));
          const due = dueOf(d);
          return (
            <section key={d} className={`lc-checkin${reached ? "" : " later"}`} aria-label={`Day ${d} check-in`}>
              <header><strong>Day {d}</strong><span className="lc-dim">{due ? shortDate(due) : ""}{!reached ? ` · opens in ${d - day} days` : ""}</span></header>
              {!reached && halves.every((h) => !h.r) ? null : writing === d ? (
                <CheckinForm id={id} day={d} qs={c.questions[mine]} onCancel={() => setWriting(null)} onDone={() => { setWriting(null); data.reload(); }} />
              ) : (
                <div className="lc-halves">
                  {halves.map(({ role, r }) => (
                    <div key={role} className="lc-half">
                      <p className="lc-label">{role === "hire" ? (rel === "hire" ? "You" : "Hire") : rel === "manager" ? "You" : "Manager"}</p>
                      {r ? <Answers r={r} qs={c.questions[role]} />
                        : role === mine && reached ? <button type="button" className="dp-btn small" onClick={() => setWriting(d)}>Write your half</button>
                        : <p className="lc-dim">{reached ? "Not written yet" : "Opens on the day"}</p>}
                    </div>
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- the anonymous survey */

/** The survey day waiting for the hire, from their home's focus list (GET /home: "survey:<day>"). */
export function useSurveyDue(onboardingId: string | null) {
  const home = useApi<HireHome>(onboardingId ? "/home" : null);
  const key = home.data?.focus.find((f) => f.key.startsWith("survey:"))?.key;
  return key ? Number(key.split(":")[1]) || null : null;
}

const SURVEY: Question[] = [
  { id: "clarity", text: "I knew what was expected of me." },
  { id: "support", text: "I got help when I was stuck." },
  { id: "docs", text: "The documentation and KT sessions answered my questions." },
  { id: "tools", text: "My access and tools were ready when I needed them." },
  { id: "belonging", text: "I feel part of the team." },
];

export function SurveySection({ id, day }: SectionProps & { day: number }) {
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [sent, setSent] = useState<"sent" | "already" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await post(`/surveys`, { onboarding_id: id, day, answers }); setSent("sent"); toast("Sent anonymously"); }
    catch (err) { if (err instanceof ApiError && err.status === 409) setSent("already"); else setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  if (sent) return <Quiet>{sent === "already" ? "You already answered this one." : "Thank you. Your answers are anonymous."}</Quiet>;
  const done = SURVEY.every((q) => answers[q.id]);
  return (
    <form className="lc-form" onSubmit={submit}>
      <div className="lc-inline"><p className="lc-dim">1 disagree · 5 agree</p>
        <How label="Anonymous" title="Who sees your answers" text="No name is stored with your answers. Managers see only averages of five or more people." /></div>
      {SURVEY.map((q) => (
        <div key={q.id} className="field lc-survey-q">
          <span>{q.text}</span>
          <Scale name={`survey-${q.id}`} label={q.text} value={answers[q.id]} onChange={(v) => setAnswers((a) => ({ ...a, [q.id]: v }))} />
        </div>
      ))}
      <div className="lc-form-foot"><button type="submit" className="dp-btn primary" disabled={!done || busy}>{busy ? "Sending…" : "Send anonymously"}</button></div>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}

/* ---------------------------------------------------------------- completion */

function evidenceOf(e: Record<string, unknown>) {
  const parts = Object.entries(e).filter(([, v]) => Array.isArray(v) ? v.length : v !== null && v !== undefined)
    .map(([k, v]) => `${titleCase(k)}: ${Array.isArray(v) ? v.join(", ") : String(v)}`);
  return parts.length ? parts : null;
}

export function CompletionSection({ id, rel, reload }: SectionProps) {
  const data = useApi<{ completion: Completion }>(`/onboardings/${encodeURIComponent(id)}/completion`);
  const act = useAction();
  const [mode, setMode] = useState<"complete" | "extend" | null>(null);
  const [note, setNote] = useState("");
  const [days, setDays] = useState(14);
  if (data.error) return <p className="error" role="alert">The completion review could not be loaded.</p>;
  if (!data.data) return <div className="lc-skel-line" aria-busy="true" />;
  const c = data.data.completion;
  const met = c.criteria.filter((x) => x.met).length;
  function decide(e: FormEvent) {
    e.preventDefault();
    if (!mode) return;
    act.run(mode, () => post(`/onboardings/${encodeURIComponent(id)}/completion`, { decision: mode, note, extend_days: mode === "extend" ? days : 0 })).then((r) => {
      if (r) { toast(mode === "complete" ? "Program signed off" : `Extended by ${days} days`); setMode(null); setNote(""); data.reload(); reload(); }
    });
  }
  return (
    <div className="lc-stack">
      <div className="lc-inline">
        <p className="lc-strong">{c.completed_at ? `Completed ${shortDate(c.completed_at)}` : c.ready ? "Ready to sign off" : `${met} of ${c.criteria.length} criteria met`}</p>
        <span className="lc-dim">Day {c.day} of {c.duration_days}</span>
      </div>
      <ul className="lc-list lc-criteria-list">
        {c.criteria.map((x) => {
          const ev = evidenceOf(x.evidence);
          return (
            <li key={x.key} className={`lc-li${x.met ? " met" : ""}`}>
              <span className="lc-li-icon"><span className={`lc-check${x.met ? " on" : ""}`} aria-hidden="true" /></span>
              <span className="lc-li-main">{x.label}<span className="lc-li-sub">{x.why}</span></span>
              {ev && <span className="lc-li-actions"><How label="Evidence" text={<>{ev.map((l) => <span key={l} className="lc-ev">{l}</span>)}</>} align="end" /></span>}
            </li>
          );
        })}
      </ul>
      {rel === "manager" && !c.completed_at && !c.signed_off && c.draft && (
        // the Planner's draft of the decision: the manager reads it, may start from it, and decides
        <div className="lc-callout lc-draft">
          <div className="lc-draft-text">
            <p className="lc-strong">{c.draft.decision === "complete" ? "Recommends signing off" : `Recommends extending${c.draft.extend_days ? ` by ${plural(c.draft.extend_days, "day")}` : ""}`}</p>
            {c.draft.note && <p className="lc-dim">{c.draft.note}</p>}
          </div>
          <span className="lc-inline">
            <WhyThis explain={explainOf(c)} />
            {!mode && (
              <button type="button" className="dp-btn small" onClick={() => {
                setMode(c.draft!.decision === "complete" ? "complete" : "extend");
                setNote(c.draft!.note ?? "");
                if (c.draft!.extend_days) setDays(c.draft!.extend_days);
              }}>Start from this</button>
            )}
          </span>
        </div>
      )}
      {c.signed_off && (
        <div className="lc-callout">
          <p><strong>{c.signed_off.decision === "complete" ? "Signed off" : `Extended${c.signed_off.extend_days ? ` by ${c.signed_off.extend_days} days` : ""}`}</strong>
            {c.signed_off.at ? ` · ${shortDate(c.signed_off.at)}` : ""}{c.signed_off.note ? ` · ${c.signed_off.note}` : ""}</p>
          {c.signed_off.unmet && c.signed_off.unmet.length > 0 && <How label="Unmet" text={c.signed_off.unmet.join(", ")} align="end" />}
        </div>
      )}
      {rel === "manager" && !c.completed_at && (mode ? (
        <form className="lc-form" onSubmit={decide}>
          {mode === "extend" && (
            <label className="field lc-days"><span>Extend by</span>
              <span className="lc-inline"><input type="number" min={1} max={180} value={days} onChange={(e) => setDays(Math.max(1, Math.min(180, Number(e.target.value) || 1)))} /><span className="lc-dim">days</span></span>
            </label>
          )}
          <label className="field"><span>Note {mode === "complete" && met < c.criteria.length ? "(the unmet criteria are recorded)" : "(optional)"}</span>
            <textarea rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} /></label>
          <div className="lc-form-foot">
            <button type="button" className="dp-btn ghost" onClick={() => setMode(null)}>Cancel</button>
            <button type="submit" className="dp-btn primary" disabled={!!act.busy}>{mode === "complete" ? "Sign off" : `Extend by ${plural(days, "day")}`}</button>
          </div>
          {act.error && <p className="error" role="alert">{act.error}</p>}
        </form>
      ) : (
        <Actions>
          <button type="button" className="dp-btn primary" onClick={() => setMode("complete")}>Sign off</button>
          <button type="button" className="dp-btn" onClick={() => setMode("extend")}>Extend</button>
        </Actions>
      ))}
    </div>
  );
}
