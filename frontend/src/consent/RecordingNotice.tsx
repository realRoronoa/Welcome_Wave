/** The recording notice, acknowledged once per version by each member (document "recording" of POST /api/legal/accept).
 *  It shows at the top of KT sessions the first time someone opens them, and again when the notice changes: what a
 *  recording is, who sees it, and that they can say no. The words are the notice's own "In brief" (src/legal/recording.ts),
 *  so the card and the published notice cannot drift apart. Reading the list is never blocked; adding a recording asks for
 *  the same acknowledgement in its own step (pages/Meetings.tsx).
 *
 *  Acknowledging says "I have read this". It is not agreement to any particular recording: that is asked before each
 *  session, of everyone in it, by its host. */
import { useEffect, useState } from "react";
import { errorMessage } from "../api";
import { LEGAL_PATH, versionDate } from "../legal/paths";
import { recording } from "../legal/recording";
import { useSession } from "../session";
import { toast } from "../ui";
import { focusOn } from "./focus";
import { acceptDocuments, loadLegalStatus, useLegalStatus } from "./legalStatus";
import "./consent.css";

/** The version of the notice this build shows (the status's own `versions` when it is loaded; they are checked on accept). */
export function useRecordingNotice() {
  const { me } = useSession();
  const status = useLegalStatus();
  useEffect(() => { if (!status) loadLegalStatus(me.id).catch(() => { /* asked again when a recording is added */ }); }, [status, me.id]);
  const version = status?.versions.recording ?? null;
  return {
    /** True once we know this person has not acknowledged the current notice. */
    needed: !!status && status.needs.includes("recording"),
    version,
    /** Record the acknowledgement. Rejects with the API's error. */
    acknowledge: () => acceptDocuments(["recording"], version ? { recording: version } : {}),
  };
}

export const NOTICE_ID = "recording-notice";

/** Bring the notice into view and move focus to it (someone tried to add a recording before acknowledging it). */
export function showRecordingNotice() {
  const el = document.getElementById(NOTICE_ID);
  if (!el) return;
  el.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  el.focus({ preventScroll: true });
}

export default function RecordingNotice() {
  const notice = useRecordingNotice();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!notice.needed) return null;

  async function acknowledge() {
    setBusy(true); setError(null);
    try {
      // the card goes once this is recorded: focus moves to the page's heading, not to nowhere
      const heading = document.getElementById(NOTICE_ID)?.parentElement?.closest("main, section")?.querySelector("h1") ?? document.querySelector("main h1");
      await notice.acknowledge();
      toast("Noted. Thank you.");
      requestAnimationFrame(() => focusOn(heading));
    }
    catch (e) { setError(errorMessage(e)); setBusy(false); }
  }
  return (
    <section id={NOTICE_ID} className="ww-panel lgc-notice" aria-labelledby="recording-notice-h" tabIndex={-1}>
      <div className="lgc-notice-head">
        <div>
          <p className="lgc-notice-eyebrow">Recording and AI notice</p>
          <h2 id="recording-notice-h">Before you take part in a KT session</h2>
        </div>
        {notice.version && <span className="lgc-notice-version">Version of {versionDate(notice.version)}</span>}
      </div>
      <ul className="lgc-notice-points">
        {recording.summary.map((s, i) => <li key={i}>{s}</li>)}
      </ul>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="lgc-notice-foot">
        <p>This says you have read the notice. It is not your agreement to any one recording.</p>
        <div className="lgc-notice-actions">
          <a className="dp-btn small ghost" href={LEGAL_PATH.recording} target="_blank" rel="noopener">Read the full notice<span className="sr-only"> (opens in a new tab)</span></a>
          <button type="button" className="dp-btn small primary" onClick={acknowledge} disabled={busy}>{busy ? "Saving…" : "I have read this"}</button>
        </div>
      </div>
    </section>
  );
}
