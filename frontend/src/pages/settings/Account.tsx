import { FormEvent, useState } from "react";
import { ApiError, errorMessage, Me, patch, post, ROLE_LABEL } from "../../api";
import { useSession } from "../../session";
import { Msg, Note, toast } from "../../ui";
import { ThemePref, useTheme } from "../../theme";
import { Avatar, GitHubMark, RolePill, SectionCard } from "./kit";
import { YourDocuments } from "./Legal";
import "./settings-more.css";

/** Leaving unbinds this login from the organisation, so the person can join another one they were invited to. The last admin
 *  can leave only an organisation nobody else uses (one created by mistake); roles of existing members cannot be changed. */
export default function Account() {
  const { me, reloadAccount } = useSession();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  async function leave() {
    setBusy(true); setMsg(null);
    try {
      await post("/me/leave", {});
      reloadAccount?.(); // back to the invitations or the no-account screen
    } catch (e) {
      setBusy(false); setConfirming(false);
      setMsg({ ok: false, text: e instanceof ApiError && e.code === "last_admin"
        ? `You are the only admin of ${me.org_name}, and other people use it. Invite a new admin by email in People, then leave once they have joined.`
        : errorMessage(e) });
    }
  }

  return (
    <>
      <SectionCard id="account-h" title="Your account">
        <div className="set-profile">
          <Avatar name={me.name || me.email} you size={52} />
          <div className="set-profile-text">
            <p className="set-profile-name">{me.name}</p>
            <p className="muted">{me.email}</p>
          </div>
          <RolePill role={me.role} />
        </div>
        <dl className="set-facts">
          <div><dt>Organisation</dt><dd>{me.org_name}</dd></div>
          <div><dt>Projects</dt><dd>{me.projects.length ? me.projects.map((p) => p.name).join(", ") : "None yet"}</dd></div>
          {me.role !== "executive" ? <div><dt>GitHub</dt><dd><OwnGitHub initial={me.github_login} /></dd></div>
            : me.github_login && <div><dt>GitHub</dt><dd><span className="set-gh"><GitHubMark size={13} />{me.github_login}</span></dd></div>}
        </dl>
      </SectionCard>

      <Appearance />

      <YourDocuments />

      {reloadAccount && (
        <SectionCard id="danger-h" tone="danger" title="Leave the organisation">
          <div className={`set-danger-row ${confirming ? "confirming" : ""}`}>
            <div>
              <p className="set-danger-title">Leave {me.org_name}</p>
              <p className="set-danger-text">{confirming
                ? `You will lose access to every project in ${me.org_name}. To come back, someone there has to invite you again.`
                : "For example, to join another team that invited you."}</p>
            </div>
            {!confirming ? (
              <button className="set-danger-outline" onClick={() => setConfirming(true)}>Leave {me.org_name}</button>
            ) : (
              <div className="actions">
                <button className="secondary" onClick={() => setConfirming(false)} disabled={busy}>Cancel</button>
                <button className="set-danger-solid" onClick={leave} disabled={busy} autoFocus>
                  {busy ? <><span className="en-spin" aria-hidden="true" />Leaving…</> : `Leave ${me.org_name}`}
                </button>
              </div>
            )}
          </div>
          <Note msg={msg} />
        </SectionCard>
      )}
    </>
  );
}

/** Your GitHub username: your pull requests move your tasks only when it is on record. PATCH /api/me. */
function OwnGitHub({ initial }: { initial: string | null }) {
  const [login, setLogin] = useState(initial);
  const [value, setValue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save(e: FormEvent) {
    e.preventDefault();
    if (value === null) return;
    setBusy(true); setError(null);
    try {
      const r = await patch<Me>("/me", { github_login: value.trim() || null });
      setLogin(r.github_login); setValue(null); toast(r.github_login ? "GitHub username saved" : "GitHub username removed");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 422 ? "Letters, numbers and dashes only." : errorMessage(err));
    } finally { setBusy(false); }
  }
  if (value !== null) {
    return (
      <form className="set-gh-edit" onSubmit={save}>
        <label className="sr-only" htmlFor="own-gh">Your GitHub username</label>
        <input id="own-gh" value={value} maxLength={39} pattern="[A-Za-z0-9\-]*" placeholder="e.g. octocat" autoFocus
               onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setValue(null); }} />
        <button type="submit" className="set-primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="set-ghost" onClick={() => { setValue(null); setError(null); }} disabled={busy}>Cancel</button>
        {error && <span className="error" role="alert">{error}</span>}
      </form>
    );
  }
  return (
    <span className="set-gh-cell">
      {login ? <span className="set-gh"><GitHubMark size={13} />{login}</span> : <span className="set-none">Not linked: your pull requests cannot move your tasks</span>}
      <button type="button" className="set-ghost" onClick={() => setValue(login ?? "")}>{login ? "Change" : "Add"}</button>
    </span>
  );
}

const THEMES: { id: ThemePref; label: string }[] = [{ id: "system", label: "System" }, { id: "light", label: "Light" }, { id: "dark", label: "Dark" }];

/** Light, dark, or follow the system: a radio group of small previews. */
function Appearance() {
  const [pref, setPref] = useTheme();
  return (
    <SectionCard id="appearance-h" title="Appearance" description="For this browser.">
      <div className="set-themes" role="radiogroup" aria-labelledby="appearance-h">
        {THEMES.map((t) => (
          <label key={t.id} className={`set-theme ${pref === t.id ? "on" : ""}`}>
            <input type="radio" name="ww-theme" value={t.id} checked={pref === t.id} onChange={() => setPref(t.id)} />
            <span className={`set-theme-preview t-${t.id}`} aria-hidden="true">
              <span className="tp-side" /><span className="tp-main"><span /><span /><span /></span>
            </span>
            <span className="set-theme-label">{t.label}</span>
          </label>
        ))}
      </div>
    </SectionCard>
  );
}

/** Settings for people who do not manage projects: only their own account. */
export function AccountPage() {
  return (
    <section className="set-page">
      <header className="page-head set-head">
        <h1>Settings</h1>
      </header>
      <div className="set-panel">
        <Account />
      </div>
    </section>
  );
}
