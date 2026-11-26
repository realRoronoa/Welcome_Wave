/** #/me (the hire) and #/me/<onboardingId> (their manager): "what your manager sees". Every data point a manager or
 *  mentor can read about the hire, with who sees it, where it comes from, how it is worked out, and the same value they
 *  see. What is kept private is said first. GET /api/onboardings/{id}/visibility (docs/specs/visibility.md). */
import { ReactNode, useState } from "react";
import { useSession } from "../session";
import { EmptyState } from "../fx";
import { Glyph } from "../dataui";
import { How, isStatus, LcHead, LcSkeleton, LoadError, nameOf, NextAction, plural, shortDate, titleCase, useApi, useCrumb } from "./kit";
import type { HireHome, Visibility as VisibilityT, VisibilityField } from "./types";

const AUDIENCE: Record<string, string> = { hire: "You", mentor: "Mentor", manager: "Manager" };

export default function Visibility({ id }: { id: string | null }) {
  const { me } = useSession();
  const home = useApi<HireHome>(id ? null : "/home");
  const own = home.data?.role === "new_hire" ? home.data.sections.program?.profile.onboarding_id ?? null : null;
  const target = id ?? own;
  const v = useApi<VisibilityT>(target ? `/onboardings/${encodeURIComponent(target)}/visibility` : null);
  const self = !!v.data && v.data.hire?.id === me.id;
  useCrumb(v.data && !self ? nameOf(v.data.hire) : null);

  if (!id && home.data && !own) {
    return <section className="lc"><LcHead title="What your manager sees" />
      <EmptyState title="Nothing to show yet" action={<a className="dp-btn" href="#/home">Back to Home</a>}>This page lists what your manager can see once your program starts.</EmptyState></section>;
  }
  const err = v.error ?? home.error;
  if (err && !v.data) {
    return <section className="lc"><LcHead title="What your manager sees" />
      {isStatus(err, 403, 404) ? <EmptyState title="Not available" action={<a className="dp-btn" href="#/home">Back to Home</a>}>Only the hire and their managers can open this page.</EmptyState>
        : <LoadError error={err} retry={() => { home.reload(); v.reload(); }} />}</section>;
  }
  if (!v.data) return <section className="lc"><LcSkeleton rows={6} /></section>;
  const d = v.data;
  const fields = Object.entries(d.data);
  const hireName = nameOf(d.hire);
  return (
    <section className="lc lc-narrow lc-vis-page">
      <LcHead title={self ? "What your manager sees" : `What ${hireName} sees`}
              sub={self ? "Everything others in Welcome Wave can see about you, and who sees it." : `${hireName} sees this page with the same values you do.`}
              back={{ href: `#/program/${encodeURIComponent(d.onboarding_id)}`, label: self ? "My program" : `${hireName}'s program` }} />
      <div className="lc-card lc-private">
        <p className="lc-private-head"><Glyph name="shield" size={15} /><strong>Kept private</strong></p>
        <ul>{d.private.map((p) => <li key={p}>{p}</li>)}</ul>
      </div>
      <p className="lc-label lc-vis-count">{plural(fields.length, "data point")}</p>
      <ul className="lc-vis">
        {fields.map(([key, f]) => <Field key={key} k={key} f={f} self={self} />)}
      </ul>
      <NextAction>
        <a className="dp-btn primary" href={`#/program/${encodeURIComponent(d.onboarding_id)}`}>{self ? "Back to my program" : `Back to ${hireName}'s program`}<Glyph name="arrow" size={14} /></a>
      </NextAction>
    </section>
  );
}

