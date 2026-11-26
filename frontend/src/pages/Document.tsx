import { useEffect, useState } from "react";
import { api, day, download, errorMessage } from "../api";
import { reducedMotion, Skeleton } from "../fx";
import { Glyph } from "../dataui";
import FeedbackButton from "../lifecycle/FeedbackButton";

type Doc = { id: string; title: string; mime: string; status: "pending" | "ready" | "failed"; page_count: number | null; created_at: string };
type View = { document: Doc; pages: { page: number; text: string }[] };

/** A document the chat cites, page by page: what a "Runbook, page 2" source opens. The cited page is marked and in view. */
export default function DocumentView({ id, page }: { id: string; page: number | null }) {
  const [v, setV] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { api<View>(`/documents/${encodeURIComponent(id)}`).then(setV).catch((e) => setError(errorMessage(e))); }, [id]);
  useEffect(() => {
    if (!v || !page) return;
    requestAnimationFrame(() => document.getElementById(`doc-page-${page}`)?.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" }));
  }, [v, page]);

  async function save() {
    if (!v) return;
    setSaving(true); setError(null);
    try { await download(`/documents/${encodeURIComponent(id)}/file`, v.document.title); }
    catch (e) { setError(errorMessage(e)); } finally { setSaving(false); }
  }

  const back = <a href="#/chat" className="back" onClick={(e) => { if (history.length > 1) { e.preventDefault(); history.back(); } }}>Back</a>;
  if (error && !v) return <section className="dp">{back}<p className="error" role="alert">{error}</p></section>;
  if (!v) {
    return (
      <section className="dp" aria-busy="true">
        <span className="sr-only" role="status">Loading…</span>
        {back}
        <Skeleton lines={1} height={26} style={{ width: "min(420px, 80%)", marginBottom: 20 }} />
        <div className="dp-card"><Skeleton lines={8} /></div>
      </section>
    );
  }
  const d = v.document;
  return (
    <section className="dp doc-view">
      <header className="dp-head dp-head-tight">
        <div>
          {back}
          <h1>{d.title}</h1>
          <p className="dp-chips">
            <span className="dp-chip"><Glyph name="file" size={12} />{d.mime.split("/").pop()?.replace("vnd.openxmlformats-officedocument.wordprocessingml.document", "word")}</span>
            {d.page_count ? <span className="dp-chip">{d.page_count} {d.page_count === 1 ? "page" : "pages"}</span> : null}
            <span className="dp-chip">Added {day(d.created_at)}</span>
          </p>
        </div>
        <button type="button" className="dp-btn" onClick={save} disabled={saving}>{saving ? "Downloading…" : "Download"}</button>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {d.status !== "ready" ? (
        <p className="dp-hint"><Glyph name="clock" size={14} /><span>{d.status === "failed" ? "This document could not be read." : "This document is still being read."}</span></p>
      ) : v.pages.length === 0 ? (
        <p className="dp-hint"><Glyph name="alert" size={14} /><span>The text could not be shown. Download it instead.</span></p>
      ) : (
        <ol className="doc-pages">
          {v.pages.map((p) => (
            <li key={p.page} id={`doc-page-${p.page}`} className={`dp-card doc-page${p.page === page ? " here" : ""}`}>
              <div className="doc-page-head">
                <p className="doc-page-n">Page {p.page}{p.page === page && <span className="doc-cited">Cited</span>}</p>
                <FeedbackButton kind="doc" target={d.id} where={`Page ${p.page}`} label={`page ${p.page}`} className="doc-flag" />
              </div>
              <div className="doc-text">{p.text.trim() || <span className="dp-meta">No text on this page.</span>}</div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
