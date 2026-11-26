/** The milestone board's parts: a keyboard menu, a card, a column, the milestone tabs and a milestone's header.
 *  Board.tsx owns the data, drag and drop and the dialogs; these draw and report what was asked. */
import { DragEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Glyph, GlyphName, kindOf } from "../../dataui";
import { nameOf, plural, shortDate } from "../kit";
import type { BoardData, BoardMilestone, Card, Column } from "./types";

/* ---------------------------------------------------------------- dates */

const dayMs = 86_400_000;
const utc = (iso: string) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
/** Whole days from `from` to `to` (YYYY-MM-DD). */
export const daysBetween = (from: string, to: string) => Math.round((utc(to) - utc(from)) / dayMs);

export function dueText(due: string | null, today: string, done: boolean): { text: string; late: boolean } | null {
  if (!due) return null;
  const n = daysBetween(today, due);
  if (done) return { text: `Due ${shortDate(due)}`, late: false };
  if (n < 0) return { text: `${plural(-n, "day")} overdue`, late: true };
  if (n === 0) return { text: "Due today", late: false };
  if (n === 1) return { text: "Due tomorrow", late: false };
  return { text: `Due ${shortDate(due)}`, late: false };
}

export const COLUMN_LABEL: Record<Column, string> = { todo: "To do", in_progress: "In progress", done: "Done" };
export const COLUMNS: Column[] = ["todo", "in_progress", "done"];

/* ---------------------------------------------------------------- a small keyboard menu (role=menu) */

export type MenuItem =
  | { kind: "item"; label: string; onSelect: () => void; disabled?: boolean; hint?: string; tone?: "danger"; checked?: boolean; icon?: ReactNode }
  | { kind: "label"; label: string }
  | { kind: "sep" };

