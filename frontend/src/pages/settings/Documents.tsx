import { FormEvent, useRef, useState } from "react";
import { api, day, del, download, errorMessage } from "../../api";
import { useSession } from "../../session";
import { Msg, Note } from "../../ui";
import type { SectionProps } from "../Settings";
import { EmptyState } from "../../fx";
import { extOf, FileTile, Glyph, SectionCard } from "./kit";

const ACCEPT = [".pdf", ".docx", ".md", ".markdown", ".txt"];
const MAX_MB = 25;
const STATUS: Record<string, string> = { pending: "Processing", ready: "Ready", failed: "Failed" };

export default function Documents({ d, reload }: SectionProps) {
  const { project } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null); // the document whose Delete asks once more
  const [working, setWorking] = useState<string | null>(null);

  async function save(id: string, title: string) {
    setWorking(id); setMsg(null);
    try { await download(`/documents/${encodeURIComponent(id)}/file`, title); }
    catch (err) { setMsg({ ok: false, text: errorMessage(err) }); } finally { setWorking(null); }
  }
  async function remove(id: string, title: string) {
    if (confirming !== id) return setConfirming(id);
    setConfirming(null); setWorking(id); setMsg(null);
    try {
      await del(`/projects/${project.id}/documents/${encodeURIComponent(id)}`);
      setMsg({ ok: true, text: `“${title}” deleted. Answers that cited it are marked out of date.` });
      reload();
    } catch (err) { setMsg({ ok: false, text: errorMessage(err) }); } finally { setWorking(null); }
  }

  async function upload(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    const ext = file.name.includes(".") ? "." + file.name.split(".").pop()!.toLowerCase() : "";
    if (!ACCEPT.includes(ext)) return setMsg({ ok: false, text: "Upload a PDF, Word (.docx), Markdown or text file." });
    if (file.size > MAX_MB * 1024 * 1024) return setMsg({ ok: false, text: `That file is too large. The limit is ${MAX_MB} MB.` });
    const form = new FormData();
    form.append("file", file);
    form.append("title", title.trim());
    setBusy(true); setMsg(null);
    try {
      await api(`/projects/${project.id}/documents`, { method: "POST", body: form });
      setMsg({ ok: true, text: `“${title.trim() || file.name}” uploaded. Answers can cite it once it is ready.` });
      setFile(null); setTitle("");
      if (input.current) input.current.value = "";
      reload();
    } catch (err) { setMsg({ ok: false, text: errorMessage(err) }); } finally { setBusy(false); }
  }

  const fileExt = file?.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  return (
    <SectionCard id="docs-h" title="Documents"
                 description="Answers cite them by page."
                 actions={d.documents.length > 0 ? <span className="set-count">{d.documents.length} {d.documents.length === 1 ? "document" : "documents"}</span> : undefined}>
      <form className="set-upload" onSubmit={upload}>
        <label className={`set-drop ${dragging ? "over" : ""} ${file ? "has-file" : ""}`}
               onDragEnter={() => setDragging(true)} onDragLeave={() => setDragging(false)} onDrop={() => setDragging(false)}>
          {file ? (
            <span className="set-drop-file"><FileTile ext={fileExt} />
              <span className="set-drop-text"><strong>{file.name}</strong><span>{size(file.size)} · choose another file to replace it</span></span>
            </span>
          ) : (
            <span className="set-drop-empty">
              <span className="set-drop-icon"><Glyph name="upload" size={18} /></span>
              <span className="set-drop-text"><strong>Drop a file here, or choose one</strong><span>PDF, Word, Markdown or text, up to {MAX_MB} MB</span></span>
            </span>
          )}
          <span className="sr-only">File (PDF, Word, Markdown or text, up to {MAX_MB} MB)</span>
          <input ref={input} type="file" required accept={ACCEPT.join(",")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <div className="set-upload-row">
          <label className="field"><span>Title <em>optional</em></span>
            <input maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={file?.name ?? "Architecture overview"} /></label>
          <button type="submit" className="set-primary" disabled={busy || !file}>{busy ? <><span className="en-spin" aria-hidden="true" />Uploading…</> : "Upload"}</button>
        </div>
        <Note msg={msg} />
      </form>
      {d.documents.length === 0 ? (
        <EmptyState title="No documents yet">Architecture notes, runbooks, the release checklist.</EmptyState>
      ) : (
        <ul className="set-docs">
          {d.documents.map((x) => (
            <li key={x.id}>
              <FileTile ext={extOf(x)} />
              <span className="set-doc-text">
                <strong>{x.title}</strong>
                <span>Added {day(x.created_at)}{x.page_count ? ` · ${x.page_count} ${x.page_count === 1 ? "page" : "pages"}` : ""}</span>
              </span>
              <span className={`status s-${x.status}`}>{STATUS[x.status] ?? x.status}</span>
              <span className="set-doc-actions">
                {x.status === "ready" && <a className="set-ghost" href={`#/documents/${encodeURIComponent(x.id)}`} aria-label={`View ${x.title}`}>View</a>}
                <button type="button" className="set-ghost" onClick={() => save(x.id, x.title)} disabled={working === x.id} aria-label={`Download ${x.title}`}>Download</button>
                <button type="button" className="set-ghost-danger" onClick={() => remove(x.id, x.title)} disabled={working === x.id}
                        onBlur={() => setConfirming((c) => (c === x.id ? null : c))} aria-label={`${confirming === x.id ? "Confirm deleting" : "Delete"} ${x.title}`}>
                  {confirming === x.id ? "Confirm delete" : "Delete"}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

const size = (b: number) => (b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`);
