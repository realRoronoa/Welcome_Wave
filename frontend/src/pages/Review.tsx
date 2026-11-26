import { useCallback, useEffect, useState } from "react";
import { api, errorMessage, isManager, Task } from "../api";
import { useSession } from "../session";
import { EmptyState, Reveal, Skeleton } from "../fx";
import { CodeBlock, Glyph, GlyphName, Pill, SectionHead, Tone } from "../dataui";
import { useLive } from "../live";

export type Finding = { file: string; line: number | null; severity: string; message: string; evidence: string; source: string };
export type ReviewResult = { recommendation: string; summary: string; findings: Finding[]; questions: string[]; dropped_findings?: number };
export type ReviewDetail = {
  id: string; task_id: string; pr_number: number; pr_url: string; head_sha: string; result: ReviewResult;
  manager_decision: "approved" | "changes_requested" | null; task: Task;
  // pending: waiting for the manager; decided; closed: no decision (a newer commit replaced it, or the PR was merged first)
  approval_state?: "pending" | "decided" | "closed" | null; newer_review_id?: string | null;
};

export const RECOMMENDS: Record<string, string> = {
  looks_good: "approve", needs_work: "request changes", not_enough_information: "not enough information to judge",
};
const DECISION = { approved: "Your manager approved this work.", changes_requested: "Your manager asked for changes." };

const REC: Record<string, { tone: Tone; glyph: GlyphName }> = {
  looks_good: { tone: "neutral", glyph: "check" }, needs_work: { tone: "warn", glyph: "alert" }, not_enough_information: { tone: "warn", glyph: "clock" },
};
/** The review agent's recommendation as a badge. */
export function RecBadge({ rec }: { rec: string }) {
  const s = REC[rec] ?? { tone: "neutral" as Tone, glyph: "sparkle" as GlyphName };
  return (
    <p className={`dp-rec t-${s.tone} rec-${rec}`}>
      <span className="dp-rec-icon"><Glyph name={s.glyph} size={15} /></span>
      <span><span className="dp-rec-k">Review agent recommends</span><strong>{RECOMMENDS[rec] ?? rec}</strong></span>
    </p>
  );
}

/** owner/repo from a GitHub pull request URL. */
const repoOf = (url: string) => url.match(/github\.com\/([^/]+\/[^/]+)/)?.[1] ?? null;

