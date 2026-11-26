/** #/board[/<onboardingId>][?m=<milestone>&view=all]: the milestone board. One hire's tasks by milestone (Day 15, 30, 45, 60,
 *  90 ...) and by state (To do, In progress, Done). The hire opens their own and moves their cards between columns; the
 *  project's managers and admins pick any hire, move cards between milestones, sign off, and change the tasks and the
 *  milestones themselves. Drag and drop (native HTML5), or the keyboard: Alt+arrows on a focused card, or its ⋯ menu.
 *  A move shows at once and is undone if the server refuses it. API: app/api/board.py; rules: app/lifecycle/board.py. */
import { DragEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, patch, post } from "../../api";
import { useSession } from "../../session";
import { replaceRoute } from "../../route";
import { toast } from "../../ui";
import { EmptyState } from "../../fx";
import { Avatar, Glyph } from "../../dataui";
import { forgetShared, How, isStatus, LoadError, nameOf, plural, useApi, useCrumb } from "../kit";
import { Bar, CardHandlers, COLUMN_LABEL, COLUMNS, ColumnView, MenuButton, MenuItem, MilestoneHead, MilestoneTabs, MovedOut } from "./parts";
import { AddTaskDialog, MilestonesDialog, TaskDialog } from "./dialogs";
import type { BoardData, BoardHires, BoardMilestone, Card, Column, MoveBody } from "./types";
import "./board.css";

const pickKey = (projectId: string) => `ww-board:${projectId}`;

export default function Board({ id: routeId, params }: { id: string | null; params: URLSearchParams }) {
  const { me, project } = useSession();
  const hires = useApi<BoardHires>(`/board/hires?project_id=${encodeURIComponent(project.id)}`, ["lifecycle", "people", "tasks"]);
  const remembered = (() => { try { return localStorage.getItem(pickKey(project.id)); } catch { return null; } })();
  const list = hires.data?.hires ?? [];
  const id = routeId ?? (list.find((h) => h.onboarding_id === remembered)?.onboarding_id ?? list[0]?.onboarding_id ?? null);
  useEffect(() => { // a board chosen by default gets its address, so a reload or a shared link lands on it
    if (!routeId && id) replaceRoute(`#/board/${encodeURIComponent(id)}${params.toString() ? `?${params}` : ""}`);
  }, [routeId, id, params]);
  useEffect(() => { if (id) try { localStorage.setItem(pickKey(project.id), id); } catch { /* storage blocked */ } }, [id, project.id]);

  if (hires.error && !hires.data) return <section className="lc bd"><LoadError error={hires.error} retry={hires.reload} /></section>;
  if (!hires.data) return <section className="lc bd"><BoardSkeleton /></section>;
  if (!id) {
    return (
      <section className="lc bd">
        <BoardHead title={me.role === "new_hire" ? "My board" : "Board"} sub={null} />
        {hires.data.can_manage
          ? <EmptyState title="No one to show yet" action={<a className="dp-btn primary" href="#/enrol">Enrol a new hire</a>}>
              Each new hire gets a board with their milestones and tasks once you enrol them.</EmptyState>
          : <EmptyState title="Your board appears once you are enrolled" action={<a className="dp-btn" href="#/home">Back to Home</a>}>
              Your manager enrols you in a program; its milestones and tasks show up here.</EmptyState>}
      </section>
    );
  }
  return <HireBoard key={id} id={id} params={params} hires={hires.data} />;
}

/* ---------------------------------------------------------------- one hire's board */

type Drag = { card: Card; from: string };
type Over = { milestone: string; column: Column; index: number };

