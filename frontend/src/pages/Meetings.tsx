import { CSSProperties, FormEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, isManager, mmss, post } from "../api";
import { useSession } from "../session";
import { MarkLine, toast } from "../ui";
import { EmptyState, reducedMotion, Reveal, Skeleton } from "../fx";
import { Avatar, Glyph, Pill, StatusPill, Tone } from "../dataui";
import { Accordion, AccordionItem } from "../accordion";
import { FollowUp, FollowUpList, Rich, TranscriptList, useFollowUps } from "./kt-parts";
import { firstName, JoinLink, KtSession, ScheduleDialog, ScheduleValues, sessionWhen, useScheduling, whereLabel } from "./schedule";
import { useLive } from "../live";
import FeedbackButton from "../lifecycle/FeedbackButton";
import { Check } from "../consent/Check";
import { useFocusOnClose } from "../consent/focus";
import RecordingNotice, { useRecordingNotice } from "../consent/RecordingNotice";
import "./settings/settings-more.css";
import "../styles/recording-consent.css";

type Meeting = KtSession;
type Notes = {
  summary: string; decisions: string[]; action_items: { text: string; at_s: number | null }[];
  terms: { term: string; definition: string }[];
  utterances: { start_s: number; speaker: string; text: string }[];
  kt_links: { file_path: string; symbol: string | null; start_s: number; start_line: number | null }[];
};

const STATUS: Record<string, string> = {
  scheduled: "Upcoming", live: "Live", processing: "Writing notes", ready: "Notes ready", failed: "Recording failed",
  cancelled: "Cancelled", ended: "No recording",
};
const GRACE_MS = 5 * 60_000;
/** Where a session stands now: upcoming, happening, waiting for its recording, or its stored status. */
function phase(m: Meeting, now = Date.now()): { key: string; label: string; tone: Tone; live?: boolean } {
  if (m.status === "scheduled") {
    if (!m.starts_at) return { key: "unscheduled", label: "No time set", tone: "neutral" };
    const s = Date.parse(m.starts_at), e = m.ends_at ? Date.parse(m.ends_at) : s + 3_600_000;
    if (now < s - GRACE_MS) return { key: "upcoming", label: "Upcoming", tone: "neutral" };
    if (now <= e) return { key: "now", label: "Now", tone: "neutral", live: true };
    return { key: "awaiting", label: "Awaiting recording", tone: "warn" };
  }
  const tone: Tone = m.status === "ready" ? "ok" : m.status === "processing" ? "info" : m.status === "failed" ? "rec" : "neutral";
  return { key: m.status, label: STATUS[m.status] ?? m.status, tone, live: m.status === "processing" };
}

type Group = { id: string; title: string; glyph: "calendar" | "play" | "clock"; calls: Meeting[] };
/** Sessions in three sections: coming up (soonest first), recorded, and the rest (newest first). */
function groupCalls(rows: Meeting[]): Group[] {
  const t = (m: Meeting) => Date.parse(m.starts_at ?? m.started_at ?? "") || 0;
  const coming = rows.filter((m) => ["upcoming", "now", "unscheduled"].includes(phase(m).key))
    .sort((a, b) => (a.starts_at ? t(a) : Infinity) - (b.starts_at ? t(b) : Infinity));
  const recorded = rows.filter((m) => m.status === "ready" || m.status === "processing").sort((a, b) => t(b) - t(a));
  const past = rows.filter((m) => !coming.includes(m) && !recorded.includes(m)).sort((a, b) => t(b) - t(a));
  return [
    { id: "upcoming", title: "Coming up", glyph: "calendar" as const, calls: coming },
    { id: "recorded", title: "Recorded", glyph: "play" as const, calls: recorded },
    { id: "other", title: "Past", glyph: "clock" as const, calls: past },
  ].filter((g) => g.calls.length > 0);
}

/** focus: a session to pick out in the list (a booking notification). */
export default function Meetings({ id, at, focus }: { id: string | null; at: number; focus?: string | null }) {
  return id ? <MeetingNotes id={id} at={at} /> : <MeetingList focus={focus ?? null} />;
}

/** A stable small number from an id, so each call's tile keeps its own light. */
const seed = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
const lengthOf = (s: number | null) => (s ? (s >= 3600 ? `${Math.floor(s / 3600)}:${mmss(s % 3600)}` : mmss(s)) : null);

/** The "video" face of a recorded call on its notes page: a dark tile with the mark's line as a watermark, its state and its length. */
function CallTile({ m, big, bare }: { m: Meeting; big?: boolean; bare?: boolean }) {
  const h = seed(m.id);
  const style = {
    "--gx": `${10 + (h % 80)}%`, "--gy": `${5 + ((h >> 3) % 70)}%`,
    "--mx-r": `${-12 + ((h >> 7) % 20)}%`, "--mx-b": `${-34 + ((h >> 9) % 26)}%`, "--mx-w": `${50 + ((h >> 11) % 22)}%`,
  } as CSSProperties;
  const len = lengthOf(m.duration_s);
  const bars = !bare && (m.status === "ready" || m.status === "processing")
    ? Array.from({ length: 42 }, (_, i) => 0.22 + 0.78 * Math.abs(Math.sin((i + 1) * (1.7 + (h % 7) / 5)) * Math.cos(i / (3 + (h % 5)))))
    : [];
  const p = phase(m);
  return (
    <span className={`dp-tile-video s-${m.status}${big ? " big" : ""}`} style={style}>
      <MarkLine className="dp-tile-mark" />
      {bars.length > 0 && <span className="dp-wave" aria-hidden="true">{bars.map((v, i) => <i key={i} style={{ height: `${v * 100}%` }} />)}</span>}
      <span className="dp-tile-status"><Pill tone={p.tone} live={p.live}>{p.label}</Pill></span>
      {m.status === "ready" && <span className="dp-tile-play" aria-hidden="true"><Glyph name="play" size={big ? 22 : 16} /></span>}
      {len && <span className="dp-tile-len">{len}</span>}
    </span>
  );
}

/** How long a video file runs, read by the browser. A recording made in a browser (WebM) often reports no length until it is
 *  asked to seek to the end, so that is tried before giving up. */
