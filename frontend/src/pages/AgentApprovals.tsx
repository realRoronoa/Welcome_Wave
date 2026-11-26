/** The approval kinds the Keeper and the Planner propose (agents contract): a re-recording of a KT session whose code has
 *  changed, a fix to a document people found unclear or outdated, and a change to a program template. Each is decided in
 *  the existing approval card pattern; nothing is written anywhere until a person approves, and code is never edited.
 *  Styles: styles/agents.css (ap-). */
import { ReactNode, useMemo, useState } from "react";
import { Glyph, GlyphName } from "../dataui";
import WhyThis from "../lifecycle/WhyThis";
import { explainOf } from "../lifecycle/agentModel";
import { firstName, nameOf, plural } from "../lifecycle/kit";
import { Decided, useDecide } from "./decide";
import "../styles/agents.css";

export type AgentApproval = { id: string; kind: string; created_at: string; payload: any };
export const AGENT_KINDS = new Set(["rerecord", "doc_edit", "template_change"]);

/** What a folded card of these kinds says about itself. */
export function agentHeaderOf(a: AgentApproval): { icon: GlyphName; eyebrow: string; title: string; sub: string } {
  const p = a.payload ?? {};
  if (a.kind === "rerecord") {
    const n = Array.isArray(p.paths) ? p.paths.length : 0;
    return { icon: "video", eyebrow: "Re-record", title: p.meeting_title ?? "A KT session",
      sub: [p.speaker?.name ? `With ${nameOf(p.speaker)}` : null,
        p.trigger === "feedback.created" ? "flagged by readers" : n ? `${plural(n, "file")} changed since` : null].filter(Boolean).join(" · ") };
  }
  if (a.kind === "doc_edit") {
    const n = Array.isArray(p.feedback_ids) ? p.feedback_ids.length : 0;
    return { icon: "file", eyebrow: p.target_kind === "code" ? "Suggested fix" : "Doc fix", title: p.title ?? p.target_ref ?? "A document",
      sub: n ? `From ${plural(n, "feedback flag")}` : "From feedback" };
  }
  const n = Array.isArray(p.changes) ? p.changes.length : 0;
  return { icon: "layers", eyebrow: "Template change", title: p.template_title ?? "A program template", sub: n ? `${plural(n, "change")} proposed` : "Changes proposed" };
}

export function AgentApprovalBody({ a, done }: { a: AgentApproval; done: () => void }) {
  if (a.kind === "rerecord") return <RerecordApproval a={a} done={done} />;
  if (a.kind === "doc_edit") return <DocEditApproval a={a} done={done} />;
  return <TemplateChangeApproval a={a} done={done} />;
}

const DECLINED = { tone: "warn" as const, title: "Declined", text: "Nothing was changed." };

/** The decision bar every kind shares: a link and "Why this?" on the left, decline and approve on the right. */
function Foot({ start, busy, approve, onApprove, onDecline }:
  { start?: ReactNode; busy: boolean; approve: ReactNode; onApprove: () => void; onDecline: () => void }) {
  return (
    <footer className="dp-approval-foot">
      <span className="ap-foot-start">{start}</span>
      <button type="button" className="dp-btn" onClick={onDecline} disabled={busy}>Decline</button>
      <button type="button" className="dp-btn primary" onClick={onApprove} disabled={busy}>{busy ? "Saving…" : approve}</button>
    </footer>
  );
}

/* ---------------------------------------------------------------- re-record */

const share = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
  if (!Number.isFinite(n)) return null;
  return Math.max(0, Math.min(1, n > 1 ? n / 100 : n));
};

