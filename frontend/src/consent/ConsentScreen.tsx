/** The consent screen (LegalGate.tsx decides when): the documents this person has not accepted in their current version,
 *  each with what it is, when it took effect, its short version (or, for a new version of one they accepted before, what
 *  changed), and a link to the full text in a new tab. One action: "Agree and continue", which records the acceptance of
 *  exactly the versions shown (POST /api/legal/accept). The sentence above the button says what the click means.
 *
 *  Nobody is cornered: "If you do not agree" keeps the ways out reachable without accepting anything. Sign out; leave the
 *  organisation (production); and, for an admin, download the organisation's data or delete the organisation. With
 *  LEGAL_ENFORCE on, the API still answers exactly these calls (backend/app/legal.py `_EXEMPT`).
 *
 *  In the entry frame, like every screen between sign-in and the workspace. Its own chunk: it imports the documents. */
import { FormEvent, useEffect, useRef, useState } from "react";
import { api, ApiError, Config, download, errorMessage, Me, post } from "../api";
import { DocLink } from "../auth/Agree";
import { Busy, EntryLayout, Identity } from "../auth/Entry";
import { DOCUMENTS, VERSIONS, detail, plain, versionDate } from "../legal/documents";
import { focusIn } from "../surface";
import { Msg, Note } from "../ui";
import { acceptDocuments, LegalStatus, PersonalDoc } from "./legalStatus";
import "./consent.css";

type Props = {
  config: Config; me: Me; status: LegalStatus; signOut?: () => void; reloadAccount?: () => void;
  onAccepted: (status: LegalStatus) => void;
};

/** What the click means, for exactly the documents asked for. */
function meaning(docs: PersonalDoc[]): string {
  const terms = docs.includes("terms"), privacy = docs.includes("privacy");
  if (terms && privacy) return "By continuing you agree to the Terms of Service, which include the Acceptable Use rules, and confirm that you have read the Privacy Policy.";
  if (terms) return "By continuing you agree to the Terms of Service, which include the Acceptable Use rules.";
  return "By continuing you confirm that you have read the Privacy Policy.";
}

export default function ConsentScreen({ config, me, status, signOut, reloadAccount, onAccepted }: Props) {
  const legal = config.legal ?? null;
  const docs = status.required.filter((d) => status.needs.includes(d));
  /** The version of a document this person accepted before, if any: then this is an update, not a first reading. */
  const before = (d: PersonalDoc) => status.accepted.find((a) => a.document === d && a.version !== DOCUMENTS[d].version) ?? null;
  const updated = docs.some((d) => before(d));
  /** The server is on a newer version than this page's build shows: reload, so nobody accepts words they did not see. */
  const [stale, setStale] = useState(() => docs.some((d) => status.versions[d] !== VERSIONS[d]));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const frame = useRef<HTMLDivElement>(null);

  // the screen replaces the sign-in form (or a loading card): reading starts at its heading
  useEffect(() => { const h = frame.current?.closest(".en-form"); if (h instanceof HTMLElement) focusIn(h); }, []);

  async function agree() {
    setBusy(true); setMsg(null);
    try {
      onAccepted(await acceptDocuments(docs, VERSIONS));
    } catch (e) {
      setBusy(false);
      if (e instanceof ApiError && e.code === "version_changed") return setStale(true);
      setMsg({ ok: false, text: errorMessage(e) });
    }
  }

  const changed = docs.filter((d) => before(d));
  const title = !updated ? "Before you continue"
    : changed.length > 1 ? "We have updated our terms and privacy policy"
    : changed[0] === "privacy" ? "We have updated our privacy policy" : "We have updated our terms";
  const said = docs.some((d) => before(d) && DOCUMENTS[d].changes?.length); // the new version says what changed
  const lead = updated ? `Read ${said ? "what changed" : "the current version"}, then agree to carry on.`
    : `Using Welcome Wave with ${me.org_name} comes with ${docs.length === 1 ? "this document" : "these two documents"}.`;
  return (
    <EntryLayout wide title={title} lead={lead}>
      <div ref={frame}>
        <ul className="lgc-docs">
          {docs.map((d) => {
            const doc = DOCUMENTS[d], prev = before(d);
            const changes = prev && doc.changes?.length ? doc.changes : null;
            return (
              <li key={d} className="lgc-doc">
                <div className="lgc-doc-head">
                  <h2>{doc.title}</h2>
                  <span className={`lgc-tag${prev ? " is-updated" : ""}`}>{prev ? "Updated" : "New"}</span>
                </div>
                <p className="lgc-doc-lede">{plain(doc.lede, legal)}</p>
                {changes ? (
                  <>
                    <p className="lgc-doc-k">What changed</p>
                    <ul className="lgc-points">{changes.map((c, i) => <li key={i}>{plain(c, legal)}</li>)}</ul>
                  </>
                ) : (
                  <details className="lgc-brief">
                    <summary>In brief</summary>
                    <ul className="lgc-points">{doc.summary.map((s, i) => <li key={i}>{plain(s, legal)}</li>)}</ul>
                  </details>
                )}
                <p className="lgc-doc-foot">
                  <span>In effect from {versionDate(doc.version)}{prev ? `. You accepted the version of ${versionDate(prev.version)}.` : ""}</span>
                  <DocLink to={d}>Read it in full</DocLink>
                </p>
              </li>
            );
          })}
        </ul>

        {stale ? (
          <div className="lgc-stale" role="alert">
            <p>These documents changed while this page was open. Reload to read the current ones.</p>
            <button type="button" className="en-primary" onClick={() => location.reload()}>Reload</button>
          </div>
        ) : (
          <>
            <p className="lgc-says" id="lgc-says">{meaning(docs)}</p>
            <button type="button" className="en-primary lgc-agree" onClick={agree} disabled={busy} aria-describedby="lgc-says lgc-kept">
              <Busy busy={busy} label="Saving…">Agree and continue</Busy>
            </button>
            <p className="lgc-kept" id="lgc-kept">We keep a record of this: your account, the versions, the time, and the network and browser it came from.</p>
          </>
        )}
        <Note msg={msg} />

        <WaysOut me={me} privacyEmail={detail(legal, "privacy_email")} signOut={signOut} reloadAccount={reloadAccount} />
      </div>
      <Identity email={me.email} onSwitch={signOut ?? (() => location.assign("/login"))} />
    </EntryLayout>
  );
}

