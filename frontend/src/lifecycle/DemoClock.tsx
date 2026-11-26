/** The demo clock (demo mode, managers and admins): "Day N" of one hire's program in the top bar, with +1 day, +1 week,
 *  jumps to the check-in days, and a reset. Advancing moves every program of the demo organisation (POST /api/demo/advance);
 *  resetting puts the clock back where the demo started (POST /api/demo/reset); the shell then reloads the page on screen.
 *  The lifecycle's today comes from GET /api/lifecycle/config (`today`, `offset_days`) when the server sends it. The same
 *  actions are palette commands. docs/specs/demo-clock-and-flags.md. */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError, errorMessage, post } from "../api";
import { toast } from "../ui";
import { useLive } from "../live";
import { forgetShared, getShared, nameOf } from "./kit";
import type { Advance, LifecycleConfig, Program } from "./types";

type Hire = { id: string; name: string; start: string; status: string };
type Detail = { members: { id: string; name: string }[]; onboardings: { id: string; new_hire_id: string; start_date: string; status: string }[] };

const FOCUS_KEY = "ww-demo-focus";
const dayNum = (iso: string) => Math.round(Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 86400000);

export type Clock = {
  ready: boolean; hires: Hire[]; focus: Hire | null; setFocus: (id: string) => void;
  day: number | null; duration: number; today: number | null; dayOf: (h: Hire) => number | null;
  busy: boolean; advance: (days: number) => Promise<void>; jump: (to: number) => Promise<void>;
  /** days the demo has moved (null: the server does not say); reset puts it back to 0 */
  offset: number | null; reset: () => Promise<void>;
};

/** The project's hires (managers: the palette lists them), and in demo mode the clock's state and actions, shared by the
 *  top-bar control and the palette. */
export function useDemoClock(projectId: string | null, enabled: boolean, demo: boolean, onAdvanced: () => void): Clock {
  const [hires, setHires] = useState<Hire[]>([]);
  const [focusId, setFocusId] = useState<string | null>(() => { try { return localStorage.getItem(FOCUS_KEY); } catch { return null; } });
  const [today, setToday] = useState<number | null>(null); // the lifecycle's today, as a day number
  const [duration, setDuration] = useState(90);
  const [offset, setOffset] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const after = useRef(onAdvanced);
  after.current = onAdvanced;

  const load = useCallback(async () => {
    if (!enabled || !projectId) return;
    try {
      const d = await getShared<Detail>(`/projects/${encodeURIComponent(projectId)}`);
      const list = d.onboardings.map((o) => ({ id: o.id, start: o.start_date, status: o.status,
        name: nameOf(d.members.find((m) => m.id === o.new_hire_id)?.name ?? "A hire") }));
      setHires(list);
      if (!demo) return;
      const active = list.filter((h) => h.status === "active");
      const pick = list.find((h) => h.id === focusId) ?? active[0] ?? list[0];
      if (!pick) return;
      const [p, cfg] = await Promise.all([
        getShared<Program>(`/onboardings/${encodeURIComponent(pick.id)}/program`, 0),
        getShared<LifecycleConfig>("/lifecycle/config", 0).catch(() => null),
      ]);
      // the server's today when it sends one; else the hire's day counted back from their start
      setToday(cfg?.today ? dayNum(cfg.today) : dayNum(pick.start) + p.profile.day);
      setOffset(typeof cfg?.offset_days === "number" ? cfg.offset_days : null);
      setDuration(p.profile.duration_days);
    } catch { /* the control stays hidden */ }
  }, [enabled, demo, projectId, focusId]);
  useEffect(() => { void load(); }, [load]);

  const focus = hires.find((h) => h.id === focusId) ?? hires.find((h) => h.status === "active") ?? hires[0] ?? null;
  const dayOf = (h: Hire) => (today === null ? null : today - dayNum(h.start));
  const day = focus ? dayOf(focus) : null;

  const advance = useCallback(async (days: number) => {
    if (busy || days < 1) return;
    setBusy(true);
    try {
      let left = days;
      let out: Advance | null = null;
      while (left > 0) { const step = Math.min(90, left); out = await post<Advance>("/demo/advance", { days: step }); left -= step; }
      forgetShared(); // every page reads again at the new date
      if (out) { setToday(dayNum(out.today)); setOffset(out.offset_days); }
      toast(days === 1 ? "Moved one day forward" : days === 7 ? "Moved one week forward" : `Moved ${days} days forward`);
      after.current();
      void load();
    } catch (e) { toast(errorMessage(e), "error"); }
    finally { setBusy(false); }
  }, [busy, load]);
  const jump = useCallback(async (to: number) => { if (day !== null && to > day) await advance(to - day); }, [advance, day]);
  const reset = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      const out = await post<Partial<Advance>>("/demo/reset", {});
      forgetShared();
      if (out?.today) setToday(dayNum(out.today));
      setOffset(typeof out?.offset_days === "number" ? out.offset_days : 0);
      toast("The demo clock is back at day 0");
      after.current();
      void load();
    } catch (e) {
      toast(e instanceof ApiError && e.status === 404 ? "Reset isn't available on this server yet." : errorMessage(e), "error");
    } finally { setBusy(false); }
  }, [busy, load]);
  const setFocus = (id: string) => { setFocusId(id); try { localStorage.setItem(FOCUS_KEY, id); } catch { /* storage blocked */ } };
  useLive(["lifecycle", "people"], () => { void load(); }); // a new enrolment, a completed program
  return { ready: enabled && demo && !!focus && today !== null, hires, focus, setFocus, day, duration, today, dayOf, busy, advance, jump, offset, reset };
}

