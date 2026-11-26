/* The legal documents' pages: /terms, /privacy, /recording, /acceptable-use, /dpa and /subprocessors. One renderer for all
   six, reading src/legal/documents.ts (the one source of their words; docs/legal/*.md are written from the same data).
   An editorial reading page in the site's paper look: the title and what the document is, its version and the date it took
   effect, "In brief" first, then the sections, each with its own anchor. Wide screens keep the contents beside the text
   (sticky, the section in view marked); phones fold it under one button. Tables turn into stacked rows on a phone.
   legal.css also carries the print stylesheet.

   The operator's details (legal name, address, contacts, governing law) come from GET /api/config `legal`; one that is not
   set reads as a neutral phrase (src/legal/tokens.ts), never a made-up name and never a placeholder. The text waits a
   moment for that answer, so a set name does not flash in after the neutral phrase. The same answer says how this server
   is set up (`legal.deployment`): where a document's words depend on that, an "On this service" remark says which case
   is the reader's (src/legal/deployment.ts).

   Its own chunk (Site.tsx loads it lazily), so the landing page never downloads the documents. Nothing here animates. */
import { Fragment, MouseEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  DOCUMENTS, LEGAL_PAGES, LEGAL_PATH, LEGAL_REVIEW, parts, serviceLines, versionDate,
  type Block, type LegalDetails, type LegalDocId, type ListItem, type Section, type Table, type VersionedDocId,
} from "../legal/documents";
import { focusIn, takeArrival } from "../surface";
import { toSite } from "./handover";
import { col } from "./parts";
import "./legal.css";

/* ---------------------------------------------------------------------------------------------- the operator's details */

type Loaded = { legal: LegalDetails | null };
let cached: Loaded | null = null;
let asked: Promise<Loaded> | null = null;

/** GET /api/config, once per page load. No answer (the API is down, or the site is served alone) means no details: the
 *  documents then read with their neutral phrases, which is true to what the reader can be told. */
