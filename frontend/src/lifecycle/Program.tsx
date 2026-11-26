/** #/program/<onboardingId>?tab=<section>: one hire's program. A header (who, day, state), the timeline, then one
 *  accordion section per part of the program. `?tab=` opens a section (the API's links use it) and follows the open one,
 *  so a reload or a shared link lands on the same place. Sections load their own data when first opened.
 *  The hire, their mentor and the project's managers can open it; each sees the sections their relation allows
 *  (docs/PERMISSIONS.md). */
import { ReactNode, useEffect, useState } from "react";
import { isManager } from "../api";
import { useSession } from "../session";
import { replaceRoute } from "../route";
import { EmptyState } from "../fx";
import { Accordion, AccordionItem } from "../accordion";
import { Avatar, Glyph, GlyphName } from "../dataui";
import Timeline from "./Timeline";
import MilestoneStrip from "./board/MilestoneStrip";
import { dayFigure, HireState, isStatus, LcSkeleton, LoadError, nameOf, NextAction, plural, useApi, useCrumb } from "./kit";
import { AccessSection, MilestonesSection, PlanSection, PreboardingSection } from "./program/Basics";
import { AreasSection, BlockersSection, FirstTaskSection, SummariesSection } from "./program/Work";
import { CheckinsSection, CompletionSection, SurveySection, useSurveyDue } from "./program/Reviews";
import { Meta, Relation, SectionProps } from "./program/shared";
import type { Blocker, Program as ProgramData } from "./types";

type Part = { id: string; title: string; icon: GlyphName; who: Relation[]; meta?: ReactNode; body: ReactNode };

