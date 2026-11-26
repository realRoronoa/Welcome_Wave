import { FormEvent, useEffect, useState } from "react";
import { api, errorMessage, post, when } from "../../api";
import { useSession } from "../../session";
import { Msg, Note } from "../../ui";
import type { SectionProps } from "../Settings";
import { GitHubMark, Glyph, SectionCard } from "./kit";

export type GitHubRepo = { full_name: string; default_branch: string; private: boolean };
const STATUS: Record<string, string> = { pending: "Waiting", indexing: "Reading", ready: "Ready", failed: "Could not read it" };

export default function Repository({ d, reload }: SectionProps) {
  const { project, config, demo } = useSession();
  const [picker, setPicker] = useState<{ installationId: string | null; repos: GitHubRepo[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  // installations already bound to this organisation (from any project), so a new project needs no reinstall
  const [bound, setBound] = useState<{ installation_id: string; account_login: string }[]>([]);
  useEffect(() => {
    if (demo || !config.github_app) return;
    api<{ installation_id: string; account_login: string }[]>("/github/installations").then(setBound).catch(() => setBound([]));
  }, [demo, config.github_app]);
  const installations = bound.map((b) => b.installation_id);
  const account = (id: string | null) => bound.find((b) => b.installation_id === id)?.account_login || id;

  async function run(action: () => Promise<void>) {
    setBusy(true); setMsg(null);
    try { await action(); } catch (e) { setMsg({ ok: false, text: errorMessage(e) }); } finally { setBusy(false); }
  }
  const install = () => run(async () => {
    const { url } = await api<{ url: string | null }>(`/github/install-url?project_id=${encodeURIComponent(project.id)}`);
    if (url) location.assign(url);
    else setMsg({ ok: false, text: "The GitHub App is not configured on the server." });
  });
  const browse = (installationId: string | null) => run(async () => {
    const repos = await api<GitHubRepo[]>(`/github/repositories?installation_id=${encodeURIComponent(installationId ?? "0")}`);
    setPicker({ installationId, repos });
  });
  // a project has exactly one repository; posting the same one again re-indexes it
  const repo = d.repositories[0] ?? null;
  const reindex = () => run(async () => {
    if (!repo) return;
    await post(`/projects/${project.id}/repositories`, { full_name: repo.full_name, installation_id: repo.installation_id, default_branch: repo.default_branch });
    setMsg({ ok: true, text: `Reading ${repo.full_name} again. It takes a few minutes.` });
    reload();
  });

  const working = !!repo && (repo.status === "pending" || repo.status === "indexing");
  const via = !repo ? "" : repo.installation_id ? `Connected through the GitHub App${bound.length ? ` on ${account(repo.installation_id)}` : ""}`
    : demo ? "Demo repository" : config.repo_by_url ? "Connected by URL · new commits and pull requests are picked up every few minutes" : "Connected";
  // self-hosted: a repository is connected by its GitHub URL (public, or private with the server's read-only token)
  const [url, setUrl] = useState("");
  const connectUrl = (e: FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    run(async () => {
      const r = await post<GitHubRepo>(`/projects/${project.id}/repositories`, { full_name: url.trim() });
      setUrl("");
      setMsg({ ok: true, text: `${r.full_name} connected. Reading it takes a few minutes.` });
      reload();
    });
  };
  return (
    <SectionCard id="repo-h" title="Repository" description="One repository per project.">
      {repo ? (
        <div className={`set-repo s-${repo.status}`}>
          <div className="set-repo-main">
            <span className="set-repo-mark"><GitHubMark size={22} /></span>
            <div className="set-repo-text">
              <p className="set-repo-name"><code>{repo.full_name}</code></p>
              <p className="set-repo-via"><span className={`set-live ${repo.status === "ready" ? "on" : ""}`} aria-hidden="true" />{via}</p>
            </div>
            <span className={`status s-${repo.status}`}>{STATUS[repo.status] ?? repo.status}</span>
          </div>
          <dl className="set-repo-facts">
            <div><dt>Default branch</dt><dd><Glyph name="branch" size={14} /><code>{repo.default_branch}</code></dd></div>
            <div><dt>Last read</dt><dd>
              {repo.last_indexed_at ? <><Glyph name="calendar" size={14} />{when(repo.last_indexed_at)}</>
                : repo.last_indexed_sha && /^[0-9a-f]{7,40}$/i.test(repo.last_indexed_sha) ? <><Glyph name="commit" size={14} /><code>{repo.last_indexed_sha.slice(0, 10)}</code></>
                : <span className="set-none">{working ? "In progress" : repo.status === "ready" ? "Up to date" : "Not yet"}</span>}
            </dd></div>
          </dl>
          {working && (
            <div className="set-progress" role="progressbar" aria-label={`Reading ${repo.full_name}`} aria-valuetext={STATUS[repo.status]}>
              <span />
            </div>
          )}
          <div className="set-repo-foot">
            <p className="hint">{working ? "This page updates on its own." : `Reads ${repo.default_branch} again. Takes a few minutes.`}</p>
            <button className="secondary set-btn-icon" onClick={reindex} disabled={busy || working}>
              <Glyph name="refresh" size={15} className={busy || working ? "set-turning" : ""} />{working ? "Reading…" : busy ? "Starting…" : "Refresh"}
            </button>
          </div>
        </div>
      ) : (
        <div className="set-connect">
          <span className="set-connect-mark"><GitHubMark size={26} /></span>
          <p className="set-connect-title">No repository connected yet</p>
          {config.repo_by_url ? (
            <>
              <p className="set-connect-text">Paste its GitHub address. A private one needs a read-only <code>GITHUB_TOKEN</code> on the server.</p>
              <form className="set-url" onSubmit={connectUrl}>
                <label className="set-filter">
                  <GitHubMark size={15} />
                  <span className="sr-only">GitHub repository URL</span>
                  <input type="text" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://github.com/owner/repository"
                         value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy} required />
                </label>
                <button className="set-primary" disabled={busy || !url.trim()}>{busy ? <><span className="en-spin" aria-hidden="true" />Connecting…</> : "Connect"}</button>
              </form>
            </>
          ) : !demo && !config.github_app ? (
            <p className="hint">The GitHub App is not set up on this server yet. An administrator needs to add it.</p>
          ) : (
            <>
              <p className="set-connect-text">{demo ? "The demo connects its sample repository." : "Install the GitHub App, then choose a repository."}</p>
              <div className="actions">
                {!demo && <button className="set-primary" onClick={install} disabled={busy}><GitHubMark size={15} /> Install the GitHub App</button>}
                {(demo ? [null] : installations).map((inst) => (
                  <button key={inst ?? "demo"} className="secondary" onClick={() => browse(inst)} disabled={busy}>
                    Choose a repository{installations.length > 1 ? ` from ${account(inst)}` : ""}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}
      {picker && !repo && (
        <RepoPicker projectId={project.id} installationId={picker.installationId} repos={picker.repos} connected={d.repositories.map((r) => r.full_name)}
                    onConnected={(r) => { setPicker(null); setMsg({ ok: true, text: `${r.full_name} connected. Reading it takes a few minutes.` }); reload(); }} />
      )}
      <Note msg={msg} />
    </SectionCard>
  );
}

type PickerProps = { projectId: string; installationId: string | null; repos: GitHubRepo[]; connected: string[]; onConnected: (r: GitHubRepo) => void };

/** Repositories the GitHub App installation can read; connecting one starts indexing (202). */
export function RepoPicker({ projectId, installationId, repos, connected, onConnected }: PickerProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function connect(r: GitHubRepo) {
    setBusy(r.full_name); setError(null);
    try {
      await post(`/projects/${projectId}/repositories`, { full_name: r.full_name, installation_id: installationId, default_branch: r.default_branch });
      onConnected(r);
    } catch (e) { setError(errorMessage(e)); setBusy(null); }
  }

  const [filter, setFilter] = useState("");
  if (repos.length === 0) return <p className="set-callout">The app cannot see any repositories. On GitHub, give it access to the repository you want, then try again.</p>;
  const shown = filter.trim() ? repos.filter((r) => r.full_name.toLowerCase().includes(filter.trim().toLowerCase())) : repos;
  return (
    <div className="set-picker">
      {repos.length > 5 && (
        <label className="set-filter">
          <Glyph name="search" size={15} />
          <span className="sr-only">Filter repositories</span>
          <input type="search" placeholder={`Filter ${repos.length} repositories`} value={filter} onChange={(e) => setFilter(e.target.value)} />
        </label>
      )}
      <ul className="set-picker-list" aria-label="Repositories">
        {shown.map((r) => {
          const [owner, name] = r.full_name.includes("/") ? r.full_name.split("/", 2) : ["", r.full_name];
          return (
            <li key={r.full_name}>
              <span className="set-picker-icon"><Glyph name="folder" size={16} /></span>
              <span className="set-picker-text">
                <code><span className="set-owner">{owner}{owner && "/"}</span>{name}</code>
                <span className="set-picker-meta"><Glyph name="branch" size={12} />{r.default_branch}{r.private && <><Glyph name="lock" size={12} />private</>}</span>
              </span>
              {connected.includes(r.full_name) ? <span className="status s-ready">Connected</span> : (
                <button className="set-primary" onClick={() => connect(r)} disabled={busy !== null} aria-label={`Connect ${r.full_name}`}>
                  {busy === r.full_name ? <><span className="en-spin" aria-hidden="true" />Connecting…</> : "Connect"}
                </button>
              )}
            </li>
          );
        })}
        {shown.length === 0 && <li className="set-picker-empty">No repository matches “{filter.trim()}”.</li>}
      </ul>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