function videoLength(file: File): Promise<number> {
  return new Promise((done, fail) => {
    const v = document.createElement("video");
    const url = URL.createObjectURL(file);
    const finish = (ok: boolean) => {
      URL.revokeObjectURL(url);
      if (ok && Number.isFinite(v.duration) && v.duration > 0) done(v.duration);
      else fail(new Error("Could not read how long this video runs. Save the recording as MP4 or WebM and try again."));
    };
    v.preload = "metadata";
    v.muted = true;
    v.onloadedmetadata = () => {
      if (Number.isFinite(v.duration)) return finish(true);
      v.ondurationchange = () => { if (Number.isFinite(v.duration)) finish(true); };
      v.currentTime = Number.MAX_SAFE_INTEGER;
      window.setTimeout(() => finish(Number.isFinite(v.duration)), 8000);
    };
    v.onerror = () => finish(false);
    v.src = url;
  });
}

/** Where step 1 of an upload says to send the file (POST /api/meetings/{id}/recording/upload). */
type UploadTicket = { id: string; method: "PUT" | "POST"; url: string; headers: Record<string, string>; expires_at: string; max_bytes: number };

class SendError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** Step 2: the raw file to `ticket.url` with exactly its headers: Cloud Storage's signed URL on Google Cloud, this API on a
 *  self-hosted server. No sign-in header and no cookies: the signed URL (or the upload id) is the credential. XHR, because
 *  fetch cannot report upload progress. A file already stored (412 from Cloud Storage, 409 here) counts as sent. */
function sendFile(t: UploadTicket, file: File, onProgress: (fraction: number) => void, aborts: { current: (() => void) | null }): Promise<void> {
  return new Promise((done, fail) => {
    const xhr = new XMLHttpRequest();
    xhr.open(t.method, t.url);
    xhr.withCredentials = false;
    Object.entries(t.headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      const s = xhr.status;
      if ((s >= 200 && s < 300) || s === 412 || s === 409) return done();
      fail(new SendError(s, s === 403 ? "The upload link expired." : s === 400 ? "The upload stopped before the whole file arrived. Try again."
        : s === 413 ? "That file is too large." : s === 415 ? "That file type cannot be uploaded. Use MP4, WebM, MOV or MKV."
        : s >= 500 || s === 0 ? "File storage did not answer. Try again in a moment." : `The upload was refused (${s}). Try again.`));
    };
    xhr.onerror = () => fail(new SendError(0, "The upload was interrupted. Check your connection and try again."));
    xhr.onabort = () => fail(new SendError(-1, "Upload cancelled."));
    aborts.current = () => xhr.abort();
    xhr.send(file);
  });
}

const VIDEO = /\.(mp4|webm|mov|mkv)$/i;
type Stage = "preparing" | "uploading" | "checking";

/** Add a session's recording: import it from Google Drive (a Google Calendar session, once Meet has attached it to the
 *  event), or upload the file. Either way the Meeting agent writes the notes and links each moment to the code on screen.
 *  An upload is three calls, the same on Google Cloud and self-hosted: start (where to send it), send the file (progress
 *  shown), complete (the server checks the file and hands it to the Meeting agent). An expired link or a file that did not
 *  arrive is retried once on its own; anything else says what to do.
 *  A recording is taken in only once someone has confirmed that everyone in the session was told it is recorded and agreed:
 *  a session without that confirmation asks for it first (one explicit checkbox, the statement the API records), and the
 *  confirmation travels with the request (`consent: true`). Someone who has not yet acknowledged the recording notice
 *  (consent/RecordingNotice.tsx) does so in the same step, with a second box. */
