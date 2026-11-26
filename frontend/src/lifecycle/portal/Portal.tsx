/** A new hire's pre-boarding page, /welcome/<token>: the first thing they see of the company, before they have an account.
 *  No sign-in (the link is the permission) and no app shell: one calm column with their welcome, the people they will meet,
 *  what to do before day one (acknowledge documents, check their machine), their access, and a first week.
 *  Data: GET /api/preboarding/{token}; actions: POST …/acknowledge. The environment check runs on their machine
 *  (GET …/env-check.sh) and posts its own results; this page shows the latest. Spec: docs/specs/preboarding.md. */
import "../../theme"; // applies the saved light/dark choice (or the system's) before the page paints
import "../../styles.css";
import "../../styles/portal.css";
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, errorMessage, post } from "../../api";
import { hideBoot } from "../../boot";
import { PORTAL_PATH } from "../../surface";
import { Logo } from "../../ui";
import type { Acknowledgement, EnvCheck, PortalData } from "./types";
import { dayMonth, firstWeek, longDay, plainName, shortDay, shortTime } from "./week";

type State = { kind: "loading" } | { kind: "ready"; data: PortalData } | { kind: "gone" } | { kind: "error"; message: string };

const tokenOf = () => decodeURIComponent(PORTAL_PATH.exec(location.pathname)?.[1] ?? "");

export default function Portal() {
  const token = useRef(tokenOf()).current;
  const [state, setState] = useState<State>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);
    try {
      const data = await api<PortalData>(`/preboarding/${encodeURIComponent(token)}`);
      setState({ kind: "ready", data });
    } catch (e) {
      // unknown, replaced and expired links all answer 404 (and a feature turned off does too)
      if (e instanceof ApiError && e.status === 404) setState({ kind: "gone" });
      else if (!quiet) setState({ kind: "error", message: errorMessage(e) });
    } finally {
      if (quiet) setRefreshing(false);
      hideBoot();
    }
  }, [token]);
  useEffect(() => { void load(); }, [load]);

  // A link that grants something stays out of search engines, and the tab names the person it welcomes.
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots"; meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);
  const first = state.kind === "ready" ? state.data.hire.first_name : "";
  useEffect(() => { document.title = first ? `Welcome, ${first} · Welcome Wave` : "Welcome · Welcome Wave"; }, [first]);

  if (state.kind === "gone") {
    return (
      <Frame>
        <div className="wp-gone" role="main">
          <Logo size={44} motion="arrive" className="wp-gone-mark" />
          <h1>This link no longer works</h1>
          <p>It has expired or was replaced. Ask your manager for a new one.</p>
        </div>
      </Frame>
    );
  }
  if (state.kind === "error") {
    return (
      <Frame>
        <div className="wp-gone" role="main">
          <Logo size={44} motion="arrive" className="wp-gone-mark" />
          <h1>Couldn’t load your page</h1>
          <p role="alert">{state.message}</p>
          <button type="button" className="wp-btn" onClick={() => { setState({ kind: "loading" }); void load(); }}>Try again</button>
        </div>
      </Frame>
    );
  }
  if (state.kind === "loading") return <Frame><Skeleton /></Frame>;
  return <Frame expires={state.data.expires_at}><Welcome data={state.data} token={token} onRefresh={() => load(true)} refreshing={refreshing}
                                                          onChange={(data) => setState({ kind: "ready", data })} /></Frame>;
}

/** The page around everything: the mark at the top, a quiet footer. */
function Frame({ children, expires }: { children: ReactNode; expires?: string | null }) {
  return (
    <div className="wp">
      <header className="wp-top">
        <span className="wp-brand"><Logo size={26} />Welcome Wave</span>
      </header>
      {children}
      <footer className="wp-foot">
        {expires && <p>This link is just for you. It works until {longDay(expires)}.</p>}
      </footer>
    </div>
  );
}

function Skeleton() {
  return (
    <main className="wp-main" aria-busy="true">
      <span className="sr-only" role="status">Loading…</span>
      <div className="wp-skel" style={{ width: 160, height: 12 }} />
      <div className="wp-skel" style={{ width: "min(420px, 80%)", height: 52, marginTop: 18 }} />
      <div className="wp-skel" style={{ width: "min(360px, 70%)", height: 16, marginTop: 18 }} />
      {[0, 1, 2].map((i) => <div key={i} className="wp-skel" style={{ height: 88, marginTop: i ? 16 : 72 }} />)}
    </main>
  );
}

