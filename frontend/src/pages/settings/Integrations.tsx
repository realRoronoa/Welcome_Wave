import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, del, errorMessage, isManager, when } from "../../api";
import { useSession } from "../../session";
import { replaceRoute } from "../../route";
import { Msg, Note, toast } from "../../ui";
import { Facts, Integrations as Status } from "../schedule";
import { Glyph, SectionCard } from "./kit";
import "../../styles/schedule.css";

/** What Google's consent page came back with (the callback sends ?google=... to this tab). */
const OUTCOME: Record<string, Msg> = {
  connected: { ok: true, text: "Google Calendar connected." },
  denied: { ok: false, text: "Google Calendar was not connected. Only a manager or admin can connect it." },
  expired: { ok: false, text: "That sign-in took too long. Connect again." },
  failed: { ok: false, text: "Google did not accept the connection. Try again." },
};

/** GET /api/integrations/status (admins): one row per outside service this server can use. No secrets: a name, whether it is set up, and
 *  whether the last check worked. */
type Health = { key: string; name: string; configured: boolean; ok: boolean | null; detail: string };

/** A calm list of the services behind Welcome Wave: Connected is an ink check, Not set up an outline, Failing the one red. Hidden until
 *  the server has the endpoint (404). */
function Services({ googleConnected }: { googleConnected: boolean }) {
  const [items, setItems] = useState<Health[] | null>(null);
  const [gone, setGone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const check = useCallback(() => {
    setBusy(true); setErr(null);
    api<{ items: Health[] }>("/integrations/status")
      .then((r) => setItems(r.items))
      .catch((e) => { if (e instanceof ApiError && (e.status === 404 || e.status === 403)) setGone(true); else setErr(errorMessage(e)); })
      .finally(() => setBusy(false));
  }, []);
  useEffect(check, [check]);
  if (gone) return null;
  return (
    <SectionCard id="int-services" title="Connections"
                 actions={<button type="button" className="secondary" onClick={check} disabled={busy}>{busy ? "Checking…" : "Check again"}</button>}>
      {err && <p className="error" role="alert">{err}</p>}
      {!items ? (!err && <p className="loading" role="status"><span className="spinner" aria-hidden="true" />Checking…</p>) : (
        <ul className="int-list">
          {items.map((it) => {
            const connected = it.key === "google_calendar" && googleConnected;
            const state = !it.configured ? "off" : it.ok === false ? "bad" : it.ok === true || connected ? "ok" : "set";
            const detail = connected && state === "ok"
              ? "An organisation account is connected. Calendar and Drive access is checked when used."
              : it.detail;
            return (
              <li key={it.key} className="int-row">
                <div className="int-text">
                  <p className="int-name">{it.name}</p>
                  {detail && <p className="int-detail">{detail}</p>}
                </div>
                <span className={`int-chip is-${state}`}>
                  {state === "ok" && <Glyph name="check" size={12} />}
                  {state === "bad" && <Glyph name="alert" size={12} />}
                  {state === "ok" ? "Connected" : state === "bad" ? "Failing" : state === "set" ? "Set up" : "Not set up"}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </SectionCard>
  );
}

/** Settings -> Integrations: the services behind the app (admins), the organisation's Google Calendar, your Calendly link, and the Calendly webhook. */
export default function Integrations() {
  const { me, demo } = useSession();
  const manager = isManager(me.role);
  const [st, setSt] = useState<Status | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [link, setLink] = useState("");

  const load = useCallback(() => {
    api<Status>("/integrations").then((s) => { setSt(s); setLink(s.calendly.link ?? ""); }).catch((e) => setMsg({ ok: false, text: errorMessage(e) }));
  }, []);
  useEffect(load, [load]);
  useEffect(() => { // back from Google's consent page
    const q = new URLSearchParams(location.hash.split("?")[1] ?? "");
    const outcome = q.get("google");
    if (!outcome) return;
    setMsg(OUTCOME[outcome] ?? OUTCOME.failed);
    replaceRoute("#/settings?tab=integrations");
  }, []);

  async function run(action: () => Promise<void>) {
    setBusy(true); setMsg(null);
    try { await action(); } catch (e) { setMsg({ ok: false, text: errorMessage(e) }); } finally { setBusy(false); }
  }
  const connect = () => run(async () => {
    const { url } = await api<{ url: string }>("/integrations/google/start");
    location.assign(url);
  });
  const disconnect = () => run(async () => {
    if (!confirm) { setConfirm(true); return; }
    await del("/integrations/google");
    setConfirm(false);
    toast("Google Calendar disconnected.");
    load();
  });
  const saveLink = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      if (!link.trim()) { await del("/integrations/calendly/link"); toast("Calendly link removed."); }
      else { const r = await api<{ link: string }>("/integrations/calendly/link", { method: "PUT", body: JSON.stringify({ url: link.trim() }) }); setLink(r.link); toast("Calendly link saved."); }
      load();
    });
  };

  const g = st?.google;
  return (
    <>
      {me.role === "admin" && <Services googleConnected={!!g?.connected} />}
      <SectionCard id="int-how" title="How KT sessions work">
        <Facts items={[
          { glyph: "calendar", text: "Scheduled on Google Calendar, with Meet." },
          { glyph: "video", text: "Recorded in Meet, imported from Drive." },
          { glyph: "file", text: "Held elsewhere? Upload the recording." },
        ]} />
      </SectionCard>

      <SectionCard id="int-google" title="Google Calendar">
        <div className="sc-int">
          <span className="sc-int-mark" aria-hidden="true"><Glyph name="calendar" size={20} /></span>
          <div className="sc-int-text">
            <p className="sc-int-name">{!st ? "…" : g?.connected ? (g.account ?? "Connected") : "Not connected"}</p>
            <p className={`sc-state${g?.connected ? " on" : ""}`}>
              <i aria-hidden="true" />
              {!st ? "Checking" : g?.connected ? (demo ? "Built into the demo" : `Connected${g.connected_by ? ` by ${g.connected_by}` : ""}${g.connected_at ? `, ${when(g.connected_at)}` : ""}`)
                : g?.available ? (manager ? "Connect to schedule on Google Meet" : "A manager can connect it") : "Not set up on this server"}
            </p>
          </div>
          {g?.can_manage && (
            <div className="sc-int-actions">
              {g.connected ? (
                <>
                  {confirm && <button type="button" className="secondary" onClick={() => setConfirm(false)} disabled={busy}>Keep</button>}
                  <button type="button" className={confirm ? "" : "secondary"} onClick={disconnect} disabled={busy} onBlur={() => setConfirm(false)}>
                    {confirm ? "Disconnect" : "Disconnect…"}
                  </button>
                </>
              ) : g.available && (
                <button type="button" onClick={connect} disabled={busy}>{busy ? "Opening Google…" : "Connect Google Calendar"}</button>
              )}
            </div>
          )}
        </div>
        <Note msg={msg} />
      </SectionCard>

      <SectionCard id="int-calendly" title="Calendly">
        <form className="sc-link-form" onSubmit={saveLink}>
          <label className="field">
            <span>Your Calendly link</span>
            <input value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://calendly.com/you/kt" inputMode="url" maxLength={300} />
          </label>
          <button type="submit" className="secondary" disabled={busy || !st || link.trim() === (st.calendly.link ?? "")}>
            {link.trim() || !st?.calendly.link ? "Save" : "Remove"}
          </button>
        </form>
        <div className="sc-hook">
          <p className={`sc-state${st?.calendly.webhook ? " on" : ""}`}>
            <i aria-hidden="true" />
            {!st ? "Checking" : st.calendly.webhook ? "Bookings arrive here automatically" : "Webhook not set up"}
          </p>
          {manager && st && !st.calendly.webhook && !demo && <code>{st.calendly.webhook_url}</code>}
        </div>
      </SectionCard>
    </>
  );
}
