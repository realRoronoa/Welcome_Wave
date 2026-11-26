import { useEffect, useState } from "react";
import { ApiError, errorMessage, post } from "../api";
import { Loading } from "../ui";
import { GitHubRepo, RepoPicker } from "./settings/Repository";
import { GitHubMark, Glyph } from "./settings/kit";
import "../styles/entry.css";

type Result = { project_id: string; installation_id: string; account: string; repositories: GitHubRepo[] };

// GitHub's code works once. Every mount for the same code shares this one request, so remounting (a nav click,
// a project switch) can never post it again.
const sent = new Map<string, Promise<Result>>();
const WAIT_S = [2, 4, 6, 8]; // GitHub's installation webhook can land a few seconds after the browser does

/** 409 installation_unconfirmed: the server keeps what it needs (the code is not used up), so the same request is asked again. */
async function finish(body: { code: string; installation_id: string; state: string }): Promise<Result> {
  for (let i = 0; ; i++) {
    try {
      return await post<Result>("/github/callback", body);
    } catch (e) {
      if (!(e instanceof ApiError && e.code === "installation_unconfirmed") || i >= WAIT_S.length) throw e;
      await new Promise((r) => setTimeout(r, WAIT_S[i] * 1000));
    }
  }
}

type Failure = { text: string; restart: boolean; retry?: boolean };

function failure(e: unknown): Failure {
  if (e instanceof ApiError && e.code === "installation_unconfirmed") {
    return { restart: false, retry: true, text: "GitHub has not confirmed the installation yet. Try again in a minute." };
  }
  if (e instanceof ApiError && e.code === "not_installer") {
    return { restart: true, text: "Only the GitHub user who installed the app, or an admin of the GitHub account it is on, can connect it. Ask them to finish from Settings in Welcome Wave, or install the app again yourself." };
  }
  return { restart: e instanceof ApiError && e.status === 400, text: errorMessage(e) };
}

/** GitHub sends the browser to /github/callback?installation_id=..&setup_action=..&code=..&state=.. after installing the App. */
export default function GitHubCallback({ search, onDone }: { search: string; onDone: (projectId: string | null) => void }) {
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<Failure | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    const q = new URLSearchParams(search);
    const code = q.get("code"), installation_id = q.get("installation_id"), state = q.get("state");
    // a reload must not replay the one-time code. No fragment: the menu's links must still change the hash to leave this view
    if (location.pathname !== "/app") history.replaceState(null, "", "/app"); // the workspace's own address (not "/", the landing page)
    setError(null);
    if (q.get("setup_action") === "request") {
      setError({ restart: false, text: "Your request to install the GitHub App was sent to an owner of the GitHub organisation. Once they approve it, start again from Settings." });
    } else if (!code || !installation_id || !state) {
      setError({ restart: true, text: "GitHub did not return everything needed to finish (code, installation and state). If this keeps happening, enable “Request user authorization (OAuth) during installation” in the GitHub App settings." });
    } else {
      if (!sent.has(code)) sent.set(code, finish({ code, installation_id, state }));
      sent.get(code)!.then((r) => alive && setResult(r)).catch((e) => alive && setError(failure(e)));
    }
    return () => { alive = false; };
  }, [search, attempt]);

  function again() {
    sent.delete(new URLSearchParams(search).get("code") ?? "");
    setAttempt((n) => n + 1);
  }

  const q = new URLSearchParams(search);
  const requested = q.get("setup_action") === "request";
  const complete = !!(q.get("code") && q.get("installation_id") && q.get("state"));
  const steps: Step[] = [
    { label: "Installed", detail: requested ? "Waiting for an owner to approve" : !complete ? "Incomplete answer from GitHub" : result ? `On ${result.account}` : "GitHub App added",
      state: requested ? "wait" : complete ? "done" : "error" },
    { label: "Confirming", detail: error && complete && !requested ? "Not confirmed" : result ? "Confirmed with GitHub" : complete && !requested ? "Checking with GitHub…" : "Next",
      state: !complete || requested ? "idle" : error ? "error" : result ? "done" : "active" },
    { label: "Choose repository", detail: result ? `${result.repositories.length} available` : "Next", state: result ? "active" : "idle" },
  ];

  return (
    <section className="gh">
      <header className="page-head">
        <h1>Connect a repository</h1>
        <p>{result ? <>Installed on <strong>{result.account}</strong>. Choose a repository.</> : "Finishing the installation."}</p>
      </header>
      <ol className="gh-steps" aria-label="Progress">
        {steps.map((s, i) => (
          <li key={s.label} className={`gh-step st-${s.state}`} aria-current={s.state === "active" ? "step" : undefined}>
            <span className="gh-dot" aria-hidden="true">
              {s.state === "done" ? <Glyph name="check" size={14} /> : s.state === "error" ? <Glyph name="x" size={14} />
                : s.state === "active" ? <span className="gh-spin" /> : s.state === "wait" ? <Glyph name="calendar" size={13} /> : i + 1}
            </span>
            <span className="gh-step-text">
              <strong>{s.label}</strong>
              <span>{s.detail}<span className="sr-only">{` (${STATE_LABEL[s.state]})`}</span></span>
            </span>
          </li>
        ))}
      </ol>
      <div className="gh-body">
        {error ? (
          <div className={`gh-state ${requested ? "info" : "bad"}`}>
            <span className="gh-state-icon"><Glyph name={requested ? "mail" : "alert"} size={18} /></span>
            <div>
              <p className={requested ? "gh-state-text" : "error"} role="alert">{error.text}</p>
              <div className="actions">
                {error.retry && <button className="set-primary" onClick={again}>Try again</button>}
                <button className={error.retry ? "secondary" : "set-primary"} onClick={() => onDone(null)}>{error.restart ? "Start again" : "Back to settings"}</button>
              </div>
            </div>
          </div>
        ) : !result ? (
          <div className="gh-wait">
            <span className="gh-wait-mark"><GitHubMark size={22} /></span>
            <Loading text="Checking the installation with GitHub…" />
            <p className="hint">This can take a few seconds.</p>
          </div>
        ) : (
          <div className="set-card">
            <RepoPicker projectId={result.project_id} installationId={result.installation_id} repos={result.repositories} connected={[]}
                        onConnected={() => onDone(result.project_id)} />
            <p className="gh-skip"><button className="en-link quiet" onClick={() => onDone(result.project_id)}>Skip for now</button></p>
          </div>
        )}
      </div>
    </section>
  );
}

type Step = { label: string; detail: string; state: "idle" | "active" | "done" | "error" | "wait" };
const STATE_LABEL = { idle: "not started", active: "in progress", done: "done", error: "failed", wait: "waiting" };
