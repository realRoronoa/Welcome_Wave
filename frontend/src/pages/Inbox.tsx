import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, day, errorMessage, post, when } from "../api";
import { useSession } from "../session";
import { Msg, Note } from "../ui";
import { EmptyState, Kbd, modKey, reducedMotion, Skeleton } from "../fx";
import { Glyph, initials, useAutoGrow } from "./chat-parts";
import { SectionHead } from "../dataui";
import { useLive } from "../live";

type Request = { id: string; question: string; asked_by_name?: string | null; created_at?: string; project_id: string; message_id?: string };
type Verified = { id: string; question: string; answer: string; reuse_count: number; created_at: string; source_request_id: string | null };
/** A question answered on this page, kept on screen until its expert saves it as a verified answer or moves on. */
type Answered = { request: Request; answer: string; saved: boolean };

const SHOWN = 5; // verified answers listed before "Show all"

export default function Inbox({ focus }: { focus: string | null }) {
  const { project } = useSession();
  const [rows, setRows] = useState<Request[] | null>(null);
  const [answered, setAnswered] = useState<Answered[]>([]);
  const [verified, setVerified] = useState<Verified[] | null>(null);
  const [all, setAll] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  const load = useCallback(() => {
    api<Request[]>("/experts/requests")
      .then((xs) => { setRows(xs); setMsg((m) => (m && !m.ok ? null : m)); })
      .catch((e) => setMsg({ ok: false, text: errorMessage(e) }));
  }, []);
  const loadVerified = useCallback(() => {
    api<Verified[]>(`/verified-answers?project_id=${project.id}`)
      .then((xs) => setVerified([...xs].sort((a, b) => b.reuse_count - a.reuse_count || b.created_at.localeCompare(a.created_at))))
      .catch(() => setVerified([])); // the list is extra: the inbox works without it
  }, [project.id]);
  // Questions arrive while the page is open: fetch again when a notification opens one (the ?message= changes) and live.
  useEffect(load, [load, focus]);
  useEffect(loadVerified, [loadVerified]);
  useLive("inbox", () => { load(); loadVerified(); }); // a question routed to this expert, or answered elsewhere, shows up at once

  /** Answered: the card turns into its "save as verified answer" step instead of leaving at once. */
  const onAnswered = (r: Request, answer: string) => {
    setRows((xs) => (xs ?? []).filter((x) => x.id !== r.id));
    setAnswered((xs) => [{ request: r, answer, saved: false }, ...xs.filter((x) => x.request.id !== r.id)]);
  };
  const dismiss = (id: string) => setAnswered((xs) => xs.filter((x) => x.request.id !== id));
  const saved = (id: string) => { setAnswered((xs) => xs.map((x) => (x.request.id === id ? { ...x, saved: true } : x))); loadVerified(); };

  const open = rows?.length ?? 0;
  const shown = verified ? (all ? verified : verified.slice(0, SHOWN)) : [];
  return (
    <section className="ib">
      <header className="page-head ib-head">
        <h1>Expert inbox{open > 0 && <span className="dp-count dp-count-lg" aria-label={`${open} open`}>{open}</span>}</h1>
        <p>Questions sent to you. Your answer goes back to whoever asked.</p>
      </header>
      <Note msg={msg} />
      {!rows ? (!msg && (
        <div className="ib-list" aria-busy="true">
          <span className="sr-only" role="status">Loading…</span>
          {[0, 1].map((i) => <div key={i} className="ib-card"><Skeleton lines={1} height={12} style={{ width: 220, marginBottom: 18 }} /><Skeleton lines={2} height={16} /><div className="ib-box ib-box-skel" /></div>)}
        </div>
      )) : (
        <div className="ib-list">
          {answered.map((a) => <AnsweredCard key={a.request.id} a={a} onSaved={() => saved(a.request.id)} onDone={() => dismiss(a.request.id)} />)}
          {rows.map((r, i) => (
            <Question key={r.id} r={r} index={i} focus={!!focus && r.message_id === focus} onAnswered={(text) => onAnswered(r, text)}
                      onGone={(text) => { setRows((xs) => (xs ?? []).filter((x) => x.id !== r.id)); setMsg({ ok: true, text }); }} />
          ))}
          {rows.length === 0 && answered.length === 0 && (
            <EmptyState title="You are all caught up" action={<a className="ib-empty-link" href="#/chat">Open Ask the project <Glyph name="arrow" size={14} /></a>}>
              Questions the assistant cannot answer come here.
            </EmptyState>
          )}
        </div>
      )}

      {verified && verified.length > 0 && (
        <section className="ib-verified" aria-labelledby="ib-verified-h">
          <SectionHead id="ib-verified-h" title="Verified answers" count={verified.length} />
          <ul className="ib-va-list">
            {shown.map((v) => (
              <li key={v.id} className="ib-va">
                <p className="ib-va-q">{v.question}</p>
                <p className="ib-va-a">{v.answer}</p>
                <p className="ib-va-meta">
                  <span className={v.reuse_count > 0 ? "ib-va-reused" : ""}>{v.reuse_count === 0 ? "Not reused yet" : `Reused ${v.reuse_count} ${v.reuse_count === 1 ? "time" : "times"}`}</span>
                  <span aria-hidden="true">·</span><span>{day(v.created_at)}</span>
                </p>
              </li>
            ))}
          </ul>
          {verified.length > SHOWN && (
            <button type="button" className="dp-btn ghost small ib-va-more" onClick={() => setAll((x) => !x)}>
              {all ? "Show fewer" : `Show all ${verified.length}`}
            </button>
          )}
        </section>
      )}
    </section>
  );
}

