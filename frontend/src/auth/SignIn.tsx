import { FormEvent, useState } from "react";
import { errorMessage } from "../api";
import { Msg, Note } from "../ui";
import type { Fb } from ".";
import { Agree } from "./Agree";
import { Busy, EntryLayout, GoogleGlyph, PasswordInput } from "./Entry";

// closing the Google window is a choice, not an error
const QUIET = new Set(["auth/popup-closed-by-user", "auth/cancelled-popup-request", "auth/user-cancelled"]);

/** `initialCreating`: opened at /signup, so the form starts in "Create account" mode. Creating a login, by email or with
 *  Google, asks for the agreement first (Agree.tsx); signing in to an existing one does not. The agreement is recorded with
 *  the account itself, on the next screen (Setup.tsx, Invited.tsx), which asks for it in the request the API records. */
export default function SignIn({ fb, initialCreating = false }: { fb: Fb; initialCreating?: boolean }) {
  const [creating, setCreating] = useState(initialCreating);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [agreed, setAgreed] = useState(false);
  const [missed, setMissed] = useState(0);
  /** Creating an account needs the box ticked: say so at the box, and do nothing else. */
  const blocked = () => { if (!creating || agreed) return false; setMsg(null); setMissed((n) => n + 1); return true; };

  async function run(action: () => Promise<unknown>, done?: string) {
    setBusy(true); setMsg(null);
    try {
      await action();
      if (done) setMsg({ ok: true, text: done });
    } catch (e) {
      if (!QUIET.has((e as { code?: string })?.code ?? "")) setMsg({ ok: false, text: errorMessage(e) });
    } finally { setBusy(false); }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (blocked()) return;
    run(() => (creating ? fb.createAccount(email.trim(), password) : fb.signIn(email.trim(), password)));
  }

  function forgot() {
    if (!email.trim()) return setMsg({ ok: false, text: "Enter your email address first, then choose “Forgot password”." });
    run(() => fb.resetPassword(email.trim()), `If ${email.trim()} has an account, a link to reset the password is on its way.`);
  }

  const toggle = () => {
    setCreating(!creating); setMsg(null); setAgreed(false); setMissed(0);
    // the address follows the mode (/login and /signup), so a reload or a shared link opens the same form
    if (/^\/(login|signup)\/?$/.test(location.pathname)) history.replaceState(history.state, "", `${creating ? "/login" : "/signup"}${location.search}`);
  };
  return (
    <EntryLayout
      title={creating ? "Create your account" : "Sign in to Welcome Wave"}
      lead="Use the email your manager invited."
      footer={
        <p>{creating ? "Already have an account?" : "New to Welcome Wave?"}{" "}
          <button type="button" className="en-link" onClick={toggle}>{creating ? "Sign in" : "Create an account"}</button></p>
      }>
      <button type="button" className="en-provider" onClick={() => { if (!blocked()) run(fb.google); }} disabled={busy}>
        <GoogleGlyph /> Continue with Google
      </button>
      <p className="en-divider"><span>or with email</span></p>
      <form className="en-stack" onSubmit={submit}>
        <div className="en-field">
          <label htmlFor="si-email">Email</label>
          <input id="si-email" type="email" autoComplete="email" required placeholder="you@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <span className="en-invalid">Enter a valid email address, like name@company.com.</span>
        </div>
        <div className="en-field">
          <span className="en-label-row">
            <label htmlFor="si-password">Password</label>
            {!creating && <button type="button" className="en-link quiet" onClick={forgot} disabled={busy}>Forgot password?</button>}
          </span>
          <PasswordInput id="si-password" required minLength={creating ? 6 : undefined} value={password} onChange={(e) => setPassword(e.target.value)}
                         autoComplete={creating ? "new-password" : "current-password"} aria-describedby={creating ? "pw-hint" : undefined} />
          <span className="en-invalid">{creating ? "Use at least 6 characters." : "Enter your password."}</span>
          {creating && <span id="pw-hint" className="en-hint">At least 6 characters. We will email you a link to confirm the address.</span>}
        </div>
        {creating && <Agree id="si-agree" checked={agreed} onChange={setAgreed} disabled={busy} missed={missed} />}
        <button type="submit" className="en-primary" disabled={busy}>
          <Busy busy={busy} label="Please wait…">{creating ? "Create account" : "Sign in"}</Busy>
        </button>
      </form>
      <Note msg={msg} />
    </EntryLayout>
  );
}
