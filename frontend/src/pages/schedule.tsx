/** Scheduling KT sessions (docs/specs/scheduling.md): the schedule form shared by KT sessions and the KT planner, the
 *  data it needs (who can be invited, what is connected, whose Calendly link exists), and small session helpers. */
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage, User } from "../api";
import { useSession } from "../session";
import { Avatar, Glyph } from "../dataui";
import "../styles/schedule.css";

export type Attendee = { email: string; name: string; user_id: string | null };
export type Provider = "google" | "calendly" | "manual";
export type KtSession = {
  id: string; title: string; status: string; started_at: string | null; summary: string | null; duration_s: number | null;
  created_by?: string | null; kt_key?: string | null;
  provider?: Provider; join_url?: string | null; calendar_event_id?: string | null;
  starts_at?: string | null; ends_at?: string | null; attendees?: Attendee[];
  /** Who confirmed that everyone in the session was told it is recorded and agreed; null until someone has. */
  recording_consent?: { by: string | null; by_name: string | null; at: string; notice_version: string } | null;
};
export type Integrations = {
  google: { available: boolean; connected: boolean; account: string | null; connected_at: string | null; connected_by: string | null; can_manage: boolean };
  calendly: { webhook: boolean; webhook_url: string; link: string | null };
  default_provider: "google" | "manual";
};
type Links = { links: Record<string, string>; prefill: Record<string, string>; webhook: boolean };
export type Scheduling = { integrations: Integrations | null; links: Links | null; members: User[]; error: string | null; reload: () => void };

/** What the schedule form needs: connections, Calendly links and the project's people. */
export function useScheduling(projectId: string): Scheduling {
  const [integrations, setIntegrations] = useState<Integrations | null>(null);
  const [links, setLinks] = useState<Links | null>(null);
  const [members, setMembers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    const id = encodeURIComponent(projectId);
    Promise.all([api<Integrations>("/integrations"), api<Links>(`/scheduling/links?project_id=${id}`),
      api<{ members: User[] }>(`/projects/${id}`)])
      .then(([i, l, p]) => { setIntegrations(i); setLinks(l); setMembers(p.members); setError(null); })
      .catch((e) => setError(errorMessage(e)));
  }, [projectId]);
  useEffect(reload, [reload]);
  return { integrations, links, members, error, reload };
}

/** A person's Calendly link, prefilled with who books and tagged for the webhook (project, and the planner session). */
export function calendlyUrl(link: string, prefill: Record<string, string>, ktKey?: string | null) {
  const q = new URLSearchParams(prefill);
  if (ktKey) q.set("utm_content", ktKey);
  return `${link}?${q}`;
}

export const firstName = (name: string) => name.replace(/\(.*?\)/g, " ").trim().split(/\s+/)[0] || name;
const pad = (n: number) => String(n).padStart(2, "0");
const localDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

/** "Tue 7 Jan · 10:00–10:45" in the viewer's time zone; "No time set" for a session held whenever it suits. */
export function sessionWhen(m: KtSession) {
  if (!m.starts_at) return "No time set";
  const s = new Date(m.starts_at);
  const day = s.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  return `${day} · ${clock(m.starts_at)}${m.ends_at ? `–${clock(m.ends_at)}` : ""}`;
}

export const PROVIDER: Record<Provider, string> = { google: "Google Meet", calendly: "Calendly", manual: "Elsewhere" };

/** Where a session is held, as a short label: the provider, or the host of a link it was given. */
export function whereLabel(m: KtSession) {
  if (m.provider === "manual" && m.join_url) { try { return new URL(m.join_url).hostname.replace(/^www\./, ""); } catch { /* not a URL */ } }
  return PROVIDER[m.provider ?? "manual"];
}

/** The join link: "Join on Google Meet" for a Meet link, "Join" for any other. Opens in a new tab. */
export function JoinLink({ m, small }: { m: KtSession; small?: boolean }) {
  if (!m.join_url) return null;
  const meet = m.join_url.startsWith("https://meet.google.com/");
  return (
    <a className={`dp-btn primary${small ? " small" : ""} sc-join`} href={m.join_url} target="_blank" rel="noopener noreferrer"
       aria-label={`${meet ? "Join on Google Meet" : "Join"}: ${m.title} (opens in a new tab)`}>
      <Glyph name="video" size={small ? 13 : 15} />{meet ? "Join on Google Meet" : "Join"}<Glyph name="external" size={12} className="sc-ext" />
    </a>
  );
}

export type ScheduleValues = { title: string; starts_at: string; duration_min: number; attendees: string[]; provider: "google" | "manual"; join_url: string | null };

