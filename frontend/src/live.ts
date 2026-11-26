/** Live updates. One stream per signed-in tab (GET /api/live, Server-Sent Events) says what changed: a topic such as "tasks",
 *  "meetings", "approvals" or "notifications", and the project. Pages that show that topic reload it through the normal API,
 *  so what arrives is never data, only "look again". A stream that drops reconnects with backoff; after a gap every page
 *  reloads, because what changed meanwhile is unknown.
 *
 *  A tab that stays hidden lets its stream go and reopens it when shown again (see HIDDEN_MS).
 *
 *  Topics: notifications, approvals, tasks (onboardings, tasks, reviews), meetings (calls and their notes), knowledge (KT
 *  links), sources (repository, documents), inbox (expert questions), people (invitations, members), projects, runs. */
import { useEffect, useRef, useState } from "react";
import { liveRequest } from "./api";

export type Change = { t: string; p: string | null };
export type LiveStatus = "connecting" | "live" | "offline";
type Entry = { topics: Set<string>; fn: (c: Change) => void };

const entries = new Set<Entry>();
const statusWatchers = new Set<(s: LiveStatus) => void>();
let status: LiveStatus = "connecting";
let running = false;
let controller: AbortController | null = null;
let wake: (() => void) | null = null;

function setStatus(s: LiveStatus) {
  if (s === status) return;
  status = s;
  statusWatchers.forEach((w) => w(s));
}

function emit(c: Change) {
  entries.forEach((e) => { if (c.t === "*" || e.topics.has(c.t)) e.fn(c); });
}

const pause = (ms: number) => new Promise<void>((done) => {
  const t = window.setTimeout(() => { wake = null; done(); }, ms);
  wake = () => { window.clearTimeout(t); wake = null; done(); };
});

/** Reads a Server-Sent Events body, calling `on(event, data)` for each complete event. */
async function readEvents(body: ReadableStream<Uint8Array>, on: (event: string, data: string) => void) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      on(event, data);
    }
  }
}

function read(body: ReadableStream<Uint8Array>) {
  return readEvents(body, (event, data) => {
    if (event === "change" && data) {
      try { emit(JSON.parse(data) as Change); } catch { /* a malformed line: skip it */ }
    }
  });
}

/* A hidden tab lets its stream go (after HIDDEN_MS, so a quick look at another tab keeps it). Over HTTP/1.1 (self-hosted,
   docker compose) a browser allows 6 connections per host across all its tabs, and every open stream holds one: with a
   stream per background tab, a fifth or sixth tab left nothing for the API calls and the whole app hung. Back on the tab,
   the stream reopens and, after a gap, every page reloads (the "*" change), as after any drop. */
const HIDDEN_MS = 5000;
let hiddenTimer = 0;
let parked: (() => void) | null = null; // resolves the wait of a loop parked while the tab is hidden
const isHidden = () => typeof document !== "undefined" && document.hidden;
const untilVisible = () => new Promise<void>((done) => { parked = () => { parked = null; done(); }; });

async function loop() {
  let backoff = 1000, lostAt = 0;
  while (running) {
    if (isHidden()) {
      if (!lostAt) lostAt = Date.now();
      await untilVisible();
      if (!running) return;
    }
    controller = new AbortController();
    try {
      const res = await liveRequest(controller.signal);
      if (!res.ok || !res.body) throw new Error(`live ${res.status}`);
      setStatus("live");
      if (lostAt && Date.now() - lostAt > 2500) emit({ t: "*", p: null }); // away long enough to have missed something
      lostAt = 0; // measured from this drop only: a stale time would make every later reconnect reload every page
      backoff = 1000;
      await read(res.body);
      lostAt = Date.now(); // the server ends each stream after a while (a fresh sign-in token): reconnect at once
      continue;
    } catch {
      if (!running) return;
      if (isHidden()) { if (!lostAt) lostAt = Date.now(); continue; } // let go because the tab is hidden: park, no backoff
    }
    if (!lostAt) lostAt = Date.now();
    setStatus("offline");
    await pause(backoff + Math.random() * 500);
    backoff = Math.min(30_000, backoff * 2);
  }
}

