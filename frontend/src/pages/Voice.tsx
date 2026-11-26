import { useCallback, useEffect, useRef, useState } from "react";
import AgoraRTC, { type IAgoraRTCClient, type IMicrophoneAudioTrack, type IRemoteAudioTrack } from "agora-rtc-sdk-ng";
import AgoraRTM, { type RTMClient, type RTMEvents } from "agora-rtm-sdk";
import { api } from "../api";
import { useLive } from "../live";
import { useSession } from "../session";
import "./Voice.css";

type Action = { id: string; kind: string; payload: Record<string, string>; status: string; result: Record<string, string> };
type Turn = { speaker: "employee" | "assistant"; text: string; at?: number };
type Source = { label?: string; path?: string; title?: string; url?: string };
type Saved = { id: string; status: string; transcript: Turn[]; sources: Source[]; actions: Action[]; summary: Record<string, any>; created_at: string };
type Prepared = { id: string; channel: string; uid: number; app_id: string; rtc_token: string; status: string };

const actionLabel: Record<string, string> = {
  access_request: "Access request", kt_request: "KT session request", onboarding_task: "Onboarding task",
};

export default function Voice() {
  const { project } = useSession();
  const [available, setAvailable] = useState<boolean | null>(null);
  const [reason, setReason] = useState("");
  const [phase, setPhase] = useState<"idle" | "starting" | "live" | "stopping" | "error">("idle");
  const [status, setStatus] = useState("Ready when you are");
  const [error, setError] = useState("");
  const [transcriptNotice, setTranscriptNotice] = useState("");
  const [saved, setSaved] = useState<Saved | null>(null);
  const [history, setHistory] = useState<Saved[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [levels, setLevels] = useState([0, 0]);
  const [muted, setMuted] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const rtc = useRef<IAgoraRTCClient | null>(null);
  const rtm = useRef<RTMClient | null>(null);
  const mic = useRef<IMicrophoneAudioTrack | null>(null);
  const remoteAudio = useRef<IRemoteAudioTrack | null>(null);
  const current = useRef<Prepared | null>(null);
  const seen = useRef(new Set<string>());
  const refreshAvailability = useCallback(async () => {
    try {
      const v = await api<{ available: boolean; reason: string | null }>(`/voice/config?project_id=${encodeURIComponent(project.id)}`);
      setAvailable(v.available); setReason(v.reason ?? "");
    } catch (e) {
      setAvailable(false); setReason(e instanceof Error ? e.message : "Could not check Voice availability");
    }
  }, [project.id]);

  useLive(["lifecycle", "people"], () => { void refreshAvailability(); }, project.id);

  useEffect(() => {
    void refreshAvailability();
    const onVisible = () => { if (document.visibilityState === "visible") void refreshAvailability(); };
    document.addEventListener("visibilitychange", onVisible);
    api<Saved[]>(`/voice/sessions?project_id=${encodeURIComponent(project.id)}`).then(setHistory).catch(() => {});
    return () => { document.removeEventListener("visibilitychange", onVisible); void release(); };
  }, [project.id, refreshAvailability]);

  useEffect(() => {
    if (!saved?.id || phase !== "live") return;
    const timer = window.setInterval(() => {
      api<Saved>(`/voice/sessions/${saved.id}`).then(setSaved).catch(() => {});
      setLevels([mic.current?.getVolumeLevel() ?? 0, remoteAudio.current?.getVolumeLevel() ?? 0]);
    }, 1600);
    return () => window.clearInterval(timer);
  }, [saved?.id, phase]);

  useEffect(() => {
    if (!saved?.id || phase !== "live") return;
    const timer = window.setInterval(() => {
      api<Saved>(`/voice/sessions/${saved.id}/sync`, { method: "POST" }).then(next => {
        setSaved(next);
        if (next.status === "failed" || next.status === "ended") {
          setStatus(next.status === "failed" ? "Assistant disconnected" : "Conversation saved");
          setPhase(next.status === "failed" ? "error" : "idle");
          void release();
        }
      }).catch(() => {});
    }, 8000);
    return () => window.clearInterval(timer);
  }, [saved?.id, phase]);

  async function release() {
    mic.current?.stop(); mic.current?.close(); mic.current = null;
    remoteAudio.current = null;
    try { if (rtm.current && current.current) await rtm.current.unsubscribe(current.current.channel); } catch { /* leave continues */ }
    try { await rtm.current?.logout(); } catch { /* leave continues */ }
    try { await rtc.current?.leave(); } catch { /* leave continues */ }
    rtm.current = null; rtc.current = null; current.current = null;
  }

  function onMessage(event: RTMEvents.MessageEvent, sessionId: string) {
    let payload: any;
    try { payload = JSON.parse(event.message as string); } catch { return; }
    const agoraTranscript = payload?.object === "user.transcription" || payload?.object === "assistant.transcription";
    if (!agoraTranscript && payload?.type !== "transcript") return;
    const text = String(payload.text ?? "").trim();
    const speaker: Turn["speaker"] = payload.object === "user.transcription" || payload.role === "user" ? "employee" : "assistant";
    if (!text) return;
    setStatus(speaker === "assistant" ? "Assistant speaking" : "Listening to you");
    if (payload.object === "user.transcription" && payload.final !== true) return;
    if (payload.object === "assistant.transcription" && payload.turn_status === 0) return;
    if (!agoraTranscript && payload.final === false) return;
    const key = `${payload.turn_id ?? ""}:${speaker}:${text}`;
    if (seen.current.has(key)) return;
    seen.current.add(key);
    setTurns(v => [...v, { speaker, text }]);
    void api(`/voice/sessions/${sessionId}/transcript`, { method: "POST", body: JSON.stringify({ speaker, text }) }).catch(() => {});
  }

  async function connectSignaling(prepared: Prepared) {
    let signaling: RTMClient | null = null;
    let timer: number | undefined;
    try {
      signaling = new AgoraRTM.RTM(prepared.app_id, String(prepared.uid));
      rtm.current = signaling;
      const timeout = new Promise<never>((_, reject) => {
        timer = window.setTimeout(() => reject(new Error("Signaling timed out")), 12000);
      });
      await Promise.race([signaling.login({ token: prepared.rtc_token }), timeout]);
      if (current.current?.id !== prepared.id) {
        if (rtm.current === signaling) rtm.current = null;
        void signaling.logout().catch(() => {});
        return;
      }
      signaling.addEventListener("message", e => onMessage(e, prepared.id));
      await Promise.race([signaling.subscribe(prepared.channel), timeout]);
    } catch {
      if (current.current?.id === prepared.id) {
        setTranscriptNotice("Live transcript is unavailable. Your audio conversation can continue.");
      }
      if (rtm.current === signaling) rtm.current = null;
      if (signaling) void signaling.logout().catch(() => {});
    } finally {
      if (timer !== undefined) window.clearTimeout(timer);
    }
  }

  async function start() {
    if (phase !== "idle" && phase !== "error") return;
    setError(""); setTranscriptNotice(""); setPhase("starting"); setStatus("Requesting microphone access"); setSaved(null); setTurns([]); seen.current.clear();
    let prepared: Prepared | null = null;
    try {
      const track = await AgoraRTC.createMicrophoneAudioTrack();
      mic.current = track;
      setStatus("Preparing secure session");
      prepared = await api<Prepared>("/voice/sessions", { method: "POST", body: JSON.stringify({ project_id: project.id }) });
      current.current = prepared;
      const client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
      rtc.current = client;
      client.on("user-published", async (remote, mediaType) => {
        if (mediaType === "audio") { await client.subscribe(remote, "audio"); remoteAudio.current = remote.audioTrack ?? null; remoteAudio.current?.play(); setStatus("Assistant speaking"); }
      });
      client.on("user-unpublished", (_remote, mediaType) => { if (mediaType === "audio") remoteAudio.current = null; });
      client.on("connection-state-change", state => {
        if (state === "RECONNECTING") setStatus("Reconnecting audio");
        if (state === "CONNECTED") setStatus("Listening");
        if (state === "DISCONNECTED") setStatus("Audio disconnected");
      });
      await client.join(prepared.app_id, prepared.channel, prepared.rtc_token, prepared.uid);
      await client.publish(track);
      void connectSignaling(prepared);
      setStatus("Connecting assistant");
      const activated = await api<Saved>(`/voice/sessions/${prepared.id}/activate`, { method: "POST" });
      setSaved(activated);
      setPhase("live"); setStatus(activated.status === "active" ? "Listening" : "Assistant joining");
    } catch (e) {
      await release();
      if (prepared) void api(`/voice/sessions/${prepared.id}/stop`, { method: "POST" }).catch(() => {});
      setError(e instanceof Error ? e.message : "Could not start voice session");
      setPhase("error"); setStatus("Connection failed");
    }
  }

  async function stop() {
    if (!current.current) return;
    setPhase("stopping"); setStatus("Saving the conversation");
    const id = current.current.id;
    try {
      const result = await api<Saved>(`/voice/sessions/${id}/stop`, { method: "POST" });
      setSaved(result); setHistory(v => [result, ...v.filter(x => x.id !== id)]);
      setPhase("idle"); setStatus("Conversation saved");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not stop the session"); setPhase("error"); }
    finally { await release(); }
  }

  async function decide(action: Action, approve: boolean) {
    setBusyAction(action.id);
    try {
      await api(`/voice/actions/${action.id}/${approve ? "approve" : "decline"}`, {
        method: "POST", ...(approve ? { body: JSON.stringify({ confirm: true }) } : {}),
      });
      if (saved) setSaved(await api<Saved>(`/voice/sessions/${saved.id}`));
    } catch (e) { setError(e instanceof Error ? e.message : "The action could not be completed"); }
    finally { setBusyAction(null); }
  }

  const pending = saved?.actions.filter(a => a.status === "pending") ?? [];
  const done = saved?.actions.filter(a => a.status === "completed") ?? [];
  const shownTurns = turns.length ? turns : (saved?.transcript ?? []);
  return <main className="voice-page">
    <header className="voice-head"><div><p className="voice-kicker">Welcome Wave / Voice operations</p><h1>Ask. Understand. Move forward.</h1>
      <p>Talk through your onboarding. Answers cite the code, documents and KT sessions behind them.</p></div>
      <span className="voice-private">Only you can see this conversation</span></header>

    <div className="voice-grid">
      <section className="voice-stage" aria-label="Live voice session">
        <div className="voice-stage-top"><span className="voice-stage-mark">W</span><span>{project.name}</span><span className={`voice-state voice-state-${phase}`}>{phase === "live" ? "Live" : phase === "starting" ? "Connecting" : "Voice"}</span></div>
        <div className={`voice-orb voice-orb-${phase}`} aria-hidden="true"><div className="voice-orb-inner">W</div></div>
        <div className="voice-levels" aria-hidden="true">{Array.from({ length: 31 }, (_, i) => <span key={i} style={{ height: `${12 + Math.max(levels[0], levels[1]) * 65 + (Math.sin(i * .7) + 1) * (phase === "live" ? 8 : 2)}px` }} />)}</div>
        <div className="voice-state-copy"><strong>{status}</strong><p>{phase === "live" ? "Speak naturally. You can interrupt the assistant at any time." :
          available === false ? reason : "Your assistant can explain project knowledge and prepare follow-up work."}</p>
          {available === false && <button className="voice-recheck" type="button" onClick={() => { void refreshAvailability(); }}>Check again</button>}</div>
        {error && <p className="voice-error" role="alert">{error}</p>}
        {transcriptNotice && phase === "live" && <p className="voice-transcript-notice" role="status">{transcriptNotice}</p>}
        <div className="voice-controls">
          {phase === "live" ? <><button className="voice-secondary" onClick={() => { void mic.current?.setEnabled(muted); setMuted(!muted); }}>{muted ? "Unmute" : "Mute"}</button>
            <button className="voice-end" onClick={stop}>End conversation</button></> :
            <button className="voice-start" onClick={start} disabled={available !== true || phase === "starting" || phase === "stopping"}>
              {phase === "starting" ? "Connecting…" : "Start conversation"}</button>}
        </div>
      </section>

      <aside className="voice-side">
        <div className="voice-panel voice-transcript"><div className="voice-panel-head"><h2>Conversation</h2><span>{shownTurns.length} turns</span></div>
          {shownTurns.length ? <div className="voice-turns" aria-live="polite">{shownTurns.map((t, i) => <div className={`voice-turn voice-turn-${t.speaker}`} key={i}>
            <span>{t.speaker === "assistant" ? "Welcome Wave" : "You"}</span><p>{t.text}</p></div>)}</div> :
            <div className="voice-empty"><span>“</span><p>Ask about a function, a KT session, your plan, or what you need next.</p></div>}</div>
        <div className="voice-panel voice-action-panel"><div className="voice-panel-head"><h2>Actions</h2><span>{pending.length} to review</span></div>
          {pending.map(a => <article className="voice-action" key={a.id}><small>Approval required</small><h3>{actionLabel[a.kind] ?? a.kind}</h3>
            <p>{Object.values(a.payload).join(" · ")}</p><div className="voice-action-buttons"><button onClick={() => decide(a, true)} disabled={busyAction === a.id}>Approve</button>
              <button onClick={() => decide(a, false)} disabled={busyAction === a.id}>Decline</button></div></article>)}
          {done.map(a => <article className="voice-action voice-action-done" key={a.id}><small>Completed</small><h3>{actionLabel[a.kind] ?? a.kind}</h3>
            <p>{a.result?.status} · record {a.result?.record_id}</p></article>)}
          {!pending.length && !done.length && <p className="voice-panel-empty">Suggested work appears here for your approval.</p>}
        </div>
      </aside>
    </div>

    {(saved?.sources?.length || saved?.status === "ended") && <section className="voice-results">
      {saved?.sources?.length ? <div className="voice-panel"><div className="voice-panel-head"><h2>Sources</h2><span>{saved.sources.length} references</span></div>
        <div className="voice-source-list">{saved.sources.map((s, i) => <div className="voice-source" key={i}><span>{String(i + 1).padStart(2, "0")}</span><p>{s.label ?? s.path ?? s.title ?? "Project source"}</p></div>)}</div></div> : null}
      {saved?.status === "ended" && <div className="voice-panel"><div className="voice-panel-head"><h2>After the call</h2><span>Saved to onboarding</span></div>
        <p className="voice-panel-empty">{saved.summary?.turns ?? 0} conversation turns · {saved.summary?.completed_actions?.length ?? 0} completed actions</p>
        {saved.summary?.recap && <p className="voice-recap">{saved.summary.recap}</p>}
        {(saved.summary?.topics ?? []).map((t: string, i: number) => <p className="voice-summary-topic" key={i}>{t}</p>)}</div>}
    </section>}
    {history.length > 0 && !saved && <section className="voice-history"><h2>Recent conversations</h2>{history.slice(0, 3).map(h =>
      <button key={h.id} onClick={() => { setSaved(h); setTurns([]); }}>{new Date(h.created_at).toLocaleString()} <span>{h.summary?.turns ?? 0} turns →</span></button>)}</section>}
  </main>;
}