function RerecordApproval({ a, done }: { a: AgentApproval; done: () => void }) {
  const p = a.payload ?? {};
  const { busy, error, decide, outcome } = useDecide(a.id, done);
  const speaker = p.speaker?.name ? nameOf(p.speaker) : "the speaker";
  const first = p.speaker?.name ? firstName(p.speaker) : "the speaker";
  const paths: { path: string; changed_share?: number | null }[] = Array.isArray(p.paths) ? p.paths : [];
  const flagged = p.trigger === "feedback.created"; // readers flagged it: no share of changed lines to show
  if (outcome) return <article className="dp-approval done"><Decided o={outcome} /></article>;
  return (
    <article className="dp-approval ap-body">
      <p className="ap-lede">{typeof p.summary === "string" && p.summary
        ? p.summary
        : <>The code {speaker} explained in <strong>{p.meeting_title ?? "this session"}</strong> has changed since.</>}</p>
      {paths.length > 0 && (
        <div>
          <p className="ap-k">{flagged ? "Files it explains" : "Changed files"}</p>
          <ul className="ap-paths">
            {paths.map((x) => {
              const s = share(x.changed_share);
              return (
                <li key={x.path}>
                  <a className="ap-path" href={`#/code?path=${encodeURIComponent(x.path)}`} title={x.path}>{x.path}</a>
                  <span className="ap-share">
                    {s !== null && <span className="ap-bar" aria-hidden="true"><i style={{ width: `${Math.max(4, s * 100)}%` }} /></span>}
                    <span className="lc-mono">{s === null ? (flagged ? "Flagged" : "Changed") : s >= 1 ? "All changed" : `${Math.round(s * 100)}% changed`}</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <Foot busy={busy}
            start={<>
              {p.meeting_id && <a className="dp-foot-link" href={`#/meetings/${encodeURIComponent(p.meeting_id)}`}>Open the session<Glyph name="arrow" size={14} /></a>}
              <WhyThis explain={explainOf(p)} tag={false} />
            </>}
            approve={<><Glyph name="calendar" size={15} />Book a re-record with {first}</>}
            onApprove={() => decide({ decision: "approved" }, { tone: "ok", title: "Re-record booked", text: `${speaker} is told. Set the time in KT sessions.` })}
            onDecline={() => decide({ decision: "rejected" }, DECLINED)} />
      {error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}

/* ---------------------------------------------------------------- doc fix */

type Seg = [string, boolean];
/** A word-level comparison (longest common subsequence over words and spaces). Long texts are shown without marks. */
function wordDiff(before: string, after: string): { a: Seg[]; b: Seg[] } | null {
  const x = before.split(/(\s+)/).filter(Boolean), y = after.split(/(\s+)/).filter(Boolean);
  const n = x.length, m = y.length;
  if (!n || !m || n * m > 250_000) return null;
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const a: Seg[] = [], b: Seg[] = [];
  const push = (out: Seg[], t: string, changed: boolean) => {
    const last = out[out.length - 1];
    if (last && last[1] === changed) last[0] += t; else out.push([t, changed]);
  };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { push(a, x[i++], false); push(b, y[j++], false); }
    else if (L[i + 1][j] >= L[i][j + 1]) push(a, x[i++], true);
    else push(b, y[j++], true);
  }
  while (i < n) push(a, x[i++], true);
  while (j < m) push(b, y[j++], true);
  // a rewrite that keeps little of the text (a note for the owner rather than an edit) reads better without marks
  const words = (t: string) => t.split(/\s+/).filter(Boolean).length;
  const kept = a.filter(([, c]) => !c).reduce((k, [t]) => k + words(t), 0);
  if (kept < 0.35 * Math.min(words(before), words(after))) return null;
  return { a, b };
}

function targetHref(kind: string, ref: string): string | null {
  if (!ref) return null;
  if (kind === "code" || (kind === "doc" && /[/.]/.test(ref))) return `#/code?path=${encodeURIComponent(ref)}`;
  if (kind === "doc") return `#/documents/${encodeURIComponent(ref)}`;
  if (kind === "meeting") return `#/meetings/${encodeURIComponent(ref)}`;
  return null;
}

function DocEditApproval({ a, done }: { a: AgentApproval; done: () => void }) {
  const p = a.payload ?? {};
  const { busy, error, decide, outcome } = useDecide(a.id, done);
  const before = String(p.current_excerpt ?? ""), after = String(p.proposed_text ?? "");
  const marks = useMemo(() => wordDiff(before, after), [before, after]);
  const code = p.target_kind === "code";
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(after);
  const edited = text.trim() !== after.trim() && !!text.trim();
  const n = Array.isArray(p.feedback_ids) ? p.feedback_ids.length : 0;
  const href = targetHref(p.target_kind, String(p.target_ref ?? ""));
  const paint = (segs: Seg[] | undefined, text: string, Mark: "del" | "ins") =>
    segs ? segs.map(([t, c], i) => (c && t.trim() ? <Mark key={i}>{t}</Mark> : <span key={i}>{t}</span>)) : text;
  if (outcome) return <article className="dp-approval done"><Decided o={outcome} /></article>;
  return (
    <article className="dp-approval ap-body">
      <p className="ap-target">
        <Glyph name={code ? "code" : "file"} size={14} />
        {href ? <a href={href}>{p.target_ref}</a> : <code>{p.target_ref}</code>}
        <span className="lc-dim">· {n ? `${plural(n, "feedback flag")}` : "flagged in feedback"}</span>
      </p>
      <div className="ap-diff-wrap">
        <div className="ap-diff">
          <figure className="ap-col">
            <figcaption><span>{code ? "The file now" : "Now"}</span></figcaption>
            <p className={`ap-text${code ? " is-code" : ""}`}>{before ? paint(code ? undefined : marks?.a, before, "del") : <span className="lc-dim">No excerpt</span>}</p>
          </figure>
          <figure className="ap-col is-new">
            <figcaption>
              <span>{code ? "Suggestion for the owner" : "Proposed"}</span>
              <span className="ap-cap-end">
                {edited ? <span>Your edit</span> : p.explain?.ai ? <span className="lc-dim">Drafted with AI</span> : null}
                <button type="button" className="ap-edit-b" aria-pressed={editing} onClick={() => setEditing((e) => !e)} disabled={busy}>
                  {editing ? "Done" : "Edit"}
                </button>
              </span>
            </figcaption>
            {editing
              ? <textarea className="ap-text ap-textarea" value={text} rows={8} maxLength={8000} aria-label="Proposed wording" autoFocus onChange={(e) => setText(e.target.value)} />
              : <p className="ap-text">{edited ? text : paint(code ? undefined : marks?.b, after, "ins")}</p>}
          </figure>
        </div>
      </div>
      <p className="ap-note"><Glyph name="shield" size={14} />Code is never edited, only suggested.{code ? " The owner sees this as a comment." : ""}</p>
      <Foot busy={busy} start={<WhyThis explain={explainOf(p)} tag={false} />}
            approve={code ? "Send to the owner" : "Accept the edit"}
            onApprove={() => decide({ decision: "approved", ...(edited ? { text: text.trim() } : {}) }, code
              ? { tone: "ok", title: "Suggestion sent", text: "The owner sees it as a suggested comment. The feedback is resolved." }
              : { tone: "ok", title: "Edit accepted", text: "The document shows the new text. The feedback is resolved." })}
            onDecline={() => decide({ decision: "rejected" }, DECLINED)} />
      {error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}

/* ---------------------------------------------------------------- template change */

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "–";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map((x) => (x && typeof x === "object" ? (x as any).title ?? (x as any).name ?? (x as any).label ?? JSON.stringify(x) : String(x))).join(", ") || "–";
  const o = v as Record<string, unknown>;
  const t = o.title ?? o.label ?? o.name;
  if (typeof t === "string") return t;
  const j = JSON.stringify(v);
  return j.length > 160 ? `${j.slice(0, 157)}…` : j;
}
function evidenceLine(e: unknown): string {
  if (typeof e === "string") return e;
  if (e && typeof e === "object") {
    const o = e as Record<string, unknown>;
    if (typeof o.text === "string") return o.text;
    if (o.label !== undefined) return `${o.label}${o.value !== undefined ? `: ${show(o.value)}` : ""}${typeof o.n === "number" ? ` (${o.n} answers)` : ""}`;
  }
  return show(e);
}

function TemplateChangeApproval({ a, done }: { a: AgentApproval; done: () => void }) {
  const p = a.payload ?? {};
  const { busy, error, decide, outcome } = useDecide(a.id, done);
  const changes: { field: string; from: unknown; to: unknown; reason?: string }[] = Array.isArray(p.changes) ? p.changes : [];
  const evidence: unknown[] = Array.isArray(p.evidence) ? p.evidence : [];
  if (outcome) return <article className="dp-approval done"><Decided o={outcome} /></article>;
  return (
    <article className="dp-approval ap-body">
      {changes.length > 0 ? (
        <div className="dp-table-wrap">
          <table className="dp-table ap-table">
            <thead><tr><th scope="col">Field</th><th scope="col">From</th><th scope="col">To</th><th scope="col">Reason</th></tr></thead>
            <tbody>
              {changes.map((c, i) => (
                <tr key={`${c.field}-${i}`}>
                  <th scope="row">{c.field}</th>
                  <td className="ap-from">{show(c.from)}</td>
                  <td className="ap-to">{show(c.to)}</td>
                  <td className="ap-why">{c.reason ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="ap-lede">No field changes are listed.</p>}
      {evidence.length > 0 && (
        <div>
          <p className="ap-k">Evidence</p>
          <ul className="ap-evidence">{evidence.map((e, i) => <li key={i}>{evidenceLine(e)}</li>)}</ul>
        </div>
      )}
      <Foot busy={busy}
            start={<>
              {p.template_id && <a className="dp-foot-link" href={`#/templates?id=${encodeURIComponent(p.template_id)}`}>Open the template<Glyph name="arrow" size={14} /></a>}
              <WhyThis explain={explainOf(p)} tag={false} />
            </>}
            approve="Approve new version"
            onApprove={() => decide({ decision: "approved" }, { tone: "ok", title: "Template updated", text: "Programs enrolled from now on use the new version." })}
            onDecline={() => decide({ decision: "rejected" }, DECLINED)} />
      {error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}
