import { ReactNode, useEffect, useRef, useState } from "react";
import { api, ApiError, Config, errorMessage, getShowcaseToken, Me, setIdToken, setShowcaseToken, setTokenSource } from "../api";
import { setSignedIn } from "../surface";
import { Booting } from "../ui";
import { EntryLayout } from "./Entry";
import SignIn from "./SignIn";
import { Signup, VerifyEmail } from "./Setup";
import Invited, { PendingInvitation } from "./Invited";

export type Fb = typeof import("./firebase");

type Phase =
  | { kind: "loading" } | { kind: "signed_out" } | { kind: "unverified" } | { kind: "no_account" }
  | { kind: "invited"; invitations: PendingInvitation[]; creating?: boolean }
  | { kind: "ready"; me: Me } | { kind: "error"; message: string };

type Props = {
  config: Config; children: (me: Me, setMe: (me: Me) => void, signOut: () => void, reload: () => void) => ReactNode;
  /** The account is ready: `afterSignIn` when this page showed the sign-in form first (not a session resumed on load). */
  onSignedIn?: (me: Me, afterSignIn: boolean) => void;
  /** "Sign out" was chosen (not a visit while signed out: a deep link keeps its address). */
  onSignedOut?: () => void;
};

/** Production only: signs the person in with Identity Platform, then resolves their Welcome Wave account. */
export default function AuthGate({ config, children, onSignedIn, onSignedOut }: Props) {
  const [fb, setFb] = useState<Fb | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const uid = useRef<string | null>(null);
  const seq = useRef(0); // ignore answers to superseded /me calls
  const sawForm = useRef(false); // the sign-in form was shown on this page, so the next account is a fresh sign-in

  async function loadMe() {
    const n = ++seq.current;
    setPhase({ kind: "loading" });
    try {
      const me = await api<Me>("/me");
      if (n === seq.current) enter(me);
    } catch (e) {
      if (n !== seq.current) return;
      const err = e instanceof ApiError && e.status === 403 ? e : null;
      if (getShowcaseToken() && e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        setShowcaseToken(null);
        sawForm.current = true;
        setSignedIn(false);
        setPhase({ kind: "signed_out" });
        return;
      }
      if (err?.code === "email_not_verified") setPhase({ kind: "unverified" });
      else if (err?.code === "invitation_pending") setPhase({ kind: "invited", invitations: err.detail?.invitations ?? [] });
      else if (err?.code === "no_account") setPhase({ kind: "no_account" });
      else setPhase({ kind: "error", message: errorMessage(e) });
    }
  }

  useEffect(() => {
    const options = config.firebase;
    if (!options?.apiKey) {
      setPhase({ kind: "error", message: "Sign-in is not configured for this deployment (missing Identity Platform API key)." });
      return;
    }
    if (config.showcase?.enabled && getShowcaseToken()) {
      loadMe();
    } else if (!config.showcase?.enabled && getShowcaseToken()) {
      setShowcaseToken(null);
    }
    let alive = true, unsubscribe = () => {};
    import("./firebase").then((m) => {
      if (!alive) return;
      m.init(options);
      setTokenSource(m.token);
      setFb(m);
      unsubscribe = m.onUser(async (user) => {
        if (!user && config.showcase?.enabled && getShowcaseToken()) return;
        setIdToken(user ? await user.getIdToken().catch(() => null) : null);
        if (!user) {
          uid.current = null;
          seq.current++;
          sawForm.current = true;
          setSignedIn(false);
          setPhase({ kind: "signed_out" });
        } else if (user.uid !== uid.current) { // token refreshes fire this too; only a new login reloads the account
          uid.current = user.uid;
          loadMe();
        }
      });
    }).catch(() => alive && setPhase({ kind: "error", message: "Could not load sign-in. Check your connection and reload the page." }));
    return () => { alive = false; unsubscribe(); };
  }, [config]);

  const signOut = () => {
    const hadShowcase = !!getShowcaseToken();
    setShowcaseToken(null);
    uid.current = null;
    seq.current++;
    sawForm.current = true;
    setSignedIn(false);
    onSignedOut?.();
    if (hadShowcase) setPhase({ kind: "signed_out" });
    fb?.logOut().catch(() => {});
  };

  const ready = (me: Me) => setPhase({ kind: "ready", me }); // the account changed (setMe)
  const enter = (me: Me) => { onSignedIn?.(me, sawForm.current); setPhase({ kind: "ready", me }); }; // signed in, joined or created
  if (phase.kind === "ready") return <>{children(phase.me, ready, signOut, loadMe)}</>;
  if (phase.kind === "error") {
    return (
      <EntryLayout title="We could not sign you in">
        <p className="error" role="alert">{phase.message}</p>
        <div className="en-actions">
          <button className="en-primary" onClick={() => (fb && uid.current ? loadMe() : location.reload())}>Try again</button>
          {fb && uid.current && <button className="secondary" onClick={signOut}>Sign out</button>}
        </div>
      </EntryLayout>
    );
  }
  if (phase.kind === "loading" || !fb) return <Booting />;
  // the form's mode follows the address at the moment it shows: /signup opens "Create account"; after a sign-out the address
  // is /login (onSignedOut), so the next person gets the sign-in form even if this page started at /signup
  if (phase.kind === "signed_out") {
    return (
      <SignIn
        fb={fb}
        initialCreating={/^\/signup\/?$/.test(location.pathname)}
        showcase={config.showcase ?? null}
        onShowcaseSignedIn={(me) => { sawForm.current = true; enter(me); }}
      />
    );
  }
  if (phase.kind === "unverified") return <VerifyEmail fb={fb} onVerified={loadMe} signOut={signOut} />;
  if (phase.kind === "invited" && !phase.creating) {
    return <Invited fb={fb} invitations={phase.invitations} signupEnabled={config.signup_enabled} onJoined={enter}
                    onExpired={(id) => { // the last one gone: let the server say what this login can do now
                      const invitations = phase.invitations.filter((i) => i.org_id !== id);
                      if (invitations.length) setPhase({ ...phase, invitations }); else loadMe();
                    }}
                    onCreateOwn={() => setPhase({ ...phase, creating: true })} retry={loadMe} signOut={signOut} />;
  }
  return <Signup fb={fb} enabled={config.signup_enabled} onDone={enter} retry={loadMe} signOut={signOut}
                 onBack={phase.kind === "invited" ? () => setPhase({ ...phase, creating: false }) : undefined} />;
}
