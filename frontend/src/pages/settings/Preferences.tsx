import { FormEvent, useEffect, useState } from "react";
import { api, errorMessage, patch } from "../../api";
import { Skeleton } from "../../fx";
import { Msg, Note, toast } from "../../ui";
import { SectionCard } from "./kit";

/** GET/PATCH /api/experts/preferences: how much routed questions may interrupt this person (docs/specs/answer-routing.md). */
type Prefs = { max_interruptions_per_day: number; office_hours: { days?: string[]; start?: string; end?: string }; digest: boolean };

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];

/** Experts and managers: interruptions a day, office hours and the daily digest. */
export default function Preferences() {
  const [saved, setSaved] = useState<Prefs | null>(null);
  const [max, setMax] = useState(3);
  const [days, setDays] = useState<string[]>([]);
  const [start, setStart] = useState("15:00");
  const [end, setEnd] = useState("16:00");
  const [digest, setDigest] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  function take(p: Prefs) {
    setSaved(p);
    setMax(p.max_interruptions_per_day);
    setDays(p.office_hours?.days ?? []);
    setStart(p.office_hours?.start ?? "15:00");
    setEnd(p.office_hours?.end ?? "16:00");
    setDigest(p.digest);
  }
  useEffect(() => { api<Prefs>("/experts/preferences").then(take).catch((e) => setLoadError(errorMessage(e))); }, []);

  const hours = days.length ? { days: DAYS.filter((d) => days.includes(d)), start, end } : {};
  const dirty = !!saved && (max !== saved.max_interruptions_per_day || digest !== saved.digest
    || JSON.stringify(hours) !== JSON.stringify(saved.office_hours?.days?.length ? saved.office_hours : {}));
  const badTime = days.length > 0 && start >= end;

  async function save(e: FormEvent) {
    e.preventDefault();
    if (busy || badTime) return;
    setBusy(true); setMsg(null);
    try {
      take(await patch<Prefs>("/experts/preferences", { max_interruptions_per_day: max, office_hours: hours, digest }));
      toast("Preferences saved");
    } catch (err) { setMsg({ ok: false, text: errorMessage(err) }); } finally { setBusy(false); }
  }

  if (loadError) return <SectionCard id="prefs-h" title="Preferences"><p className="error" role="alert">{loadError}</p></SectionCard>;
  if (!saved) return <div className="set-card"><Skeleton lines={4} height={16} /></div>;
  return (
    <SectionCard id="prefs-h" title="Preferences" description="When questions may reach you.">
      <form className="set-prefs" onSubmit={save}>
        <div className="set-pref">
          <div className="set-pref-text">
            <label htmlFor="pref-max" className="set-pref-label">Interruptions a day</label>
            <p className="set-pref-help">After this, questions that are not urgent wait.</p>
          </div>
          <div className="set-stepper">
            <button type="button" aria-label="Fewer" onClick={() => setMax((n) => Math.max(0, n - 1))} disabled={max <= 0}>−</button>
            <input id="pref-max" type="number" min={0} max={50} inputMode="numeric" value={max}
                   onChange={(e) => setMax(Math.max(0, Math.min(50, Number(e.target.value) || 0)))} />
            <button type="button" aria-label="More" onClick={() => setMax((n) => Math.min(50, n + 1))} disabled={max >= 50}>+</button>
          </div>
        </div>

        <fieldset className="set-pref">
          <div className="set-pref-text">
            <legend className="set-pref-label">Office hours</legend>
            <p className="set-pref-help">{days.length ? "Questions are best sent then." : "None set."}</p>
          </div>
          <div className="set-hours">
            <div className="set-days" role="group" aria-label="Days">
              {DAYS.map((d) => (
                <button key={d} type="button" aria-pressed={days.includes(d)}
                        onClick={() => setDays((xs) => (xs.includes(d) ? xs.filter((x) => x !== d) : [...xs, d]))}>{d}</button>
              ))}
            </div>
            {days.length > 0 && (
              <div className="set-times">
                <label><span className="sr-only">From</span><input type="time" value={start} onChange={(e) => setStart(e.target.value)} /></label>
                <span aria-hidden="true">to</span>
                <label><span className="sr-only">Until</span><input type="time" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
              </div>
            )}
          </div>
        </fieldset>
        {badTime && <p className="error" role="alert">Office hours must end after they start.</p>}

        <div className="set-pref">
          <div className="set-pref-text">
            <span className="set-pref-label" id="pref-digest-l">Daily digest</span>
            <p className="set-pref-help">Collect waiting questions into one summary.</p>
          </div>
          <button type="button" role="switch" aria-checked={digest} aria-labelledby="pref-digest-l" className="set-switch" onClick={() => setDigest((x) => !x)}>
            <span aria-hidden="true" />
          </button>
        </div>

        <div className="set-pref-foot">
          <button type="submit" className="set-primary" disabled={busy || !dirty || badTime}>{busy ? <><span className="en-spin" aria-hidden="true" />Saving…</> : "Save"}</button>
        </div>
        <Note msg={msg} />
      </form>
    </SectionCard>
  );
}
