/** #/handovers (managers, admins): when someone leaves, everything they hold goes to a successor, so nobody starts from scratch.
 *  Each handover reads its live transfer set (GET /api/handovers/{id}/transfer): what they hold in nine sections (code,
 *  documents, KT recordings and explanations, KT sessions, people, questions, approvals, access, feedback), what has moved and
 *  to whom, and the gaps that would leave the successor starting cold. A person moves things (POST .../transfer), attaches any
 *  document the handover did not find (POST .../attachments), and completes it after typing the leaver's name (POST
 *  .../complete); only an admin may also deactivate the leaver's account. Agents propose, people decide: the Keeper drafts the
 *  plan, nothing moves until someone presses a button, and every step is audited. docs/specs/handover.md. */
import { FormEvent, ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { del, patch, post } from "../api";
import { useSession } from "../session";
import { toast } from "../ui";
import { EmptyState } from "../fx";
import { Avatar, Glyph, GlyphName, Pill } from "../dataui";
import { Accordion, AccordionItem } from "../accordion";
import { copy, LcHead, LcSkeleton, LoadError, nameOf, plural, shortDate, useAction, useApi } from "./kit";
import WhyThis from "./WhyThis";
import { explainOf } from "./agentModel";
import type { Handover } from "./types";
import "../styles/handovers.css";

/* ---------------------------------------------------------------- the transfer set (GET /api/handovers/{id}/transfer) */
type ItemState = "moves" | "todo" | "done";
type Item = { key: string; title: string; state: ItemState; detail: string; href: string | null; url: string | null; owner_id: string | null;
  kt: boolean; gap: string | null };
type SectionKey = "code" | "documents" | "kt" | "sessions" | "people" | "questions" | "approvals" | "access" | "feedback";
type Sec = { key: SectionKey; title: string; how: string; count: number; done: number; moves: number; todo: number; items: Item[]; more: number };
type Gap = { key: string; title: string; detail: string; severity: "blocker" | "warning"; href: string | null;
  action: "successor" | "plan" | "codeowners" | "attach" | null; count: number };
type Who = { id: string; name: string; role: string; github_login: string | null } | null;
type TransferSet = {
  handover_id: string; status: Handover["status"]; last_day: string; days_left: number;
  person: { id: string; name: string; role: string; github_login: string | null; status: "active" | "deactivated" };
  successor: Who; new_owner: Who; note: string | null;
  transfer: { to: string; by: string | null; at: string } | null;
  completed: { by: string | null; at: string; to: string; accepted_gaps: { key: string; title: string }[]; deactivated: boolean; percent: number } | null;
  repository: { full_name: string; branch: string; url: string | null } | null;
  sections: Sec[]; gaps: Gap[]; ticked: string[];
  completeness: { total: number; done: number; moves: number; todo: number; percent: number };
  codeowners: { login: string | null; new_login: string | null; lines: { before: string; after: string | null }[] };
  attachable: { repository: { path: string; url: string | null }[]; documents: { id: string; title: string }[] };
  can_complete: boolean; can_deactivate: boolean;
};
type Member = { id: string; name: string; role: string };
type Detail = { members: Member[] };

const SECTION_GLYPH: Record<SectionKey, GlyphName> = { code: "code", documents: "file", kt: "play", sessions: "calendar", people: "users",
  questions: "message", approvals: "check", access: "shield", feedback: "message" };
const SHORT: Record<SectionKey, string> = { code: "Code", documents: "Documents", kt: "KT recordings", sessions: "KT sessions", people: "People",
  questions: "Questions", approvals: "Approvals", access: "Access", feedback: "Feedback" };
/* what the transfer moves, in plain nouns (sessions and approvals never move) */
const NOUN: Partial<Record<SectionKey, [string, string]>> = { code: ["code file", "code files"], documents: ["document", "documents"],
  kt: ["KT recording or explanation", "KT recordings and explanations"], people: ["hire", "hires"], questions: ["open question", "open questions"],
  access: ["access item", "access items"], feedback: ["feedback item", "feedback items"] };
const STATUS: Record<Handover["status"], { label: string; tone: "neutral" | "info" | "ok" }> = {
  draft: { label: "Draft", tone: "neutral" }, in_progress: { label: "In progress", tone: "info" }, done: { label: "Complete", tone: "ok" } };
const TAKES_OVER = new Set(["expert", "manager", "admin"]);
const bare = (name: string | null | undefined) => nameOf(name ?? null).replace(/\s*\([^)]*\)\s*$/, "");
const first = (name: string | null | undefined) => bare(name).split(/\s+/)[0] || "They";

