/* The product, rendered by the site (React and SVG, no screenshots), from the demo project's data (data.ts), as panels
   for the 3D world (World.tsx): the recorded explanation, the exact lines it covers, a verified answer, the learning
   path; the first contribution's clip, journey and details; the team's clip and the leadership report (illustrative
   figures, labelled so). Teal marks only what a recorded KT session explains.
   Continuous state: the journey's steps and details read CSS variables the film's controller sets from the scroll
   (--s per step, --o per detail), so they change with the scroll, never on a timer. Elements with data-anchor are where
   the teal threads attach. */
import { CSSProperties } from "react";
import { Avatar, Check, CodeLine, FigureTile, KtMark, Timeline, Waveform } from "./parts";
import { ANSWER, CODE, DAY, DURATION, EXPLAINED, FILE, JOURNEY, KT, LINKS, MILESTONES, REPO, REPORT, TASK } from "./data";

export function RecPanel() {
  return (
    <article className="u-card u-rec" aria-label={`Recorded explanation: ${KT.title}, ${KT.speaker}`}>
      <p className="u-eyebrow">Recorded explanation</p>
      <div className="u-rec-head">
        {/* the recording plays here while the panel is on screen (a world clip of film.ts, run by the film's controller) */}
        <div className="u-rec-thumb u-clip" data-shot="mentor" aria-hidden="true" style={{ "--focus": "50% 45%" } as CSSProperties}>
          <img className="s-poster" alt="" decoding="async" width="960" height="540" sizes="112px"
               data-src="/media/welcome-wave/posters/mentor-960.jpg" data-srcset="/media/welcome-wave/posters/mentor-960.jpg 960w" />
          <video className="s-video" tabIndex={-1} />
          <span className="u-rec-play"><svg viewBox="0 0 12 12" width="12" height="12"><path d="M4 2.5v7l5.5-3.5z" /></svg></span>
        </div>
        <div className="u-rec-meta">
          <h3>{KT.title}</h3>
          <p className="u-soft"><Avatar name={KT.speaker} /> {KT.speaker} · {KT.recorded} · {KT.length}</p>
        </div>
      </div>
      <div className="u-rec-wave">
        <Waveform speech={KT.speech} length={KT.lengthS} moment={[KT.moment.start, KT.moment.end]} />
        <span className="u-rec-head-line" style={{ left: `${(KT.moment.start / KT.lengthS) * 100}%` } as CSSProperties} aria-hidden="true" />
      </div>
      <blockquote className="u-quote">
        <span className="u-anchor u-anchor-moment" data-anchor="moment" aria-hidden="true" />
        <span className="u-time">{KT.moment.at}</span>
        <p><b>{KT.speaker}</b> {KT.line}</p>
      </blockquote>
    </article>
  );
}

