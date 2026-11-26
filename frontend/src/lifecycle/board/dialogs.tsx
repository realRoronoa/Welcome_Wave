/** The milestone board's dialogs, on the navy panel surface (.ww-panel): a task's details (moves, sign-off, edits, removal),
 *  adding a task, and a hire's milestones (add, rename, re-date, reorder, remove). Native <dialog>: focus stays inside,
 *  Escape closes, focus goes back to where it was. */
import { FormEvent, ReactNode, useEffect, useId, useRef, useState } from "react";
import { safeHref } from "../../api";
import { Glyph, kindOf } from "../../dataui";
import { nameOf, plural, shortDate } from "../kit";
import { COLUMN_LABEL, COLUMNS, dueText } from "./parts";
import type { BoardData, Card, Column, MoveBody, TaskKind } from "./types";

/* ---------------------------------------------------------------- the dialog frame */

function Dialog({ title, sub, onClose, children, wide, foot }:
  { title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean; foot?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const d = ref.current;
    const back = document.activeElement as HTMLElement | null;
    if (d && !d.open) d.showModal();
    return () => { if (d?.open) d.close(); if (back?.isConnected) back.focus(); };
  }, []);
  return (
    <dialog ref={ref} className={`bd-dialog ww-panel${wide ? " wide" : ""}`} aria-labelledby={id}
            onCancel={(e) => { e.preventDefault(); onClose(); }} onMouseDown={(e) => { if (e.target === ref.current) onClose(); }}>
      <div className="bd-dialog-in">
        <header className="bd-dialog-head">
          <div>
            <h2 id={id}>{title}</h2>
            {sub && <div className="bd-dialog-sub">{sub}</div>}
          </div>
          <button type="button" className="bd-x" aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </header>
        <div className="bd-dialog-body">{children}</div>
        {foot && <footer className="bd-dialog-foot">{foot}</footer>}
      </div>
    </dialog>
  );
}

const STATUS_LABEL: Record<Card["status"], string> = {
  approved: "To do", in_progress: "In progress", changes_requested: "Changes requested", submitted: "Waiting for sign-off", done: "Done",
};

/* ---------------------------------------------------------------- a task */

type TaskProps = {
  card: Card; board: BoardData; confirmRemove?: boolean; onClose: () => void;
  move: (body: MoveBody, label?: string) => Promise<BoardData | null> | undefined;
  save: (fields: { title?: string; description?: string; due_date?: string | null }) => Promise<BoardData | null>;
  remove: () => Promise<BoardData | null>;
};