/** Start the stream (once, after sign-in). */
export function startLive() {
  if (running) return;
  running = true;
  setStatus("connecting");
  void loop();
}

/** Stop it (sign-out). */
export function stopLive() {
  running = false;
  window.clearTimeout(hiddenTimer);
  controller?.abort();
  wake?.();
  parked?.();
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    window.clearTimeout(hiddenTimer);
    if (document.hidden) {
      hiddenTimer = window.setTimeout(() => { if (running && document.hidden) controller?.abort(); }, HIDDEN_MS);
      return;
    }
    parked?.(); // back on the tab: reopen a stream let go while hidden
    if (status === "offline") wake?.(); // or try now instead of waiting out the backoff
  });
  addEventListener("online", () => wake?.());
}

/** Reload when one of `topics` changes in this project (or anywhere, when `projectId` is not given). Bursts of changes
 *  (an agent writing a plan task by task) arrive as one call. */
export function useLive(topics: string | string[], onChange: (c: Change) => void, projectId?: string | null) {
  const fn = useRef(onChange);
  fn.current = onChange;
  const key = Array.isArray(topics) ? topics.join(",") : topics;
  useEffect(() => {
    let timer = 0;
    const entry: Entry = {
      topics: new Set(key.split(",")),
      fn: (c) => {
        if (c.t !== "*" && projectId && c.p && c.p !== projectId) return;
        window.clearTimeout(timer);
        timer = window.setTimeout(() => fn.current(c), 300);
      },
    };
    entries.add(entry);
    return () => { entries.delete(entry); window.clearTimeout(timer); };
  }, [key, projectId]);
}

export function useLiveStatus(): LiveStatus {
  const [s, setS] = useState(status);
  useEffect(() => { statusWatchers.add(setS); setS(status); return () => { statusWatchers.delete(setS); }; }, []);
  return s;
}

/* ---------------------------------------------------------------- one agent run, step by step */

/** The step objects one stream event carries for `topic`: `{t, p, step}` on a change event, a bare step on a "step" event,
 *  or a batch (`steps`). Events for other topics carry none. */
function stepsIn(d: unknown, topic: string): unknown[] {
  if (!d || typeof d !== "object") return [];
  const o = d as Record<string, unknown>;
  if (typeof o.t === "string" && o.t !== topic) return [];
  if (Array.isArray(o.steps)) return o.steps;
  for (const k of ["step", "s", "data", "payload", "event"]) {
    const v = o[k];
    if (v && typeof v === "object" && !Array.isArray(v)) return [v];
  }
  return typeof o.text === "string" || typeof o.summary === "string" ? [o] : [];
}

/** Watches one agent run as it thinks: its own short stream, GET /api/live?topics=agent:<run_id> (agentic contract §2).
 *  It is separate from the page-wide stream because steps are data for this one view, shown the moment they arrive, while
 *  page-wide changes are "look again" and are batched.
 *
 *  `ready` settles once the server holds the subscription (its "ready" event), after `wait` ms at most, or at once when the
 *  stream cannot open: the question is asked either way, and the answer still carries its steps. `close` ends the stream. */
export function watchRun(runId: string, onStep: (raw: unknown) => void, wait = 700): { ready: Promise<void>; close: () => void } {
  const topic = `agent:${runId}`;
  const controller = new AbortController();
  let settle: () => void = () => {};
  const ready = new Promise<void>((done) => { settle = done; });
  const timer = window.setTimeout(() => settle(), wait);
  void (async () => {
    try {
      const res = await liveRequest(controller.signal, topic);
      if (!res.ok || !res.body) return;
      await readEvents(res.body, (event, data) => {
        if (event === "ready") { settle(); return; }
        if (!data) return;
        let d: unknown;
        try { d = JSON.parse(data); } catch { return; } // a malformed line: skip it
        for (const s of stepsIn(d, topic)) onStep(s);
      });
    } catch { /* closed by us, or the stream failed: the answer's own steps still render */ }
    finally { window.clearTimeout(timer); settle(); }
  })();
  return { ready, close: () => { window.clearTimeout(timer); settle(); controller.abort(); } };
}