/** A button that opens a menu below it. Arrow keys move, Enter or Space chooses, Escape closes and gives focus back. */
export function MenuButton({ label, items, className = "", children, align = "end", onOpenChange }:
  { label: string; items: MenuItem[]; className?: string; children: ReactNode; align?: "start" | "end"; onOpenChange?: (open: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const [flip, setFlip] = useState(false);
  const set = (v: boolean) => { setOpen(v); onOpenChange?.(v); };
  useLayoutEffect(() => { // open upwards when there is no room below
    if (!open || !menu.current || !btn.current) return;
    const r = btn.current.getBoundingClientRect();
    setFlip(r.bottom + menu.current.offsetHeight + 12 > innerHeight && r.top > menu.current.offsetHeight + 12);
    menu.current.querySelector<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])')?.focus();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (!menu.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) set(false); };
    addEventListener("pointerdown", away, true);
    return () => removeEventListener("pointerdown", away, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const close = (refocus = true) => { set(false); if (refocus) btn.current?.focus(); };
  const keys = (e: ReactKeyboardEvent) => {
    const list = [...(menu.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])') ?? [])];
    const at = list.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      list[(at + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length]?.focus();
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault(); list[e.key === "Home" ? 0 : list.length - 1]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation(); close();
    } else if (e.key === "Tab") {
      close(false);
    }
  };
  return (
    <span className={`bd-menu-wrap ${className}`}>
      <button ref={btn} type="button" className="bd-menu-btn" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined}
              aria-label={label} title={label} onClick={(e) => { e.stopPropagation(); set(!open); }}
              onKeyDown={(e) => { if (e.key === "ArrowDown" && !open) { e.preventDefault(); set(true); } }}>
        {children}
      </button>
      {open && (
        <div ref={menu} id={id} role="menu" aria-label={label} className={`bd-menu ww-panel align-${align}${flip ? " up" : ""}`} onKeyDown={keys}
             onClick={(e) => e.stopPropagation()}>
          {items.map((it, i) => it.kind === "sep" ? <div key={i} role="separator" className="bd-menu-sep" />
            : it.kind === "label" ? <div key={i} role="presentation" className="bd-menu-label">{it.label}</div>
            : (
              <button key={i} type="button" role={it.checked !== undefined ? "menuitemradio" : "menuitem"} tabIndex={-1}
                      aria-checked={it.checked} aria-disabled={it.disabled || undefined} className={`bd-menu-item${it.tone ? ` ${it.tone}` : ""}`}
                      onClick={() => { if (it.disabled) return; close(); it.onSelect(); }}>
                <span className="bd-menu-ic" aria-hidden="true">{it.checked ? <Glyph name="check" size={13} /> : it.icon}</span>
                <span className="bd-menu-t">{it.label}</span>
                {it.hint && <span className="bd-menu-hint">{it.hint}</span>}
              </button>
            ))}
        </div>
      )}
    </span>
  );
}

/* ---------------------------------------------------------------- progress */

export function Bar({ done, total, awaiting = 0, label }: { done: number; total: number; awaiting?: number; label: string }) {
  const pct = total ? (done / total) * 100 : 0;
  const wait = total ? (awaiting / total) * 100 : 0;
  return (
    <span className="bd-bar" role="img" aria-label={`${label}: ${done} of ${total} done${awaiting ? `, ${awaiting} waiting for sign-off` : ""}`}>
      <span className="bd-bar-wait" style={{ transform: `scaleX(${Math.min(1, (pct + wait) / 100)})` }} />
      <span className="bd-bar-fill" style={{ transform: `scaleX(${pct / 100})` }} />
    </span>
  );
}

/* ---------------------------------------------------------------- the milestone tabs */

type TabsProps = {
  board: BoardData; selected: string | null; onSelect: (key: string) => void; all: boolean; onAll: () => void;
  dropTarget: string | null; canDrop: (key: string) => boolean;
  onTabDrag: (key: string | null, e?: DragEvent) => void; onTabDrop: (key: string, e: DragEvent) => void;
};

/** One tab per milestone (role=tablist): its day, title, progress, and whether it is now, overdue or complete. While a
 *  manager drags a card, a tab is a drop target: the card moves to that milestone. */
export function MilestoneTabs({ board, selected, onSelect, all, onAll, dropTarget, canDrop, onTabDrag, onTabDrop }: TabsProps) {
  const list = useRef<HTMLDivElement>(null);
  const keys = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const tabs = [...(list.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? [])];
    const at = tabs.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0) return;
    const go = (i: number) => { e.preventDefault(); const t = tabs[(i + tabs.length) % tabs.length]; t.focus(); t.click(); };
    if (e.key === "ArrowRight") go(at + 1);
    else if (e.key === "ArrowLeft") go(at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(tabs.length - 1);
  };
  useEffect(() => { // keep the chosen tab in view on a narrow screen
    const el = list.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [selected, all]);
  return (
    <div className="bd-tabs" role="tablist" aria-label="Milestones" ref={list} onKeyDown={keys}>
      {board.milestones.map((m) => {
        const on = !all && selected === m.key;
        const drop = dropTarget === m.key;
        return (
          <button key={m.key} type="button" role="tab" id={`bd-tab-${m.key}`} aria-selected={on} aria-controls="bd-panel" tabIndex={on || (all && m.key === board.milestones[0]?.key) ? 0 : -1}
                  className={`bd-tab s-${m.state}${on ? " on" : ""}${drop ? " drop" : ""}`} onClick={() => onSelect(m.key)}
                  onDragOver={(e) => { if (canDrop(m.key)) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (!drop) onTabDrag(m.key, e); } }}
                  onDragLeave={() => drop && onTabDrag(null)}
                  onDrop={(e) => { if (canDrop(m.key)) { e.preventDefault(); onTabDrop(m.key, e); } }}>
            <span className="bd-tab-top">
              <span className="bd-tab-day">Day {m.due_day}</span>
              {m.state === "current" && <span className="bd-now">Now</span>}
              {m.state === "complete" && <span className="bd-tab-ok" aria-label="Complete"><Glyph name="check" size={11} /></span>}
              {m.overdue > 0 && <span className="bd-tab-late" aria-label={`${m.overdue} overdue`}>{m.overdue}</span>}
            </span>
            <span className="bd-tab-title" title={m.title}>{m.title}</span>
            <span className="bd-tab-foot">
              <Bar done={m.done} total={m.total} awaiting={m.awaiting} label={m.title} />
              <span className="bd-tab-count">{m.total ? `${m.done}/${m.total}` : "—"}</span>
            </span>
          </button>
        );
      })}
      <button type="button" role="tab" aria-selected={all} aria-controls="bd-panel" tabIndex={all ? 0 : -1} className={`bd-tab bd-tab-all${all ? " on" : ""}`}
              onClick={onAll}>
        <span className="bd-tab-top"><span className="bd-tab-day">All</span></span>
        <span className="bd-tab-title">Every milestone</span>
        <span className="bd-tab-foot"><span className="bd-tab-count">{board.tasks.filter((t) => t.status === "done").length}/{board.tasks.length}</span></span>
      </button>
    </div>
  );
}

/* ---------------------------------------------------------------- a milestone's header */

const CHECK_STATE: Record<string, string> = { done: "Done", skipped: "Skipped", overdue: "Overdue", due_soon: "Due soon", upcoming: "Upcoming" };

export function MilestoneHead({ m, board, compact, actions }: { m: BoardMilestone; board: BoardData; compact?: boolean; actions?: ReactNode }) {
  const left = daysBetween(board.today, m.due_date);
  const when = m.state === "complete" ? `Complete · due ${shortDate(m.due_date)}`
    : left < 0 ? `Was due ${shortDate(m.due_date)}` : left === 0 ? "Due today" : `Due ${shortDate(m.due_date)} · in ${plural(left, "day")}`;
  const [more, setMore] = useState(false);
  const shown = more ? m.checkpoints : m.checkpoints.slice(0, 4);
  return (
    <div className={`bd-mhead${compact ? " compact" : ""}`}>
      <div className="bd-mhead-main">
        <p className="bd-mhead-eyebrow"><span className="bd-mono">Day {m.from_day}–{m.due_day}</span><span className="bd-dot-sep" aria-hidden="true" />{when}</p>
        <h2 className="bd-mhead-title">{m.title}</h2>
        <p className="bd-mhead-sum">
          <strong>{m.done}</strong> of {plural(m.total, "task")} done
          {m.awaiting > 0 && <> · {m.awaiting} waiting for sign-off</>}
          {m.overdue > 0 && <span className="bd-late"> · {m.overdue} overdue</span>}
        </p>
      </div>
      {actions && <div className="bd-mhead-actions">{actions}</div>}
      {!compact && m.checkpoints.length > 0 && (
        <div className="bd-checks">
          <p className="bd-label">Checkpoints</p>
          <ul>
            {shown.map((c) => (
              <li key={c.key} className={`c-${c.state}`} title={`${c.title} · day ${c.due_day} · ${(CHECK_STATE[c.state] ?? c.state).toLowerCase()}`}>
                <span className="bd-diamond" aria-hidden="true" />
                <span className="bd-check-t">{c.title}</span>
                <span className="sr-only">, {CHECK_STATE[c.state] ?? c.state}</span>
              </li>
            ))}
            {m.checkpoints.length > 4 && (
              <li><button type="button" className="bd-linkish" onClick={() => setMore(!more)} aria-expanded={more}>
                {more ? "Fewer" : `${m.checkpoints.length - 4} more`}
              </button></li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- a card */

export type CardHandlers = {
  open: (c: Card) => void; menu: (c: Card) => MenuItem[];
  dragStart: (c: Card, e: DragEvent) => void; dragEnd: () => void;
  keyMove: (c: Card, dir: "left" | "right" | "up" | "down", milestone: boolean) => void;
};

const KIND_ICON: Record<string, GlyphName> = { learn: "learn", code: "code", meet: "meet" };

export function CardView({ card, board, dragging, dropBefore, busy, h, milestoneTitle }:
  { card: Card; board: BoardData; dragging: boolean; dropBefore: boolean; busy: boolean; h: CardHandlers; milestoneTitle?: string }) {
  const due = dueText(card.due_date, board.today, card.column === "done");
  const k = kindOf(card.kind);
  const draggable = card.moves.length > 0;
  const keys = (e: ReactKeyboardEvent<HTMLElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); h.open(card); return; }
    if (!e.altKey) return;
    const dir = ({ ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" } as const)[e.key as "ArrowLeft"];
    if (dir) { e.preventDefault(); h.keyMove(card, dir, e.shiftKey); }
  };
  const label = [card.title, COLUMN_LABEL[card.column], card.badge, card.kt ? "from a KT session" : null, card.moved ? `moved from ${card.moved.from_title}` : null,
    due?.text].filter(Boolean).join(", ");
  return (
    <article data-card={card.id} className={`bd-card k-${card.kind}${dragging ? " dragging" : ""}${dropBefore ? " drop-before" : ""}${busy ? " busy" : ""}${card.status === "done" ? " is-done" : ""}${card.kt ? " is-kt" : ""}`}
             tabIndex={0} aria-label={label} aria-roledescription="task card" draggable={draggable}
             onDragStart={(e) => h.dragStart(card, e)} onDragEnd={h.dragEnd} onKeyDown={keys}
             onClick={(e) => { if (!(e.target as HTMLElement).closest("button, a")) h.open(card); }}>
      <div className="bd-card-top">
        <span className="bd-kind"><Glyph name={KIND_ICON[card.kind] ?? "task"} size={13} />{k.label}</span>
        {card.kt && <span className="kt-badge" title="Explained in a recorded KT session">KT</span>}
        <MenuButton label={`Actions for ${card.title}`} items={h.menu(card)} className="bd-card-menu"><span aria-hidden="true">⋯</span></MenuButton>
      </div>
      <h3 className="bd-card-title">{card.title}</h3>
      {(card.badge || card.moved || milestoneTitle) && (
        <div className="bd-card-tags">
          {milestoneTitle && <span className="bd-tag">{milestoneTitle}</span>}
          {card.badge && <span className={`bd-tag ${card.status === "changes_requested" ? "warn" : "wait"}`}>{card.badge}</span>}
          {card.moved && (
            <span className="bd-tag moved" title={`Moved ${shortDate(card.moved.at)}${card.moved.by ? ` by ${nameOf(card.moved.by)}` : ""}`}>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M15 4l5 5-5 5" /><path d="M4 20v-7a4 4 0 0 1 4-4h12" />
              </svg>
              From {card.moved.from_day != null ? `Day ${card.moved.from_day}` : card.moved.from_title}
            </span>
          )}
        </div>
      )}
      <div className="bd-card-meta">
        {due && <span className={`bd-due${due.late ? " late" : ""}`}>{due.late && <Glyph name="alert" size={12} />}{due.text}</span>}
        {card.pr_number && card.pr_url && <a className="bd-pr" href={card.pr_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
          <Glyph name="pr" size={12} />#{card.pr_number}</a>}
        {card.status === "done" && <span className="bd-done-mark"><Glyph name="check" size={12} />Done</span>}
      </div>
    </article>
  );
}

/* ---------------------------------------------------------------- a column */

export type ColumnProps = {
  board: BoardData; milestone: string; column: Column; cards: Card[]; drag: Card | null;
  over: { milestone: string; column: Column; index: number } | null; busy: Set<string>; h: CardHandlers;
  allowed: boolean; onOver: (milestone: string, column: Column, index: number) => void; onDrop: (milestone: string, column: Column) => void;
  compact?: boolean; empty?: ReactNode; titles?: Record<string, string>;
};

const COL_HELP: Record<Column, string> = {
  todo: "Not started yet.", in_progress: "Being worked on, or changes were asked for.", done: "Finished. Some wait for a manager's sign-off.",
};

export function ColumnView({ board, milestone, column, cards, drag, over, busy, h, allowed, onOver, onDrop, compact, empty, titles }: ColumnProps) {
  const body = useRef<HTMLDivElement>(null);
  const index = (y: number) => {
    const els = [...(body.current?.querySelectorAll<HTMLElement>("[data-card]") ?? [])].filter((el) => el.dataset.card !== drag?.id);
    const i = els.findIndex((el) => { const r = el.getBoundingClientRect(); return y < r.top + r.height / 2; });
    return i < 0 ? els.length : i;
  };
  const here = over && over.milestone === milestone && over.column === column;
  const visible = cards.filter((c) => c.id !== drag?.id || !here);
  const shown = drag && here ? cards.filter((c) => c.id !== drag.id) : cards;
  const beforeId = here ? shown[over!.index]?.id : undefined;
  const headId = `bd-col-${milestone}-${column}`;
  return (
    <section className={`bd-col c-${column}${drag ? (allowed ? " can-drop" : " no-drop") : ""}${here ? " over" : ""}${compact ? " compact" : ""}`}
             aria-labelledby={headId}
             onDragOver={(e) => { if (!allowed) return; e.preventDefault(); e.dataTransfer.dropEffect = "move"; const i = index(e.clientY); if (!here || over!.index !== i) onOver(milestone, column, i); }}
             onDrop={(e) => { if (!allowed) return; e.preventDefault(); onDrop(milestone, column); }}>
      <header className="bd-col-head">
        <span className={`bd-col-dot d-${column}`} aria-hidden="true" />
        <h3 id={headId} className="bd-col-title" title={COL_HELP[column]}>{COLUMN_LABEL[column]}</h3>
        <span className="bd-col-count" aria-label={plural(cards.length, "task")}>{cards.length}</span>
      </header>
      <div className={`bd-col-body${here && !beforeId ? " drop-end" : ""}`} ref={body} role="list" aria-label={`${COLUMN_LABEL[column]}, ${plural(cards.length, "task")}`}>
        {visible.length === 0 && !here && <div className="bd-col-empty">{drag && allowed ? "Drop here" : empty ?? "Nothing here"}</div>}
        {cards.map((c) => (
          <div role="listitem" key={c.id} className="bd-li">
            <CardView card={c} board={board} dragging={drag?.id === c.id} dropBefore={beforeId === c.id} busy={busy.has(c.id)} h={h}
                      milestoneTitle={titles?.[c.milestone]} />
          </div>
        ))}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- moved out of a milestone */

export function MovedOut({ m, onShow }: { m: BoardMilestone; onShow: (key: string, taskId: string) => void }) {
  if (!m.moved_out.length) return null;
  return (
    <details className="bd-moved">
      <summary>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M15 4l5 5-5 5" /><path d="M4 20v-7a4 4 0 0 1 4-4h12" />
        </svg>
        Moved out of this milestone <span className="bd-col-count">{m.moved_out.length}</span>
      </summary>
      <ul>
        {m.moved_out.map((g) => (
          <li key={g.task_id}>
            <span className="bd-moved-t">{g.title}</span>
            <span className="bd-moved-to">to {g.to_title ?? "another milestone"}</span>
            <span className="bd-moved-when">{shortDate(g.at)}{g.by ? ` · ${nameOf(g.by)}` : ""}</span>
            <button type="button" className="bd-linkish" onClick={() => onShow(g.to_key, g.task_id)}>Show</button>
          </li>
        ))}
      </ul>
    </details>
  );
}