export function TaskDialog({ card, board, confirmRemove, onClose, move, save, remove }: TaskProps) {
  const manage = board.can.edit;
  const m = board.milestones.find((x) => x.key === card.milestone);
  const due = dueText(card.due_date, board.today, card.column === "done");
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(!!confirmRemove);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState(card.title);
  const [description, setDescription] = useState(card.description);
  const [dueDate, setDueDate] = useState(card.due_date ?? "");
  const k = kindOf(card.kind);
  const pr = safeHref(card.pr_url);
  const run = async (p: Promise<unknown> | undefined) => { setBusy(true); try { await p; } finally { setBusy(false); } };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    const fields: { title?: string; description?: string; due_date?: string } = {};
    if (title.trim() !== card.title) fields.title = title.trim();
    if (description !== card.description) fields.description = description;
    if (dueDate && dueDate !== card.due_date) fields.due_date = dueDate;
    run(save(fields).then((r) => { if (r) setEditing(false); }));
  };
  return (
    <Dialog onClose={onClose} title={editing ? "Edit task" : card.title}
            sub={<span className="bd-dialog-tags">
              <span className="bd-kind"><Glyph name={card.kind === "code" ? "code" : card.kind === "meet" ? "meet" : "learn"} size={13} />{k.label}</span>
              {card.kt && <span className="kt-badge" title="Explained in a recorded KT session">KT</span>}
              <span className={`bd-status s-${card.status}`}>{STATUS_LABEL[card.status]}</span>
            </span>}>
      {editing ? (
        <form className="bd-form" onSubmit={submit} id="bd-edit-form">
          <label className="field"><span>Title</span><input value={title} maxLength={200} required onChange={(e) => setTitle(e.target.value)} autoFocus /></label>
          <label className="field"><span>What it asks</span><textarea rows={4} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
          <label className="field"><span>Due</span><input type="date" value={dueDate} min={board.start_date} onChange={(e) => setDueDate(e.target.value)} /></label>
          <div className="bd-form-foot">
            <button type="button" className="dp-btn ghost" onClick={() => setEditing(false)}>Cancel</button>
            <button type="submit" className="dp-btn primary" disabled={busy || !title.trim()}>{busy ? "Saving…" : "Save"}</button>
          </div>
        </form>
      ) : (
        <div className="bd-detail">
          <dl className="bd-facts">
            <div><dt>Milestone</dt><dd>{m ? `Day ${m.due_day} · ${m.title}` : "—"}</dd></div>
            <div><dt>Due</dt><dd className={due?.late ? "bd-late" : undefined}>{card.due_date ? `${shortDate(card.due_date)}${due?.late ? ` · ${due.text}` : ""}` : "No date"}</dd></div>
            {pr && <div><dt>Pull request</dt><dd><a href={pr} target="_blank" rel="noreferrer">#{card.pr_number}<Glyph name="external" size={12} /></a></dd></div>}
          </dl>
          {card.description && card.description !== card.title && <p className="bd-desc">{card.description}</p>}
          {card.acceptance_criteria.length > 0 && (
            <div><p className="bd-label">Done when</p><ul className="bd-criteria">{card.acceptance_criteria.map((c) => <li key={c}>{c}</li>)}</ul></div>
          )}

          <div className="bd-move">
            <p className="bd-label" id="bd-move-l">Move to</p>
            <div className="bd-seg" role="group" aria-labelledby="bd-move-l">
              {COLUMNS.map((col: Column) => (
                <button key={col} type="button" aria-pressed={card.column === col} disabled={busy || card.column === col || !card.moves.includes(col)}
                        onClick={() => run(move({ column: col }))}>{COLUMN_LABEL[col]}</button>
              ))}
            </div>
            {!manage && card.column !== "done" && <p className="bd-hint">Done sends it to your manager to sign off.</p>}
            {!manage && card.status === "done" && <p className="bd-hint">Your manager reopens a done task.</p>}
            {card.review_pending && <p className="bd-hint">A review of its pull request waits in Approvals: it moves when that is decided.</p>}
          </div>
          {manage && card.status === "submitted" && !card.review_pending && (
            <div className="bd-row-actions">
              <button type="button" className="dp-btn primary small" disabled={busy} onClick={() => run(move({ sign_off: true }, `${card.title} signed off`))}>
                <Glyph name="check" size={13} />Sign off</button>
              <button type="button" className="dp-btn small" disabled={busy}
                      onClick={() => run(move({ column: "in_progress", changes_requested: true }, `Changes asked for on ${card.title}`))}>Ask for changes</button>
            </div>
          )}
          {manage && (
            <label className="field bd-inline-field"><span>Milestone</span>
              <select value={card.milestone} disabled={busy} onChange={(e) => run(move({ milestone: e.target.value }))}>
                {board.milestones.map((x) => <option key={x.key} value={x.key}>Day {x.due_day} · {x.title}</option>)}
              </select>
            </label>
          )}

          {card.history.length > 0 && (
            <div>
              <p className="bd-label">History</p>
              <ol className="bd-history">
                {[...card.history].reverse().map((hst, i) => (
                  <li key={i}>
                    <span className="bd-history-dot" aria-hidden="true" />
                    <span>Moved from <strong>{hst.from_title ?? "another milestone"}</strong> to <strong>{hst.to_title}</strong>
                      {hst.to_due ? <>, now due {shortDate(hst.to_due)}</> : null}</span>
                    <span className="bd-history-when">{shortDate(hst.at)}{hst.by ? ` · ${nameOf(hst.by)}` : ""}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {manage && (removing ? (
            <div className="bd-danger" role="group" aria-label="Remove this task">
              <p>Remove <strong>{card.title}</strong> from the board? It is marked rejected; its history stays in the audit log.</p>
              <div className="bd-row-actions">
                <button type="button" className="dp-btn ghost small" onClick={() => setRemoving(false)}>Keep it</button>
                <button type="button" className="dp-btn small danger" disabled={busy} autoFocus onClick={() => run(remove())}>Remove</button>
              </div>
            </div>
          ) : (
            <div className="bd-row-actions end">
              <button type="button" className="dp-btn ghost small" onClick={() => setRemoving(true)}>Remove</button>
              <button type="button" className="dp-btn small" onClick={() => setEditing(true)}>Edit</button>
            </div>
          ))}
        </div>
      )}
    </Dialog>
  );
}

/* ---------------------------------------------------------------- adding a task */

type AddBody = { title: string; description: string; kind: TaskKind; milestone: string; due_date?: string };

export function AddTaskDialog({ board, milestone, onClose, add }:
  { board: BoardData; milestone: string | null; onClose: () => void; add: (body: AddBody) => Promise<BoardData | null> }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<TaskKind>("learn");
  const [ms, setMs] = useState(milestone ?? board.current ?? board.milestones[0]?.key ?? "");
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  const m = board.milestones.find((x) => x.key === ms);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !ms) return;
    setBusy(true);
    try { await add({ title: title.trim(), description: description.trim(), kind, milestone: ms, ...(due ? { due_date: due } : {}) }); }
    finally { setBusy(false); }
  };
  return (
    <Dialog onClose={onClose} title="Add a task" sub={`To ${nameOf(board.hire)}'s board. They are told.`}>
      <form className="bd-form" onSubmit={submit}>
        <label className="field"><span>Title</span><input value={title} maxLength={200} required autoFocus placeholder="Pair on the release checklist"
                                                          onChange={(e) => setTitle(e.target.value)} /></label>
        <fieldset className="bd-kinds">
          <legend>Kind</legend>
          {(["learn", "code", "meet"] as TaskKind[]).map((k) => (
            <label key={k} className={kind === k ? "on" : ""}>
              <input type="radio" name="bd-kind" value={k} checked={kind === k} onChange={() => setKind(k)} />
              <Glyph name={k} size={14} />{kindOf(k).label}
            </label>
          ))}
        </fieldset>
        <div className="bd-grid2">
          <label className="field"><span>Milestone</span>
            <select value={ms} onChange={(e) => setMs(e.target.value)}>
              {board.milestones.map((x) => <option key={x.key} value={x.key}>Day {x.due_day} · {x.title}</option>)}
            </select>
          </label>
          <label className="field"><span>Due <span className="bd-opt">optional</span></span>
            <input type="date" value={due} min={board.start_date} onChange={(e) => setDue(e.target.value)} />
          </label>
        </div>
        {m && !due && <p className="bd-hint">Due on day {m.due_day} ({shortDate(m.due_date)}) unless you pick a date.</p>}
        <label className="field"><span>What it asks <span className="bd-opt">optional</span></span>
          <textarea rows={3} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <div className="bd-form-foot">
          <button type="button" className="dp-btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="dp-btn primary" disabled={busy || !title.trim()}>{busy ? "Adding…" : "Add task"}</button>
        </div>
      </form>
    </Dialog>
  );
}

/* ---------------------------------------------------------------- milestones */

type MilestoneProps = {
  board: BoardData; onClose: () => void;
  add: (title: string, day: number) => Promise<BoardData | null>;
  edit: (key: string, fields: { title?: string; due_day?: number }) => Promise<BoardData | null>;
  order: (keys: string[]) => Promise<BoardData | null>;
  remove: (key: string, moveTo: string | null) => Promise<BoardData | null>;
};

export function MilestonesDialog({ board, onClose, add, edit, order, remove }: MilestoneProps) {
  const ms = board.milestones;
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [moveTo, setMoveTo] = useState("");
  const [title, setTitle] = useState("");
  const [day, setDay] = useState("");
  const run = async (p: Promise<unknown>) => { setBusy(true); try { await p; } finally { setBusy(false); } };
  const commit = (key: string, field: "title" | "due_day", raw: string, was: string | number) => {
    const value = field === "due_day" ? Math.round(Number(raw)) : raw.trim();
    if (value === was || (field === "title" && !value) || (field === "due_day" && !(Number(value) >= 1))) return;
    run(edit(key, { [field]: value }));
  };
  const shift = (i: number, by: number) => {
    const keys = ms.map((m) => m.key);
    const [k] = keys.splice(i, 1);
    keys.splice(i + by, 0, k);
    run(order(keys));
  };
  const usedDays = new Set(ms.map((m) => m.due_day));
  const newDay = Number(day);
  const dayError = day && (!(newDay >= 1 && newDay <= 730) ? "A day between 1 and 730" : usedDays.has(newDay) ? `Day ${newDay} has a milestone` : null);
  return (
    <Dialog wide onClose={onClose} title="Milestones" sub={<>{nameOf(board.hire)}'s board only. Days count from the start date, {shortDate(board.start_date)}; tasks follow their milestone.</>}>
      <ol className="bd-ms-list" aria-busy={busy}>
        {ms.map((m, i) => (
          <li key={`${m.key}:${m.title}:${m.due_day}`} className="bd-ms-row">
            <span className="bd-ms-order">
              <button type="button" className="bd-icon-btn" aria-label={`Move ${m.title} earlier`} disabled={busy || i === 0} onClick={() => shift(i, -1)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 15l-6-6-6 6" /></svg>
              </button>
              <button type="button" className="bd-icon-btn" aria-label={`Move ${m.title} later`} disabled={busy || i === ms.length - 1} onClick={() => shift(i, 1)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
              </button>
            </span>
            <label className="bd-ms-day"><span>Day</span>
              <input type="number" min={1} max={730} defaultValue={m.due_day} aria-label={`Day of ${m.title}`} disabled={busy}
                     onBlur={(e) => commit(m.key, "due_day", e.target.value, m.due_day)}
                     onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
            </label>
            <input className="bd-ms-title" defaultValue={m.title} maxLength={120} aria-label={`Title of day ${m.due_day}`} disabled={busy}
                   onBlur={(e) => commit(m.key, "title", e.target.value, m.title)}
                   onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
            <span className="bd-ms-n">{plural(m.total, "task")}</span>
            <button type="button" className="dp-btn ghost small" disabled={busy || ms.length === 1} onClick={() => { setRemoving(m.key); setMoveTo(""); }}>Remove</button>
            {removing === m.key && (
              <div className="bd-danger bd-ms-remove">
                <p>{m.total ? <>Its {plural(m.total, "task")} move to</> : "Remove this milestone?"}</p>
                {m.total > 0 && (
                  <select value={moveTo} onChange={(e) => setMoveTo(e.target.value)} aria-label="Move its tasks to">
                    <option value="">The next milestone</option>
                    {ms.filter((x) => x.key !== m.key).map((x) => <option key={x.key} value={x.key}>Day {x.due_day} · {x.title}</option>)}
                  </select>
                )}
                <button type="button" className="dp-btn ghost small" onClick={() => setRemoving(null)}>Keep</button>
                <button type="button" className="dp-btn small danger" disabled={busy} onClick={() => run(remove(m.key, moveTo || null).then(() => setRemoving(null)))}>Remove</button>
              </div>
            )}
          </li>
        ))}
      </ol>
      <form className="bd-ms-add" onSubmit={(e) => { e.preventDefault(); if (!title.trim() || dayError || !day) return;
                                                     run(add(title.trim(), newDay).then((r) => { if (r) { setTitle(""); setDay(""); } })); }}>
        <p className="bd-label">Add a milestone</p>
        <div className="bd-ms-add-row">
          <label className="bd-ms-day"><span>Day</span><input type="number" min={1} max={730} value={day} placeholder="e.g. 75" onChange={(e) => setDay(e.target.value)} aria-invalid={!!dayError} /></label>
          <input className="bd-ms-title" value={title} maxLength={120} placeholder="e.g. Leads a design review" aria-label="New milestone's title" onChange={(e) => setTitle(e.target.value)} />
          <button type="submit" className="dp-btn primary small" disabled={busy || !title.trim() || !day || !!dayError}>Add</button>
        </div>
        {dayError && <p className="bd-hint bd-late" role="alert">{dayError}</p>}
        <p className="bd-hint">Usual days are 15, 30, 45, 60 and 90. Template defaults are set in Templates.</p>
      </form>
    </Dialog>
  );
}