/** The pull request's number, title and reference chips. */
export function PRHeader({ r, title, as: H = "h1" }: { r: ReviewDetail; title?: string; as?: "h1" | "h2" }) {
  const repo = repoOf(r.pr_url);
  return (
    <div className="dp-pr-head">
      <span className="dp-pr-num" aria-hidden="true"><Glyph name="pr" size={18} />{r.pr_number}</span>
      <div className="dp-pr-text">
        <H>{title ?? r.task.title}</H>
        <p className="dp-chips">
          <a className="dp-chip" href={r.pr_url} target="_blank" rel="noreferrer">
            <Glyph name="pr" size={12} />{repo ? `${repo}#${r.pr_number}` : `Pull request #${r.pr_number}`}<Glyph name="external" size={11} />
          </a>
          <span className="dp-chip" title="The commit the review agent read"><Glyph name="commit" size={12} />{r.head_sha.slice(0, 7)}</span>
          {r.task.github_issue_number ? <span className="dp-chip"><Glyph name="branch" size={12} />Closes #{r.task.github_issue_number}</span> : null}
        </p>
      </div>
    </div>
  );
}

const SEV_ORDER = ["blocker", "major", "minor", "info"];
const SEV_TONE: Record<string, Tone> = { blocker: "rec", major: "warn", minor: "neutral", info: "neutral" };

/** Line numbers refer to the pull request's head, which the code view (default branch) does not show; link to GitHub.
 *  No findings is "no problems" only when the agent could judge the change: otherwise it says it could not. */
export function Findings({ findings, prUrl, rec }: { findings: Finding[]; prUrl?: string; rec?: string }) {
  if (findings.length === 0) {
    return rec === "not_enough_information"
      ? <p className="dp-clean t-unknown"><Glyph name="alert" size={15} />No findings, but the agent could not judge this change. Read it yourself.</p>
      : <p className="dp-clean"><Glyph name="check" size={15} />No problems found.</p>;
  }
  const files = prUrl ? `${prUrl.replace(/\/+$/, "")}/files` : null; // a private review has no pull request to link to
  const sorted = [...findings].sort((a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity));
  return (
    <ul className="dp-findings">
      {sorted.map((f, i) => (
        <li key={i} className={`dp-finding sev-${f.severity}`}>
          <div className="dp-finding-head">
            <Pill tone={SEV_TONE[f.severity] ?? "neutral"}>{f.severity}</Pill>
            <span className="dp-chip"><Glyph name="file" size={12} />{f.file}{f.line ? `:${f.line}` : ""}</span>
            <span className="dp-meta">{f.source === "rule" ? "Automatic check" : "Review agent"}</span>
            {files && (
              <a className="dp-finding-link" href={files} target="_blank" rel="noreferrer" aria-label={`See ${f.file} in the pull request on GitHub`}>
                See in pull request<Glyph name="external" size={11} />
              </a>
            )}
          </div>
          <p className="dp-finding-msg">{f.message}</p>
          {f.evidence && <CodeBlock code={f.evidence} start={f.line} label={`Evidence${f.line ? ` from line ${f.line}` : ""}`} />}
        </li>
      ))}
    </ul>
  );
}

export default function Review({ id }: { id: string }) {
  const { me } = useSession();
  const [r, setR] = useState<ReviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { api<ReviewDetail>(`/reviews/${id}`).then((x) => { setR(x); setError(null); }).catch((e) => setError(errorMessage(e))); }, [id]);
  useEffect(load, [load]);
  useLive("tasks", load); // the manager's decision, or a review of a newer commit, shows up while the page is open

  if (error && !r) return <p className="error" role="alert">{error}</p>;
  if (!r) {
    return (
      <section className="dp" aria-busy="true">
        <span className="sr-only" role="status">Loading…</span>
        <a href="#/tasks" className="back">Tasks</a>
        <div className="dp-pr-head"><Skeleton lines={2} height={18} style={{ width: "min(520px, 100%)" }} /></div>
        <div className="dp-card"><Skeleton lines={4} /></div>
      </section>
    );
  }
  const { recommendation: rec, summary, findings, questions, dropped_findings: dropped = 0 } = r.result;
  const decided = r.manager_decision;
  const manager = isManager(me.role);
  const closed = !decided && r.approval_state === "closed";
  const step = decided ? DECISION[decided]
    : closed ? (r.newer_review_id ? <>A newer commit replaced this review. <a href={`#/reviews/${r.newer_review_id}`}>See the latest review</a>.</>
      : r.task.status === "done" ? "The pull request was merged before a decision was made." : "Closed without a decision.")
    : manager ? <>Waiting for your decision in <a href="#/approvals">Approvals</a>.</> : "Waiting for your manager's decision.";

  return (
    <section className="dp">
      <header className="dp-review-top">
        <a href="#/tasks" className="back">Tasks</a>
        <p className="dp-eyebrow">Review of pull request #{r.pr_number}</p>
        <PRHeader r={r} />
      </header>
      <div className="dp-review-grid">
        <div className="dp-review-main">
          <Reveal className="dp-card">
            <RecBadge rec={rec} />
            <p className="dp-summary">{summary}</p>
            <ol className="dp-steps" aria-label="Decision">
              <li className="done"><i aria-hidden="true" /><span>Review agent read commit <code>{r.head_sha.slice(0, 7)}</code></span></li>
              <li className={decided ? `done ${decided}` : closed ? "done" : "wait"}>
                <i aria-hidden="true" /><span>{step}</span>
              </li>
            </ol>
            <p className="dp-card-foot">
              <a href={r.pr_url} target="_blank" rel="noreferrer">Open pull request #{r.pr_number} on GitHub</a>
              <span className="dp-meta"> · reviewed at commit <code>{r.head_sha.slice(0, 7)}</code></span>
            </p>
          </Reveal>
          <Reveal delay={80}>
            <SectionHead title="Findings" count={findings.length} />
            <Findings findings={findings} prUrl={r.pr_url} rec={rec} />
            {dropped > 0 && <p className="dp-hint"><Glyph name="alert" size={14} /><span>{dropped} more left out: {dropped === 1 ? "its" : "their"} evidence was not in the diff.</span></p>}
          </Reveal>
        </div>
        <Reveal as="section" delay={140} className="dp-review-side">
          <SectionHead title="Check your understanding" count={questions.length || undefined} />
          {questions.length > 0 ? (
            <>
              <p className="dp-lede">Be ready to answer these.</p>
              <ol className="dp-qa">{questions.map((q, i) => <li key={i}><span className="dp-qa-n" aria-hidden="true">Q{i + 1}</span><span>{q}</span></li>)}</ol>
            </>
          ) : <EmptyState title="No questions for this change" />}
        </Reveal>
      </div>
    </section>
  );
}
