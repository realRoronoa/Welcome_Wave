/** Demo mode's sign-in: the production page's form (email and password against POST /api/demo/login), plus the demo
    accounts, one click each, and everyone invited and not yet joined, who accept here as their first sign-in would.
    Google needs Identity Platform, so its button stays visible but explains why it is off. */
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, Config, errorMessage, Me, post, ROLE_LABEL, setDemoUser, User } from "../api";
import { Busy, EntryLayout, GoogleGlyph, initials, PasswordInput } from "./Entry";
import "../styles/signin.css";

type Member = { id: string; name: string; email: string; role: string };
type Invited = { invitation_id: string; project_id: string; email: string; name: string | null; role: string };
type People = { members: Member[]; invited: Invited[] };

const RANK: Record<string, number> = { admin: 0, manager: 1, executive: 2, expert: 3, new_hire: 4 };
/** The accounts the demo is told through; the rest (more new hires at other points of their program) fold under "More". */
const CORE = new Set(["admin", "priya", "elena", "ravi", "meera", "vijay"]);
const isCore = (m: Member) => CORE.has(m.email.split("@")[0].toLowerCase());
const roleLabel = (r: string) => ROLE_LABEL[r] ?? (r === "executive" ? "Executive" : r.replace(/_/g, " "));
/** "Priya (Manager)" reads "Priya" next to a "Manager" badge. */
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
  config: Config;
  onSignedIn: (me: Me) => void;
  notice?: string | null; // why the page is shown again, e.g. the demo data was reset under a saved session
  /** Opened at /signup. The demo has no open sign-up (a manager invites each account), so the page stays "Sign in", says why,
   *  and offers the demo accounts and invitations, which are how an account starts here; the address becomes /login. */
  creating?: boolean;
};

