import { FormEvent, useState } from "react";
import { errorMessage, isManager, Me, post, Project } from "../api";
import { Logo, Msg, Note } from "../ui";
import { EmptyState } from "../fx";
import { GitHubMark } from "./settings/kit";
import "../styles/entry.css";

export default function NewProject({ onCreated, intro }: { onCreated: (p: Project) => void; intro?: string }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      onCreated(await post<Project>("/projects", { name: name.trim(), description: description.trim() }));
    } catch (err) {
      setMsg({ ok: false, text: errorMessage(err) });
      setBusy(false);
    }
  }

  return (
    <section className="np">
      <header className="page-head np-head">
        <Logo size={40} motion="arrive" className="np-mark" />
        <h1>New project</h1>
        <p>{intro ?? "One codebase and the people who work on it."}</p>
      </header>
      <form className="np-card" onSubmit={submit}>
        <label className="field"><span>Project name</span>
          <input required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder="Payments API" autoFocus /></label>
        <label className="field"><span>Description <em>optional</em></span>
          <textarea maxLength={2000} rows={3} value={description} onChange={(e) => setDescription(e.target.value)}
                    placeholder="What the codebase does, in a sentence." aria-describedby="np-count" />
          <span id="np-count" className="np-count" aria-live="polite">{description.length > 1800 ? `${2000 - description.length} characters left` : ""}</span>
        </label>
        <button type="submit" className="set-primary np-submit" disabled={busy || !name.trim()}>
          {busy ? <><span className="en-spin" aria-hidden="true" />Creating…</> : "Create project"}
        </button>
        <Note msg={msg} />
      </form>
      <ol className="np-steps" aria-label="What happens next">
        <li className="on"><span className="np-step-n">1</span><span><strong>Name it</strong></span></li>
        <li><span className="np-step-n">2</span><span><strong><GitHubMark size={13} />Connect the repository</strong></span></li>
        <li><span className="np-step-n">3</span><span><strong>Invite the team</strong></span></li>
      </ol>
    </section>
  );
}

/** Signed in, but not a member of any project yet. */
export function NoProjects({ me, onCreated }: { me: Me; onCreated: (p: Project) => void }) {
  if (isManager(me.role)) return <NewProject onCreated={onCreated} intro={`Create the first project for ${me.org_name || "your team"}.`} />;
  return (
    <section className="np">
      <h1 className="sr-only">Welcome, {me.name}</h1>
      <EmptyState title={`Welcome, ${me.name.replace(/\s*\(.*?\)/g, "")}`} action={<button className="secondary" onClick={() => location.reload()}>Reload</button>}>
        You are not in a project yet. Ask your manager to add you.
      </EmptyState>
    </section>
  );
}