function loadDetails(): Promise<Loaded> {
  asked ??= fetch("/api/config", { headers: { Accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : null))
    .then((c: { legal?: LegalDetails } | null) => (cached = { legal: c?.legal ?? null }))
    .catch(() => (cached = { legal: null }));
  return asked;
}

/** How long the text waits for the operator's details before it shows with neutral phrases (it fills in if they arrive). */
const DETAILS_WAIT_MS = 2500;

function useDetails(): Loaded | null {
  const [state, setState] = useState<Loaded | null>(cached);
  useEffect(() => {
    if (cached) return;
    let alive = true;
    const give = (v: Loaded) => { if (alive) setState(v); };
    const late = window.setTimeout(() => give({ legal: null }), DETAILS_WAIT_MS);
    loadDetails().then((v) => { clearTimeout(late); give(v); });
    return () => { alive = false; clearTimeout(late); };
  }, []);
  return state;
}

/* ------------------------------------------------------------------------------------------------------------ the text */

const VERSION_OF: Record<LegalDocId, VersionedDocId> = {
  terms: "terms", privacy: "privacy", dpa: "dpa", recording: "recording", subprocessors: "subprocessors", acceptable_use: "terms",
};

/** One string of a document: its text, the operator's details filled in, addresses and other documents as links. */
function Text({ text, legal }: { text: string; legal: LegalDetails | null }) {
  return (
    <>
      {parts(text, legal).map((s, i) => {
        if (s.kind === "email") return <a key={i} className="lg-a" href={`mailto:${s.address}`}>{s.address}</a>;
        if (s.kind === "doc") {
          const href = LEGAL_PATH[s.id];
          return <a key={i} className="lg-a" href={href} onClick={(e) => toSite(e, href)}>{s.title}</a>;
        }
        return <Fragment key={i}>{s.text}</Fragment>;
      })}
    </>
  );
}

function Item({ item, legal }: { item: ListItem; legal: LegalDetails | null }) {
  if (typeof item === "string") return <li><Text text={item} legal={legal} /></li>;
  return <li><b><Text text={item.term} legal={legal} />.</b> <Text text={item.text} legal={legal} /></li>;
}

/** A table that is still a table to a screen reader once a phone has stacked its rows (the roles are stated, because
 *  `display: block` on a table drops them in some browsers). Each cell carries its column's name for the stacked view. */
function DataTable({ t, legal }: { t: Table; legal: LegalDetails | null }) {
  return (
    <div className="lg-table">
      <table role="table">
        {t.caption && <caption><Text text={t.caption} legal={legal} /></caption>}
        <thead role="rowgroup">
          <tr role="row">{t.columns.map((c) => <th key={c} role="columnheader" scope="col">{c}</th>)}</tr>
        </thead>
        <tbody role="rowgroup">
          {t.rows.map((r, i) => (
            <tr key={i} role="row">
              {r.map((cell, j) => j === 0
                ? <th key={j} role="rowheader" scope="row" data-label={t.columns[0]}><Text text={cell} legal={legal} /></th>
                : <td key={j} role="cell" data-label={t.columns[j]}><Text text={cell} legal={legal} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const NOTE_LABEL = { plain: "Note", caution: "Important", example: "Example" } as const;

function BlockView({ b, legal }: { b: Block; legal: LegalDetails | null }) {
  if (b.type === "paragraph") return <p><Text text={b.text} legal={legal} /></p>;
  if (b.type === "list") {
    const items = b.items.map((it, i) => <Item key={i} item={it} legal={legal} />);
    return b.ordered ? <ol className="lg-list">{items}</ol> : <ul className="lg-list">{items}</ul>;
  }
  if (b.type === "table") return <DataTable t={b} legal={legal} />;
  if (b.type === "service") {
    // how this server is set up, for the statements the text leaves open; nothing when the server did not say
    const lines = serviceLines(b.topics, legal?.deployment);
    if (!lines.length) return null;
    return (
      <aside className="lg-note is-service" aria-label="On this service">
        <p className="lg-note-h">On this service</p>
        {lines.length === 1 ? <p>{lines[0]}</p> : <ul className="lg-service">{lines.map((l) => <li key={l}>{l}</li>)}</ul>}
      </aside>
    );
  }
  return (
    <aside className={`lg-note is-${b.tone}`} aria-label={b.title ?? NOTE_LABEL[b.tone]}>
      <p className="lg-note-h">{b.title ?? NOTE_LABEL[b.tone]}</p>
      <p><Text text={b.text} legal={legal} /></p>
    </aside>
  );
}

function SectionView({ s, legal }: { s: Section; legal: LegalDetails | null }) {
  return (
    <section id={s.id} className="lg-sec" aria-labelledby={`${s.id}-h`}>
      {/* the link sits beside the heading, not in it, so the heading's name is only its words */}
      <div className="lg-sec-h">
        <h2 id={`${s.id}-h`}><Text text={s.heading} legal={legal} /></h2>
        <a className="lg-anchor" href={`#${s.id}`} aria-label={`Link to this section: ${s.heading}`}><span aria-hidden="true">#</span></a>
      </div>
      {s.blocks.map((b, i) => <BlockView key={i} b={b} legal={legal} />)}
    </section>
  );
}

/* ------------------------------------------------------------------------------------------------------------ the page */

/** The section in view: the last one whose top has passed a line a little under the nav. */
function useSpy(ids: string[], ready: boolean): string {
  const [at, setAt] = useState(ids[0] ?? "");
  const key = ids.join("|");
  useEffect(() => {
    if (!ready) return;
    const els = ids.map((id) => document.getElementById(id)).filter((el): el is HTMLElement => !!el);
    if (!els.length) return;
    const seen = new Set<string>();
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) { if (e.isIntersecting) seen.add(e.target.id); else seen.delete(e.target.id); }
      // sections stack down the page, so the one being read is the first whose box crosses the line
      const first = els.find((el) => seen.has(el.id));
      if (first) setAt(first.id);
    }, { rootMargin: "-112px 0px -62% 0px" });
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [key, ready]); // eslint-disable-line react-hooks/exhaustive-deps
  return at;
}

export default function Legal({ id }: { id: LegalDocId }) {
  const doc = DOCUMENTS[id];
  const details = useDetails();
  const legal = details?.legal ?? null;
  const ready = details !== null;
  const [contents, setContents] = useState(false); // phones: the contents list is folded
  const head = useRef<HTMLHeadingElement>(null);

  const entries = [{ id: "brief", label: "In brief" }, ...doc.sections.map((s) => ({ id: s.id, label: s.heading }))];
  const at = useSpy(entries.map((e) => e.id), ready);

  // this page's own title (Site.tsx set it from src/legal/paths.ts; the document's own title is the one that holds)
  useEffect(() => { document.title = `${doc.title} · Welcome Wave`; }, [doc.title]);

  // arrived by a link on the site: focus starts at the title, as on a page load (Site.tsx leaves that to this page, because
  // the page is not there yet when its own effect runs)
  useLayoutEffect(() => {
    if (takeArrival() && !location.hash && head.current) focusIn(head.current);
    setContents(false);
  }, [id]);

  // opened at a section (/privacy#rights), or Back to where the page was left: the sections exist only once the text does
  useLayoutEffect(() => {
    if (!ready) return;
    const y = (history.state as { y?: number } | null)?.y;
    const target = location.hash.length > 1 ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
    if (target && !y) { target.scrollIntoView({ behavior: "instant" }); focusIn(target); }
    else if (typeof y === "number" && y > 0) scrollTo({ top: y, behavior: "instant" });
  }, [ready, id]);

  // opened to be kept (Settings, Organisation: "Save the agreement as PDF" links to /dpa?print=1): the print dialog opens
  // once the text is there, so the copy that is saved is the version that was accepted, with the operator's details in it
  useEffect(() => {
    if (!ready || !new URLSearchParams(location.search).has("print")) return;
    const t = window.setTimeout(() => window.print(), 400);
    return () => clearTimeout(t);
  }, [ready, id]);

  const draft = LEGAL_REVIEW.status !== "reviewed";
  const current = legal?.versions?.[VERSION_OF[id]];
  const stale = !!current && current !== doc.version; // this tab holds an older build than the server
  const others = LEGAL_PAGES.filter((p) => p.id !== id);
  const jump = (e: MouseEvent<HTMLAnchorElement>) => { if (!e.defaultPrevented) setContents(false); };

  return (
    <article className="lg" aria-labelledby="lg-h">
      <header className="s-grid lg-head">
        <p className="s-kicker lg-kicker" style={col("1 / -1")}>
          <span>Legal</span>
          {draft && <span className="lg-draft" title={LEGAL_REVIEW.note}>Draft</span>}
        </p>
        <h1 id="lg-h" ref={head} className="lg-h1" style={col("1 / 11", "1 / -1")}>{doc.title}</h1>
        <p className="s-lede lg-lede" style={col("1 / 8", "1 / -1")}>{doc.lede}</p>
        <dl className="lg-meta" style={col("1 / -1")}>
          <div><dt>Version</dt><dd className="s-tnum">{doc.version}</dd></div>
          <div><dt>In effect from</dt><dd><time dateTime={doc.version}>{versionDate(doc.version)}</time></dd></div>
          <div><dt>For</dt><dd>{doc.audience}</dd></div>
          {draft && <div><dt>Status</dt><dd>{LEGAL_REVIEW.note}</dd></div>}
        </dl>
        {stale && (
          <p className="lg-stale" role="status" style={col("1 / 9", "1 / -1")}>
            This page shows the version of {versionDate(doc.version)}. A newer one is in effect.{" "}
            <button type="button" className="lg-a" onClick={() => location.reload()}>Reload to read it</button>
          </p>
        )}
      </header>

      <div className="s-grid lg-body">
        <nav className="lg-toc" aria-label="On this page" style={col("1 / 4", "1 / -1")}>
          <button type="button" className="lg-toc-btn" aria-expanded={contents} aria-controls="lg-toc-list" onClick={() => setContents((v) => !v)}>
            <span>On this page</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d={contents ? "M6 15l6-6 6 6" : "M6 9l6 6 6-6"} />
            </svg>
          </button>
          <p className="lg-toc-h" aria-hidden="true">On this page</p>
          <ol id="lg-toc-list" className={`lg-toc-list${contents ? " is-open" : ""}`}>
            {entries.map((e) => (
              <li key={e.id}>
                <a href={`#${e.id}`} aria-current={ready && at === e.id ? "location" : undefined} onClick={jump}>{e.label}</a>
              </li>
            ))}
          </ol>
          <button type="button" className="lg-print" onClick={() => window.print()}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v7H6z" />
            </svg>
            Print or save as PDF
          </button>
        </nav>

        <div className="lg-text" style={col("5 / 13", "1 / -1")} aria-busy={!ready}>
          <section id="brief" className="lg-brief" aria-labelledby="brief-h">
            <h2 id="brief-h" className="lg-brief-h">In brief</h2>
            <ul>{doc.summary.map((s, i) => <li key={i}><Text text={s} legal={legal} /></li>)}</ul>
            <p className="lg-brief-note">A summary. The sections below are what applies.</p>
          </section>

          {ready ? doc.sections.map((s) => <SectionView key={s.id} s={s} legal={legal} />) : (
            <div className="lg-wait" role="status"><span className="sr-only">Loading the document…</span><i /><i /><i /><i /><i /></div>
          )}

          {ready && (
            <footer className="lg-more">
              <h2 className="lg-more-h">Other documents</h2>
              <ul>
                {others.map((p) => <li key={p.id}><a href={p.path} onClick={(e) => toSite(e, p.path)}>{p.title}</a></li>)}
              </ul>
            </footer>
          )}
        </div>
      </div>
    </article>
  );
}
