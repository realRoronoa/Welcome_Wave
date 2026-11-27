import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, Me, post, ROLE_LABEL, setShowcaseToken, User } from "../api";
import { Msg, Note } from "../ui";
import type { Fb } from ".";
import { Agree } from "./Agree";
import { Busy, EntryLayout, GoogleGlyph, initials, PasswordInput } from "./Entry";
import "../styles/signin.css";

// closing the Google window is a choice, not an error
const QUIET = new Set(["auth/popup-closed-by-user", "auth/cancelled-popup-request", "auth/user-cancelled"]);

type Member = { id: string; name: string; email: string; role: string };
type Invited = { invitation_id: string; project_id: string; email: string; name: string | null; role: string };
type People = { members: Member[]; invited: Invited[] };

const RANK: Record<string, number> = { admin: 0, manager: 1, executive: 2, expert: 3, new_hire: 4 };
const CORE = new Set(["admin", "priya", "elena", "ravi", "meera", "vijay"]);
const isCore = (m: Member) => CORE.has(m.email.split("@")[0].toLowerCase());
const roleLabel = (r: string) => ROLE_LABEL[r] ?? (r === "executive" ? "Executive" : r.replace(/_/g, " "));
const plain = (name: string) => name.replace(/\s*\(.*?\)/g, "").trim() || name;
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