function AddRecording({ m, canImport, canUpload, onDone }: { m: Meeting; canImport: boolean; canUpload: boolean; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<(() => void) | null>(null);
  const [stage, setStage] = useState<Stage | null>(null);
  const [progress, setProgress] = useState(0);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState<"upload" | "import" | null>(null); // the consent question, before either starts
  const agreed = useRef(false); // confirmed in this visit (the session shows it once the request has recorded it)
  const needsConsent = !m.recording_consent && !agreed.current;
  const notice = useRecordingNotice();
  const [everyone, setEveryone] = useState(false); // the two boxes of the question, never ticked for the person
  const [read, setRead] = useState(false);
  const [missed, setMissed] = useState(0);
  const ask = (what: "upload" | "import") => { opened.current = what; setEveryone(false); setRead(false); setMissed(0); setAsking(what); };
  // the question closes (confirmed, "Not now" or Esc): focus returns to the button that opened it, or, while that button is
  // busy importing, to the session's title
  const opened = useRef<"upload" | "import" | null>(null);
  const importBtn = useRef<HTMLButtonElement>(null);
  const uploadBtn = useRef<HTMLButtonElement>(null);
  useFocusOnClose(asking !== null, () => {
    const btn = opened.current === "import" ? importBtn.current : uploadBtn.current;
    return [btn, btn?.closest(".sc-row")?.querySelector(".sc-title")];
  });

  useEffect(() => { // a long upload: leaving the page would drop it
    if (stage !== "uploading") return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    addEventListener("beforeunload", warn);
    return () => removeEventListener("beforeunload", warn);
  }, [stage]);
  useEffect(() => () => abort.current?.(), []);

  async function chosen(file: File) {
    setError(null); setProgress(0); setStage("preparing");
    try {
      if (!VIDEO.test(file.name)) throw new Error("Choose a video file: MP4, WebM, MOV or MKV.");
      if (!file.size) throw new Error("That file is empty.");
      const duration = await videoLength(file);
      const start = () => post<{ upload: UploadTicket }>(`/meetings/${m.id}/recording/upload`,
        { filename: file.name, size: file.size, duration_s: Number(duration.toFixed(3)), consent: agreed.current }).then((r) => r.upload);
      let ticket = await start();
      const send = async () => {
        setStage("uploading"); setProgress(0);
        try { await sendFile(ticket, file, setProgress, abort); }
        catch (e) {
          if (!(e instanceof SendError) || e.status !== 403) throw e;
          ticket = await start(); // the link expired before the upload began (a sleeping laptop): once more with a fresh one
          setProgress(0);
          await sendFile(ticket, file, setProgress, abort);
        }
      };
      await send();
      setStage("checking");
      const complete = () => post(`/meetings/${m.id}/recording/complete`, { upload_id: ticket.id });
      try { await complete(); }
      catch (e) {
        if (!(e instanceof ApiError && e.code === "upload_missing")) throw e;
        await send(); // the file had not landed yet: send it again, then check again
        setStage("checking");
        await complete();
      }
      toast("Recording added. Notes are on the way.");
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.code === "consent_required") { agreed.current = false; ask("upload"); return; }
      setError(e instanceof SendError ? (e.status === -1 ? null : e.message)
        : e instanceof ApiError && e.status === 409 && !e.code ? "This session already has its recording."
        : e instanceof ApiError && e.status === 404 ? "Uploads are not available on this server."
        : errorMessage(e));
    } finally {
      abort.current = null;
      setStage(null);
      if (input.current) input.current.value = "";
    }
  }
  async function fromDrive() {
    setError(null); setImporting(true);
    try {
      await post(`/meetings/${m.id}/recording/import`, { consent: agreed.current });
      toast("Recording imported. Notes are on the way.");
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.code === "consent_required") { agreed.current = false; ask("import"); return; }
      setError(e instanceof ApiError && e.code === "no_recording_yet" ? "No recording on the event yet. Meet saves it a few minutes after the call."
        : e instanceof ApiError && e.code === "import_in_progress" ? "This recording is already being imported. The notes follow once it is in."
        : errorMessage(e));
    } finally { setImporting(false); }
  }
  function confirmed(e: FormEvent) { // the click that confirms is also the click that opens the file picker, or starts the import
    e.preventDefault();
    if (!everyone || (notice.needed && !read)) return setMissed((n) => n + 1);
    // the acknowledgement of the notice is its own record; it is not awaited, so the file picker still opens on this click
    if (notice.needed) notice.acknowledge().catch(() => { /* the notice at the top of the page asks again */ });
    const next = asking;
    agreed.current = true;
    setAsking(null);
    if (next === "upload") input.current?.click(); else if (next === "import") void fromDrive();
  }
  const pct = Math.round(progress * 100);
  const busy = stage !== null || importing || asking !== null;
  const label = stage === "preparing" ? "Preparing…" : stage === "uploading" ? `Uploading ${pct}%` : stage === "checking" ? "Checking the file…" : null;
  return (
    <>
      {canImport && (
        <button ref={importBtn} type="button" className="dp-btn small" onClick={() => (needsConsent ? ask("import") : void fromDrive())} disabled={busy}
                aria-label={`Import the recording of ${m.title} from Google Drive`}>
          {importing ? <><span className="sc-spin" aria-hidden="true" />Importing…</> : <><Glyph name="video" size={13} />Import from Google Drive</>}
        </button>
      )}
      {canUpload && (
        <>
          <input ref={input} type="file" hidden accept="video/mp4,video/webm,video/quicktime,video/x-matroska,.mp4,.webm,.mov,.mkv"
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) void chosen(f); }} />
          <button ref={uploadBtn} type="button" className={`dp-btn small${canImport ? " ghost" : ""}`} disabled={busy}
                  onClick={() => (needsConsent ? ask("upload") : input.current?.click())}
                  aria-label={`Upload the recording of ${m.title}`}>
            {label ?? <><Glyph name="plus" size={13} />{canImport ? "Upload" : "Add recording"}</>}
          </button>
          {stage === "uploading" && (
            <button type="button" className="dp-btn small ghost" onClick={() => abort.current?.()} aria-label={`Cancel the upload of ${m.title}`}>Cancel</button>
          )}
        </>
      )}
      {stage !== null && (
        <span className="dp-upload" role="progressbar" aria-label={`Uploading the recording of ${m.title}`} aria-valuetext={label ?? undefined}
              aria-valuenow={stage === "uploading" ? pct : stage === "checking" ? 100 : 0} aria-valuemin={0} aria-valuemax={100}>
          <i style={{ transform: `scaleX(${stage === "checking" ? 1 : stage === "uploading" ? progress : 0})` }} />
        </span>
      )}
      {asking && (
        <form className="ww-panel rc-ask" aria-labelledby={`rc-ask-${m.id}`} onSubmit={confirmed} noValidate
              onKeyDown={(e) => { if (e.key === "Escape") setAsking(null); }}>
          <p className="rc-ask-q" id={`rc-ask-${m.id}`}>Before this recording is added</p>
          <Check id={`rc-all-${m.id}`} checked={everyone} onChange={setEveryone} missed={missed} autoFocus>
            Everyone in this session was told it is recorded and agreed to it.
          </Check>
          {notice.needed && (
            <Check id={`rc-read-${m.id}`} checked={read} onChange={setRead} missed={missed}>
              I have read the <a href="/recording" target="_blank" rel="noopener">Recording and AI Notice<span className="sr-only"> (opens in a new tab)</span></a>.
            </Check>
          )}
          <p className="rc-ask-more">
            Your confirmation is kept with the session: your name, the time and the notice's version.
            {!notice.needed && <> <a href="/recording" target="_blank" rel="noopener">Recording notice<span className="sr-only"> (opens in a new tab)</span></a></>}
          </p>
          <div className="rc-ask-actions">
            <button type="button" className="dp-btn small ghost" onClick={() => setAsking(null)}>Not now</button>
            <button type="submit" className="dp-btn small primary">{asking === "upload" ? "Confirm and choose the file" : "Confirm and import"}</button>
          </div>
        </form>
      )}
      {error && <p className="error dp-upload-error" role="alert">{error}</p>}
    </>
  );
}

