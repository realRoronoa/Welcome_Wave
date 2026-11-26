import { useEffect, useState } from "react";
import { api, ApiError, Config, demoSignOut, errorMessage, getDemoUser, Me, Role, setMode } from "./api";
import AuthGate from "./auth";
import DemoSignIn from "./auth/DemoSignIn";
import Workspace from "./Workspace";
import { setSignedIn } from "./surface";
import { Booting, Splash } from "./ui";

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    api<Config>("/config")
      .then((c) => { setMode(c.mode === "demo"); setConfig(c); })
      .catch((e) => setError(errorMessage(e)));
  };
  useEffect(load, []);
  useEffect(() => { document.title = "Welcome Wave"; }, []); // the site's title until the workspace names the project

  if (!config) {
    if (!error) return <Booting />;
    return <Splash><h1>Welcome Wave is unreachable</h1><p className="error" role="alert">{error}</p><button onClick={load}>Try again</button></Splash>;
  }
  if (config.mode === "demo") return <DemoSession config={config} />;
  return (
    <AuthGate config={config} onSignedIn={enterApp} onSignedOut={leaveApp}>
      {(me, setMe, signOut, reload) => <Workspace config={config} me={me} setMe={setMe} signOut={signOut} reloadAccount={reload} />}
    </AuthGate>
  );
}

/** Where each role starts: Home, which adapts to the role (lifecycle/Home.tsx). */
const LANDING: Record<Role, string> = { new_hire: "#/home", expert: "#/home", manager: "#/home", admin: "#/home", executive: "#/home" };

/** Open the role's own page: on load when the address names no page, and after signing in unless it links to one item
 *  (#/meetings/mtg-1?t=55, a review, a file: a link someone followed is kept). No history entry, so Back skips it. */
function landOnRolePage(me: Me, afterSignIn: boolean) {
  if (location.pathname === "/github/callback") return;
  const route = location.hash.replace(/^#/, "");
  const empty = /^\/?$/.test(route), item = /^\/[^/?]+\/[^/?]+|\?/.test(route);
  if (empty || (afterSignIn && !item)) history.replaceState(history.state, "", `${location.pathname}${location.search}${LANDING[me.role] ?? "#/home"}`);
}

/** Signed in (just now, or a session resumed): the workspace lives at /app, so /login and /signup become /app#/<role page>, a deep
 *  link (/app#/meetings/x, opened while signed out) is kept, and the landing page learns to offer "Open Welcome Wave". */
export function enterApp(me: Me, afterSignIn: boolean) {
  if (/^\/(login|signup)?\/?$/.test(location.pathname)) history.replaceState(history.state, "", `/app${location.search}${location.hash}`);
  landOnRolePage(me, afterSignIn);
  setSignedIn(true);
}

/** Signed out: the sign-in page, at its own address (no page in the hash, so the next person starts on their own page). */
export function leaveApp() {
  setSignedIn(false);
  history.replaceState(null, "", "/login");
}

/** Demo mode: a real sign-in (email and the demo password against POST /api/demo/login, or one click on a demo account);
 *  the member's id, sent as X-Demo-User, is the session. A stored id is a session already signed in. Signing out (or
 *  "Switch account") forgets it and clears the page from the address, so the next person starts on their own page. */
function DemoSession({ config }: { config: Config }) {
  const [signedIn, setSignedIn] = useState(() => !!getDemoUser());
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => { // resume a stored session
    if (!signedIn || me) return;
    let current = true;
    setError(null);
    api<Me>("/me")
      .then((m) => { if (!current) return; enterApp(m, false); setMe(m); })
      .catch((e) => {
        if (!current) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) { // that person is gone: the demo data was reset
          signOut();
          setNotice("Your demo session ended because the demo data was reset. Sign in again.");
        } else setError(errorMessage(e));
      });
    return () => { current = false; };
  }, [signedIn, me, attempt]);

  function signedInAs(m: Me) {
    enterApp(m, true);
    setNotice(null); setError(null); setMe(m); setSignedIn(true);
  }
  function signOut() {
    demoSignOut();
    leaveApp();
    setMe(null); setSignedIn(false); setNotice(null); setError(null);
  }

  if (!signedIn) return <DemoSignIn config={config} onSignedIn={signedInAs} notice={notice} creating={/^\/signup\/?$/.test(location.pathname)} />;
  if (!me) {
    if (!error) return <Booting />;
    return (
      <Splash>
        <p className="error" role="alert">{error}</p>
        <button onClick={() => setAttempt((n) => n + 1)}>Try again</button>{" "}
        <button className="secondary" onClick={signOut}>Sign out</button>
      </Splash>
    );
  }
  return <Workspace key={me.id} config={config} me={me} setMe={setMe} signOut={signOut} />;
}