function Field({ k, f, self }: { k: string; f: VisibilityField; self: boolean }) {
  const [open, setOpen] = useState(false);
  // the registry speaks to the hire ("Your plan's approval"); a manager reads it about them
  const label = self ? f.label : f.label.replace(/^Your (\S+)'s /, "$1 ").replace(/^Your /, "").replace(/ you asked/, " asked")
    .replace(/^./, (c) => c.toUpperCase());
  return (
    <li className={`lc-vis-row${open ? " open" : ""}`}>
      <div className="lc-vis-main">
        <p className="lc-vis-label">{label}</p>
        <p className="lc-vis-summary">{summary(k, f.value)}</p>
      </div>
      <div className="lc-vis-who" aria-label={`Visible to ${f.visible_to.map((w) => AUDIENCE[w] ?? w).join(", ")}`}>
        {f.visible_to.map((w) => <span key={w} className="lc-who">{w === "hire" && !self ? "Hire" : AUDIENCE[w] ?? titleCase(w)}</span>)}
      </div>
      <div className="lc-vis-side">
        <How text={<><span className="lc-ev">{f.how}</span><span className="lc-ev-why">Source: <code>{f.source}</code></span></>} align="end" />
        <button type="button" className="lc-src-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{open ? "Hide value" : "Value"}</button>
      </div>
      {open && <div className="lc-vis-value"><Value v={f.value} depth={0} /></div>}
    </li>
  );
}

/* ---------------------------------------------------------------- values */

const isDate = (s: string) => /^\d{4}-\d{2}-\d{2}(T|$)/.test(s);

/** One line that says what the value is, before anyone opens it. */
function summary(k: string, v: unknown): string {
  if (v === null || v === undefined) return k === "first_task" ? "Not selected yet" : k === "dashboard" ? "Not on the dashboard" : "Nothing recorded";
  const a = Array.isArray(v) ? v : null;
  const o = typeof v === "object" && !a ? (v as Record<string, any>) : null;
  switch (k) {
    case "profile": return o ? `${o.day < 0 ? `Starts ${shortDate(o.start_date)}` : `Day ${o.day} of ${o.duration_days}`} · ${[o.role_title, o.team?.name].filter(Boolean).join(" · ")}` : "";
    case "plan": return o ? `${titleCase(o.status ?? "")}${o.proposed_tasks ? ` · ${plural(o.proposed_tasks, "proposal")} waiting (count only)` : ""}` : "";
    case "milestones": return a ? `${a.filter((m: any) => m.status !== "pending").length} of ${a.length} settled${a.some((m: any) => m.state === "overdue") ? " · some overdue" : ""}` : "";
    case "plan_items": return a ? (a.length ? plural(a.length, "task") : "No approved tasks") : "";
    case "access": return a ? `${a.filter((x: any) => x.status !== "requested").length} of ${a.length} settled` : "";
    case "acknowledgements": return a ? `${a.filter((x: any) => x.acknowledged_at).length} of ${a.length} acknowledged` : "";
    case "environment_checks": return a ? (a.length ? `${plural(a.length, "run")} · last ${a[a.length - 1].ok ? "passing" : "failing"}` : "Not run") : "";
    case "preboarding": return o?.expires_at ? `Link live until ${shortDate(o.expires_at)}` : "No live link";
    case "first_task": return o ? String(o.title ?? "Selected") : "Not selected";
    case "first_task_shortlist": return o ? `${plural((o.candidates ?? []).length, "candidate")}` : "";
    case "areas": return a ? a.map((x: any) => `${x.area}: ${titleCase(x.level)}`).join(" · ") : "";
    case "blockers": return a ? `${a.filter((b: any) => !b.resolved_at).length} open · ${a.filter((b: any) => b.resolved_at).length} resolved` : "";
    case "summaries": return a ? (a.length ? plural(a.length, "weekly summary", "weekly summaries") : "None yet") : "";
    case "checkins": return o ? (o.reviews?.length ? `${plural(o.reviews.length, "half", "halves")} written` : "None written yet") : "";
    case "completion": return o ? `${(o.criteria ?? []).filter((c: any) => c.met).length} of ${(o.criteria ?? []).length} criteria met` : "";
    case "status": return o ? (o.on_track ? "On track" : `Needs attention · ${(o.why ?? []).join(" ")}`) : "";
    case "questions": return o ? `${o.asked} asked · ${o.answered} answered from sources${o.answered_by_expert ? ` · ${o.answered_by_expert} by an expert` : ""}` : "";
    case "dashboard": return o ? `${o.tasks_done ?? 0} of ${o.tasks_total ?? 0} tasks done${o.at_risk ? " · at risk" : ""}` : "Not on the dashboard";
  }
  if (a) return plural(a.length, "item");
  return typeof v === "object" ? "Recorded" : String(v);
}

/** The value itself, as a quiet key/value tree (dates read as dates; ids and long lists kept short). */
function Value({ v, depth }: { v: unknown; depth: number }): ReactNode {
  if (v === null || v === undefined || v === "") return <span className="lc-dim">–</span>;
  if (typeof v === "boolean") return <span>{v ? "Yes" : "No"}</span>;
  if (typeof v === "number") return <span className="lc-mono">{v}</span>;
  if (typeof v === "string") return <span>{isDate(v) ? shortDate(v) : v}</span>;
  if (Array.isArray(v)) {
    if (!v.length) return <span className="lc-dim">None</span>;
    if (v.every((x) => typeof x !== "object" || x === null)) return <span>{v.map(String).join(", ")}</span>;
    const shown = v.slice(0, 8);
    return (
      <ol className="lc-val-list">
        {shown.map((x, i) => <li key={i}><Value v={x} depth={depth + 1} /></li>)}
        {v.length > shown.length && <li className="lc-dim">and {v.length - shown.length} more</li>}
      </ol>
    );
  }
  if (depth > 3) return <span className="lc-dim">…</span>;
  const entries = Object.entries(v as Record<string, unknown>).filter(([k]) => !/^(id|org_id|onboarding_id|project_id)$/.test(k));
  if (!entries.length) return <span className="lc-dim">–</span>;
  // a person is shown by name
  if ("name" in (v as object) && entries.length <= 2) return <span>{nameOf((v as { name: string }).name)}</span>;
  return (
    <dl className="lc-val">
      {entries.map(([k, x]) => <div key={k}><dt>{titleCase(k)}</dt><dd><Value v={x} depth={depth + 1} /></dd></div>)}
    </dl>
  );
}