export default function DemoClock({ clock }: { clock: Clock }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); button.current?.focus(); } };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (!clock.ready || clock.day === null || !clock.focus) return null;
  const { day, duration, focus } = clock;
  const marks = [30, 60, 90].map((d) => Math.round((d * duration) / 90));
  return (
    <div className="demo-clock" ref={root}>
      <button ref={button} type="button" className="demo-clock-btn" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}
              title={`Demo clock: ${focus.name}, day ${day}`}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
        <span>Day {day}</span>
      </button>
      {open && (
        <div id={id} className="demo-clock-pop" role="dialog" aria-label="Demo clock">
          <div className="demo-clock-top">
            <p className="demo-clock-k">Demo clock</p>
            <label className="sr-only" htmlFor={`${id}-hire`}>Hire</label>
            <select id={`${id}-hire`} value={focus.id} onChange={(e) => clock.setFocus(e.target.value)}>
              {clock.hires.map((h) => <option key={h.id} value={h.id}>{h.name}{h.status !== "active" ? ` (${h.status})` : ""}</option>)}
            </select>
          </div>
          <p className="demo-clock-day"><strong>Day {day}</strong><span>of {duration}</span></p>
          <div className="demo-clock-bar" aria-hidden="true"><i style={{ width: `${Math.min(100, (day / duration) * 100)}%` }} />{marks.map((m) => <b key={m} style={{ left: `${Math.min(100, (m / duration) * 100)}%` }} />)}</div>
          <div className="demo-clock-grid g2">
            <button type="button" className="dp-btn small" disabled={clock.busy} onClick={() => clock.advance(1)}>+1 day</button>
            <button type="button" className="dp-btn small" disabled={clock.busy} onClick={() => clock.advance(7)}>+1 week</button>
          </div>
          <div className="demo-clock-jump">
            <span className="demo-clock-k">Jump to</span>
            <div className="demo-clock-grid g3">
              {marks.map((m) => <button key={m} type="button" className="dp-btn small" disabled={clock.busy || m <= day} onClick={() => clock.jump(m)}>Day {m}</button>)}
            </div>
          </div>
          <div className="demo-clock-foot">
            <p className="demo-clock-note">Moves every program at once.</p>
            {clock.offset !== null && ( // a server that reports the offset can reset it
              <button type="button" className="dp-btn ghost small" disabled={clock.busy || clock.offset === 0} onClick={() => clock.reset()}
                      title="Back to where the demo started: no days moved">Reset to day 0</button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