function HireBoard({ id, params, hires }: { id: string; params: URLSearchParams; hires: BoardHires }) {
  const { me } = useSession();
  const path = `/onboardings/${encodeURIComponent(id)}/board`;
  const loaded = useApi<BoardData>(path, ["lifecycle", "tasks", "approvals"]);
  const b = loaded.data;
  const own = b?.hire?.id === me.id;
  const name = b ? nameOf(b.hire) : "";
  useCrumb(b ? (own ? "My board" : name) : null);

  const all = params.get("view") === "all";
  const wanted = params.get("m");
  const selected = b ? (b.milestones.find((m) => m.key === wanted)?.key ?? b.current ?? b.milestones[0]?.key ?? null) : null;
  const go = (q: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(q)) if (v) next.set(k, v); else next.delete(k);
    const s = next.toString();
    replaceRoute(`#/board/${encodeURIComponent(id)}${s ? `?${s}` : ""}`);
  };

  // ---- writes: optimistic, undone when the server refuses
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [said, setSaid] = useState("");
  const refocus = useRef<string | null>(null);
  const say = (text: string) => { setSaid(""); requestAnimationFrame(() => setSaid(text)); };
  const write = useCallback(async (label: string, fn: () => Promise<BoardData>, optimistic?: (d: BoardData) => BoardData, cardId?: string) => {
    const before = loaded.data;
    if (optimistic && before) loaded.set(() => optimistic(before));
    if (cardId) setBusy((s) => new Set(s).add(cardId));
    try {
      const next = await fn();
      forgetShared();
      loaded.set(() => next);
      say(label);
      return next;
    } catch (e) {
      if (before) loaded.set(() => before);
      toast(errorMessage(e), "error");
      say(`Not saved: ${errorMessage(e)}`);
      return null;
    } finally {
      if (cardId) setBusy((s) => { const n = new Set(s); n.delete(cardId); return n; });
    }
  }, [loaded]);

  const move = (card: Card, body: MoveBody, label?: string) => {
    if (!b) return;
    const to = body.milestone ?? card.milestone;
    const m = b.milestones.find((x) => x.key === to);
    const text = label ?? describe(card, body, b, m);
    return write(text, () => post<BoardData>(`${path}/tasks/${encodeURIComponent(card.id)}/move`, body),
      (d) => applyMove(d, card.id, body, b.can.edit), card.id);
  };

  // ---- drag and drop
  const [drag, setDrag] = useState<Drag | null>(null);
  const [over, setOver] = useState<Over | null>(null);
  const [tabDrop, setTabDrop] = useState<string | null>(null);
  const allowed = (milestone: string, column: Column) =>
    !!drag && drag.card.moves.includes(column) && (milestone === drag.card.milestone || !!b?.can.reschedule);
  const dragStart = (card: Card, e: DragEvent) => {
    if (!card.moves.length) { e.preventDefault(); return; }
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", card.title);
    const el = e.currentTarget as HTMLElement;
    e.dataTransfer.setDragImage(el, Math.min(40, el.offsetWidth / 2), 20);
    requestAnimationFrame(() => setDrag({ card, from: card.milestone }));
  };
  const dragEnd = () => { setDrag(null); setOver(null); setTabDrop(null); };
  const drop = (milestone: string, column: Column) => {
    if (!drag || !b) return;
    const lane = b.tasks.filter((t) => t.milestone === milestone && t.column === column && t.id !== drag.card.id);
    const index = over && over.milestone === milestone && over.column === column ? over.index : lane.length;
    const before = lane[index]?.id ?? null;
    const card = drag.card;
    dragEnd();
    const same = card.milestone === milestone && card.column === column;
    const was = b.tasks.filter((t) => t.milestone === milestone && t.column === column);
    if (same && was.findIndex((t) => t.id === card.id) === index) return; // dropped where it was
    move(card, { column, ...(milestone !== card.milestone ? { milestone } : {}), before_id: before });
  };
  const dropOnTab = (key: string) => {
    if (!drag) return;
    const card = drag.card;
    dragEnd();
    if (key !== card.milestone) move(card, { milestone: key });
  };

  // ---- keyboard: Alt+left/right moves a focused card between columns, Alt+up/down within its column; with Shift, left/right
  // moves it to the milestone before or after (managers)
  const keyMove = (card: Card, dir: "left" | "right" | "up" | "down", shift: boolean) => {
    if (!b || !card.moves.length) return;
    if (dir === "up" || dir === "down") {
      const lane = b.tasks.filter((t) => t.milestone === card.milestone && t.column === card.column);
      const at = lane.findIndex((t) => t.id === card.id);
      if ((dir === "up" && at <= 0) || (dir === "down" && at >= lane.length - 1)) return;
      refocus.current = card.id;
      move(card, { column: card.column, before_id: dir === "up" ? lane[at - 1].id : lane[at + 2]?.id ?? null },
           `${card.title} moved ${dir}`);
      return;
    }
    if (shift) {
      if (!b.can.reschedule) { say("Your manager moves tasks between milestones."); return; }
      const at = b.milestones.findIndex((m) => m.key === card.milestone);
      const next = b.milestones[at + (dir === "right" ? 1 : -1)];
      if (!next) return;
      if (all) refocus.current = card.id; // every milestone is on screen: the card keeps focus in its new lane
      else {
        // the card leaves the milestone on screen: focus stays where it was, on the card after it (or before it) in its
        // column, else on the milestone tab it went to; the live region says where it went
        const lane = b.tasks.filter((t) => t.milestone === card.milestone && t.column === card.column);
        const i = lane.findIndex((t) => t.id === card.id);
        const near = lane[i + 1] ?? lane[i - 1];
        const el = near ? document.querySelector<HTMLElement>(`[data-card="${CSS.escape(near.id)}"]`) : document.getElementById(`bd-tab-${next.key}`);
        el?.focus();
        refocus.current = null;
      }
      move(card, { milestone: next.key });
      return;
    }
    const target = COLUMNS[COLUMNS.indexOf(card.column) + (dir === "right" ? 1 : -1)];
    if (!target) return;
    if (!card.moves.includes(target)) { say(`${card.title} cannot move to ${COLUMN_LABEL[target]}.`); return; }
    refocus.current = card.id;
    move(card, { column: target });
  };
  useEffect(() => { // after a keyboard move the card keeps focus, wherever it went; one try, so focus never jumps later
    if (!refocus.current) return;
    const el = document.querySelector<HTMLElement>(`[data-card="${CSS.escape(refocus.current)}"]`);
    refocus.current = null;
    el?.focus({ preventScroll: false });
  }, [b, selected, all]);

  // ---- dialogs
  const [detail, setDetail] = useState<string | null>(null);
  const [adding, setAdding] = useState<string | null>(null);
  const [editMilestones, setEditMilestones] = useState(false);

  const menu = (card: Card): MenuItem[] => {
    if (!b) return [];
    const items: MenuItem[] = [{ kind: "item", label: "Open", onSelect: () => setDetail(card.id), hint: "Enter" }, { kind: "label", label: "Move to" }];
    for (const col of COLUMNS) {
      items.push({ kind: "item", label: COLUMN_LABEL[col], checked: card.column === col, disabled: !card.moves.includes(col) || card.column === col,
                   onSelect: () => move(card, { column: col }) });
    }
    if (b.can.edit && card.status === "submitted") {
      items.push({ kind: "sep" }, { kind: "item", label: "Sign off", icon: <Glyph name="check" size={13} />, disabled: card.review_pending,
                   onSelect: () => move(card, { sign_off: true }, `${card.title} signed off`) },
                 { kind: "item", label: "Ask for changes", disabled: card.review_pending,
                   onSelect: () => move(card, { column: "in_progress", changes_requested: true }, `Changes asked for on ${card.title}`) });
    }
    if (b.can.reschedule) {
      items.push({ kind: "label", label: "Milestone" });
      for (const m of b.milestones) {
        items.push({ kind: "item", label: `Day ${m.due_day} · ${m.title}`, checked: card.milestone === m.key, disabled: card.milestone === m.key,
                     onSelect: () => move(card, { milestone: m.key }) });
      }
    }
    if (b.can.edit) items.push({ kind: "sep" }, { kind: "item", label: "Remove from board", tone: "danger", onSelect: () => setDetail(`remove:${card.id}`) });
    return items;
  };
  const h: CardHandlers = { open: (c) => setDetail(c.id), menu, dragStart, dragEnd, keyMove };

  if (loaded.error && !b) {
    return (
      <section className="lc bd">
        {isStatus(loaded.error, 403, 404)
          ? <EmptyState title="This board is not available" action={<a className="dp-btn" href="#/home">Back to Home</a>}>
              Boards are for the new hire and the project's managers.</EmptyState>
          : <LoadError error={loaded.error} retry={loaded.reload} />}
      </section>
    );
  }
  if (!b) return <section className="lc bd"><BoardSkeleton /></section>;

  const titles = Object.fromEntries(b.milestones.map((m) => [m.key, `Day ${m.due_day}`]));
  const groups = group(b);
  const done = b.tasks.filter((t) => t.status === "done").length;
  const overdue = b.tasks.filter((t) => t.overdue).length;
  const sel = b.milestones.find((m) => m.key === selected) ?? null;
  const card = detail ? b.tasks.find((t) => t.id === detail.replace(/^remove:/, "")) ?? null : null;
  const lane = (m: BoardMilestone, compact: boolean) => (
    <div className="bd-cols" key={m.key}>
      {COLUMNS.map((col) => (
        <ColumnView key={col} board={b} milestone={m.key} column={col} cards={groups[m.key]?.[col] ?? []} drag={drag?.card ?? null}
                    over={over} busy={busy} h={h} allowed={allowed(m.key, col)} compact={compact}
                    onOver={(ms, c, i) => setOver({ milestone: ms, column: c, index: i })} onDrop={drop}
                    empty={col === "todo" && m.total === 0 ? (b.can.edit ? "No tasks yet" : "Nothing planned yet") : undefined} />
      ))}
    </div>
  );

  return (
    <section className={`lc bd${drag ? " is-dragging" : ""}`}>
      <BoardHead
        title={own ? "My board" : `${name.split(" ")[0]}'s board`}
        sub={<>
          <span>Day {Math.max(b.day, 0)} of {b.duration_days}</span><span className="bd-dot-sep" aria-hidden="true" />
          <span>{b.role_title}</span><span className="bd-dot-sep" aria-hidden="true" />
          <span><strong>{done}</strong> of {plural(b.tasks.length, "task")} done</span>
          {overdue > 0 && <><span className="bd-dot-sep" aria-hidden="true" /><span className="bd-late">{overdue} overdue</span></>}
          {b.status !== "active" && <span className="bd-tag">{b.status === "completed" ? "Completed" : "Paused"}</span>}
        </>}
        picker={hires.can_manage && hires.hires.length > 0 ? <HirePicker hires={hires} current={id} /> : null}
        actions={<>
          <span className="bd-keys-hint"><How label="Keys" title="Keyboard" align="end" text={<ul className="bd-keys">
            <li><kbd>Tab</kbd> to a card, <kbd>Enter</kbd> opens it</li>
            <li><kbd>Alt</kbd> + <kbd>←</kbd> <kbd>→</kbd> moves it between columns</li>
            <li><kbd>Alt</kbd> + <kbd>↑</kbd> <kbd>↓</kbd> moves it up or down</li>
            {b.can.reschedule && <li><kbd>Alt</kbd> + <kbd>Shift</kbd> + <kbd>←</kbd> <kbd>→</kbd> moves it to another milestone</li>}
            <li>Or use a card's <span aria-hidden="true">⋯</span> menu</li></ul>} /></span>
          {b.can.edit && <button type="button" className="dp-btn" onClick={() => setEditMilestones(true)}><Glyph name="layers" size={14} />Milestones</button>}
          {b.can.edit && <button type="button" className="dp-btn primary" onClick={() => setAdding(all ? b.current ?? b.milestones[0].key : selected)}>
            <Glyph name="plus" size={14} />Task</button>}
          {!own && <a className="dp-btn ghost" href={`#/program/${encodeURIComponent(id)}`}>Program<Glyph name="arrow" size={13} /></a>}
        </>}
      />

      {b.waiting_approval > 0 && (
        <div className="bd-callout" role="note">
          <Glyph name="clock" size={15} />
          <p>{plural(b.waiting_approval, "proposed task")} {b.waiting_approval === 1 ? "waits" : "wait"} for {b.can.edit ? "your approval" : "your manager's approval"}.
            {b.can.edit ? " Approved tasks join this board." : " They join your board once approved."}</p>
          {b.can.edit && <a className="dp-btn small" href="#/approvals">Review in Approvals</a>}
        </div>
      )}

      <MilestoneTabs board={b} selected={selected} all={all} onSelect={(k) => go({ m: k, view: null })} onAll={() => go({ view: "all", m: null })}
                     dropTarget={tabDrop} canDrop={(k) => !!drag && !!b.can.reschedule && k !== drag.card.milestone}
                     onTabDrag={(k) => setTabDrop(k)} onTabDrop={(k) => dropOnTab(k)} />

      <div id="bd-panel" role="tabpanel" aria-labelledby={all ? undefined : `bd-tab-${selected}`} aria-label={all ? "Every milestone" : undefined} className="bd-panel">
        {b.tasks.length === 0 && (
          <div className="bd-empty">
            <EmptyState title={b.can.edit ? "No tasks on this board yet" : "Nothing on your board yet"}
                        action={b.can.edit ? <button type="button" className="dp-btn primary" onClick={() => setAdding(selected)}><Glyph name="plus" size={14} />Add a task</button> : undefined}>
              {b.waiting_approval ? "The plan's tasks join it once they are approved." : b.can.edit ? "Add tasks to a milestone, or approve a plan." : "Tasks appear here when your manager adds or approves them."}
            </EmptyState>
          </div>
        )}
        {b.tasks.length > 0 && !all && sel && (
          <div className="bd-stage" key={sel.key}>
            <MilestoneHead m={sel} board={b} actions={b.can.edit ? (
              <button type="button" className="dp-btn small ghost" onClick={() => setAdding(sel.key)}><Glyph name="plus" size={13} />Task</button>) : undefined} />
            {lane(sel, false)}
            <MovedOut m={sel} onShow={(key, taskId) => { go({ m: key, view: null }); refocus.current = taskId; }} />
          </div>
        )}
        {b.tasks.length > 0 && all && (
          <div className="bd-lanes">
            {b.milestones.map((m) => (
              <section key={m.key} className={`bd-lane s-${m.state}`} aria-label={`Day ${m.due_day}: ${m.title}`}>
                <MilestoneHead m={m} board={b} compact actions={<>
                  <Bar done={m.done} total={m.total} awaiting={m.awaiting} label={m.title} />
                  <button type="button" className="bd-linkish" onClick={() => go({ m: m.key, view: null })}>Open</button>
                </>} />
                {lane(m, true)}
              </section>
            ))}
          </div>
        )}
      </div>

      <p className="sr-only" role="status" aria-live="polite">{said}</p>

      {card && (
        <TaskDialog key={card.id} card={card} board={b} confirmRemove={!!detail?.startsWith("remove:")} onClose={() => setDetail(null)}
                    move={(body, label) => move(card, body, label)}
                    save={(fields) => write("Task saved", () => patch<BoardData>(`${path}/tasks/${encodeURIComponent(card.id)}`, fields))}
                    remove={() => write(`${card.title} removed`, () => api<BoardData>(`${path}/tasks/${encodeURIComponent(card.id)}`, { method: "DELETE" }))
                      .then((r) => { if (r) setDetail(null); return r; })} />
      )}
      {adding !== null && (
        <AddTaskDialog board={b} milestone={adding} onClose={() => setAdding(null)}
                       add={(body) => write(`${body.title} added`, () => post<BoardData>(`${path}/tasks`, body)).then((r) => { if (r) { setAdding(null); toast("Task added"); } return r; })} />
      )}
      {editMilestones && (
        <MilestonesDialog board={b} onClose={() => setEditMilestones(false)}
                          add={(title, due_day) => write(`Milestone ${title} added`, () => post<BoardData>(`${path}/milestones`, { title, due_day }))}
                          edit={(key, fields) => write("Milestone saved", () => patch<BoardData>(`${path}/milestones/${encodeURIComponent(key)}`, fields))}
                          order={(keys) => write("Milestones reordered", () => api<BoardData>(`${path}/milestones/order`, { method: "PUT", body: JSON.stringify({ keys }) }))}
                          remove={(key, moveTo) => write("Milestone removed", () => api<BoardData>(`${path}/milestones/${encodeURIComponent(key)}${moveTo ? `?move_to=${encodeURIComponent(moveTo)}` : ""}`, { method: "DELETE" }))} />
      )}
    </section>
  );
}