function Chevron() {
  return (
    <svg className="si-acct-go" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

type Props = {
  fb: Fb;
  initialCreating?: boolean;
  showcase?: { enabled: boolean; password: string } | null;
  onShowcaseSignedIn?: (me: Me) => void;
};

/** `initialCreating`: opened at /signup, so the form starts in "Create account" mode. Creating a login, by email or with
 *  Google, asks for the agreement first (Agree.tsx); signing in to an existing one does not. When `showcase.enabled` is on,
 *  one-click demo accounts appear right underneath the sign-in form so visitors can explore every role immediately. */
export default function SignIn({ fb, initialCreating = false, showcase = null, onShowcaseSignedIn }: Props) {
  const [creating, setCreating] = useState(initialCreating);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<string | false>(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [agreed, setAgreed] = useState(false);
  const [missed, setMissed] = useState(0);
  const [people, setPeople] = useState<People | null>(null);
  const [more, setMore] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const hasShowcase = !!showcase?.enabled;
  const demoPassword = showcase?.password ?? "";

  const loadPeople = useCallback(() => {
    if (!hasShowcase) return;
    api<People>("/showcase/people")
      .then((p) => alive.current && setPeople(p))
      .catch(() => alive.current && setPeople({ members: [], invited: [] }));
  }, [hasShowcase]);
  useEffect(loadPeople, [loadPeople]);

  /** Creating an account needs the box ticked: say so at the box, and do nothing else. */
  const blocked = () => { if (!creating || agreed) return false; setMsg(null); setMissed((n) => n + 1); return true; };

  async function run(action: () => Promise<unknown>, done?: string) {
    setBusy("form"); setMsg(null);
    try {
      await action();
      if (done) setMsg({ ok: true, text: done });
    } catch (e) {
      if (!QUIET.has((e as { code?: string })?.code ?? "")) setMsg({ ok: false, text: errorMessage(e) });
    } finally { if (alive.current) setBusy(false); }
  }

  async function signInShowcase(address: string, secret: string, key: string) {
    setBusy(key); setMsg(null);
    try {
      const { token } = await post<{ user: User; token: string }>("/showcase/login", { email: address.trim(), password: secret });
      setShowcaseToken(token);
      let me: Me;
      try { me = await api<Me>("/me"); } catch (e) { setShowcaseToken(null); throw e; }
      if (!alive.current) return;
      window.dispatchEvent(new CustomEvent("ww-terrain-pulse"));
      if (!reducedMotion()) await new Promise((r) => setTimeout(r, 360));
      if (alive.current) onShowcaseSignedIn?.(me);
    } catch (e) {
      if (!alive.current) return;
      const text = e instanceof ApiError && (e.status === 401 || e.status === 429) ? e.message : errorMessage(e);
      setMsg({ ok: false, text });
      setBusy(false);
    }
  }

  const sorted = (people?.members ?? []).slice().sort((a, b) => (RANK[a.role] ?? 9) - (RANK[b.role] ?? 9) || a.name.localeCompare(b.name));
  const core = sorted.some(isCore) ? sorted.filter(isCore) : sorted;
  const extra = sorted.filter((m) => !core.includes(m));
  const members = more ? [...core, ...extra] : core;
  const invited = people?.invited ?? [];
  const showcaseEmails = new Set(sorted.map((m) => m.email.toLowerCase()));

  function submit(e: FormEvent) {
    e.preventDefault();
    if (blocked() || busy) return;
    const addr = email.trim();
    if (!creating && hasShowcase && (addr.toLowerCase().endsWith("@demo.dev") || showcaseEmails.has(addr.toLowerCase()))) {
      void signInShowcase(addr, password, "form");
      return;
    }
    void run(() => (creating ? fb.createAccount(addr, password) : fb.signIn(addr, password)));
  }

  function pickDemo(m: Member) {
    if (busy) return;
    setEmail(m.email); setPassword(demoPassword);
    void signInShowcase(m.email, demoPassword, m.email);
  }

  async function acceptDemo(i: Invited) {
    if (busy) return;
    setBusy(i.invitation_id); setMsg(null);
    try {
      const joined = await post<User>("/showcase/invitations/accept", { project_id: i.project_id, invitation_id: i.invitation_id });
      if (!alive.current) return;
      setEmail(joined.email); setPassword(demoPassword);
      await signInShowcase(joined.email, demoPassword, i.invitation_id);
    } catch (e) {
      if (!alive.current) return;
      setMsg({ ok: false, text: errorMessage(e) });
      setBusy(false);
      loadPeople();
    }
  }

  function forgot() {
    const addr = email.trim();
    if (!addr) return setMsg({ ok: false, text: "Enter your email address first, then choose “Forgot password”." });
    if (hasShowcase && (addr.toLowerCase().endsWith("@demo.dev") || showcaseEmails.has(addr.toLowerCase()))) {
      setMsg({ ok: true, text: `Every demo account uses the password “${demoPassword}”.` });
      return;
    }
    void run(() => fb.resetPassword(addr), `If ${addr} has an account, a link to reset the password is on its way.`);
  }

  const toggle = () => {
    setCreating(!creating); setMsg(null); setAgreed(false); setMissed(0);
    // the address follows the mode (/login and /signup), so a reload or a shared link opens the same form
    if (/^\/(login|signup)\/?$/.test(location.pathname)) history.replaceState(history.state, "", `${creating ? "/login" : "/signup"}${location.search}`);
  };

  const first = (name: string) => plain(name).split(/\s+/)[0];
  const who = busy && busy !== "form"
    ? sorted.find((m) => m.email === busy)?.name ?? invited.find((i) => i.invitation_id === busy)?.name ?? null
    : null;
  const busyLabel = who ? `Signing in as ${first(who)}…` : "Please wait…";

  return (
    <EntryLayout
      title={creating ? "Create your account" : "Sign in to Welcome Wave"}
      lead="Use the email your manager invited, or pick a live demo account below."
      footer={
        <p>{creating ? "Already have an account?" : "New to Welcome Wave?"}{" "}
          <button type="button" className="en-link" onClick={toggle}>{creating ? "Sign in" : "Create an account"}</button></p>
      }>
      <button type="button" className="en-provider" onClick={() => { if (!blocked()) void run(fb.google); }} disabled={!!busy}>
        <GoogleGlyph /> Continue with Google
      </button>
      <p className="en-divider"><span>or with email</span></p>
      <form className="en-stack" onSubmit={submit} aria-busy={busy === "form"}>
        <div className="en-field">
          <label htmlFor="si-email">Email</label>
          <input id="si-email" type="email" autoComplete="email" required placeholder="you@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <span className="en-invalid">Enter a valid email address, like name@company.com.</span>
        </div>
        <div className="en-field">
          <span className="en-label-row">
            <label htmlFor="si-password">Password</label>
            {!creating && <button type="button" className="en-link quiet" onClick={forgot} disabled={!!busy}>Forgot password?</button>}
          </span>
          <PasswordInput id="si-password" required minLength={creating ? 6 : undefined} value={password} onChange={(e) => setPassword(e.target.value)}
                         autoComplete={creating ? "new-password" : "current-password"} aria-describedby={creating ? "pw-hint" : undefined} />
          <span className="en-invalid">{creating ? "Use at least 6 characters." : "Enter your password."}</span>
          {creating && <span id="pw-hint" className="en-hint">At least 6 characters. We will email you a link to confirm the address.</span>}
        </div>
        {creating && <Agree id="si-agree" checked={agreed} onChange={setAgreed} disabled={!!busy} missed={missed} />}
        <button type="submit" className="en-primary" disabled={!!busy}>
          <Busy busy={!!busy} label={busyLabel}>{creating ? "Create account" : "Sign in"}</Busy>
        </button>
      </form>
      <Note msg={msg} />

      {hasShowcase && !creating && (!people || sorted.length > 0) && (
        <section className="si-demo" aria-labelledby="si-demo-h">
          <div className="si-demo-head">
            <h2 id="si-demo-h">Demo accounts</h2>
            {demoPassword && <span>one click · password <code>{demoPassword}</code></span>}
          </div>
          <ul className="si-list" aria-busy={!people}>
            {!people && [0, 1, 2].map((n) => <li key={n} className="si-skel" aria-hidden="true"><span /><span /></li>)}
            {members.map((m) => {
              const role = roleLabel(m.role);
              return (
                <li key={m.id} className={core.includes(m) ? undefined : "si-extra"}>
                  <button type="button" className="si-acct" onClick={() => pickDemo(m)} disabled={!!busy && busy !== m.email}
                          aria-busy={busy === m.email}>
                    <span className="si-av" aria-hidden="true">{busy === m.email ? <span className="en-spin" /> : initials(m.name)}</span>
                    <span className="si-acct-text"><span className="sr-only">Sign in as </span><strong>{plain(m.name)}</strong><span>{m.email}</span></span>
                    <span className="si-role">{role}</span>
                    <Chevron />
                  </button>
                </li>
              );
            })}
          </ul>
          {extra.length > 0 && (
            <button type="button" className="si-more" aria-expanded={more} onClick={() => setMore((x) => !x)}>
              {more ? "Fewer accounts" : `${extra.length} more ${extra.length === 1 ? "account" : "accounts"}`}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d={more ? "M6 15l6-6 6 6" : "M6 9l6 6 6-6"} />
              </svg>
            </button>
          )}
        </section>
      )}

      {hasShowcase && !creating && invited.length > 0 && (
        <section className="si-demo" aria-labelledby="si-invited-h">
          <div className="si-demo-head">
            <h2 id="si-invited-h">Invited</h2>
            <span>accept to join and sign in</span>
          </div>
          <ul className="si-list">
            {invited.map((i) => {
              const role = roleLabel(i.role), name = i.name || i.email;
              return (
                <li key={i.invitation_id}>
                  <button type="button" className="si-acct" onClick={() => acceptDemo(i)} disabled={!!busy && busy !== i.invitation_id}
                          aria-busy={busy === i.invitation_id}>
                    <span className="si-av invited" aria-hidden="true">{busy === i.invitation_id ? <span className="en-spin" /> : initials(name)}</span>
                    <span className="si-acct-text"><span className="sr-only">Accept the invitation for </span><strong>{name}</strong><span>{i.email}</span></span>
                    <span className="si-role">{role}</span>
                    <span className="si-accept">Accept</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </EntryLayout>
  );
}
