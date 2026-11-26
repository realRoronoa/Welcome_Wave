/** Accordions for the KT pages: one section open at a time, the rest folded to a single header line.
 *  The first section (or `initial`) starts open; opening another closes it. A header carries its title, an optional
 *  one-line summary and meta (chips, counts), and optional actions beside it (buttons cannot sit inside the toggle).
 *  Bodies mount on first open (a long transcript costs nothing until it is asked for) and stay mounted after. */
import { createContext, ReactNode, useContext, useEffect, useId, useRef, useState } from "react";
import { reducedMotion } from "./fx";
import "./styles/accordion.css";

type Ctx = { open: string | null; toggle: (id: string) => void; clicked: React.MutableRefObject<string | null> };
const AccordionContext = createContext<Ctx | null>(null);

type AccordionProps = {
  children: ReactNode;
  /** uncontrolled: the section open at first (null: all folded) */
  initial?: string | null;
  /** controlled: the open section, and the request to change it */
  open?: string | null;
  onChange?: (id: string | null) => void;
  className?: string;
};

export function Accordion({ children, initial = null, open, onChange, className }: AccordionProps) {
  const [own, setOwn] = useState<string | null>(initial);
  const current = open !== undefined ? open : own;
  const clicked = useRef<string | null>(null);
  const toggle = (id: string) => {
    const next = current === id ? null : id;
    clicked.current = next;
    if (open === undefined) setOwn(next);
    onChange?.(next);
  };
  return (
    <AccordionContext.Provider value={{ open: current, toggle, clicked }}>
      <div className={`acc${className ? ` ${className}` : ""}`}>{children}</div>
    </AccordionContext.Provider>
  );
}

type ItemProps = {
  id: string;
  title: ReactNode;
  children: ReactNode;
  eyebrow?: ReactNode;
  /** one quiet line under the title: what is inside, so a folded section still says enough */
  sub?: ReactNode;
  count?: number;
  icon?: ReactNode;
  /** chips on the right of the header (inside the toggle: not interactive) */
  meta?: ReactNode;
  /** buttons or links beside the header (outside the toggle) */
  actions?: ReactNode;
  level?: 2 | 3;
  className?: string;
  domId?: string;
};

export function AccordionItem({ id, title, children, eyebrow, sub, count, icon, meta, actions, level = 3, className, domId }: ItemProps) {
  const ctx = useContext(AccordionContext);
  if (!ctx) throw new Error("AccordionItem must be inside an Accordion");
  const open = ctx.open === id;
  const [seen, setSeen] = useState(open);
  useEffect(() => { if (open) setSeen(true); }, [open]);
  const uid = useId();
  const root = useRef<HTMLElement>(null);

  // Opening a section folds the one above it, which can carry this header out of view: bring it back once things settle.
  useEffect(() => {
    if (!open || ctx.clicked.current !== id) return;
    ctx.clicked.current = null;
    const t = window.setTimeout(() => {
      const el = root.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top;
      if (top < 72 || top > innerHeight - 96) el.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
    }, reducedMotion() ? 0 : 300);
    return () => window.clearTimeout(t);
  }, [open, id, ctx.clicked]);

  const H = level === 2 ? "h2" : "h3";
  const panel = { inert: open ? undefined : "" } as Record<string, string | undefined>; // React 18 passes inert through as a string
  return (
    <section ref={root} id={domId} className={`acc-item${open ? " open" : ""}${icon ? " has-icon" : ""}${className ? ` ${className}` : ""}`}>
      <div className="acc-head">
        <H className="acc-h">
          <button type="button" className="acc-trigger" id={`${uid}-b`} aria-expanded={open} aria-controls={`${uid}-p`} onClick={() => ctx.toggle(id)}>
            <svg className="acc-chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
            {icon && <span className="acc-icon" aria-hidden="true">{icon}</span>}
            <span className="acc-text">
              {eyebrow && <span className="acc-eyebrow">{eyebrow}</span>}
              <span className="acc-title">{title}{count !== undefined && <span className="dp-count">{count}</span>}</span>
              {sub && <span className="acc-sub">{sub}</span>}
            </span>
            {meta && <span className="acc-meta">{meta}</span>}
          </button>
        </H>
        {actions && <div className="acc-actions">{actions}</div>}
      </div>
      <div className="acc-panel" id={`${uid}-p`} role="region" aria-labelledby={`${uid}-b`} {...panel}>
        <div className="acc-clip"><div className="acc-body">{seen && children}</div></div>
      </div>
    </section>
  );
}