/* ---------------------------------------------------------------- header and hire picker */

function BoardHead({ title, sub, picker, actions }: { title: string; sub: ReactNode; picker?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="bd-head">
      <div className="bd-head-text">
        <p className="bd-eyebrow">Milestone board</p>
        <div className="bd-title-row"><h1>{title}</h1>{picker}</div>
        {sub && <div className="bd-sub">{sub}</div>}
      </div>
      {actions && <div className="bd-head-actions">{actions}</div>}
    </header>
  );
}

function HirePicker({ hires, current }: { hires: BoardHires; current: string }) {
  const now = hires.hires.find((h) => h.onboarding_id === current);
  const items: MenuItem[] = [{ kind: "label", label: "New hires" }, ...hires.hires.map((h): MenuItem => ({
    kind: "item", label: `${nameOf(h.name)} · Day ${Math.max(h.day, 0)}`, checked: h.onboarding_id === current,
    hint: h.total ? `${h.done}/${h.total}${h.overdue ? ` · ${h.overdue} late` : ""}` : "No tasks",
    onSelect: () => { location.hash = `#/board/${encodeURIComponent(h.onboarding_id)}`; },
  }))];
  return (
    <MenuButton label="Choose a new hire's board" items={items} className="bd-picker" align="start">
      <Avatar name={nameOf(now?.name ?? "")} size={22} />
      <span className="bd-picker-name">{nameOf(now?.name ?? "Choose")}</span>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
    </MenuButton>
  );
}

