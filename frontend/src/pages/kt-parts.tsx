/** The manager side of a recorded KT session, shared by the call's page and its review in Approvals: the transcript, and the
 *  follow-up KT sessions the call gave reasons for (GET /api/meetings/{id}/followups), booked from there. */
import { ReactNode, RefObject, useCallback, useEffect, useMemo, useState } from "react";
import { api, errorMessage, mmss } from "../api";
import { Skeleton } from "../fx";
import { Avatar, daysUntil, Glyph, Pill } from "../dataui";
import "../styles/kt.css";
import { useLive } from "../live";

export type Utterance = { start_s: number; speaker: string; text: string };

/** Identifiers and file names in notes and transcripts render as inline code, as in the chat. */
const IDENT = /`([^`\n]+)`|\b([A-Za-z]\w*_\w+(?:\(\))?|[\w-]+(?:\/[\w.-]+)*\.(?:py|tsx?|jsx?|go|rb|java|rs|md|ya?ml|json|toml|sql)\b)/g;
export function Rich({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let last = 0, m: RegExpExecArray | null;
  IDENT.lastIndex = 0;
  while ((m = IDENT.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(<code key={m.index} className="dp-ic">{m[1] ?? m[2]}</code>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}

/** The transcript, speaker by speaker. `active` marks the line in focus; a timestamp seeks (plays, when there is video). */
export function TranscriptList({ utterances, active, onSeek, listRef, playable, idPrefix = "t" }: {
  utterances: Utterance[]; active?: number; onSeek?: (s: number) => void; listRef?: RefObject<HTMLOListElement>; playable?: boolean; idPrefix?: string;
}) {
  const [find, setFind] = useState("");
  const needle = find.trim().toLowerCase();
  const hits = needle ? utterances.filter((u) => u.text.toLowerCase().includes(needle) || u.speaker.toLowerCase().includes(needle)).length : 0;
  return (
    <div className="kt-transcript">
      {utterances.length > 8 && (
        <label className="kt-find">
          <span className="sr-only">Find in the transcript</span>
          <Glyph name="message" size={14} />
          <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find in the transcript" />
          {needle && <span className="kt-find-n" role="status">{hits} {hits === 1 ? "line" : "lines"}</span>}
        </label>
      )}
      <div className="dp-transcript kt-transcript-box">
        <ol ref={listRef}>
          {utterances.map((u, i) => {
            const same = i > 0 && utterances[i - 1].speaker === u.speaker;
            const dim = needle && !(u.text.toLowerCase().includes(needle) || u.speaker.toLowerCase().includes(needle));
            return (
              <li key={i} id={`${idPrefix}${Math.floor(u.start_s)}`}
                  className={[active !== undefined && u.start_s === active && "here", same && "cont", dim && "kt-dim"].filter(Boolean).join(" ")}>
                {onSeek ? <button className="dp-time" onClick={() => onSeek(u.start_s)} aria-label={`${playable ? "Play from" : "Go to"} ${mmss(u.start_s)}`}>{mmss(u.start_s)}</button>
                  : <span className="dp-time">{mmss(u.start_s)}</span>}
                <span className="dp-utt">
                  {same ? <span className="sr-only">{u.speaker}: </span> : <span className="dp-speaker"><Avatar name={u.speaker} size={20} /><strong>{u.speaker}</strong></span>}
                  <span className="dp-utt-text"><Rich text={u.text} /></span>
                </span>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- follow-up sessions */
type Reason = { kind: string; text: string; path: string; symbol: string | null; ref: { meeting_id?: string; at_s?: number } };
export type FollowUp = {
  key: string; title: string; est_minutes: number; deadline: string | null;
  presenter: { user_id: string; name: string } | null;
  items: { path: string; symbol: string; start_line: number; reasons: Reason[] }[];
  booked: { meeting_id: string; status: string } | null;
};

export function useFollowUps(meetingId: string, enabled = true) {
  const [rows, setRows] = useState<FollowUp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    if (!enabled) return Promise.resolve();
    return api<{ sessions: FollowUp[] }>(`/meetings/${encodeURIComponent(meetingId)}/followups`)
      .then((r) => { setRows(r.sessions); setError(null); }).catch((e) => setError(errorMessage(e)));
  }, [meetingId, enabled]);
  useEffect(() => { void load(); }, [load]);
  useLive(["meetings", "knowledge", "people"], () => { void load(); }); // booked elsewhere, a new code owner, new KT links
  return { rows, error, load };
}

const firstName = (name: string) => name.replace(/\(.*?\)/g, " ").trim().split(/\s+/)[0] || name;
const mins = (m: number) => (Number.isInteger(m) ? String(m) : m.toFixed(1));
const REASON_LABEL: Record<string, string> = { action_item: "Action item", named_not_shown: "Named, never shown" };

/** Why this call asks for the session: its own reasons only, once each, in call order. */
function callReasons(s: FollowUp, meetingId: string) {
  const seen = new Set<string>();
  return s.items.flatMap((i) => i.reasons)
    .filter((r) => r.ref.meeting_id === meetingId)
    .filter((r) => { const k = `${r.kind}|${r.text}|${r.ref.at_s}`; return seen.has(k) ? false : (seen.add(k), true); })
    .sort((a, b) => (a.ref.at_s ?? 0) - (b.ref.at_s ?? 0));
}

/** The sessions as a list. mode 'book': a Book button each; mode 'select': checkboxes, for approving several at once. */
export function FollowUpList({ meetingId, rows, mode, selected, onToggle, onBook, busy, onSeek }: {
  meetingId: string; rows: FollowUp[] | null; mode: "book" | "select";
  selected?: Set<string>; onToggle?: (key: string) => void; onBook?: (s: FollowUp) => void; busy?: string | null; onSeek?: (s: number) => void;
}) {
  const sorted = useMemo(() => rows ?? [], [rows]);
  if (!rows) return <Skeleton lines={3} />;
  if (rows.length === 0) {
    return <p className="dp-meta kt-none"><Glyph name="check" size={14} />Nothing left to explain from this session.</p>;
  }
  return (
    <ul className="kt-follow">
      {sorted.map((s) => {
        const why = callReasons(s, meetingId);
        const files = [...new Set(s.items.map((i) => i.path))];
        const on = !!selected?.has(s.key);
        const head = (
          <>
            <span className="kt-follow-who">{s.presenter ? <Avatar name={s.presenter.name} size={28} /> : <span className="kt-follow-none" aria-hidden="true"><Glyph name="users" size={14} /></span>}</span>
            <span className="kt-follow-main">
              <strong>KT with {s.presenter ? firstName(s.presenter.name) : "no presenter yet"}</strong>
              <span className="kt-follow-files">{files.map((p) => <code key={p}>{p}</code>)}</span>
              <span className="kt-follow-meta">
                <span><Glyph name="clock" size={12} />~{mins(s.est_minutes)} min</span>
                <span>{s.items.length} {s.items.length === 1 ? "function" : "functions"}</span>
                {s.deadline && <span className={daysUntil(s.deadline) <= 3 ? "kt-soon" : undefined}><Glyph name="calendar" size={12} />before {new Date(`${s.deadline}T00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</span>}
              </span>
            </span>
          </>
        );
        return (
          <li key={s.key} className={`kt-follow-row${s.booked ? " is-booked" : ""}${on ? " on" : ""}`}>
            {mode === "select" && !s.booked && s.presenter ? (
              <label className="kt-follow-head">
                <input type="checkbox" className="dp-check-input" checked={on} onChange={() => onToggle?.(s.key)} />
                <span className="dp-check" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" /></svg></span>
                {head}
              </label>
            ) : <div className="kt-follow-head">{head}</div>}
            <div className="kt-follow-side">
              {s.booked ? <><Pill tone="ok">Booked</Pill><a className="dp-btn small ghost" href={`#/meetings?call=${encodeURIComponent(s.booked.meeting_id)}`}>Open<Glyph name="arrow" size={13} /></a></>
                : !s.presenter ? <span className="dp-meta">Needs a code owner</span>
                : mode === "book" ? (
                  <button type="button" className="dp-btn primary small" disabled={!!busy} onClick={() => onBook?.(s)}>
                    {busy === s.key ? "Booking…" : <><Glyph name="calendar" size={13} />Book</>}
                  </button>
                ) : null}
            </div>
            {why.length > 0 && (
              <ul className="kt-follow-why" aria-label="Why this session asks for it">
                {why.map((r, i) => (
                  <li key={i}>
                    <span className="kt-why-k">{REASON_LABEL[r.kind] ?? r.kind}</span>
                    <span className="kt-why-t"><Rich text={r.text} /></span>
                    {r.ref.at_s != null && (onSeek
                      ? <button type="button" className="dp-time" onClick={() => onSeek(r.ref.at_s!)} aria-label={`Go to ${mmss(r.ref.at_s)} in the transcript`}>{mmss(r.ref.at_s)}</button>
                      : <span className="dp-time">{mmss(r.ref.at_s)}</span>)}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}
