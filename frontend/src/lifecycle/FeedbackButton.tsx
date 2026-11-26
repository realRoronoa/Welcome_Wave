/** Documentation feedback: a small flag beside a file, a KT moment, a document page or a verified answer. One click opens a
 *  little card: "Unclear" or "Outdated", an optional note, Send. It posts to POST /api/feedback, which routes it to whoever
 *  owns the thing (docs/specs/documentation-feedback.md) and tells them.
 *
 *  The API's target is the whole thing (a file path, a meeting id, a document id, a verified answer id); `where` says which
 *  part ("Line 42", "At 03:12", "Page 2") and is put in front of the note, so the owner knows where to look.
 *  The card is portalled to <body> with fixed coordinates, so a scrolling code view or a clipped card never cuts it off. */
import { FormEvent, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { errorMessage, post } from "../api";
import { useSession } from "../session";
import { toast } from "../ui";
import "../styles/data.css";

export type FeedbackTarget = "doc" | "code" | "meeting" | "answer";
type Kind = "unclear" | "outdated";

type Props = {
  kind: FeedbackTarget;
  target: string;
  /** which part of the target, e.g. "Line 42", "At 03:12", "Page 2" */
  where?: string;
  /** what the flag is about, for screen readers: "this file", "this moment" */
  label?: string;
  /** show the word "Feedback" beside the flag */
  text?: boolean;
  className?: string;
};

const W = 288; // the card's width

export default function FeedbackButton({ kind: targetKind, target, where, label = "this", text = false, className = "" }: Props) {
  const { project } = useSession();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<Kind>("unclear");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [at, setAt] = useState<{ top: number; left: number; up: boolean } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const card = useRef<HTMLFormElement>(null);
  const uid = useId();

  // place the card under the button (above it when there is no room below), inside the viewport; follow scrolling
  useLayoutEffect(() => {
    if (!open) return;
    const measure = () => {
      const r = button.current?.getBoundingClientRect();
      if (!r) return null;
      const h = card.current?.offsetHeight ?? 220;
      const up = r.bottom + 8 + h > innerHeight - 8 && r.top - 8 - h > 8;
      const left = Math.min(Math.max(8, r.right - W), innerWidth - W - 8);
      return { top: up ? r.top - 8 - h : r.bottom + 8, left, up };
    };
    const first = measure();
    if (first) setAt(first);
    // while the page scrolls: once a frame at most, written straight to the card, so a scroll never re-renders it
    let raf = 0;
    const follow = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const m = measure(), el = card.current;
        if (!m || !el) return;
        el.style.top = `${m.top}px`; el.style.left = `${m.left}px`; el.classList.toggle("up", m.up);
      });
    };
    addEventListener("scroll", follow, { capture: true, passive: true });
    addEventListener("resize", follow);
    return () => { cancelAnimationFrame(raf); removeEventListener("scroll", follow, true); removeEventListener("resize", follow); };
  }, [open, error]);

  // Escape and a click outside close it; focus goes back to the flag
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!card.current?.contains(t) && !button.current?.contains(t)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc, true);
    requestAnimationFrame(() => card.current?.querySelector<HTMLElement>("[aria-checked='true']")?.focus());
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc, true); };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  function close() {
    setOpen(false);
    button.current?.focus();
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setError(null);
    const body = [where, note.trim()].filter(Boolean).join(": ");
    try {
      await post("/feedback", { project_id: project.id, target_kind: targetKind, target_ref: target, kind, note: body.slice(0, 2000) });
      setSent(true); setOpen(false); setNote("");
      toast(kind === "unclear" ? "Marked as unclear. The owner will see it." : "Marked as outdated. The owner will see it.");
      button.current?.focus();
    } catch (err) {
      setError(errorMessage(err));
    } finally { setBusy(false); }
  }

  const name = `Feedback on ${label}`;
  return (
    <>
      <button ref={button} type="button" className={`fb-flag${text ? " with-text" : ""}${sent ? " is-sent" : ""}${open ? " is-open" : ""} ${className}`}
              aria-label={sent ? `${name} (sent)` : name} title={sent ? "Feedback sent" : "Unclear or outdated?"}
              aria-expanded={open} aria-haspopup="dialog" aria-controls={open ? `${uid}-card` : undefined}
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); setError(null); setOpen((o) => !o); }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round"
             strokeLinejoin="round" aria-hidden="true">
          <path d="M4 22V4" /><path d="M4 4h12l-2 4 2 4H4" />
        </svg>
        {text && <span>{sent ? "Sent" : "Feedback"}</span>}
      </button>
      {open && createPortal(
        <form ref={card} id={`${uid}-card`} className={`fb-card${at?.up ? " up" : ""}`} role="dialog" aria-label={name} onSubmit={send}
              style={{ top: at?.top ?? -9999, left: at?.left ?? -9999, width: W }}
              onClick={(e) => e.stopPropagation()}>
          <div className="fb-seg" role="radiogroup" aria-label="What is wrong">
            {(["unclear", "outdated"] as Kind[]).map((k) => (
              <button key={k} type="button" role="radio" aria-checked={kind === k} tabIndex={kind === k ? 0 : -1}
                      onClick={() => setKind(k)}
                      onKeyDown={(e) => {
                        if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
                        e.preventDefault();
                        const next: Kind = k === "unclear" ? "outdated" : "unclear";
                        setKind(next);
                        (e.currentTarget.parentElement?.querySelector(`[data-k="${next}"]`) as HTMLElement | null)?.focus();
                      }}
                      data-k={k}>
                {k === "unclear" ? "Unclear" : "Outdated"}
              </button>
            ))}
          </div>
          <label className="sr-only" htmlFor={`${uid}-note`}>Note (optional)</label>
          <textarea id={`${uid}-note`} className="fb-note" rows={2} maxLength={1800} value={note} onChange={(e) => setNote(e.target.value)}
                    placeholder={where ? `${where}: add a note (optional)` : "Add a note (optional)"} />
          {error && <p className="fb-error" role="alert">{error}</p>}
          <div className="fb-foot">
            <button type="button" className="dp-btn small ghost" onClick={close}>Cancel</button>
            <button type="submit" className="dp-btn small primary" disabled={busy}>{busy ? "Sending…" : "Send"}</button>
          </div>
        </form>,
        document.body,
      )}
    </>
  );
}