/** "If you do not agree": what a person can still do without accepting. Nothing here accepts anything. */
function WaysOut({ me, privacyEmail, signOut, reloadAccount }: { me: Me; privacyEmail: string | null; signOut?: () => void; reloadAccount?: () => void }) {
  const admin = me.role === "admin";
  const [busy, setBusy] = useState<"export" | "leave" | "delete" | null>(null);
  const [confirming, setConfirming] = useState<"leave" | "delete" | null>(null);
  const [typed, setTyped] = useState("");
  const [msg, setMsg] = useState<Msg>(null);
  const [gone, setGone] = useState(false);

  async function exportData() {
    setBusy("export"); setMsg(null);
    try { await download("/org/export", "welcome-wave-export.json"); setMsg({ ok: true, text: "Export downloaded." }); }
    catch (e) { setMsg({ ok: false, text: errorMessage(e) }); }
    finally { setBusy(null); }
  }
  async function leave() {
    setBusy("leave"); setMsg(null);
    try { await post("/me/leave", {}); reloadAccount?.(); }
    catch (e) {
      setBusy(null); setConfirming(null);
      setMsg({ ok: false, text: e instanceof ApiError && e.code === "last_admin"
        ? `You are the only admin of ${me.org_name}, and other people use it. Another admin has to join before you can leave.`
        : errorMessage(e) });
    }
  }
  async function remove(e: FormEvent) {
    e.preventDefault();
    if (typed.trim() !== me.org_name || busy) return;
    setBusy("delete"); setMsg(null);
    try {
      await api("/org", { method: "DELETE", body: JSON.stringify({ confirm: typed.trim() }) });
      setGone(true);
      window.setTimeout(() => signOut?.(), 3500);
    } catch (err) { setBusy(null); setMsg({ ok: false, text: errorMessage(err) }); }
  }

  if (gone) return <p className="notice lgc-gone" role="status">{me.org_name} is deleted. Signing you out.</p>;
  return (
    <details className="lgc-out">
      <summary>If you do not agree</summary>
      <div className="lgc-out-body">
        <p>To sign out, choose “Use a different account” below. Nothing is recorded, and nothing in {me.org_name} changes while you decide.</p>
        {admin
          ? <p>As an admin you can take {me.org_name}'s data out, or delete the organisation, without accepting.</p>
          : <p>To have your personal data exported or erased, ask an admin of {me.org_name}
              {privacyEmail ? <>, or write to <a href={`mailto:${privacyEmail}`}>{privacyEmail}</a></> : null}.</p>}
        <div className="lgc-out-actions">
          {admin && (
            <button type="button" className="secondary" onClick={exportData} disabled={busy !== null}>
              {busy === "export" ? "Preparing…" : "Export the organisation's data"}
            </button>
          )}
          {reloadAccount && confirming !== "leave" && (
            <button type="button" className="secondary" onClick={() => { setConfirming("leave"); setMsg(null); }} disabled={busy !== null}>Leave {me.org_name}</button>
          )}
          {admin && confirming !== "delete" && (
            <button type="button" className="set-danger-outline" onClick={() => { setConfirming("delete"); setTyped(""); setMsg(null); }} disabled={busy !== null}>
              Delete the organisation
            </button>
          )}
        </div>
        {confirming === "leave" && (
          <div className="lgc-confirm" role="group" aria-label={`Leave ${me.org_name}`}>
            <p>You will lose access to every project in {me.org_name}. To come back, someone there has to invite you again.</p>
            <div className="lgc-out-actions">
              <button type="button" className="secondary" onClick={() => setConfirming(null)} disabled={busy !== null}>Cancel</button>
              <button type="button" className="set-danger-solid" onClick={leave} disabled={busy !== null} autoFocus>
                {busy === "leave" ? "Leaving…" : `Leave ${me.org_name}`}
              </button>
            </div>
          </div>
        )}
        {confirming === "delete" && (
          <form className="lgc-confirm" onSubmit={remove}>
            <p>This removes every project, person and record of {me.org_name}. It cannot be undone.</p>
            <div className="en-field">
              <label htmlFor="lgc-delete">Type <strong>{me.org_name}</strong> to confirm</label>
              <input id="lgc-delete" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} autoFocus
                     disabled={busy !== null} />
            </div>
            <div className="lgc-out-actions">
              <button type="button" className="secondary" onClick={() => setConfirming(null)} disabled={busy !== null}>Cancel</button>
              <button type="submit" className="set-danger-solid" disabled={typed.trim() !== me.org_name || busy !== null}>
                {busy === "delete" ? "Deleting…" : "Delete organisation"}
              </button>
            </div>
          </form>
        )}
        <Note msg={msg} />
      </div>
    </details>
  );
}