export function CodePanel() {
  return (
    <article className="u-card u-repo" aria-label={`${REPO}, ${FILE}`}>
      <div className="u-repo-head">
        <p className="u-path"><span className="u-soft">{REPO} /</span> engine / <b>validate.py</b></p>
        <span className="u-pill">Read-only</span>
      </div>
      <div className="u-code" role="img" aria-label={`${FILE}, lines ${CODE[0][0]} to ${CODE[CODE.length - 1][0]}. Lines ${EXPLAINED[0]} to ${EXPLAINED[1]}, check_labels, are explained in ${KT.title} at ${KT.moment.at}.`}>
        {CODE.map(([n, t]) => (
          <CodeLine key={n} n={n} text={t} marked={n >= EXPLAINED[0] && n <= EXPLAINED[1]} anchor={n === EXPLAINED[0] ? "lines" : undefined}>
            {n === EXPLAINED[0] && <span className="u-code-mark"><KtMark>KT #1 · {KT.moment.at} · {KT.speaker}</KtMark></span>}
          </CodeLine>
        ))}
      </div>
      <ul className="u-links" aria-label="Moments of the call linked to code">
        {LINKS.map((l) => (
          <li key={l.symbol}><code>{l.symbol}</code><span className="u-soft">{l.file}</span><span className="u-kt-time">{l.at}</span></li>
        ))}
      </ul>
    </article>
  );
}

export function AnswerPanel() {
  return (
    <article className="u-card u-ans" aria-label="A verified answer">
      <p className="u-eyebrow">Answer</p>
      <p className="u-q">{ANSWER.q}</p>
      <p className="u-a">{ANSWER.a}</p>
      <div className="u-ans-foot">
        <ul className="u-sources" aria-label="Sources">
          {ANSWER.sources.map((s) => (
            <li key={s.label} className={s.kind === "kt" ? "is-kt" : undefined}>
              {s.kind === "kt" && <span className="u-anchor u-anchor-chip" data-anchor="source" aria-hidden="true" />}{s.label}
            </li>
          ))}
        </ul>
        <span className="u-verified"><Check />{ANSWER.verified}</span>
      </div>
    </article>
  );
}

export function PathPanel() {
  return (
    <article className="u-card u-path-card" aria-label="Learning path">
      <div className="u-path-top">
        <p className="u-eyebrow">Learning path</p>
        <p className="u-soft"><b>Vijay</b> · Backend engineer · day {DAY} of {DURATION}</p>
      </div>
      <Timeline milestones={MILESTONES} day={DAY} duration={DURATION} label="Vijay's program" />
    </article>
  );
}

/** a screen in the world: the footage plays (is scrubbed) inside it */
export function ClipPanel({ shot, chip }: { shot: string; chip?: string }) {
  return (
    <div className="u-clip" data-shot={shot} aria-hidden="true">
      <img className="s-poster" alt="" decoding="async" width="960" height="540" />
      <video className="s-video" tabIndex={-1} />
      {chip && <span className="u-clip-chip" data-reveal="merged"><Check />{chip}</span>}
    </div>
  );
}

export function StepsPanel() {
  return (
    <article className="u-card u-journey" aria-label="Vijay's first contribution, step by step">
      <p className="u-eyebrow">First contribution · Vijay</p>
      <ol className="u-steps">
        {JOURNEY.map((s, i) => (
          <li key={s.key} data-step={i} style={{ "--s": 0 } as CSSProperties}>
            <span className="u-step-dot" aria-hidden="true"><Check /></span>
            <span className="u-step-t">{s.title}</span>
            {s.key === "kt" && <span className="u-anchor u-anchor-step" data-anchor="step-kt" aria-hidden="true" />}
            <span className={`u-step-d${s.key === "kt" ? " is-kt" : ""}`}>{s.detail}</span>
          </li>
        ))}
      </ol>
    </article>
  );
}

export function DetailPanel() {
  return (
    <div className="u-detail" aria-hidden="true">
      <div className="u-card u-det" data-detail="0">
        <p className="u-eyebrow">First task · picked for Vijay</p>
        <p className="u-issue"><span className="u-soft">#{TASK.issue}</span> {TASK.title}</p>
        <p className="u-chips"><span className="u-pill">{TASK.label}</span><span className="u-pill">engine/policy.py</span></p>
      </div>
      <div className="u-card u-det" data-detail="1">
        <p className="u-eyebrow">Private review · only Vijay sees this</p>
        <p className="u-note">No test covers a policy without a name. Add one to <code>tests/test_policy.py</code>.</p>
        <p className="u-note">Raising here is fine: the no-raise rule covers validation only. <span className="u-kt-time">KT #1 · 1:38</span></p>
      </div>
      <div className="u-card u-det u-diff" data-detail="2">
        <p className="u-eyebrow">#16 · {TASK.title}</p>
        <pre><code><span className="d-ctx">  rules = [Rule(**r) for r in data.get("rules", [])]</span>{"\n"}<span className="d-add">+ if not data.get("name"):</span>{"\n"}<span className="d-add">+     raise ValueError("policy has no name")</span>{"\n"}<span className="d-ctx">  return Policy(name=data["name"], rules=rules, ...)</span></code></pre>
      </div>
      <div className="u-card u-det u-done" data-detail="3">
        <p className="u-eyebrow">Review</p>
        <p className="u-approve"><Avatar name="Ravi" /><span><b>Ravi</b> approved these changes</span><span className="u-pill is-ok"><Check />Merged</span></p>
        <p className="u-soft">Day 12 · first change merged, 18 days ahead of the milestone</p>
      </div>
    </div>
  );
}

const GOOD: Record<string, "up" | "down"> = { days_to_first_merge: "down", single_owner_critical_areas: "down" };

export function ReportPanel() {
  return (
    <section className="u-card u-report" aria-labelledby="report-h">
      <header className="u-report-head">
        <div>
          <p className="u-eyebrow">Leadership report</p>
          <h3 id="report-h">Engine team · last 90 days</h3>
        </div>
        <span className="u-pill is-note">Illustrative figures</span>
      </header>
      <div className="u-tiles">
        {REPORT.map((f, i) => (
          <div key={f.key} className="u-tile-host" style={{ "--i": i } as CSSProperties}><FigureTile f={f} good={GOOD[f.key] ?? "up"} /></div>
        ))}
      </div>
    </section>
  );
}