function Question({ r, focus, onAnswered, onGone, index }:
  { r: Request; focus: boolean; onAnswered: (answer: string) => void; onGone: (text: string) => void; index: number }) {
  const { me } = useSession();
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const project = me.projects.find((p) => p.id === r.project_id)?.name;
  const box = useAutoGrow(answer, 320);
  const form = useRef<HTMLFormElement>(null);
  const card = useRef<HTMLElement>(null);
  const fid = `ib-a-${r.id}`;

  useEffect(() => { if (focus) card.current?.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" }); }, [focus]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !answer.trim()) return;
    setBusy(true); setError(null);
    try {
      await post(`/experts/requests/${r.id}/answer`, { answer: answer.trim() });
      onAnswered(answer.trim());
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) return onGone("That question had already been answered.");
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.current?.requestSubmit(); }
  }

  const who = r.asked_by_name?.replace(/\s*\(.*?\)/g, "") || "A new engineer";
  return (
    <article ref={card} className={`ib-card spot${focus ? " here" : ""}`} style={{ animationDelay: `${index * 60}ms` }}>
      <header className="ib-who">
        <span className="ib-avatar" aria-hidden="true">{initials(r.asked_by_name)}</span>
        <span className="ib-who-text">
          <strong>{who}</strong>
          <span className="muted">{r.created_at ? `Asked ${when(r.created_at)}` : "Asked"}{project && me.projects.length > 1 ? ` in ${project}` : ""}</span>
        </span>
      </header>
      <h2 className="ib-q">{r.question}</h2>
      <form ref={form} className="ib-form" onSubmit={submit}>
        <label htmlFor={fid} className="sr-only">Your answer</label>
        <div className="ib-box">
          <textarea id={fid} ref={box} rows={3} required maxLength={8000} autoFocus={focus} value={answer} disabled={busy}
                    onChange={(e) => setAnswer(e.target.value)} onKeyDown={onKey}
                    placeholder="Answer as you would to a colleague. Name the file if it helps." />
          <div className="ib-box-foot">
            <span className="ib-hint"><span className="ib-keys"><Kbd>{modKey}</Kbd><Kbd>↵</Kbd></span>to send</span>
            {answer.length > 7000 && <span className="ib-chars">{answer.length}/8000</span>}
            <button type="submit" className="ib-send" disabled={busy || !answer.trim()}>{busy ? "Sending…" : "Send answer"}</button>
          </div>
        </div>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}

/** After answering: the answer went to the person who asked. Saving it as a verified answer lets the assistant reuse it. */
function AnsweredCard({ a, onSaved, onDone }: { a: Answered; onSaved: () => void; onDone: () => void }) {
  const { project } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const who = a.request.asked_by_name?.replace(/\s*\(.*?\)/g, "") || "them";

  async function save() {
    setBusy(true); setError(null);
    try {
      await post("/verified-answers", { project_id: a.request.project_id || project.id, question: a.request.question, answer: a.answer,
                                        source_request_id: a.request.id });
      onSaved();
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  function done() {
    if (reducedMotion()) return onDone();
    setLeaving(true); // let the card fold away before it leaves the list
    window.setTimeout(onDone, 340);
  }

  return (
    <article className={`ib-card ib-answered${leaving ? " is-leaving" : ""}`} aria-live="polite">
      <p className="ib-answered-head"><Glyph name="check" size={15} /><strong>Answered</strong><span className="muted">Sent to {who}</span></p>
      <p className="ib-answered-q">{a.request.question}</p>
      <p className="ib-answered-a">{a.answer}</p>
      <div className="ib-answered-foot">
        {a.saved ? (
          <span className="ib-saved"><Glyph name="shield" size={14} />Saved as a verified answer</span>
        ) : (
          <button type="button" className="dp-btn primary small" onClick={save} disabled={busy}>
            <Glyph name="shield" size={14} />{busy ? "Saving…" : "Save as verified answer"}
          </button>
        )}
        <button type="button" className="dp-btn ghost small" onClick={done}>Done</button>
      </div>
      {!a.saved && <p className="ib-answered-hint">Verified answers are reused when someone asks again.</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}