function BoardSkeleton() {
  return (
    <div className="bd-skel" aria-busy="true" aria-label="Loading the board">
      <div className="bd-skel-head"><span /><span /></div>
      <div className="bd-skel-tabs">{[0, 1, 2, 3, 4].map((i) => <span key={i} />)}</div>
      <div className="bd-skel-cols">{[0, 1, 2].map((i) => <div key={i}><span /><span /><span /></div>)}</div>
    </div>
  );
}

/* ---------------------------------------------------------------- the data */

/** Cards by milestone and column, in the server's order. */
function group(b: BoardData): Record<string, Record<Column, Card[]>> {
  const out: Record<string, Record<Column, Card[]>> = {};
  for (const m of b.milestones) out[m.key] = { todo: [], in_progress: [], done: [] };
  for (const t of b.tasks) (out[t.milestone] ??= { todo: [], in_progress: [], done: [] })[t.column].push(t);
  return out;
}

/** What a move does, shown before the server answers (it replaces this with the real board, or undoes it). */
function applyMove(d: BoardData, id: string, body: MoveBody, manage: boolean): BoardData {
  const card = d.tasks.find((t) => t.id === id);
  if (!card) return d;
  const column: Column = body.sign_off ? "done" : body.changes_requested ? "in_progress" : body.column ?? card.column;
  const milestone = body.milestone ?? card.milestone;
  const status: Card["status"] = body.sign_off ? "done" : body.changes_requested ? "changes_requested"
    : column === card.column ? card.status : column === "todo" ? "approved" : column === "in_progress" ? "in_progress" : manage ? "done" : "submitted";
  const badge = status === "submitted" ? (card.pr_number ? "In review" : "Waiting for sign-off") : status === "changes_requested" ? "Changes requested" : null;
  const moved: Card = { ...card, column, milestone, status, badge, overdue: column !== "done" && !!card.due_date && card.due_date < d.today };
  const rest = d.tasks.filter((t) => t.id !== id);
  let at = body.before_id ? rest.findIndex((t) => t.id === body.before_id) : -1;
  if (at < 0) {
    let last = -1;
    rest.forEach((t, i) => { if (t.milestone === milestone && t.column === column) last = i; });
    at = last >= 0 ? last + 1 : rest.length;
  }
  rest.splice(at, 0, moved);
  const milestones = d.milestones.map((m) => {
    const mine = rest.filter((t) => t.milestone === m.key);
    const doneN = mine.filter((t) => t.status === "done").length;
    return { ...m, total: mine.length, done: doneN, awaiting: mine.filter((t) => t.status === "submitted").length,
             overdue: mine.filter((t) => t.overdue).length,
             counts: { todo: mine.filter((t) => t.column === "todo").length, in_progress: mine.filter((t) => t.column === "in_progress").length,
                       done: mine.filter((t) => t.column === "done").length },
             state: mine.length && doneN === mine.length ? "complete" as const : m.state === "complete" ? "current" as const : m.state };
  });
  return { ...d, tasks: rest, milestones };
}

function describe(card: Card, body: MoveBody, b: BoardData, m: BoardMilestone | undefined): string {
  if (body.milestone && body.milestone !== card.milestone) return `${card.title} moved to Day ${m?.due_day} · ${m?.title}`;
  if (body.sign_off) return `${card.title} signed off`;
  const col = body.column ?? card.column;
  if (col === card.column) return `${card.title} reordered`;
  if (col === "done" && !b.can.edit) return `${card.title} moved to Done, waiting for sign-off`;
  return `${card.title} moved to ${COLUMN_LABEL[col]}`;
}