export default function DemoSignIn({ config, onSignedIn, notice, creating: cameToSignUp }: Props) {
  const password0 = config.demo_password ?? "";
  const [creating] = useState(!!cameToSignUp); // as opened: the address changes to /login below
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<string | null>(null); // "form", a member's email or an invitation id
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [people, setPeople] = useState<People | null>(null);
  const alive = useRef(true);
  // set on every mount: StrictMode mounts, unmounts and mounts again in development
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const loadPeople = useCallback(() => {
    api<People>("/demo/people").then((p) => alive.current && setPeople(p))
      .catch(() => alive.current && setPeople({ members: [], invited: [] })); // the form still works: the list just stays away
  }, []);
  useEffect(loadPeople, [loadPeople]);
  useEffect(() => { // the address matches the form: there is no sign-up form in the demo
    if (/^\/signup\/?$/.test(location.pathname)) history.replaceState(history.state, "", `/login${location.search}`);
  }, []);

  const sorted = (people?.members ?? []).slice().sort((a, b) => (RANK[a.role] ?? 9) - (RANK[b.role] ?? 9) || a.name.localeCompare(b.name));
  // the picker stays short: the core accounts, and the others one click away (all of them when none is a core account)
  const core = sorted.some(isCore) ? sorted.filter(isCore) : sorted;
  const extra = sorted.filter((m) => !core.includes(m));
  const [more, setMore] = useState(false);
  const members = more ? [...core, ...extra] : core;
  const invited = people?.invited ?? [];

  /** The one way in: the login, then the account, then a beat for the brand panel's pulse before the workspace replaces the page. */
  async function signIn(address: string, secret: string, key: string) {
    setBusy(key); setError(null); setHint(null);
    try {
      const { user } = await post<{ user: User }>("/demo/login", { email: address.trim(), password: secret });
      setDemoUser(user.id);
      let me: Me;
      try { me = await api<Me>("/me"); } catch (e) { setDemoUser(null); throw e; }
      if (!alive.current) return;
      window.dispatchEvent(new CustomEvent("ww-terrain-pulse"));
      if (!reducedMotion()) await new Promise((r) => setTimeout(r, 420));
      if (alive.current) onSignedIn(me);
    } catch (e) {
      if (!alive.current) return;
      // the login's own words for a wrong password (401) and the attempt limit (429); everything else the usual way
      setError(e instanceof ApiError && (e.status === 401 || e.status === 429) ? e.message : errorMessage(e));
      setBusy(null);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!busy) signIn(email, password, "form");
  }

  function pick(m: Member) {
    if (busy) return;
    setEmail(m.email); setPassword(password0);
    signIn(m.email, password0, m.email);
  }

  /** Accepting is the invitee's first sign-in (as in production); after it their email signs in like any other. */
  async function accept(i: Invited) {
    if (busy) return;
    setBusy(i.invitation_id); setError(null); setHint(null);
    try {
      const joined = await post<User>("/demo/invitations/accept", { project_id: i.project_id, invitation_id: i.invitation_id });
      if (!alive.current) return;
      setEmail(joined.email); setPassword(password0);
      await signIn(joined.email, password0, i.invitation_id);
    } catch (e) {
      if (!alive.current) return;
      setError(errorMessage(e));
      setBusy(null);
      loadPeople(); // accepted or revoked elsewhere: show the list as it is now
    }
  }

  const first = (name: string) => plain(name).split(/\s+/)[0];
  const who = busy === "form" ? null : sorted.find((m) => m.email === busy)?.name ?? invited.find((i) => i.invitation_id === busy)?.name ?? null;
  const signingIn = who ? `Signing in as ${first(who)}…` : "Signing in…";

  return (
    <EntryLayout
      title="Sign in to Welcome Wave"
      lead={creating ? "The demo has no open sign-up. Pick an account below." : "Use the email your manager invited."}
      footer={<p>No account yet? A manager invites you from Settings.</p>}>
      <div className="si-google">
        <button type="button" className="en-provider si-off" aria-disabled="true" aria-describedby="si-google-tip"
                onClick={() => setHint("Google sign-in needs Identity Platform, which the demo runs without. Use a demo account below.")}>
          <GoogleGlyph /> Continue with Google
        </button>
        <span id="si-google-tip" role="tooltip" className="si-tip">Not available in the demo: sign in with email</span>
      </div>
      <p className="en-divider"><span>or with email</span></p>
      <form className="en-stack" onSubmit={submit} aria-busy={busy === "form"}>
        <div className="en-field">
          <label htmlFor="si-email">Email</label>
          <input id="si-email" type="email" autoComplete="email" required placeholder="you@company.com" value={email}
                 onChange={(e) => setEmail(e.target.value)} />
          <span className="en-invalid">Enter a valid email address, like name@company.com.</span>
        </div>
        <div className="en-field">
          <span className="en-label-row">
            <label htmlFor="si-password">Password</label>
            <button type="button" className="en-link quiet"
                    onClick={() => setHint(password0 ? `Every demo account uses the password “${password0}”.` : "Ask whoever runs this demo for its password.")}>
              Forgot password?
            </button>
          </span>
          <PasswordInput id="si-password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <span className="en-invalid">Enter your password.</span>
        </div>
        <button type="submit" className="en-primary" disabled={!!busy}>
          <Busy busy={!!busy} label={signingIn}>Sign in</Busy>
        </button>
      </form>
      {error && <p className="error si-msg" role="alert">{error}</p>}
      {!error && (hint || notice) && <p className="si-note si-msg" role="status">{hint ?? notice}</p>}

      {(!people || sorted.length > 0) && <section className="si-demo" aria-labelledby="si-demo-h">
        <div className="si-demo-head">
          <h2 id="si-demo-h">Demo accounts</h2>
          {password0 && <span>one click · password <code>{password0}</code></span>}
        </div>
        <ul className="si-list" aria-busy={!people}>
          {!people && [0, 1, 2].map((n) => <li key={n} className="si-skel" aria-hidden="true"><span /><span /></li>)}
          {members.map((m) => {
            const role = roleLabel(m.role);
            return (
              <li key={m.id} className={core.includes(m) ? undefined : "si-extra"}>
                <button type="button" className="si-acct" onClick={() => pick(m)} disabled={!!busy && busy !== m.email}
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
      </section>}

      {invited.length > 0 && (
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
                  <button type="button" className="si-acct" onClick={() => accept(i)} disabled={!!busy && busy !== i.invitation_id}
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