type DialogProps = {
  open: boolean;
  onClose: () => void;
  onSubmit: (v: ScheduleValues) => Promise<void>;
  data: Scheduling;
  heading: string;
  title?: string; // fixed: the planner's session, or a session that only needs its time
  duration?: number;
  presenterId?: string | null; // invited and locked (the planner's presenter)
  ktKey?: string | null; // tags a Calendly booking with the planner session
  submitLabel?: string;
};

const DURATIONS = [15, 30, 45, 60, 90, 120];
const lengthLabel = (x: number) => (x < 60 ? `${x} min` : x % 60 ? `${Math.floor(x / 60)} h ${x % 60}` : `${x / 60} h`);

/** The schedule form, in a modal dialog: title, date and time, length, who is invited, and where it is held. */
export function ScheduleDialog({ open, onClose, onSubmit, data, heading, title: fixedTitle, duration = 30, presenterId, ktKey, submitLabel }: DialogProps) {
  const { me } = useSession();
  const ref = useRef<HTMLDialogElement>(null);
  const google = data.integrations?.google;
  const others = useMemo(() => data.members.filter((u) => u.id !== me.id), [data.members, me.id]);
  // whose Calendly to book: the presenter's, for a planner session; anyone's with a link otherwise
  const calendlyHosts = useMemo(() => others.filter((u) => data.links?.links[u.id] && (!presenterId || u.id === presenterId)),
                                [others, data.links, presenterId]);
  const calendlyOn = !!data.links?.webhook && calendlyHosts.length > 0;

  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [length, setLength] = useState(duration);
  const [people, setPeople] = useState<Set<string>>(new Set());
  const [where, setWhere] = useState<"google" | "calendly" | "other">("other");
  const [link, setLink] = useState("");
  const [host, setHost] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // a fresh form each time it opens: the next half hour, the presenter invited, Google when connected
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (!open) { if (d.open) d.close(); return; }
    const next = new Date(Math.ceil((Date.now() + 5 * 60_000) / 1_800_000) * 1_800_000);
    setTitle(fixedTitle ?? ""); setDate(localDate(next)); setTime(localTime(next));
    setLength(DURATIONS.includes(duration) ? duration : DURATIONS.find((x) => x >= duration) ?? 120);
    setPeople(new Set(presenterId && presenterId !== me.id ? [presenterId] : []));
    const presenterLink = presenterId && presenterId !== me.id && data.links?.links[presenterId];
    setWhere(google?.connected ? "google" : presenterLink && data.links?.webhook ? "calendly" : "other");
    setHost(presenterLink ? presenterId! : null);
    setLink(""); setError(null); setBusy(false);
    if (!d.open) d.showModal();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggle(id: string) {
    if (id === presenterId) return;
    setPeople((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (where === "calendly") {
      const url = host && data.links?.links[host];
      if (!url || !data.links) return setError("Pick who to book with.");
      window.open(calendlyUrl(url, data.links.prefill, ktKey), "_blank", "noopener");
      onClose();
      return;
    }
    const starts = new Date(`${date}T${time}`);
    if (Number.isNaN(starts.getTime())) return setError("Pick a date and time.");
    const url = link.trim();
    if (where === "other" && url && !/^https:\/\/\S+$/.test(url)) return setError("Links start with https://");
    setBusy(true);
    try {
      await onSubmit({ title: title.trim(), starts_at: starts.toISOString(), duration_min: length, attendees: [...people],
                       provider: where === "google" ? "google" : "manual", join_url: where === "other" && url ? url : null });
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const options: { id: "google" | "calendly" | "other"; label: string; glyph: "video" | "calendar" | "external"; off: string | null }[] = [
    { id: "google", label: "Google Meet", glyph: "video", off: google?.connected ? null : google?.available ? "Connect in Settings" : "Not set up" },
    { id: "calendly", label: "Calendly", glyph: "calendar", off: calendlyOn ? null : !data.links?.webhook ? "Not set up" : "No link yet" },
    { id: "other", label: "Other", glyph: "external", off: null },
  ];

  return (
    <dialog ref={ref} className="sc-dialog" aria-labelledby="sc-h" onClose={onClose}
            onClick={(e) => { if (e.target === ref.current && !busy) onClose(); }}>
      <form className="sc-form" onSubmit={submit} noValidate>
        <header className="sc-head">
          <h2 id="sc-h">{heading}</h2>
          <button type="button" className="sc-x" onClick={onClose} aria-label="Close" disabled={busy}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </header>

        <fieldset className="sc-where">
          <legend>Where</legend>
          <div className="sc-seg" role="radiogroup" aria-label="Where">
            {options.map((o) => (
              <label key={o.id} className={`sc-seg-opt${where === o.id ? " on" : ""}${o.off ? " off" : ""}`}>
                <input type="radio" name="sc-where" value={o.id} checked={where === o.id} disabled={!!o.off}
                       onChange={() => { setWhere(o.id); if (o.id === "calendly" && !host) setHost(calendlyHosts[0]?.id ?? null); }} />
                <Glyph name={o.glyph} size={15} />
                <span className="sc-seg-text"><span>{o.label}</span>{o.off && <small>{o.off}</small>}</span>
              </label>
            ))}
          </div>
        </fieldset>

        {where === "calendly" ? (
          <fieldset className="sc-people">
            <legend>Book with</legend>
            <div className="sc-chips" role="radiogroup" aria-label="Book with">
              {calendlyHosts.map((u) => (
                <label key={u.id} className={`sc-chip${host === u.id ? " on" : ""}`}>
                  <input type="radio" name="sc-host" checked={host === u.id} onChange={() => setHost(u.id)} />
                  <Avatar name={u.name} size={22} /><span>{firstName(u.name)}</span>
                </label>
              ))}
            </div>
            <p className="sc-quiet">Calendly opens in a new tab. The session appears here once booked.</p>
          </fieldset>
        ) : (
          <>
            {fixedTitle === undefined ? (
              <label className="field">
                <span>Title</span>
                <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="KT: the webhook server" maxLength={200} required autoFocus />
              </label>
            ) : <p className="sc-fixed">{fixedTitle}</p>}
            <div className="sc-when">
              <label className="field"><span>Date</span><input type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
              <label className="field"><span>Time</span><input type="time" value={time} step={300} onChange={(e) => setTime(e.target.value)} required /></label>
              <label className="field"><span>Length</span>
                <select value={length} onChange={(e) => setLength(Number(e.target.value))}>
                  {DURATIONS.map((x) => <option key={x} value={x}>{lengthLabel(x)}</option>)}
                </select>
              </label>
            </div>
            {others.length > 0 && (
              <fieldset className="sc-people">
                <legend>Invite</legend>
                <div className="sc-chips">
                  {others.map((u) => {
                    const on = people.has(u.id), locked = u.id === presenterId;
                    return (
                      <button key={u.id} type="button" className={`sc-chip${on ? " on" : ""}${locked ? " locked" : ""}`} aria-pressed={on}
                              onClick={() => toggle(u.id)} aria-disabled={locked || undefined} title={locked ? "Presents this session" : u.email}>
                        <Avatar name={u.name} size={22} /><span>{firstName(u.name)}</span>
                        {on && <Glyph name="check" size={12} className="sc-chip-tick" />}
                      </button>
                    );
                  })}
                </div>
              </fieldset>
            )}
            {where === "other" && (
              <label className="field">
                <span>Link <em>optional</em></span>
                <input type="url" inputMode="url" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://" maxLength={500} />
              </label>
            )}
          </>
        )}

        <p className="sc-recorded">
          <Glyph name="video" size={14} />
          <span>
            {where === "google" ? "The invitation says this session will be recorded and links the recording notice."
              : "Planning to record? Tell everyone before the session starts."}{" "}
            A recording is added only after someone confirms that everyone agreed.{" "}
            <a href="/recording" target="_blank" rel="noopener">Recording notice<span className="sr-only"> (opens in a new tab)</span></a>
          </span>
        </p>
        {error && <p className="error" role="alert">{error}</p>}
        <footer className="sc-foot">
          <button type="button" className="dp-btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="dp-btn primary" disabled={busy || (where !== "calendly" && fixedTitle === undefined && !title.trim())}>
            {busy ? <><span className="sc-spin" aria-hidden="true" />Scheduling…</>
              : where === "calendly" ? <>Open Calendly<Glyph name="external" size={13} /></> : submitLabel ?? "Schedule"}
          </button>
        </footer>
      </form>
    </dialog>
  );
}

/** A quiet row of three one-line facts (Integrations): what gets scheduled, and what gets recorded. */
export function Facts({ items }: { items: { glyph: "calendar" | "video" | "play" | "file"; text: ReactNode }[] }) {
  return (
    <ul className="sc-facts">
      {items.map((x, i) => <li key={i}><span className="sc-fact-icon" aria-hidden="true"><Glyph name={x.glyph} size={15} /></span><span>{x.text}</span></li>)}
    </ul>
  );
}