function Welcome({ data, token, onChange, onRefresh, refreshing }:
  { data: PortalData; token: string; onChange: (d: PortalData) => void; onRefresh: () => void; refreshing: boolean }) {
  const manager = plainName(data.manager), mentor = plainName(data.mentor);
  const acks = data.acknowledgements;
  const read = acks.filter((a) => a.acknowledged_at).length;
  const granted = data.access.filter((a) => a.status === "granted" || a.status === "not_needed").length;
  const week = firstWeek(data);

  const acknowledged = (a: Acknowledgement) =>
    onChange({ ...data, acknowledgements: acks.map((x) => (x.doc_key === a.doc_key ? a : x)) });

  return (
    <main className="wp-main">
      <section className="wp-hero wp-rise" aria-labelledby="wp-h">
        {data.role_title && <p className="wp-eyebrow">{data.role_title}</p>}
        <h1 id="wp-h">Welcome, {data.hire.first_name}.</h1>
        <p className="wp-lede">
          {data.team ? <>You’re joining the <strong>{data.team}</strong> team on <strong>{data.project}</strong>.</>
            : <>You’re joining <strong>{data.project}</strong>.</>}
        </p>
        <Countdown start={data.start_date} days={data.days_until_start} />
      </section>

      {(manager || mentor) && (
        <Section id="people" title="Your people" delay={1}>
          <ul className="wp-people">
            {manager && <Person name={manager} role="Manager" />}
            {mentor && <Person name={mentor} role="Mentor" />}
          </ul>
        </Section>
      )}

      {acks.length > 0 && (
        <Section id="docs" title="Before day one" meta={`${read} of ${acks.length} read`} delay={2}>
          <ul className="wp-rows">
            {acks.map((a) => <AckRow key={a.doc_key} ack={a} token={token} onDone={acknowledged} />)}
          </ul>
        </Section>
      )}

      <Section id="machine" title="Your machine" sub="One command checks your tools. It installs nothing." delay={3}>
        <Environment token={token} tools={data.environment.tools} latest={data.environment.latest} onRefresh={onRefresh} refreshing={refreshing} />
      </Section>

      {data.access.length > 0 && (
        <Section id="access" title="Access" meta={`${granted} of ${data.access.length} ready`} delay={4}>
          <ul className="wp-rows">
            {data.access.map((a) => (
              <li key={a.system} className="wp-row">
                <span className="wp-row-main">{a.system}</span>
                <AccessState status={a.status} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section id="week" title={week.typical ? "A typical first week" : "Your first week"} delay={5}>
        <ol className="wp-week">
          {week.days.map((d) => (
            <li key={d.date} className={`wp-day${d.today ? " is-today" : ""}${d.done ? " is-done" : ""}`}>
              <span className="wp-day-when">
                <span className="wp-day-n">{d.label}</span>
                <span className="wp-day-date">{shortDay(d.date)}</span>
              </span>
              <span className="wp-day-dot" aria-hidden="true">{d.done && <Check size={10} />}</span>
              <span className="wp-day-text">
                <strong>{d.title}{d.today && <span className="wp-today">Today</span>}</strong>
                <span>{d.text}</span>
              </span>
            </li>
          ))}
        </ol>
      </Section>
    </main>
  );
}

/** Days to go, or the day it started. The server counts on its own clock and stops at 0 on the start date. */
function Countdown({ start, days }: { start: string; days: number }) {
  if (days > 0) {
    return (
      <div className="wp-count">
        <span className="wp-count-n">{days}</span>
        <span className="wp-count-text">
          <span>{days === 1 ? "day until your first day" : "days until your first day"}</span>
          <span className="wp-count-date">{longDay(start)}</span>
        </span>
      </div>
    );
  }
  const started = new Date(`${start}T00:00:00`).getTime() < new Date(new Date().toDateString()).getTime();
  return <p className="wp-started">{started ? <>You started on <strong>{longDay(start)}</strong>.</> : <>Today is your first day.</>}</p>;
}

function Section({ id, title, sub, meta, delay, children }:
  { id: string; title: string; sub?: string; meta?: string; delay: number; children: ReactNode }) {
  return (
    <section className="wp-sec wp-rise" aria-labelledby={`wp-${id}`} style={{ animationDelay: `${80 + delay * 60}ms` }}>
      <header className="wp-sec-head">
        <h2 id={`wp-${id}`}>{title}</h2>
        {meta && <span className="wp-sec-meta">{meta}</span>}
      </header>
      {sub && <p className="wp-sec-sub">{sub}</p>}
      {children}
    </section>
  );
}

const initials = (name: string) => name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

function Person({ name, role }: { name: string; role: string }) {
  return (
    <li className="wp-person">
      <span className="wp-avatar" aria-hidden="true">{initials(name)}</span>
      <span className="wp-person-text"><strong>{name}</strong><span>{role}</span></span>
    </li>
  );
}

function AckRow({ ack, token, onDone }: { ack: Acknowledgement; token: string; onDone: (a: Acknowledgement) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function confirm() {
    setBusy(true); setError(null);
    try {
      const a = await post<Acknowledgement>(`/preboarding/${encodeURIComponent(token)}/acknowledge`, { doc_key: ack.doc_key });
      onDone(a);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? "This link has expired. Ask your manager for a new one." : errorMessage(e));
    } finally { setBusy(false); }
  }
  return (
    <li className={`wp-row${ack.acknowledged_at ? " is-done" : ""}`}>
      <span className="wp-row-main">
        {ack.title}
        {error && <span className="wp-row-error" role="alert">{error}</span>}
      </span>
      {ack.acknowledged_at ? (
        <span className="wp-state ok"><Check />Read {shortDay(ack.acknowledged_at)}</span>
      ) : (
        <button type="button" className="wp-btn" onClick={confirm} disabled={busy}>
          {busy ? "Saving…" : <>I’ve read this<span className="sr-only">: {ack.title}</span></>}
        </button>
      )}
    </li>
  );
}

const ACCESS: Record<string, { label: string; tone: string }> = {
  granted: { label: "Ready", tone: "ok" }, requested: { label: "Being set up", tone: "wait" }, not_needed: { label: "Not needed", tone: "off" },
};

function AccessState({ status }: { status: string }) {
  const s = ACCESS[status] ?? { label: status, tone: "wait" };
  return <span className={`wp-state ${s.tone}`}>{s.tone === "ok" ? <Check /> : <i className="wp-pending" aria-hidden="true" />}{s.label}</span>;
}

function Environment({ token, tools, latest, onRefresh, refreshing }:
  { token: string; tools: string[]; latest: EnvCheck | null; onRefresh: () => void; refreshing: boolean }) {
  const command = `curl -fsSL ${location.origin}/api/preboarding/${encodeURIComponent(token)}/env-check.sh | sh`;
  const results = latest?.results ?? [];
  const failed = results.filter((r) => !r.ok).length;
  // every tool the check asks about, then anything else the last run reported
  const names = [...tools, ...results.map((r) => r.name).filter((n) => !tools.includes(n))];
  return (
    <>
      <div className="wp-cmd">
        <code>{command}</code>
        <CopyButton text={command} />
      </div>
      <div className="wp-env-head">
        <span className={`wp-env-sum${latest ? (failed ? " bad" : " ok") : ""}`} role="status">
          {!latest ? "Not run yet" : failed ? `${failed} of ${results.length} need attention` : "Every tool passed"}
          {latest && <span className="wp-env-when"> · {dayMonth(latest.ran_at)}, {shortTime(latest.ran_at)}</span>}
        </span>
        <button type="button" className="wp-link" onClick={onRefresh} disabled={refreshing}>{refreshing ? "Checking…" : "Refresh"}</button>
      </div>
      <ul className="wp-rows wp-tools">
        {names.map((name) => {
          const r = results.find((x) => x.name === name);
          return (
            <li key={name} className="wp-row">
              <span className="wp-row-main">
                <code className="wp-tool">{name}</code>
                {r?.detail && <span className="wp-tool-detail">{r.detail}</span>}
              </span>
              {!r ? <span className="wp-state off"><i className="wp-pending" aria-hidden="true" />Not checked</span>
                : r.ok ? <span className="wp-state ok"><Check />OK</span>
                : <span className="wp-state bad"><Cross />Failed</span>}
            </li>
          );
        })}
      </ul>
    </>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState<"" | "ok" | "fail">("");
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  async function copy() {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch {
      const ta = document.createElement("textarea"); // insecure origins: the old way
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { ok = document.execCommand("copy"); } catch { /* nothing else to try */ }
      ta.remove();
    }
    setDone(ok ? "ok" : "fail");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setDone(""), 1800);
  }
  return (
    <button type="button" className={`wp-copy${done === "ok" ? " is-done" : ""}`} onClick={copy} aria-label="Copy the command">
      {done === "ok" ? <><Check />Copied</> : done === "fail" ? "Select and copy" : "Copy"}
      <span className="sr-only" aria-live="polite">{done === "ok" ? "Copied" : ""}</span>
    </button>
  );
}

function Check({ size = 13 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5" /></svg>
  );
}

function Cross() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"
         aria-hidden="true"><path d="M18 6L6 18M6 6l12 12" /></svg>
  );
}