function MeetingList({ focus }: { focus: string | null }) {
  const { project, config, me, demo } = useSession();
  const sched = useScheduling(project.id);
  const google = sched.integrations?.google.connected ?? false;
  const [rows, setRows] = useState<Meeting[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null); // the session whose Cancel asks once more
  const [form, setForm] = useState<{ timing?: Meeting } | null>(null); // open: a new session, or the time for one booked without it
  const [group, setGroup] = useState<string | null>(null); // the one section open
  const [, setTick] = useState(0); // "Now" and "Awaiting recording" follow the clock

  const load = useCallback(() => {
    api<Meeting[]>(`/meetings?project_id=${project.id}`).then(setRows).catch((e) => setError(errorMessage(e)));
  }, [project.id]);
  useEffect(load, [load]);
  useLive("meetings", load, project.id); // a session booked (here, by the planner or through Calendly), imported or given its notes
  useEffect(() => { const t = window.setInterval(() => setTick((n) => n + 1), 60_000); return () => window.clearInterval(t); }, []);
  const groups = groupCalls(rows ?? []);
  const groupOf = (id: string | null) => groups.find((g) => g.calls.some((m) => m.id === id))?.id;
  const loaded = rows !== null;
  useEffect(() => { // first load: the upcoming sessions, else the recorded ones; a booking link opens its session's section
    if (!loaded) return;
    setGroup((g) => (focus && groupOf(focus)) || g || groups[0]?.id || null);
  }, [loaded, focus]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!focus || !rows) return;
    const t = window.setTimeout(() => document.getElementById(`call-${focus}`)?.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" }),
      reducedMotion() ? 0 : 340);
    return () => window.clearTimeout(t);
  }, [focus, rows]);

  async function save(v: ScheduleValues) {
    const body = { starts_at: v.starts_at, duration_min: v.duration_min, attendees: v.attendees, provider: v.provider, join_url: v.join_url };
    const r = form?.timing
      ? await post<{ meeting: Meeting }>(`/meetings/${form.timing.id}/schedule`, body)
      : await post<{ meeting: Meeting }>("/meetings", { project_id: project.id, title: v.title, ...body });
    setForm(null);
    setGroup("upcoming");
    toast(r.meeting.provider === "google" ? "Scheduled. Invitations are on their way." : "Scheduled.");
    load();
  }

  async function cancel(m: Meeting) {
    if (confirming !== m.id) return setConfirming(m.id);
    setConfirming(null); setBusy(true); setError(null);
    try {
      const r = await post<{ warning: string | null }>(`/meetings/${m.id}/cancel`, {});
      toast(r.warning ?? (m.kt_key ? "Cancelled. The session can be booked again." : "Cancelled."), r.warning ? "error" : "ok");
      load();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }

  return (
    <section className="dp sc-page">
      <header className="dp-head sc-page-head">
        <div>
          <p className="dp-eyebrow">{project.name}</p>
          <h1>KT sessions</h1>
          <p className="dp-sub">Recorded sessions become notes linked to the code.</p>
        </div>
        <button type="button" className="dp-btn primary sc-new" onClick={() => setForm({})}><Glyph name="plus" size={15} />Schedule session</button>
      </header>
      <RecordingNotice />
      {error && <p className="error" role="alert">{error}</p>}
      {!rows ? (!error && (
        <div className="sc-list" aria-busy="true">
          <span className="sr-only" role="status">Loading…</span>
          {[0, 1, 2].map((i) => <div key={i} className="sc-row"><Skeleton lines={2} /></div>)}
        </div>
      )) : rows.length === 0 ? (
        <EmptyState title="No KT sessions yet" action={<button type="button" className="dp-btn primary" onClick={() => setForm({})}><Glyph name="plus" size={15} />Schedule session</button>}>
          A senior walks through the code; the recording becomes notes.
        </EmptyState>
      ) : (
        <Accordion open={group} onChange={setGroup}>
          {groups.map((g) => (
            <AccordionItem key={g.id} id={g.id} title={g.title} count={g.calls.length} icon={<Glyph name={g.glyph} size={16} />}
                           sub={g.calls.slice(0, 3).map((m) => m.title).join(" · ") + (g.calls.length > 3 ? ` · and ${g.calls.length - 3} more` : "")}
                           meta={g.calls.some((m) => phase(m).key === "now") ? <Pill tone="neutral" live>Now</Pill> : undefined}>
              <ul className="sc-list">
                {g.calls.map((m, i) => {
                  const p = phase(m);
                  const mine = isManager(me.role) || m.created_by === me.id;
                  const canCancel = m.status === "scheduled" && mine;
                  // a recording can only be added once the session has been held: never for an upcoming one (the demo included)
                  const started = p.key !== "upcoming" && (p.key === "now" || p.key === "awaiting" || p.key === "unscheduled" || ["ended", "failed"].includes(m.status) || demo);
                  const adder = me.role !== "new_hire" || m.created_by === me.id;
                  const open = ["scheduled", "ended", "failed"].includes(m.status) && started && adder;
                  const canImport = open && m.provider === "google" && !!m.calendar_event_id && google;
                  const canUpload = open && !!config.recording_upload;
                  const joinable = m.status === "scheduled" && p.key !== "awaiting" && !!m.join_url;
                  const invited = (m.attendees ?? []).filter((a) => a.user_id !== me.id);
                  const start = m.starts_at ? new Date(m.starts_at) : m.started_at ? new Date(m.started_at) : null;
                  // where it happens matters before it does; afterwards, how long it ran
                  const where = m.status === "scheduled" || m.join_url ? whereLabel(m) : lengthOf(m.duration_s);
                  return (
                    <Reveal as="li" key={m.id} delay={i * 40} className={`sc-row${m.id === focus ? " here" : ""} p-${p.key}`} id={`call-${m.id}`}>
                      <span className="sc-date" aria-hidden="true">
                        {start ? <><b>{start.getDate()}</b><small>{start.toLocaleDateString([], { month: "short" })}</small></> : <Glyph name="calendar" size={16} />}
                      </span>
                      <div className="sc-main">
                        <a className="sc-title" href={`#/meetings/${m.id}`}>{m.title}</a>
                        <p className="sc-meta">
                          {m.starts_at && <span>{sessionWhen(m)}</span>}
                          {m.starts_at && where && <span className="sc-dot" aria-hidden="true">·</span>}
                          {where && <span>{where}</span>}
                          {invited.length > 0 && (
                            <span className="sc-faces" title={invited.map((a) => a.name || a.email).join(", ")}>
                              <span className="sr-only">With {invited.map((a) => firstName(a.name || a.email)).join(", ")}</span>
                              {invited.slice(0, 4).map((a) => <Avatar key={a.email} name={a.name || a.email} size={20} />)}
                              {invited.length > 4 && <span className="sc-more">+{invited.length - 4}</span>}
                            </span>
                          )}
                        </p>
                      </div>
                      <div className="sc-side">
                        <StatusPill status={m.status} label={p.label} />
                        {(joinable || canCancel || canImport || canUpload || (p.key === "unscheduled" && mine)) && (
                          <div className="sc-actions">
                            {joinable && <JoinLink m={m} small />}
                            {p.key === "unscheduled" && mine && (
                              <button type="button" className="dp-btn small" onClick={() => setForm({ timing: m })}><Glyph name="calendar" size={13} />Set time</button>
                            )}
                            {(canImport || canUpload) && <AddRecording m={m} canImport={canImport} canUpload={canUpload} onDone={load} />}
                            {canCancel && (
                              <button type="button" className="dp-btn small ghost" onClick={() => cancel(m)} disabled={busy}
                                      aria-label={`${confirming === m.id ? "Confirm cancelling" : "Cancel"} ${m.title}`}
                                      onBlur={() => setConfirming((c) => (c === m.id ? null : c))}>
                                {confirming === m.id ? "Confirm cancel" : "Cancel session"}
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    </Reveal>
                  );
                })}
              </ul>
            </AccordionItem>
          ))}
        </Accordion>
      )}
      <ScheduleDialog open={form !== null} onClose={() => setForm(null)} onSubmit={save} data={sched}
                      heading={form?.timing ? "Set a time" : "Schedule session"} title={form?.timing?.title}
                      ktKey={form?.timing?.kt_key} />
    </section>
  );
}

type NotesResponse = { meeting: Meeting; notes: Notes | null; transcript?: "full" | "managers_only" | null };

function MeetingNotes({ id, at }: { id: string; at: number }) {
  const { project, me } = useSession();
  const manager = isManager(me.role);
  const [data, setData] = useState<NotesResponse | null>(null);
  const [video, setVideo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const player = useRef<HTMLVideoElement>(null);
  const list = useRef<HTMLOListElement>(null);
  const [pos, setPos] = useState(at); // the moment in focus: from the link, the player, or a clicked timestamp
  const [section, setSection] = useState<string | null>(null); // the one section of the notes open
  const [booking, setBooking] = useState<string | null>(null);
  const track = useRef<HTMLDivElement>(null);
  const [trackW, setTrackW] = useState(0); // markers closer than a fingertip on the track merge into one
  const follow = useFollowUps(id, manager);
  useEffect(() => {
    const el = track.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setTrackW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [data]);

  const loadNotes = useCallback(() => {
    api<NotesResponse>(`/meetings/${id}/notes`).then((x) => { setData(x); setError(null); }).catch((e) => setError(errorMessage(e)));
    api<{ url: string | null }>(`/meetings/${id}/recording`).then((r) => setVideo((v) => (r.url && v && v.split("?")[0] === r.url.split("?")[0] ? v : r.url)))
      .catch(() => { /* notes still work without video */ });
  }, [id]);
  useEffect(loadNotes, [loadNotes]);
  useLive("meetings", loadNotes, project.id); // the call goes live, ends, or its notes are written while the page is open

  const utterances = data?.notes?.utterances ?? [];
  const transcript = manager && data?.transcript === "full" && utterances.length > 0;

  /** Scroll the transcript (not the page) to the line spoken at s, once its section has opened and drawn. */
  const showLine = useCallback((s: number, wait: boolean, page = false) => {
    window.setTimeout(() => {
      const ol = list.current;
      if (!ol || !utterances.length) return;
      const u = [...utterances].reverse().find((x) => x.start_s <= s + 0.5) ?? utterances[0];
      const li = document.getElementById(`t${Math.floor(u.start_s)}`);
      if (li) ol.scrollTo({ top: ol.scrollTop + li.getBoundingClientRect().top - ol.getBoundingClientRect().top - 12, behavior: reducedMotion() ? "auto" : "smooth" });
      if (page) document.getElementById("kt-transcript")?.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
    }, wait && !reducedMotion() ? 360 : 30);
  }, [utterances]);

  // A link to a moment opens the transcript there (managers) or the code shown at that moment; otherwise the summary.
  useEffect(() => {
    if (!data) return;
    setPos(at);
    if (player.current && at) player.current.currentTime = at;
    if (!at) { setSection((x) => x ?? "summary"); return; }
    if (transcript) { setSection("transcript"); showLine(at, true, true); } else setSection(data.notes?.kt_links.length ? "code" : "summary");
  }, [at, data, video]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Jump to a moment: play it when there is a recording; managers also see it in the transcript. */
  function seek(s: number, toTranscript = false) {
    setPos(s);
    if (player.current) { player.current.currentTime = s; player.current.play().catch(() => { /* autoplay blocked: the position is still set */ }); }
    if (transcript && (toTranscript || !player.current || section === "transcript")) {
      const wait = section !== "transcript";
      setSection("transcript");
      showLine(s, wait);
    } else if (!player.current && !transcript) setSection("code");
  }

  async function book(f: FollowUp) {
    setBooking(f.key);
    try {
      await post("/kt-plan/schedule", { project_id: project.id, key: f.key });
      toast(f.presenter ? `Booked. ${f.presenter.name.replace(/\s*\(.*?\)/g, "")} has the agenda.` : "Booked.");
    } catch (e) {
      toast(e instanceof ApiError && e.status === 409 ? "That session changed or was booked meanwhile. Showing the latest." : errorMessage(e), e instanceof ApiError && e.status === 409 ? undefined : "error");
    } finally { await follow.load(); setBooking(null); }
  }

  if (error) return <><a href="#/meetings" className="back">All KT sessions</a><p className="error" role="alert">{error}</p></>;
  if (!data) {
    return (
      <section className="dp notes" aria-busy="true">
        <span className="sr-only" role="status">Loading notes…</span>
        <a href="#/meetings" className="back">All KT sessions</a>
        <Skeleton lines={1} height={26} style={{ width: "min(420px, 80%)", marginBottom: 20 }} />
        <div className="dp-stage"><span className="dp-tile-video big s-processing" /></div>
        <div className="acc">{[0, 1, 2].map((i) => <div key={i} className="acc-item" style={{ padding: "1rem 1.1rem" }}><Skeleton lines={1} /></div>)}</div>
      </section>
    );
  }
  const n = data.notes;
  const m = data.meeting;
  const nearest = utterances.length ? utterances.reduce((best, u) => (Math.abs(u.start_s - at) < Math.abs(best - at) ? u.start_s : best), utterances[0].start_s) : undefined;
  // the segment being spoken at `pos`; a deep link with no recording highlights the nearest one, as before
  const spoken = pos ? [...utterances].reverse().find((u) => u.start_s <= pos + 0.5)?.start_s : undefined;
  const active = pos === at && at ? nearest : spoken;
  const length = Math.max(1, m.duration_s ?? 0, ...(utterances.map((u) => u.start_s + 10)), ...(n?.kt_links.map((k) => k.start_s + 10) ?? []));
  // timeline markers, sorted; any within 24px of the previous group's first marker join it and seek to its start
  type Mark = { s: number; kind: "code" | "action"; label: string; title: string };
  const marks: Mark[] = !n ? [] : [
    ...n.action_items.filter((a) => a.at_s != null).map((a) => ({ s: a.at_s!, kind: "action" as const, label: `action item: ${a.text}`, title: a.text })),
    ...n.kt_links.map((k) => ({ s: k.start_s, kind: "code" as const, label: `code on screen: ${k.file_path}${k.symbol ? `, ${k.symbol}` : ""}`, title: k.symbol ?? k.file_path })),
  ].sort((a, b) => a.s - b.s);
  const groups: Mark[][] = [];
  for (const k of marks) {
    const g = groups[groups.length - 1];
    if (g && trackW > 0 && ((k.s - g[0].s) / length) * trackW < 24) g.push(k); else groups.push([k]);
  }
  const codeHref = (k: { file_path: string; start_line: number | null }) => `#/code?path=${encodeURIComponent(k.file_path)}${k.start_line ? `&line=${k.start_line}` : ""}`;
  const speakers = [...new Set(utterances.map((u) => u.speaker))];
  const files = n ? [...new Set(n.kt_links.map((k) => k.file_path))] : [];
  const open = (follow.rows ?? []).filter((f) => !f.booked).length;
  const plural = (k: number, one: string) => `${k} ${one}${k === 1 ? "" : "s"}`;

  return (
    <section className="dp notes">
      <header className="dp-head dp-head-tight">
        <div>
          <a href="#/meetings" className="back">All KT sessions</a>
          <h1>{m.title}</h1>
          <p className="dp-chips">
            <StatusPill status={m.status} label={phase(m).label} />
            {m.starts_at && <span className="dp-chip"><Glyph name="calendar" size={12} />{sessionWhen(m)}</span>}
            {lengthOf(m.duration_s) && <span className="dp-chip"><Glyph name="clock" size={12} />{lengthOf(m.duration_s)}</span>}
            {n && n.kt_links.length > 0 && <span className="dp-chip is-count"><Glyph name="code" size={12} />{n.kt_links.length} code moment{n.kt_links.length === 1 ? "" : "s"}</span>}
            {n && n.action_items.length > 0 && <span className="dp-chip is-count"><Glyph name="task" size={12} />{n.action_items.length} action item{n.action_items.length === 1 ? "" : "s"}</span>}
          </p>
        </div>
        {m.status === "scheduled" && m.join_url && phase(m).key !== "awaiting" && <JoinLink m={m} />}
      </header>
      <RecordingNotice />

      <div className="dp-stage">
        {video ? <video ref={player} src={video} controls className="player dp-video" onTimeUpdate={(e) => setPos(e.currentTarget.currentTime)} /> : (
          <div className="dp-poster">
            <CallTile m={m} big bare />
            <p className="dp-poster-note">
              <span className="dp-poster-icon" aria-hidden="true"><Glyph name="video" size={16} /></span>
              <span>{poster(m.status, !!n, transcript)}</span>
            </p>
          </div>
        )}
        {n && (n.kt_links.length > 0 || n.action_items.length > 0) && (
          <div className="dp-timeline">
            <div className="dp-track" ref={track} aria-label="Moments in this call">
              <span className="dp-track-fill" style={{ width: `${Math.min(100, (pos / length) * 100)}%` }} />
              {groups.map((g, i) => {
                const first = g[0], code = g.some((k) => k.kind === "code");
                return (
                  <button key={i} className={`dp-mark ${code ? "code" : "action"}${code && pos >= first.s ? " passed" : ""}`} style={{ left: `${(first.s / length) * 100}%` }}
                    onClick={() => seek(first.s)}
                    aria-label={g.length === 1 ? `${mmss(first.s)} ${first.label}` : `${mmss(first.s)}, ${g.length} moments: ${g.map((k) => `${mmss(k.s)} ${k.label}`).join("; ")}`}
                    title={g.map((k) => `${mmss(k.s)} · ${k.title}`).join("\n")} />
                );
              })}
            </div>
            <div className="dp-track-legend">
              <span className="dp-mono">{mmss(pos)}</span>
              <span className="dp-legend"><i className="code" />Code on screen</span>
              <span className="dp-legend"><i className="action" />Action item</span>
              <span className="dp-mono">{mmss(length)}</span>
            </div>
          </div>
        )}
      </div>

      {!n ? (
        <EmptyState title={m.status === "failed" || m.status === "cancelled" || m.status === "ended" ? "No notes" : m.status === "processing" ? "Writing notes" : phase(m).label}>
          {m.status === "cancelled" ? "This session was cancelled." : m.status === "failed" ? "The recording failed, so there are no notes." :
            m.status === "ended" ? "No recording yet. Add one from KT sessions." :
            m.status === "processing" ? "Notes appear here when they are ready." :
            phase(m).key === "awaiting" ? "Add the recording from KT sessions to get notes." :
            "Notes are written from the recording, after the session."}
        </EmptyState>
      ) : (
        <Accordion open={section} onChange={setSection} className="kt-notes">
          <AccordionItem id="summary" title="Summary" icon={<Glyph name="sparkle" size={16} />}
                         sub={n.decisions.length ? plural(n.decisions.length, "decision") : "What the call covered"}>
            <p className="dp-summary"><Rich text={n.summary} /></p>
            {n.decisions.length > 0 && (
              <>
                <h3 className="dp-sub-h">Decisions</h3>
                <ul className="dp-ticks">{n.decisions.map((d, i) => <li key={i}><Glyph name="check" size={13} /><span><Rich text={d} /></span></li>)}</ul>
              </>
            )}
          </AccordionItem>
          {n.action_items.length > 0 && (
            <AccordionItem id="actions" title="Action items" count={n.action_items.length} icon={<Glyph name="task" size={16} />}
                           sub={n.action_items[0].text + (n.action_items.length > 1 ? ` · and ${n.action_items.length - 1} more` : "")}>
              <ul className="dp-actions-list">
                {n.action_items.map((a, i) => (
                  <li key={i}>
                    <span className="dp-box" aria-hidden="true" />
                    <span><Rich text={a.text} /></span>
                    {a.at_s != null && <button className="dp-time" onClick={() => seek(a.at_s!)} aria-label={`Go to ${mmss(a.at_s)}`}>{mmss(a.at_s)}</button>}
                  </li>
                ))}
              </ul>
            </AccordionItem>
          )}
          <AccordionItem id="code" title="Code shown on screen" count={n.kt_links.length} icon={<Glyph name="code" size={16} />}
                         sub={files.length ? files.join(" · ") : "No code was shown"}>
            {n.kt_links.length === 0 ? <p className="dp-meta">No code was shown in this session.</p> : (
              <ol className="dp-chapters">
                {n.kt_links.map((k, i) => (
                  <li key={i} className={pos >= k.start_s && (i === n.kt_links.length - 1 || pos < n.kt_links[i + 1].start_s) && pos > 0 ? "now" : ""}>
                    <i className="dp-chapter-dot" aria-hidden="true" />
                    <button className="dp-time" onClick={() => seek(k.start_s)} aria-label={`Go to ${mmss(k.start_s)}`}>{mmss(k.start_s)}</button>
                    <a href={codeHref(k)}>
                      {k.symbol && <span className="dp-chapter-sym">{k.symbol}</span>}
                      <span className="dp-chapter-path">{k.file_path}{k.start_line ? `:${k.start_line}` : ""}</span>
                    </a>
                    <FeedbackButton kind="meeting" target={m.id} where={`At ${mmss(k.start_s)}, ${k.file_path}`}
                                    label={`the moment at ${mmss(k.start_s)}`} className="dp-chapter-flag" />
                  </li>
                ))}
              </ol>
            )}
          </AccordionItem>
          {n.terms.length > 0 && (
            <AccordionItem id="terms" title="New terms" count={n.terms.length} icon={<Glyph name="learn" size={16} />}
                           sub={n.terms.map((t) => t.term).join(" · ")}>
              <dl className="dp-terms">{n.terms.map((t) => <div key={t.term}><dt>{t.term}</dt><dd><Rich text={t.definition} /></dd></div>)}</dl>
            </AccordionItem>
          )}
          {transcript && (
            <AccordionItem id="transcript" domId="kt-transcript" title="Transcript" count={utterances.length} icon={<Glyph name="message" size={16} />}
                           sub={speakers.join(", ")}
                           meta={<Pill tone="neutral">Managers only</Pill>}>
              <TranscriptList utterances={utterances} active={active} onSeek={(s) => seek(s)} listRef={list} playable={!!video} />
            </AccordionItem>
          )}
          {manager && (
            <AccordionItem id="followups" title="Follow-up KT sessions" count={follow.rows?.length} icon={<Glyph name="calendar" size={16} />}
                           sub={follow.rows === null ? "What is left to explain" : follow.rows.length === 0 ? "Nothing left to explain"
                             : open ? `${plural(open, "session")} to book` : "Every follow-up is booked"}
                           meta={open > 0 ? <Pill tone="warn">{open} to book</Pill> : undefined}>
              {follow.error && <p className="error" role="alert">{follow.error}</p>}
              <FollowUpList meetingId={m.id} rows={follow.rows} mode="book" onBook={book} busy={booking}
                            onSeek={transcript ? (s) => seek(s, true) : undefined} />
            </AccordionItem>
          )}
        </Accordion>
      )}
      {(n || video || m.recording_consent) && <RecordingRecord m={m} projectId={project.id} meId={me.id} manager={manager} recorded={!!(n || video)} />}
      {manager && (n || video) && m.status !== "processing" && <RemoveRecording m={m} onDone={loadNotes} />}
    </section>
  );
}

type RemovalRequest = {
  id: string; meeting_id: string; requested_by: string | null; requested_by_name: string | null; reason: string;
  status: "open" | "removed" | "declined"; decided_by_name: string | null; note: string; created_at: string;
};
const day = (iso: string) => new Date(iso).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });

/** What a recorded session carries besides its notes: who confirmed that everyone was told and agreed, and the requests
 *  to remove the recording. Anyone on the project can ask; the project's managers are told, and a manager removes the
 *  recording (below) or declines with a note. Asking removes nothing. */
function RecordingRecord({ m, projectId, meId, manager, recorded }: { m: Meeting; projectId: string; meId: string; manager: boolean; recorded: boolean }) {
  const [rows, setRows] = useState<RemovalRequest[]>([]);
  const [form, setForm] = useState<"ask" | string | null>(null); // "ask": the member's request; an id: the manager's decline
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() =>
    api<{ requests: RemovalRequest[] }>(`/recording-removal-requests?project_id=${encodeURIComponent(projectId)}&meeting_id=${encodeURIComponent(m.id)}`)
      .then((r) => setRows(r.requests)).catch(() => { /* the notes still read without it */ }),
  [projectId, m.id]);
  useEffect(() => { void load(); }, [load]);
  // a form closes: focus returns to the button that opened it, or, when the request was sent or declined (the button is
  // gone), to the line that now says so, or to this section
  const section = useRef<HTMLElement>(null);
  const lastForm = useRef<string | null>(null);
  useFocusOnClose(form !== null, () => {
    const s = section.current;
    return [s?.querySelector(`[data-rc-return="${CSS.escape(lastForm.current ?? "")}"]`), s?.querySelector("[data-rc-return='sent']"), s];
  });
  useLive("meetings", load, projectId);
  const open = rows.filter((r) => r.status === "open");
  const mine = open.find((r) => r.requested_by === meId);
  const answered = [...rows].reverse().find((r) => r.requested_by === meId && r.status === "declined");
  const c = m.recording_consent;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !form) return;
    setBusy(true); setError(null);
    try {
      if (form === "ask") {
        await post(`/meetings/${m.id}/recording/removal-requests`, { reason: text.trim() });
        toast("Sent. A manager decides.");
      } else {
        await post(`/meetings/${m.id}/recording/removal-requests/${form}/decline`, { note: text.trim() });
        toast("Declined. They have been told.");
      }
      await load(); // the list first, so the line that says what happened is there when the form closes and focus moves to it
      setForm(null); setText("");
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  const close = () => { setForm(null); setText(""); setError(null); };
  const openForm = (which: string) => { lastForm.current = which; setForm(which); setText(""); };

  return (
    <section ref={section} className="rc-record" aria-label="Recording consent and removal">
      {c && (
        <p className="rc-line">
          <Glyph name="check" size={14} />
          <span>Everyone was told and agreed to the recording: confirmed by {c.by_name ?? "a former member"}, {day(c.at)}.{" "}
            <a href="/recording" target="_blank" rel="noopener">Recording notice<span className="sr-only"> (opens in a new tab)</span></a></span>
        </p>
      )}
      {manager && open.map((r) => (
        <div key={r.id} className="rc-request">
          <div className="rc-request-row">
            <p><strong>{r.requested_by_name ?? "A former member"}</strong> asked for this recording to be removed, {day(r.created_at)}.</p>
            {form !== r.id && <button type="button" className="dp-btn small ghost" data-rc-return={r.id} onClick={() => openForm(r.id)}>Decline</button>}
          </div>
          {r.reason && <blockquote className="rc-reason">{r.reason}</blockquote>}
          {form !== r.id && <p className="dp-meta">To agree, remove the recording below.</p>}
          {form === r.id && (
            <form className="ww-panel rc-form" onSubmit={submit} onKeyDown={(e) => { if (e.key === "Escape") close(); }}>
              <label className="field"><span>A note for {firstName(r.requested_by_name ?? "them")} (optional)</span>
                <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} rows={2} autoFocus disabled={busy} />
              </label>
              <div className="rc-form-actions">
                <button type="button" className="dp-btn small ghost" onClick={close} disabled={busy}>Cancel</button>
                <button type="submit" className="dp-btn small primary" disabled={busy}>{busy ? "Declining…" : "Decline request"}</button>
              </div>
            </form>
          )}
        </div>
      ))}
      {recorded && !manager && (mine ? (
        <p className="rc-line" data-rc-return="sent"><Glyph name="clock" size={14} /><span>You asked for this recording to be removed, {day(mine.created_at)}. A manager decides.</span></p>
      ) : (
        <div className="rc-request">
          <div className="rc-request-row">
            <p>{answered ? <>Your last request was declined{answered.decided_by_name ? ` by ${answered.decided_by_name}` : ""}{answered.note ? <>: <q>{answered.note}</q></> : "."}</>
              : "Something here that should not be kept?"}</p>
            {form !== "ask" && <button type="button" className="dp-btn small ghost" data-rc-return="ask" onClick={() => openForm("ask")}>Ask to remove it</button>}
          </div>
          {form === "ask" && (
            <form className="ww-panel rc-form" onSubmit={submit} onKeyDown={(e) => { if (e.key === "Escape") close(); }}>
              <label className="field"><span>Why (optional). Only the project's managers read this.</span>
                <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} rows={2} autoFocus disabled={busy} />
              </label>
              <div className="rc-form-actions">
                <button type="button" className="dp-btn small ghost" onClick={close} disabled={busy}>Cancel</button>
                <button type="submit" className="dp-btn small primary" disabled={busy}>{busy ? "Sending…" : "Ask a manager"}</button>
              </div>
            </form>
          )}
        </div>
      ))}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

/** Managers: remove what this session's recording put in Welcome Wave (the video, transcript, notes, KT links and the
 *  windows Ask cites), for a call where something was said or shown that should not have been. The session stays, as held.
 *  The title is typed to confirm. DELETE /api/meetings/{id}/recording. */
function RemoveRecording({ m, onDone }: { m: Meeting; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function remove(e: FormEvent) {
    e.preventDefault();
    if (typed.trim() !== m.title.trim() || busy) return;
    setBusy(true); setError(null);
    try {
      await api(`/meetings/${m.id}/recording`, { method: "DELETE", body: JSON.stringify({ confirm: typed.trim() }) });
      toast("Recording and notes removed");
      setOpen(false); setTyped(""); onDone();
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return (
    <section className="mt-remove" aria-labelledby={`rm-${m.id}`}>
      <div className="mt-remove-row">
        <div>
          <p className="mt-remove-title" id={`rm-${m.id}`}>Remove this recording</p>
          <p className="dp-meta">The video, transcript, notes and code links go, and Ask stops citing this call. The session stays.</p>
        </div>
        {!open && <button type="button" className="dp-btn small ghost" onClick={() => setOpen(true)}>Remove recording</button>}
      </div>
      {open && (
        <form onSubmit={remove}>
          <label className="field"><span>Type <strong>{m.title}</strong> to confirm</span>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} autoFocus disabled={busy}
                   onKeyDown={(e) => { if (e.key === "Escape") { setOpen(false); setTyped(""); } }} />
          </label>
          <button type="button" className="dp-btn small ghost" onClick={() => { setOpen(false); setTyped(""); setError(null); }} disabled={busy}>Cancel</button>
          <button type="submit" className="dp-btn small danger" disabled={typed.trim() !== m.title.trim() || busy}>
            {busy ? "Removing…" : "Remove for good"}
          </button>
        </form>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

/** The line under the session's tile when there is no video to play, true to what the session is. */
function poster(status: string, notes: boolean, transcript: boolean): ReactNode {
  if (notes) return <><strong>No video for this session.</strong> <span className="dp-poster-more">{transcript ? "The timestamps below jump to each moment in the transcript." : "The timestamps below mark each moment."}</span></>;
  switch (status) {
    case "scheduled": return <strong>Not recorded yet.</strong>;
    case "live": return <strong>Live now.</strong>;
    case "processing": return <><strong>Processing the recording.</strong> <span className="dp-poster-more">Notes and the video appear when it is done.</span></>;
    case "cancelled": return <strong>Cancelled.</strong>;
    case "ended": return <strong>No recording.</strong>;
    case "failed": return <strong>The recording failed.</strong>;
    default: return <strong>No video for this session.</strong>;
  }
}