export default function Program({ id, params }: { id: string; params: URLSearchParams }) {
  const { me } = useSession();
  const path = `/onboardings/${encodeURIComponent(id)}`;
  const prog = useApi<ProgramData>(`${path}/program`, ["lifecycle", "tasks", "approvals"]);
  const p = prog.data;
  const rel: Relation = p && p.profile.hire?.id === me.id ? "hire" : isManager(me.role) ? "manager" : "mentor";
  const blockers = useApi<{ blockers: Blocker[] }>(p ? `${path}/blockers` : null);
  const surveyDue = useSurveyDue(rel === "hire" && p ? id : null);
  // once shown, the survey section stays until the page is left, so its "thank you" is not whisked away by the reload
  const [surveySeen, setSurveySeen] = useState<number | null>(null);
  useEffect(() => { if (surveyDue) setSurveySeen(surveyDue); }, [surveyDue]);
  const surveyDay = surveyDue ?? surveySeen;
  const people = useApi<{ members: { id: string; name: string }[] }>(p ? `/projects/${encodeURIComponent(p.profile.project_id)}` : null, ["people"]);
  const who = (uid: string | null | undefined) => uid === me.id ? "you" : nameOf(people.data?.members.find((m) => m.id === uid)?.name ?? null, "someone");
  useCrumb(p ? (rel === "hire" ? "My program" : nameOf(p.profile.hire)) : null);

  const tab = params.get("tab");
  const setTab = (t: string | null) => {
    const q = new URLSearchParams(params);
    if (t) q.set("tab", t); else q.delete("tab");
    const qs = q.toString();
    replaceRoute(`#/program/${encodeURIComponent(id)}${qs ? `?${qs}` : ""}`);
  };
  useEffect(() => { // a linked section scrolls into view once the page has drawn it
    if (!p || !tab) return;
    const t = window.setTimeout(() => document.getElementById(`sec-${tab}`)?.scrollIntoView({ block: "start", behavior: "smooth" }), 250);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [!!p]);

  if (prog.error && !p) {
    const gone = isStatus(prog.error, 404, 403);
    return (
      <section className="lc">
        {gone ? <EmptyState title="This program is not available" action={<a className="dp-btn" href="#/home">Back to Home</a>}>
          It may belong to another project, or you are not related to this hire.
        </EmptyState> : <LoadError error={prog.error} retry={prog.reload} />}
      </section>
    );
  }
  if (!p) return <section className="lc"><LcSkeleton rows={6} /></section>;

  const pr = p.profile;
  const name = nameOf(pr.hire);
  const open = (blockers.data?.blockers ?? []).filter((b) => !b.resolved_at);
  const overdue = p.milestones.filter((m) => m.state === "overdue").length;
  const onTrack = !overdue && !open.some((b) => b.stage === "manager");
  const settled = p.milestones.filter((m) => m.status !== "pending").length;
  const granted = p.access.filter((a) => a.status !== "requested").length;
  const acked = p.acknowledgements.filter((a) => a.acknowledged_at).length;
  const lastEnv = p.environment_checks[p.environment_checks.length - 1];
  const props: SectionProps = { id, p, rel, reload: prog.reload, who, meId: me.id };

  const parts: Part[] = [
    { id: "plan", title: "Plan", icon: "layers", who: ["hire", "mentor", "manager"],
      meta: <Meta tone={p.plan.status === "approved" ? undefined : "attn"}>{PLAN_LABEL[p.plan.status]}</Meta>, body: <PlanSection {...props} /> },
    // the checkpoints the product checks or a manager marks (onboarding_milestones); the board's milestones are the strip above
    { id: "milestones", title: "Checkpoints", icon: "check", who: ["hire", "mentor", "manager"],
      meta: <><Meta>{settled} of {p.milestones.length} done</Meta>{overdue > 0 && <Meta tone="late">{overdue} overdue</Meta>}</>, body: <MilestonesSection {...props} /> },
    { id: "access", title: "Access", icon: "shield", who: ["hire", "mentor", "manager"],
      meta: <Meta tone={granted < p.access.length ? "attn" : undefined}>{granted} of {p.access.length} settled</Meta>, body: <AccessSection {...props} /> },
    { id: "preboarding", title: "Pre-boarding", icon: "file", who: ["hire", "mentor", "manager"],
      meta: <><Meta>{acked} of {p.acknowledgements.length} acknowledged</Meta>{lastEnv && !lastEnv.ok && <Meta tone="late">Check failing</Meta>}</>,
      body: <PreboardingSection {...props} /> },
    { id: "first-task", title: "First task", icon: "pr", who: ["hire", "mentor", "manager"],
      meta: <Meta tone={p.first_task ? undefined : "attn"}>{p.first_task ? "Selected" : "Not selected"}</Meta>, body: <FirstTaskSection {...props} /> },
    { id: "areas", title: "Progress by area", icon: "learn", who: ["hire", "mentor", "manager"], body: <AreasSection {...props} /> },
    { id: "blockers", title: "Blockers", icon: "alert", who: ["hire", "mentor", "manager"],
      meta: open.length ? <Meta tone="late">{plural(open.length, "open blocker")}</Meta> : <Meta>None open</Meta>,
      body: <BlockersSection {...props} onChange={blockers.reload} /> },
    { id: "summaries", title: "Weekly summaries", icon: "calendar", who: ["hire", "manager"], body: <SummariesSection {...props} /> },
    { id: "checkins", title: "Check-ins", icon: "message", who: ["hire", "manager"], body: <CheckinsSection {...props} /> },
    ...(surveyDay ? [{ id: "survey", title: "Onboarding survey", icon: "sparkle" as GlyphName, who: ["hire"] as Relation[],
      meta: <Meta tone="attn">Day {surveyDay}</Meta>, body: <SurveySection {...props} day={surveyDay} /> }] : []),
    { id: "completion", title: "Completion", icon: "task" as GlyphName, who: ["hire", "manager"], body: <CompletionSection {...props} /> },
  ].filter((x) => x.who.includes(rel)) as Part[];
  const openId = tab && parts.some((x) => x.id === tab) ? tab : null;

  return (
    <section className="lc lc-program">
      <header className="lc-head lc-prog-head">
        <div className="lc-head-text">
          <a className="lc-back" href={rel === "manager" ? "#/team" : "#/home"}><Glyph name="arrow" size={13} />{rel === "manager" ? "Team" : "Home"}</a>
          <div className="lc-prog-who">
            <Avatar name={name} size={44} />
            <div>
              <h1>{rel === "hire" ? "My program" : name}</h1>
              <p className="lc-sub">{[pr.role_title, pr.team?.name, pr.mentor ? `Mentor ${nameOf(pr.mentor)}` : null].filter(Boolean).join(" · ")}</p>
            </div>
          </div>
        </div>
        <div className="lc-head-actions">
          <HireState onTrack={onTrack} status={pr.status} />
          {rel !== "mentor" && (
            <a className="dp-btn small" href={rel === "hire" ? "#/me" : `#/me/${encodeURIComponent(id)}`}>
              <Glyph name="shield" size={14} />{rel === "hire" ? "What your manager sees" : `What ${nameOf(pr.hire).split(" ")[0]} sees`}
            </a>
          )}
        </div>
      </header>

      <div className="lc-card lc-tl-card">
        <div className="lc-tl-head">
          <p><strong>{dayFigure(pr).main}</strong> <span className="lc-dim">{dayFigure(pr).of}</span></p>
          <p className="lc-dim">{pr.template ? `${pr.template.title} · v${pr.template.version}` : null}</p>
        </div>
        <Timeline milestones={p.milestones} day={pr.day} duration={pr.duration_days} label={`${name}'s program`} />
      </div>
      {rel !== "mentor" && <MilestoneStrip onboardingId={id} />}

      <Accordion open={openId} onChange={setTab} className="lc-acc">
        {parts.map((x) => (
          <AccordionItem key={x.id} id={x.id} domId={`sec-${x.id}`} title={x.title} icon={<Glyph name={x.icon} size={16} />} meta={x.meta} className="ruled">
            {x.body}
          </AccordionItem>
        ))}
      </Accordion>

      <NextAction>
        {rel === "manager" ? (p.plan.pending_approval_id
          ? <a className="dp-btn primary" href={`#/approvals/${encodeURIComponent(p.plan.pending_approval_id)}`}>Review the proposed tasks<Glyph name="arrow" size={14} /></a>
          : <a className="dp-btn primary" href="#/team">Back to Team<Glyph name="arrow" size={14} /></a>)
          : rel === "hire" ? <a className="dp-btn primary" href={`#/board/${encodeURIComponent(id)}`}>Open my board<Glyph name="arrow" size={14} /></a>
          : <a className="dp-btn primary" href="#/inbox">Open expert inbox<Glyph name="arrow" size={14} /></a>}
        {rel === "manager" && <a className="dp-btn" href={`#/board/${encodeURIComponent(id)}`}>Open {nameOf(pr.hire).split(" ")[0]}'s board</a>}
        <a className="dp-btn" href="#/chat">Ask the project</a>
      </NextAction>
    </section>
  );
}

const PLAN_LABEL: Record<string, string> = {
  approved: "Approved", awaiting_approval: "Awaiting approval", drafting: "Drafting", not_started: "Not started",
};
