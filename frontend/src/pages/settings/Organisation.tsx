import { FormEvent, useEffect, useRef, useState } from "react";
import { api, download, errorMessage, post } from "../../api";
import { useSession } from "../../session";
import { Msg, Note, toast } from "../../ui";
import { Glyph, SectionCard } from "./kit";
import { OrgLegal } from "./Legal";
import "./settings-more.css";

/** Settings -> Organisation (admins only): the legal side first (the Data Processing Agreement, the documents, who runs the
 *  service: Legal.tsx), then download everything the organisation has, erase one person's personal data on their request,
 *  or delete the organisation. Deleting asks for the organisation's name, then shows that it is done and
 *  signs the admin out. */
export default function Organisation() {
  const { me, signOut } = useSession();
  const name = me.org_name;
  const [exporting, setExporting] = useState(false);
  const [typed, setTyped] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [done, setDone] = useState(false);
  const [exportMsg, setExportMsg] = useState<Msg>(null);
  const [deleteMsg, setDeleteMsg] = useState<Msg>(null);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function exportData() {
    setExporting(true); setExportMsg(null);
    try { await download("/org/export", "welcome-wave-export.json"); toast("Export downloaded"); }
    catch (e) { setExportMsg({ ok: false, text: errorMessage(e) }); }
    finally { setExporting(false); }
  }

  async function remove(e: FormEvent) {
    e.preventDefault();
    if (typed.trim() !== name || deleting) return;
    setDeleting(true); setDeleteMsg(null);
    try {
      await api("/org", { method: "DELETE", body: JSON.stringify({ confirm: typed.trim() }) });
      setDone(true);
      timer.current = window.setTimeout(() => signOut?.(), 3500);
    } catch (err) {
      setDeleting(false);
      setDeleteMsg({ ok: false, text: errorMessage(err) });
    }
  }

  if (done) {
    return (
      <SectionCard id="org-gone" title="Organisation">
        <div className="set-gone" role="status">
          <span className="set-gone-mark" aria-hidden="true"><Glyph name="check" size={20} /></span>
          <p className="set-gone-title">{name} is deleted</p>
          <p className="set-gone-text">Its projects, people and records are gone. Signing you out.</p>
          {signOut && <button type="button" className="secondary" onClick={signOut}>Sign out now</button>}
        </div>
      </SectionCard>
    );
  }

  return (
    <>
      <OrgLegal />

      <SectionCard id="org-export" title="Export" description="Everything this organisation has, as one file. Secrets are left out.">
        <button type="button" className="secondary set-btn-icon" onClick={exportData} disabled={exporting}>
          <Glyph name="upload" size={15} className="set-flip" />{exporting ? "Preparing…" : "Export my organisation's data"}
        </button>
        <Note msg={exportMsg} />
      </SectionCard>

      <ErasePerson />

      <SectionCard id="org-delete" tone="danger" title="Delete organisation">
        <form className="set-delete" onSubmit={remove}>
          <p className="set-danger-text">This removes every project, person and record. It cannot be undone.</p>
          <label className="field"><span>Type <strong>{name}</strong> to confirm</span>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} aria-label={`Type ${name} to confirm`}
                   placeholder={name} disabled={deleting} />
          </label>
          <div className="actions">
            <button type="submit" className="set-danger-solid" disabled={typed.trim() !== name || deleting}>
              {deleting ? <><span className="en-spin" aria-hidden="true" />Deleting…</> : "Delete organisation"}
            </button>
          </div>
          <Note msg={deleteMsg} />
        </form>
      </SectionCard>
    </>
  );
}

type Erased = { erased: string; removed: Record<string, number>; kept: string[] };
const REMOVED: Record<string, string> = { chat_messages: "questions to the assistant", expert_requests: "questions sent to a person",
  notifications: "notifications", scheduling_links: "scheduling links", invitations: "invitations",
  recording_removal_reasons: "reasons in recording removal requests" };

/** A person's right to erasure: a member, someone who left, or someone enrolled who never joined. POST /api/org/people/erase
 *  with their email typed twice. Their name, email and GitHub username are replaced and what is theirs alone is deleted. */
function ErasePerson() {
  const [email, setEmail] = useState("");
  const [again, setAgain] = useState("");
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [done, setDone] = useState<Erased | null>(null);
  const ready = /\S+@\S+\.\S+/.test(email.trim()) && again.trim().toLowerCase() === email.trim().toLowerCase() && sure;

  async function erase(e: FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true); setMsg(null); setDone(null);
    try {
      const r = await post<Erased>("/org/people/erase", { email: email.trim(), confirm: again.trim() });
      setDone(r); setEmail(""); setAgain(""); setSure(false);
      toast("Personal data erased");
    } catch (err) { setMsg({ ok: false, text: errorMessage(err) }); } finally { setBusy(false); }
  }

  const removed = done ? Object.entries(done.removed).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${REMOVED[k] ?? k}`) : [];
  return (
    <SectionCard id="org-erase" tone="danger" title="Erase a person's data"
                 description="When someone asks for their personal data to be erased. Their onboarding records stay, under an erased name.">
      <form className="set-delete" onSubmit={erase}>
        <label className="field"><span>Their email address</span>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" spellCheck={false} disabled={busy} />
        </label>
        <label className="field"><span>Type it again to confirm</span>
          <input type="email" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="off" spellCheck={false} disabled={busy} />
        </label>
        <label className="set-check">
          <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} disabled={busy} />
          <span>Their name, email and GitHub username are replaced on their account, they can no longer sign in, and their
            conversations with the assistant, questions, notifications and invitations are deleted. Copies made earlier, such as
            a transcript they speak in, are not removed here: the result lists what stays. This cannot be undone.</span>
        </label>
        <div className="actions">
          <button type="submit" className="set-danger-solid" disabled={!ready || busy}>
            {busy ? <><span className="en-spin" aria-hidden="true" />Erasing…</> : "Erase their data"}
          </button>
        </div>
        <Note msg={msg} />
        {done && (
          <div className="notice" role="status">
            <p>Erased{removed.length ? `: ${removed.join(", ")}` : ""}. What stays:</p>
            <ul className="set-erase-kept">{done.kept.map((k) => <li key={k}>{k}</li>)}</ul>
          </div>
        )}
      </form>
    </SectionCard>
  );
}
