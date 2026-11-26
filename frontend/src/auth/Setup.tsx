import { FormEvent, useState } from "react";
import { ApiError, errorMessage, Me, post } from "../api";
import { Msg, Note } from "../ui";
import type { Fb } from ".";
import { Busy, EntryLayout, Identity } from "./Entry";

/** 403 email_not_verified: the login works but its address is not confirmed yet. */
export function VerifyEmail({ fb, onVerified, signOut }: { fb: Fb; onVerified: () => void; signOut: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const address = fb.email();

  async function run(action: () => Promise<void>) {
    setBusy(true); setMsg(null);
    try { await action(); } catch (e) { setMsg({ ok: false, text: errorMessage(e) }); } finally { setBusy(false); }
  }
  const resend = () => run(async () => { await fb.resendVerification(); setMsg({ ok: true, text: `We sent another email to ${address}.` }); });
  const check = () => run(async () => {
    if (await fb.refreshVerification()) onVerified();
    else setMsg({ ok: false, text: "We cannot see the confirmation yet. Open the link in the email, then try again." });
  });

  return (
    <EntryLayout title="Check your inbox" lead={<>Open the link we sent to <strong>{address}</strong>.</>}>
      <div className="en-mail" aria-hidden="true">
        <svg viewBox="0 0 64 48" width="64" height="48">
          <rect x="2" y="4" width="60" height="40" rx="7" className="en-mail-body" />
          <path d="M4 9l28 19L60 9" className="en-mail-flap" />
        </svg>
        <span className="en-mail-ping" />
      </div>
      <ol className="en-steps">
        <li>Open the email from Welcome Wave. Check spam if it is not there.</li>
        <li>Choose the confirmation link in it.</li>
        <li>Come back here and continue.</li>
      </ol>
      <div className="en-actions">
        <button className="en-primary" onClick={check} disabled={busy}><Busy busy={busy} label="Checking…">I've verified it</Busy></button>
        <button className="secondary" onClick={resend} disabled={busy}>Resend email</button>
      </div>
      <Note msg={msg} />
      <Identity email={address} onSwitch={signOut} />
    </EntryLayout>
  );
}

type SignupProps = { fb: Fb; enabled: boolean; onDone: (me: Me) => void; retry: () => void; signOut: () => void; onBack?: () => void };

/** 403 no_account (or a person with invitations who prefers their own team): create a new organisation, if sign-up is open. */
export function Signup({ fb, enabled, onDone, retry, signOut, onBack }: SignupProps) {
  const [closed, setClosed] = useState(!enabled);
  const [name, setName] = useState(fb.displayName());
  const [org, setOrg] = useState("");
  const [project, setProject] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const invited = <>If your manager invited you, sign in with the email they invited. This login is <strong>{fb.email()}</strong>.</>;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      onDone(await post<Me>("/signup", { name: name.trim(), org_name: org.trim(), project_name: project.trim() }));
    } catch (err) {
      if (err instanceof ApiError && (err.status === 409 || err.code === "email_not_verified")) return retry();
      if (err instanceof ApiError && err.code === "signup_closed") setClosed(true);
      setMsg({ ok: false, text: errorMessage(err) });
      setBusy(false);
    }
  }

  const back = onBack && (
    <button type="button" className="en-back" onClick={onBack}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M19 12H5M12 19l-7-7 7-7" /></svg>
      Back to my invitations
    </button>
  );
  if (closed) {
    return (
      <EntryLayout title="No account yet" lead={invited}>
        <Note msg={msg} />
        <div className="en-actions">
          <button className="en-primary" onClick={signOut}>Use a different account</button>
        </div>
        {back}
      </EntryLayout>
    );
  }
  return (
    <EntryLayout title="Set up your team"
                 lead="You will be its admin, and can invite people next.">
      {back}
      {!onBack && <p className="en-callout">Invited by your manager? Sign in with that email instead.</p>}
      <form className="en-stack" onSubmit={submit}>
        <div className="en-field">
          <label htmlFor="su-name">Your name</label>
          <input id="su-name" required maxLength={120} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
          <span className="en-invalid">Tell your team who you are.</span>
        </div>
        <div className="en-field">
          <label htmlFor="su-org">Company name</label>
          <input id="su-org" required maxLength={120} autoComplete="organization" placeholder="Acme Inc." value={org} onChange={(e) => setOrg(e.target.value)} />
          <span className="en-invalid">Name the company or team.</span>
        </div>
        <div className="en-field">
          <label htmlFor="su-project">First project name</label>
          <input id="su-project" required maxLength={120} placeholder="Payments API" aria-describedby="su-project-hint" value={project} onChange={(e) => setProject(e.target.value)} />
          <span className="en-invalid">Name the first project; it can be renamed later.</span>
          <span id="su-project-hint" className="en-hint">One codebase and the people who work on it.</span>
        </div>
        <button type="submit" className="en-primary" disabled={busy}><Busy busy={busy} label="Creating…">Create my team</Busy></button>
      </form>
      <Note msg={msg} />
      <Identity email={fb.email()} onSwitch={signOut} />
    </EntryLayout>
  );
}
