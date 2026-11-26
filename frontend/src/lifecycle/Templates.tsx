/** #/templates (admins write, managers read): one program template per role track and level. Editing makes a new
 *  version; programs keep the version they started with. `?id=` names the template open on the right.
 *  GET/PATCH /api/programs/templates (docs/specs/programs-and-templates.md). */
import { FormEvent, useMemo, useRef, useState } from "react";
import { patch } from "../api";
import { useSession } from "../session";
import { replaceRoute } from "../route";
import { toast } from "../ui";
import { Glyph, kindOf } from "../dataui";
import { LcHead, LcSkeleton, LoadError, NextAction, plural, shortDate, titleCase, useAction, useApi } from "./kit";
import type { Template as TemplateRow, TemplateMilestone } from "./types";
import "./board/board.css";

/** A template with its board milestones (`phases`: GET /api/programs/templates fills in the defaults for older ones). */
type Phase = { key: string; title: string; due_day: number };
type Template = TemplateRow & { phases?: Phase[] };

const LEVEL_ORDER = ["Junior", "Mid", "Senior", "Staff"];
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || "item";

export default function Templates({ params }: { params: URLSearchParams }) {
  const { me } = useSession();
  const admin = me.role === "admin";
  const all = useApi<Template[]>("/programs/templates?include_inactive=true");
  const [editing, setEditing] = useState(false);
  const active = useMemo(() => (all.data ?? []).filter((t) => t.active)
    .sort((a, b) => a.role_track.localeCompare(b.role_track) || LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level) || a.title.localeCompare(b.title)), [all.data]);
  const tracks = [...new Set(active.map((t) => t.role_track))];
  const wanted = params.get("id");
  const sel = (all.data ?? []).find((t) => t.id === wanted) ?? active[0] ?? null;
  const versions = sel ? (all.data ?? []).filter((t) => t.key === sel.key).sort((a, b) => b.version - a.version) : [];
  const pick = (id: string) => { setEditing(false); replaceRoute(`#/templates?id=${encodeURIComponent(id)}`); };

  if (all.error && !all.data) return <section className="lc"><LcHead title="Templates" /><LoadError error={all.error} retry={all.reload} /></section>;
  if (!all.data) return <section className="lc"><LcSkeleton rows={5} /></section>;
  return (
    <section className="lc">
      <LcHead title="Templates" sub={admin ? "One per track and level. Saving a change makes a new version." : "One per track and level. Admins change them."} />
      <div className="lc-split">
        <nav className="lc-tpl-list" aria-label="Templates">
          {tracks.map((track) => (
            <div key={track} className="lc-tpl-group">
              <p className="lc-label">{track}</p>
              <ul>
                {active.filter((t) => t.role_track === track).map((t) => (
                  <li key={t.id}>
                    <button type="button" className={`lc-tpl-item${sel?.key === t.key ? " on" : ""}`} aria-current={sel?.key === t.key ? "true" : undefined} onClick={() => pick(t.id)}>
                      <span>{t.level}</span><span className="lc-dim">{t.duration_days} d{t.version > 1 ? ` · v${t.version}` : ""}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        {sel && (
          <div className="lc-tpl-detail lc-card">
            {editing && admin && sel.active
              ? <TemplateEditor t={sel} onCancel={() => setEditing(false)} onSaved={(t) => { setEditing(false); all.reload(); pick(t.id); }} />
              : <TemplateView t={sel} versions={versions} pick={pick} canEdit={admin && sel.active} edit={() => setEditing(true)} />}
          </div>
        )}
      </div>
      <NextAction>
        <a className="dp-btn primary" href="#/enrol">Enrol a hire with a template<Glyph name="arrow" size={14} /></a>
        {admin && <a className="dp-btn" href="#/setup">Back to Setup</a>}
      </NextAction>
    </section>
  );
}

function TemplateView({ t, versions, pick, canEdit, edit }: { t: Template; versions: Template[]; pick: (id: string) => void; canEdit: boolean; edit: () => void }) {
  const ms = [...t.milestones].sort((a, b) => a.due_day - b.due_day);
  return (
    <div className="lc-stack">
      <div className="lc-tpl-head">
        <div>
          <h2>{t.title}</h2>
          <p className="lc-dim">{t.role_track} · {t.level} · {t.duration_days} days · <code>{t.key}</code> v{t.version}{!t.active ? " · older version" : ""}</p>
        </div>
        {canEdit && <button type="button" className="dp-btn" onClick={edit}><Glyph name="file" size={14} />Edit</button>}
      </div>
      {(t.phases?.length ?? 0) > 0 && (
        <div>
          <p className="lc-label">Board milestones <span className="lc-dim">{t.phases!.length}</span></p>
          <ol className="tpl-phases">
            {[...t.phases!].sort((a, b) => a.due_day - b.due_day).map((p) => (
              <li key={p.key}><span className="tpl-phase-day">Day {p.due_day}</span><span>{p.title}</span></li>
            ))}
          </ol>
        </div>
      )}
      <div className="lc-tpl-cols">
        <div>
          <p className="lc-label">Checkpoints <span className="lc-dim">{ms.length}</span></p>
          <ul className="lc-days">{ms.map((m) => <li key={m.key}><span className="lc-mono">{m.due_day}</span>{m.title}</li>)}</ul>
        </div>
        <div className="lc-stack">
          <div>
            <p className="lc-label">Access</p>
            <ul className="lc-plain">{t.access_items.map((a) => <li key={a.system}><span>{a.system}<span className="lc-dim"> · {a.owner}</span></span></li>)}</ul>
          </div>
          <div>
            <p className="lc-label">Documents</p>
            <ul className="lc-plain">{t.acknowledgements.map((a) => <li key={a.doc_key}>{a.title}</li>)}</ul>
          </div>
          <div>
            <p className="lc-label">Plan without AI <span className="lc-dim">{t.plan_items.length}</span></p>
            <ul className="lc-plain">{t.plan_items.map((p, i) => <li key={i}><Glyph name={kindOf(p.kind).glyph} size={12} /><span>{p.title}<span className="lc-dim"> · day {p.due_day}</span></span></li>)}</ul>
          </div>
        </div>
      </div>
      <div className="lc-versions">
        <p className="lc-label">Versions</p>
        <ul>
          {versions.map((v) => (
            <li key={v.id}>
              <button type="button" className={`lc-version${v.id === t.id ? " on" : ""}`} onClick={() => pick(v.id)}>
                <span>v{v.version}</span><span className="lc-dim">{v.active ? "Active" : "Older"}{v.created_at ? ` · ${shortDate(v.created_at)}` : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

type Row = TemplateMilestone & { _id: number };

function TemplateEditor({ t, onCancel, onSaved }: { t: Template; onCancel: () => void; onSaved: (t: Template) => void }) {
  const act = useAction();
  const [title, setTitle] = useState(t.title);
  const [duration, setDuration] = useState(String(t.duration_days));
  const [rows, setRows] = useState<Row[]>(() => [...t.milestones].sort((a, b) => a.due_day - b.due_day).map((m, i) => ({ ...m, _id: i })));
  const [phases, setPhases] = useState(() => [...(t.phases ?? [])].sort((a, b) => a.due_day - b.due_day).map((p, i) => ({ ...p, _id: 500 + i })));
  const phaseDays = phases.map((p) => Math.round(p.due_day));
  const phasesInvalid = phases.some((p) => !p.title.trim() || !(p.due_day >= 1 && p.due_day <= 730)) || new Set(phaseDays).size !== phaseDays.length;
  const [access, setAccess] = useState(t.access_items.map((a, i) => ({ ...a, _id: i })));
  const [docs, setDocs] = useState(t.acknowledgements.map((a, i) => ({ ...a, _id: i })));
  const dur = Number(duration);
  const invalid = phasesInvalid || !title.trim() || !(dur >= 14 && dur <= 365) || rows.some((r) => !r.title.trim() || r.due_day < 0 || r.due_day > 365)
    || access.some((a) => !a.system.trim()) || docs.some((d) => !d.title.trim());
  const seq = useRef(1000);
  const nextId = () => ++seq.current;
  function save(e: FormEvent) {
    e.preventDefault();
    if (invalid) return;
    const used = new Set<string>();
    const key = (k: string | undefined, title: string) => { let s = k || slug(title); while (used.has(s)) s = `${s}_2`.slice(0, 40); used.add(s); return s; };
    const phaseUsed = new Set<string>();
    const phaseKey = (k: string | undefined, title: string) => { let s = k || slug(title); while (phaseUsed.has(s)) s = `${s}_2`.slice(0, 40); phaseUsed.add(s); return s; };
    const body = {
      title: title.trim(), duration_days: dur,
      milestones: rows.map((r) => ({ key: key(r.key, r.title), title: r.title.trim(), due_day: Math.round(r.due_day) })),
      phases: [...phases].sort((a, b) => a.due_day - b.due_day).map((p) => ({ key: phaseKey(p.key, p.title), title: p.title.trim(), due_day: Math.round(p.due_day) })),
      access_items: access.map((a) => ({ system: a.system.trim(), owner: a.owner })),
      acknowledgements: docs.map((d) => ({ doc_key: d.doc_key || slug(d.title), title: d.title.trim(), summary: d.summary ?? "" })),
    };
    act.run("save", () => patch<Template>(`/programs/templates/${encodeURIComponent(t.id)}`, body)).then((r) => { if (r) { toast(`Saved as version ${r.version}`); onSaved(r); } });
  }
  return (
    <form className="lc-form lc-tpl-edit" onSubmit={save}>
      <div className="lc-tpl-head">
        <div><h2>Edit {t.title}</h2><p className="lc-dim">Saving makes version {t.version + 1}. Running programs keep theirs.</p></div>
      </div>
      <div className="lc-grid2">
        <label className="field"><span>Title</span><input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} /></label>
        <label className="field"><span>Length in days</span><input type="number" min={14} max={365} value={duration} onChange={(e) => setDuration(e.target.value)} /></label>
      </div>
      <fieldset>
        <legend>Board milestones</legend>
        <p className="lc-dim tpl-help">The board's columns of time: each hire's tasks are grouped by the first one due on or after them. Days of this program, in order; usually 15, 30, 45, 60 and 90.</p>
        <ul className="lc-edit-rows">
          {phases.map((p) => (
            <li key={p._id}>
              <label className="sr-only" htmlFor={`p-day-${p._id}`}>Day</label>
              <input id={`p-day-${p._id}`} className="lc-day-in" type="number" min={1} max={730} value={p.due_day}
                     onChange={(e) => setPhases((x) => x.map((y) => y._id === p._id ? { ...y, due_day: Number(e.target.value) } : y))} />
              <label className="sr-only" htmlFor={`p-t-${p._id}`}>Title</label>
              <input id={`p-t-${p._id}`} value={p.title} maxLength={120} placeholder="Settled in"
                     onChange={(e) => setPhases((x) => x.map((y) => y._id === p._id ? { ...y, title: e.target.value } : y))} />
              <button type="button" className="dp-btn ghost small" aria-label={`Remove ${p.title || "milestone"}`} disabled={phases.length <= 1}
                      onClick={() => setPhases((x) => x.filter((y) => y._id !== p._id))}>Remove</button>
            </li>
          ))}
        </ul>
        {new Set(phaseDays).size !== phaseDays.length && <p className="error" role="alert">Two board milestones share a day.</p>}
        <button type="button" className="dp-btn small" disabled={phases.length >= 12}
                onClick={() => setPhases((x) => [...x, { key: "", title: "", due_day: Math.min(730, (x.length ? Math.max(...x.map((y) => y.due_day)) : 0) + 15), _id: nextId() }])}>
          <Glyph name="plus" size={13} />Board milestone</button>
      </fieldset>
      <fieldset>
        <legend>Checkpoints</legend>
        <ul className="lc-edit-rows">
          {rows.map((r) => (
            <li key={r._id}>
              <label className="sr-only" htmlFor={`m-day-${r._id}`}>Day</label>
              <input id={`m-day-${r._id}`} className="lc-day-in" type="number" min={0} max={365} value={r.due_day}
                     onChange={(e) => setRows((x) => x.map((y) => y._id === r._id ? { ...y, due_day: Number(e.target.value) } : y))} />
              <label className="sr-only" htmlFor={`m-t-${r._id}`}>Title</label>
              <input id={`m-t-${r._id}`} value={r.title} maxLength={200} onChange={(e) => setRows((x) => x.map((y) => y._id === r._id ? { ...y, title: e.target.value } : y))} />
              <button type="button" className="dp-btn ghost small" aria-label={`Remove ${r.title || "milestone"}`} onClick={() => setRows((x) => x.filter((y) => y._id !== r._id))}>Remove</button>
            </li>
          ))}
        </ul>
        <button type="button" className="dp-btn small" onClick={() => setRows((x) => [...x, { key: "", title: "", due_day: Math.min(dur || 90, 30), _id: nextId() }])}><Glyph name="plus" size={13} />Checkpoint</button>
      </fieldset>
      <fieldset>
        <legend>Access</legend>
        <ul className="lc-edit-rows">
          {access.map((a) => (
            <li key={a._id}>
              <input value={a.system} aria-label="System" maxLength={120} onChange={(e) => setAccess((x) => x.map((y) => y._id === a._id ? { ...y, system: e.target.value } : y))} />
              <select value={a.owner} aria-label="Owner" onChange={(e) => setAccess((x) => x.map((y) => y._id === a._id ? { ...y, owner: e.target.value } : y))}>
                {["manager", "mentor", "admin"].map((o) => <option key={o} value={o}>{titleCase(o)}</option>)}
              </select>
              <button type="button" className="dp-btn ghost small" onClick={() => setAccess((x) => x.filter((y) => y._id !== a._id))}>Remove</button>
            </li>
          ))}
        </ul>
        <button type="button" className="dp-btn small" onClick={() => setAccess((x) => [...x, { system: "", owner: "manager", _id: nextId() }])}><Glyph name="plus" size={13} />Access item</button>
      </fieldset>
      <fieldset>
        <legend>Documents to acknowledge</legend>
        <ul className="lc-edit-rows">
          {docs.map((d) => (
            <li key={d._id}>
              <input value={d.title} aria-label="Document" maxLength={200} onChange={(e) => setDocs((x) => x.map((y) => y._id === d._id ? { ...y, title: e.target.value } : y))} />
              <button type="button" className="dp-btn ghost small" onClick={() => setDocs((x) => x.filter((y) => y._id !== d._id))}>Remove</button>
            </li>
          ))}
        </ul>
        <button type="button" className="dp-btn small" onClick={() => setDocs((x) => [...x, { doc_key: "", title: "", summary: "", _id: nextId() }])}><Glyph name="plus" size={13} />Document</button>
      </fieldset>
      <div className="lc-form-foot">
        <button type="button" className="dp-btn ghost" onClick={onCancel}>Cancel</button>
        <button type="submit" className="dp-btn primary" disabled={invalid || !!act.busy}>{act.busy ? "Saving…" : `Save as version ${t.version + 1}`}</button>
      </div>
      {act.error && <p className="error" role="alert">{act.error}</p>}
      <p className="lc-dim lc-footnote">{plural(t.plan_items.length, "plan item")} carried over unchanged.</p>
    </form>
  );
}