export default function Handovers() {
  const { project } = useSession();
  const list = useApi<Handover[]>(`/handovers?project_id=${encodeURIComponent(project.id)}`, ["lifecycle", "people", "meetings"]);
  const detail = useApi<Detail>(`/projects/${encodeURIComponent(project.id)}`, ["people"]);
  const [creating, setCreating] = useState(false);
  const members = detail.data?.members ?? [];
  const newButton = <button type="button" className="dp-btn primary" onClick={() => setCreating(true)}><Glyph name="plus" size={15} />New handover</button>;

  if (list.error && !list.data) return <section className="lc ho"><LcHead title="Handovers" /><LoadError error={list.error} retry={list.reload} /></section>;
  if (!list.data) return <section className="lc ho"><LcSkeleton rows={2} /></section>;
  const rows = [...list.data].sort((a, b) => Number(a.status === "done") - Number(b.status === "done") || a.last_day.localeCompare(b.last_day));
  return (
    <section className="lc ho">
      <LcHead title="Handovers" sub="When someone leaves, everything they hold goes to a successor." actions={!creating && rows.length > 0 && newButton} />
      {creating && <CreateHandover members={members} onDone={(made) => { setCreating(false); if (made) list.reload(); }} />}
      {rows.length === 0 && !creating ? (
        <EmptyState title="Nobody is leaving" action={newButton}>
          Start a handover when someone gives notice. Their code, documents, KT sessions and people move to a successor.
        </EmptyState>
      ) : (
        <div className="ho-list">
          {rows.map((h, i) => <HandoverCard key={h.id} h={h} members={members} reload={list.reload} open={h.status !== "done" || i === 0} />)}
        </div>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- starting one */
function CreateHandover({ members, onDone }: { members: Member[]; onDone: (made: boolean) => void }) {
  const { project, me } = useSession();
  const act = useAction();
  const [user, setUser] = useState("");
  const [last, setLast] = useState("");
  const people = members.filter((m) => m.role !== "new_hire" && m.role !== "executive" && m.id !== me.id);
  function submit(e: FormEvent) {
    e.preventDefault();
    act.run("create", () => post("/handovers", { project_id: project.id, user_id: user, last_day: last }))
      .then((r) => { if (r) { toast("Handover drafted. The Keeper listed what they hold."); onDone(true); } });
  }
  return (
    <form className="lc-card lc-form lc-create ho-create" onSubmit={submit} aria-labelledby="ho-new-h">
      <div>
        <p className="lc-strong" id="ho-new-h">New handover</p>
        <p className="lc-dim ho-create-sub">The Keeper lists what they hold. Nothing moves until you transfer it.</p>
      </div>
      <div className="lc-grid2">
        <label className="field"><span>Who is leaving</span>
          <select value={user} onChange={(e) => setUser(e.target.value)} required autoFocus>
            <option value="">Choose a person</option>
            {people.map((m) => <option key={m.id} value={m.id}>{bare(m.name)}</option>)}
          </select>
        </label>
        <label className="field"><span>Last day</span><input type="date" value={last} onChange={(e) => setLast(e.target.value)} required /></label>
      </div>
      <div className="lc-form-foot">
        <button type="button" className="dp-btn ghost" onClick={() => onDone(false)}>Cancel</button>
        <button type="submit" className="dp-btn primary" disabled={!user || !last || !!act.busy}>{act.busy ? "Drafting…" : "Draft the handover"}</button>
      </div>
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </form>
  );
}

/* ---------------------------------------------------------------- one handover */
function HandoverCard({ h, members, reload, open: openAtFirst }: { h: Handover; members: Member[]; reload: () => void; open: boolean }) {
  const [open, setOpen] = useState(openAtFirst);
  const name = members.find((m) => m.id === h.user_id)?.name ?? null;
  const days = Math.round((new Date(`${h.last_day}T00:00:00`).getTime() - Date.now()) / 86400000);
  const when = h.status === "done" ? `Last day ${shortDate(h.last_day)}` : days > 0 ? `Leaving ${shortDate(h.last_day)} · in ${plural(days, "day")}`
    : days === 0 ? `Leaving today, ${shortDate(h.last_day)}` : `Last day was ${shortDate(h.last_day)}`;
  const s = STATUS[h.status];
  const bodyId = useId();
  return (
    <article className={`ho-card${h.status === "done" ? " is-done" : ""}`} aria-label={`Handover for ${bare(name) || "a person leaving"}`}>
      <header className="ho-head">
        <div className="ho-who">
          <Avatar name={name ?? "?"} size={40} />
          <div>
            <h2 className="ho-name">{bare(name) || "Someone"}</h2>
            <p className={`ho-when${h.status !== "done" && days <= 7 ? " soon" : ""}`}>{when}</p>
          </div>
        </div>
        <div className="ho-head-side">
          <Pill tone={s.tone}>{s.label}</Pill>
          <WhyThis explain={explainOf(h.plan) ?? explainOf(h)} why={h.plan.why} />
          {h.status === "done" && (
            <button type="button" className="dp-btn ghost small" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((o) => !o)}>
              {open ? "Hide" : "Show"}
            </button>
          )}
        </div>
      </header>
      <div id={bodyId} hidden={!open}>{open && <TransferView h={h} members={members} reloadList={reload} />}</div>
    </article>
  );
}

function TransferView({ h, members, reloadList }: { h: Handover; members: Member[]; reloadList: () => void }) {
  const t = useApi<TransferSet>(`/handovers/${encodeURIComponent(h.id)}/transfer`, ["lifecycle", "people", "meetings"]);
  const act = useAction();
  const [dialog, setDialog] = useState<"complete" | "attach" | null>(null);
  const [showCodeowners, setShowCodeowners] = useState(false);
  const successorRef = useRef<HTMLSelectElement>(null);
  const reload = () => { t.reload(); reloadList(); };

  if (t.error && !t.data) return <LoadError error={t.error} retry={t.reload} />;
  if (!t.data) return <div className="ho-loading" aria-busy="true"><span className="sr-only" role="status">Reading what they hold…</span><i /><i /><i /></div>;
  const d = t.data;
  const done = d.status === "done";
  const leaver = first(d.person.name);
  const owner = d.new_owner;
  const takers = members.filter((m) => m.id !== d.person.id && TAKES_OVER.has(m.role));
  const memberName = (id: string | null) => bare(members.find((m) => m.id === id)?.name ?? (id === owner?.id ? owner?.name : id === d.person.id ? d.person.name : null));
  const send = (key: string, fn: () => Promise<unknown>, msg?: string) => act.run(key, fn).then((r) => { if (r !== undefined) { if (msg) toast(msg); reload(); } });
  const tick = (key: string, on: boolean) => send(key, () => patch(`/handovers/${encodeURIComponent(h.id)}`, on ? { done: [key] } : { undone: [key] }));
  const filled = d.sections.filter((s) => s.count > 0);
  const empty = d.sections.filter((s) => s.count === 0);
  const firstOpen = filled.find((s) => s.todo > 0)?.key ?? filled[0]?.key ?? null;

  function gapAction(g: Gap): ReactNode {
    if (done) return null;
    if (g.action === "successor") return <button type="button" className="dp-btn small" onClick={() => successorRef.current?.focus()}>Choose</button>;
    if (g.action === "attach") return <button type="button" className="dp-btn small" onClick={() => setDialog("attach")}><Glyph name="plus" size={13} />Attach</button>;
    if (g.action === "codeowners") return <button type="button" className="dp-btn small" aria-expanded={showCodeowners} onClick={() => setShowCodeowners((v) => !v)}>{showCodeowners ? "Hide the change" : "Show the change"}</button>;
    if (g.href) return <a className="dp-btn small" href={g.href}>{g.action === "plan" ? "Plan it" : "Open"}<Glyph name="arrow" size={13} /></a>;
    return null;
  }

  return (
    <div className="ho-body">
      {done && d.completed && <Completed d={d} name={memberName} onReopen={() => send("reopen", () => patch(`/handovers/${encodeURIComponent(h.id)}`, { status: "in_progress" }), "Handover reopened")} busy={!!act.busy} />}

      <div className="ho-summary">
        <Meter c={d.completeness} leaver={leaver} done={done} />
        {!done && (
          <div className="ho-controls">
            <label className="ho-successor">
              <span>Successor</span>
              <select ref={successorRef} value={d.successor?.id ?? ""} disabled={!!act.busy}
                      onChange={(e) => e.target.value && send("succ", () => patch(`/handovers/${encodeURIComponent(h.id)}`, { successor_id: e.target.value }), "Successor set")}>
                <option value="">Choose who takes over</option>
                {takers.map((m) => <option key={m.id} value={m.id}>{bare(m.name)}</option>)}
              </select>
            </label>
            <div className="ho-actions">
              <button type="button" className="dp-btn" disabled={!d.successor || d.completeness.moves === 0 || !!act.busy}
                      title={!d.successor ? "Choose a successor first" : d.completeness.moves === 0 ? "Nothing left to move" : undefined}
                      onClick={() => d.successor && send("transfer", () => post(`/handovers/${encodeURIComponent(h.id)}/transfer`, { successor_id: d.successor!.id }),
                                                          `Moved to ${first(d.successor.name)}`)}>
                <Glyph name="arrow" size={14} />{act.busy === "transfer" ? "Moving…" : d.completeness.moves ? `Move ${d.completeness.moves} to ${first(d.successor?.name)}` : "Nothing to move"}
              </button>
              <button type="button" className="dp-btn primary" disabled={!d.can_complete || !!act.busy} onClick={() => setDialog("complete")}
                      title={!d.can_complete ? "Choose a successor first" : undefined}>Complete handover</button>
            </div>
          </div>
        )}
      </div>
      {act.error && <p className="error" role="alert">{act.error}</p>}

      {!done && d.gaps.length > 0 && (
        <section className="ho-gaps" aria-labelledby={`${h.id}-gaps`}>
          <h3 id={`${h.id}-gaps`} className="ho-h">Before {leaver} leaves<span className="dp-count">{d.gaps.length}</span></h3>
          <ul>
            {d.gaps.map((g) => (
              <li key={g.key} className={`ho-gap is-${g.severity}`}>
                <span className="ho-gap-mark" aria-hidden="true"><Glyph name="alert" size={15} /></span>
                <div className="ho-gap-text">
                  <p className="ho-gap-title">{g.title}{g.severity === "blocker" && <span className="sr-only"> (needed before completing)</span>}</p>
                  <p className="ho-gap-detail">{g.detail}</p>
                  {g.action === "codeowners" && showCodeowners && <CodeownersChange d={d} ticked={d.ticked.includes("codeowners")} onTick={(on) => tick("codeowners", on)} busy={!!act.busy} />}
                </div>
                <div className="ho-gap-act">{gapAction(g)}</div>
              </li>
            ))}
          </ul>
        </section>
      )}
      {done && d.completed && d.completed.accepted_gaps.length > 0 && (
        <section className="ho-gaps" aria-labelledby={`${h.id}-gaps`}>
          <h3 id={`${h.id}-gaps`} className="ho-h">Accepted when it was completed<span className="dp-count">{d.completed.accepted_gaps.length}</span></h3>
          <ul>
            {d.completed.accepted_gaps.map((g) => (
              <li key={g.key} className="ho-gap is-warning">
                <span className="ho-gap-mark" aria-hidden="true"><Glyph name="alert" size={15} /></span>
                <div className="ho-gap-text"><p className="ho-gap-title">{g.title}</p></div>
                <div className="ho-gap-act" />
              </li>
            ))}
          </ul>
        </section>
      )}
      {d.gaps.length === 0 && !done && d.completeness.total > 0 && (
        <p className="ho-clear"><Glyph name="check" size={15} />No gaps. {owner ? `${first(owner.name)} can pick up everything ${leaver} held.` : ""}</p>
      )}

      <section className="ho-holds" aria-labelledby={`${h.id}-holds`}>
        <div className="ho-holds-head">
          <h3 id={`${h.id}-holds`} className="ho-h">What {leaver} holds</h3>
          {!done && <button type="button" className="dp-btn ghost small" onClick={() => setDialog("attach")}><Glyph name="plus" size={13} />Attach a document</button>}
        </div>
        {filled.length === 0 ? (
          <p className="ho-none">Nothing in Welcome Wave is theirs alone. Attach any document the successor should have.</p>
        ) : (
          <Accordion initial={firstOpen} className="ho-acc">
            {filled.map((s) => (
              <AccordionItem key={s.key} id={s.key} title={s.title} count={s.count} sub={s.how}
                             icon={<Glyph name={SECTION_GLYPH[s.key]} size={16} />} meta={<Tally s={s} />}>
                <ul className="ho-items">
                  {s.items.map((i) => <ItemRow key={i.key} i={i} owner={i.owner_id ? memberName(i.owner_id) : null} done={done}
                                               ticked={d.ticked.includes(i.key)} onTick={(on) => tick(i.key, on)} busy={act.busy === i.key}
                                               onRemove={i.key.startsWith("attach:") ? () => send(i.key, () => del(`/handovers/${encodeURIComponent(h.id)}/attachments/${encodeURIComponent(i.key.slice(7))}`), "Removed") : undefined} />)}
                </ul>
                {s.more > 0 && <p className="ho-more">and {plural(s.more, "more item")}</p>}
              </AccordionItem>
            ))}
          </Accordion>
        )}
        {empty.length > 0 && filled.length > 0 && (
          <p className="ho-nothing"><span>Nothing to hand over in</span>{empty.map((s) => <span key={s.key} className="ho-chip t-done">{SHORT[s.key]}</span>)}</p>
        )}
      </section>

      <CompleteDialog open={dialog === "complete"} d={d} handoverId={h.id} onClose={() => setDialog(null)} onDone={reload} />
      <AttachDialog open={dialog === "attach"} d={d} handoverId={h.id} takers={takers} onClose={() => setDialog(null)} onDone={reload} />
    </div>
  );
}

/* ---------------------------------------------------------------- completeness */
function Meter({ c, leaver, done }: { c: TransferSet["completeness"]; leaver: string; done: boolean }) {
  const total = Math.max(c.total, 1);
  const doneShare = c.done / total;
  const movesShare = c.moves / total;
  return (
    <div className="ho-meter">
      <p className="ho-pct"><span className="ho-pct-n">{c.percent}</span><span className="ho-pct-u">%</span><span className="ho-pct-l">handed over</span></p>
      <div className="ho-bar" role="img" aria-label={`${c.done} of ${c.total} handed over; ${c.moves} move on transfer; ${c.todo} to do`}>
        <i className="ho-bar-done" style={{ transform: `scaleX(${doneShare})` }} />
        <i className="ho-bar-moves" style={{ transform: `translateX(${doneShare * 100}%) scaleX(${movesShare})` }} />
      </div>
      <ul className="ho-legend">
        <li className="l-done">{c.done} of {c.total} done</li>
        {c.moves > 0 && <li className="l-moves">{c.moves} move on transfer</li>}
        {c.todo > 0 && <li className="l-todo">{done ? `${c.todo} left open` : `${c.todo} to do before ${leaver} leaves`}</li>}
      </ul>
    </div>
  );
}

function Tally({ s }: { s: Sec }) {
  return (
    <span className="ho-tally">
      {s.todo > 0 && <span className="ho-chip t-todo">{s.todo} to do</span>}
      {s.moves > 0 && <span className="ho-chip t-moves">{s.moves} to move</span>}
      {s.done > 0 && s.done === s.count && <span className="ho-chip t-done"><Glyph name="check" size={12} />All done</span>}
    </span>
  );
}

function ItemRow({ i, owner, done, ticked, onTick, busy, onRemove }:
  { i: Item; owner: string | null; done: boolean; ticked: boolean; onTick: (on: boolean) => void; busy: boolean; onRemove?: () => void }) {
  const title = i.href ? <a href={i.href} className="ho-item-link">{i.title}</a> : <span>{i.title}</span>;
  const isPath = /^[\w.-]+(\/[\w.-]+)+$|^[\w-]+\.\w+$/.test(i.title) && !i.title.includes(" ");
  return (
    <li className={`ho-item is-${i.state}`}>
      <span className="ho-state" aria-hidden="true">
        {i.state === "done" ? <Glyph name="check" size={13} /> : i.state === "moves" ? <Glyph name="arrow" size={13} /> : null}
      </span>
      <div className="ho-item-main">
        <p className={`ho-item-title${isPath ? " mono" : ""}`}>
          {title}
          {i.kt && <span className="kt-badge" title="Explained in a recorded KT session">KT</span>}
          <span className="sr-only">{i.state === "done" ? " (done)" : i.state === "moves" ? " (moves on transfer)" : " (to do)"}</span>
        </p>
        {i.detail && <p className="ho-item-detail">{i.detail}</p>}
      </div>
      <div className="ho-item-side">
        {owner && i.state !== "todo" && <span className="ho-owner" title={i.state === "moves" ? `Moves to ${owner}` : `With ${owner}`}>
          {i.state === "moves" && <Glyph name="arrow" size={12} />}<Avatar name={owner} size={18} />{owner}</span>}
        {i.url && <a className="ho-icon-btn" href={i.url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${i.title} on GitHub`} title="Open on GitHub">
          <Glyph name="external" size={14} /></a>}
        {!done && i.state === "todo" && (
          <button type="button" className="dp-btn small ho-tick" disabled={busy} onClick={() => onTick(true)} aria-label={`Mark done: ${i.title}`}>
            <Glyph name="check" size={13} />Done
          </button>
        )}
        {!done && i.state === "done" && ticked && (
          <button type="button" className="dp-btn ghost small" disabled={busy} onClick={() => onTick(false)} aria-label={`Undo: ${i.title}`}>Undo</button>
        )}
        {!done && onRemove && (
          <button type="button" className="dp-btn ghost small" disabled={busy} onClick={onRemove} aria-label={`Remove attachment: ${i.title}`}>Remove</button>
        )}
      </div>
    </li>
  );
}

/* ---------------------------------------------------------------- CODEOWNERS: Welcome Wave never writes to the repository */
function CodeownersChange({ d, ticked, onTick, busy }: { d: TransferSet; ticked: boolean; onTick: (on: boolean) => void; busy: boolean }) {
  const lines = d.codeowners.lines;
  const after = lines.map((l) => l.after).filter(Boolean).join("\n");
  return (
    <div className="ho-diff">
      <pre aria-label="Suggested CODEOWNERS change"><code>
        {lines.map((l, n) => (
          <span key={n}>
            <span className="del"><span aria-hidden="true">- </span><span className="sr-only">Remove: </span>{l.before}{"\n"}</span>
            {l.after && <span className="add"><span aria-hidden="true">+ </span><span className="sr-only">Add: </span>{l.after}{"\n"}</span>}
          </span>
        ))}
      </code></pre>
      {!d.codeowners.new_login && <p className="ho-diff-note">The successor has no GitHub login yet. Add one on the People page.</p>}
      <div className="ho-diff-foot">
        {after && <button type="button" className="dp-btn small" onClick={() => copy(after, (ok) => toast(ok ? "Copied the new lines" : "Copy failed", ok ? "ok" : "error"))}>Copy new lines</button>}
        <button type="button" className="dp-btn ghost small" disabled={busy} onClick={() => onTick(!ticked)}>{ticked ? "Not changed yet" : "I changed it"}</button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- after completion */
function Completed({ d, name, onReopen, busy }: { d: TransferSet; name: (id: string | null) => string; onReopen: () => void; busy: boolean }) {
  const c = d.completed!;
  return (
    <div className="ho-completed" role="status">
      <span className="ho-completed-mark" aria-hidden="true"><Glyph name="check" size={16} /></span>
      <div>
        <p className="lc-strong">Handed over to {name(c.to)} on {shortDate(c.at.slice(0, 10))}</p>
        <p className="lc-dim">
          Completed by {name(c.by)} at {c.percent}%.{c.deactivated ? ` ${first(d.person.name)}'s account was deactivated.` : ""}
          {c.accepted_gaps.length > 0 ? ` ${plural(c.accepted_gaps.length, "gap was", "gaps were")} accepted.` : ""}
        </p>
      </div>
      <button type="button" className="dp-btn ghost small" disabled={busy} onClick={onReopen}>Reopen</button>
    </div>
  );
}

/* ---------------------------------------------------------------- dialogs (navy panels: .dp-dialog is a .ww-panel) */
function useDialog(open: boolean) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);
  return ref;
}

function DialogHead({ id, title, sub, onClose }: { id: string; title: string; sub?: ReactNode; onClose: () => void }) {
  return (
    <header className="dp-dialog-head">
      <div>
        <h2 id={id}>{title}</h2>
        {sub && <p className="ho-dialog-sub">{sub}</p>}
      </div>
      <button type="button" className="dp-btn ghost small dp-dialog-x" onClick={onClose} aria-label="Close">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12" /></svg>
      </button>
    </header>
  );
}

function CompleteDialog({ open, d, handoverId, onClose, onDone }: { open: boolean; d: TransferSet; handoverId: string; onClose: () => void; onDone: () => void }) {
  const ref = useDialog(open);
  const act = useAction();
  const [typed, setTyped] = useState("");
  const [accept, setAccept] = useState(false);
  const [deactivate, setDeactivate] = useState(false);
  const uid = useId();
  useEffect(() => { if (open) { setTyped(""); setAccept(false); setDeactivate(false); act.setError(null); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const name = bare(d.person.name);
  const leaver = first(d.person.name);
  const to = first(d.successor?.name);
  const gaps = d.gaps.filter((g) => g.key !== "successor");
  const moving = useMemo(() => d.sections.filter((s) => s.moves > 0 && NOUN[s.key])
    .map((s) => `${s.moves} ${NOUN[s.key]![s.moves === 1 ? 0 : 1]}`), [d.sections]);
  const matches = typed.trim().replace(/\s+/g, " ").toLowerCase() === name.toLowerCase() || typed.trim().toLowerCase() === d.person.name.toLowerCase();
  const ready = matches && (gaps.length === 0 || accept);
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    act.run("complete", () => post(`/handovers/${encodeURIComponent(handoverId)}/complete`, { confirm: typed, accept_gaps: accept, deactivate }))
      .then((r) => { if (r) { toast(deactivate ? `Handover complete. ${leaver}'s account is deactivated.` : "Handover complete"); onClose(); onDone(); } });
  }
  return (
    <dialog ref={ref} className="dp-dialog ww-panel ho-dialog" aria-labelledby={`${uid}-h`} onClose={onClose}
            onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      <form className="dp-dialog-inner" onSubmit={submit}>
        <DialogHead id={`${uid}-h`} title={`Complete ${leaver}'s handover`} onClose={onClose}
                    sub={moving.length ? <>Moves to {to}: {moving.join(", ")}.</> : <>Everything already sits with {to}.</>} />
        {gaps.length > 0 && (
          <fieldset className="ho-fieldset">
            <legend>{plural(gaps.length, "gap is", "gaps are")} still open</legend>
            <ul className="ho-dialog-gaps">{gaps.map((g) => <li key={g.key}>{g.title}</li>)}</ul>
            <label className="ho-check">
              <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />
              <span>Complete with these gaps open. They stay on the handover and in the audit log.</span>
            </label>
          </fieldset>
        )}
        {d.can_deactivate && d.person.status === "active" && (
          <label className={`ho-check ho-danger${deactivate ? " on" : ""}`}>
            <input type="checkbox" checked={deactivate} onChange={(e) => setDeactivate(e.target.checked)} />
            <span><strong>Also deactivate {leaver}'s account</strong>They can no longer sign in. Their recordings, explanations and history stay.</span>
          </label>
        )}
        <label className="field">
          <span>Type <strong className="ho-confirm-name">{name}</strong> to confirm</span>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} aria-describedby={`${uid}-why`} />
        </label>
        <p className="ho-dialog-note" id={`${uid}-why`}>A person confirms every handover. The Keeper only suggests.</p>
        {act.error && <p className="error" role="alert">{act.error}</p>}
        <div className="lc-form-foot">
          <button type="button" className="dp-btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className={`dp-btn primary${deactivate ? " ho-danger-btn" : ""}`} disabled={!ready || !!act.busy}>
            {act.busy ? "Completing…" : deactivate ? "Complete and deactivate" : "Complete handover"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

function AttachDialog({ open, d, handoverId, takers, onClose, onDone }:
  { open: boolean; d: TransferSet; handoverId: string; takers: Member[]; onClose: () => void; onDone: () => void }) {
  const ref = useDialog(open);
  const act = useAction();
  const uid = useId();
  const [kind, setKind] = useState<"repo" | "document" | "link">("repo");
  const [path, setPath] = useState("");
  const [doc, setDoc] = useState("");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [owner, setOwner] = useState("");
  useEffect(() => {
    if (!open) return;
    setKind("repo"); setPath(""); setDoc(""); setUrl(""); setTitle(""); setNote(""); setOwner(""); act.setError(null);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const ready = kind === "repo" ? path.trim().length > 0 : kind === "document" ? !!doc : /^https:\/\/\S+$/.test(url.trim()) && title.trim().length > 0;
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    const body = { kind, ref: kind === "repo" ? path.trim() : kind === "document" ? doc : null, url: kind === "link" ? url.trim() : null,
                   title: title.trim() || null, note: note.trim() || null, owner_id: owner || null };
    act.run("attach", () => post(`/handovers/${encodeURIComponent(handoverId)}/attachments`, body))
      .then((r) => { if (r) { toast("Attached"); onClose(); onDone(); } });
  }
  const kinds: { id: typeof kind; label: string }[] = [{ id: "repo", label: "Repository file" }, { id: "document", label: "Uploaded document" }, { id: "link", label: "Link" }];
  return (
    <dialog ref={ref} className="dp-dialog ww-panel ho-dialog" aria-labelledby={`${uid}-h`} onClose={onClose}
            onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      <form className="dp-dialog-inner" onSubmit={submit}>
        <DialogHead id={`${uid}-h`} title="Attach a document" onClose={onClose}
                    sub={`Anything ${d.successor ? first(d.successor.name) : "the successor"} needs that the handover did not find.`} />
        <div className="dp-toggle" role="radiogroup" aria-label="What to attach">
          {kinds.map((k) => <button key={k.id} type="button" role="radio" aria-checked={kind === k.id} onClick={() => setKind(k.id)}>{k.label}</button>)}
        </div>
        {kind === "repo" && (
          <label className="field"><span>File in {d.repository?.full_name ?? "the repository"}</span>
            <input value={path} onChange={(e) => setPath(e.target.value)} list={`${uid}-paths`} placeholder="e.g. docs/RUNBOOK.md" spellCheck={false} autoComplete="off" />
            <datalist id={`${uid}-paths`}>{d.attachable.repository.map((p) => <option key={p.path} value={p.path} />)}</datalist>
          </label>
        )}
        {kind === "document" && (d.attachable.documents.length ? (
          <label className="field"><span>Document</span>
            <select value={doc} onChange={(e) => setDoc(e.target.value)}>
              <option value="">Choose a document</option>
              {d.attachable.documents.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
            </select>
          </label>
        ) : <p className="ho-dialog-note">No other documents are uploaded to this project. Upload one in <a href="#/settings">Settings</a>.</p>)}
        {kind === "link" && (
          <div className="lc-grid2">
            <label className="field"><span>Link</span><input type="url" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" /></label>
            <label className="field"><span>Title</span><input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="Webhook runbook" /></label>
          </div>
        )}
        <div className="lc-grid2">
          <label className="field"><span>Note <span className="ho-optional">optional</span></span>
            <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="What it covers" /></label>
          <label className="field"><span>Owner next</span>
            <select value={owner} onChange={(e) => setOwner(e.target.value)}>
              <option value="">{d.successor ? `${first(d.successor.name)} (successor)` : "The successor"}</option>
              {takers.filter((m) => m.id !== d.successor?.id).map((m) => <option key={m.id} value={m.id}>{bare(m.name)}</option>)}
            </select>
          </label>
        </div>
        {act.error && <p className="error" role="alert">{act.error}</p>}
        <div className="lc-form-foot">
          <button type="button" className="dp-btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="dp-btn primary" disabled={!ready || !!act.busy}>{act.busy ? "Attaching…" : "Attach"}</button>
        </div>
      </form>
    </dialog>
  );
}
