/** The gate between sign-in and the workspace: a signed-in person who has not accepted the current Terms of Service and
 *  Privacy Policy sees the consent screen first (ConsentScreen.tsx), once per version. People who joined before the
 *  documents existed meet it at their next visit; so does everyone when a document's version changes, and a demo invitee
 *  (whose one-click "Accept" carries no agreement). There is no way past it except signing out; leaving the organisation,
 *  and an admin's export and deletion, stay reachable from the screen itself.
 *
 *  The recording notice is not asked for here: it is acknowledged where recordings are (RecordingNotice.tsx).
 *
 *  If the status cannot be read, the workspace opens: the API refuses nothing unless LEGAL_ENFORCE is on (then this says so
 *  and offers to try again), and the question is asked at the next visit. A 403 `legal_required` from any later call
 *  (api.ts fires LEGAL_REQUIRED_EVENT) brings the screen back. */
import { lazy, ReactNode, Suspense, useCallback, useEffect, useState } from "react";
import { ApiError, Config, errorMessage, LEGAL_REQUIRED_EVENT, Me } from "../api";
import { Booting, Splash } from "../ui";
import { LegalStatus, loadLegalStatus } from "./legalStatus";

/* its own chunk, fetched only when someone has something to accept: it carries the documents' own words */
const ConsentScreen = lazy(() => import("./ConsentScreen"));

type Props = {
  config: Config; me: Me; signOut?: () => void;
  /** Production: resolve the login again after it left the organisation. */
  reloadAccount?: () => void;
  children: ReactNode;
};
type State = { kind: "loading" } | { kind: "open" } | { kind: "ask"; status: LegalStatus } | { kind: "error"; message: string };

const asks = (s: LegalStatus) => s.needs.some((d) => s.required.includes(d));

export default function LegalGate({ config, me, signOut, reloadAccount, children }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const enforced = !!config.legal?.enforced;

  const check = useCallback((force: boolean) => {
    let alive = true;
    loadLegalStatus(me.id, force)
      .then((s) => { if (alive) setState(asks(s) ? { kind: "ask", status: s } : { kind: "open" }); })
      .catch((e) => {
        if (!alive) return;
        // an API older than this page has no such route; a signed-out or deactivated session is the workspace's to report
        const pass = !enforced || (e instanceof ApiError && [401, 404].includes(e.status));
        setState(pass ? { kind: "open" } : { kind: "error", message: errorMessage(e) });
      });
    return () => { alive = false; };
  }, [me.id, enforced]);

  useEffect(() => check(false), [check]);
  useEffect(() => {
    const on = () => { check(true); };
    addEventListener(LEGAL_REQUIRED_EVENT, on);
    return () => removeEventListener(LEGAL_REQUIRED_EVENT, on);
  }, [check]);

  if (state.kind === "open") return <>{children}</>;
  if (state.kind === "loading") return <Booting />;
  if (state.kind === "error") {
    return (
      <Splash>
        <h1>We could not check the terms</h1>
        <p className="error" role="alert">{state.message}</p>
        <button onClick={() => { setState({ kind: "loading" }); check(true); }}>Try again</button>{" "}
        {signOut && <button className="secondary" onClick={signOut}>Sign out</button>}
      </Splash>
    );
  }
  return (
    <Suspense fallback={<Booting />}>
      <ConsentScreen config={config} me={me} status={state.status} signOut={signOut} reloadAccount={reloadAccount}
                     onAccepted={(s) => setState(asks(s) ? { kind: "ask", status: s } : { kind: "open" })} />
    </Suspense>
  );
}
