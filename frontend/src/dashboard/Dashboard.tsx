import { useMemo } from "react";
import type { Answer, Department, User } from "../types";
import TrustBadge from "../shared/TrustBadge";
import { DEPARTMENT_LABEL } from "../shared/labels";

interface Props {
  user: User;
  answers: Answer[];
}

interface DepartmentStats {
  department: Department;
  total: number;
  verified: number;
  drafts: number;
  stale: number;
  coverage: number;
}

const ALL_DEPARTMENTS: Department[] = ["engineering", "sales", "hr"];

/**
 * Read-only reporting. Note the props: `answers` comes in, `setAnswers` does
 * not — this component cannot mutate anything, and that is enforced by its
 * signature rather than by a comment asking nicely.
 *
 * Nothing here is hardcoded from `mockData`: every number is computed from the
 * `answers` prop, which is why verifying a draft in the Verify queue moves
 * these figures with no change to this file.
 */
export default function Dashboard({ user, answers }: Props) {
  const scope: Department[] =
    user.functionalRole === "admin" ? ALL_DEPARTMENTS : [user.department];

  const stats = useMemo<DepartmentStats[]>(
    () =>
      scope.map((department) => {
        const rows = answers.filter((answer) => answer.department === department);
        const verified = rows.filter((answer) => answer.status === "verified").length;
        return {
          department,
          total: rows.length,
          verified,
          drafts: rows.filter((answer) => answer.status === "ai_draft").length,
          // A stale answer was verified once but cannot be counted as trusted
          // now, so it sits outside `verified` and forms the backlog.
          stale: rows.filter((answer) => answer.status === "stale").length,
          coverage: rows.length ? Math.round((verified / rows.length) * 100) : 0,
        };
      }),
    // `scope` is derived from `user`, so depending on `user` covers it.
    [answers, user],
  );

  const inScope = useMemo(
    () => answers.filter((answer) => scope.includes(answer.department)),
    [answers, user],
  );

  const ranked = useMemo(
    () => [...inScope].sort((a, b) => b.askedCount - a.askedCount),
    [inScope],
  );

  const totals = useMemo(() => {
    const total = stats.reduce((sum, row) => sum + row.total, 0);
    const verified = stats.reduce((sum, row) => sum + row.verified, 0);
    return {
      total,
      verified,
      coverage: total ? Math.round((verified / total) * 100) : 0,
      stale: stats.reduce((sum, row) => sum + row.stale, 0),
      drafts: stats.reduce((sum, row) => sum + row.drafts, 0),
    };
  }, [stats]);

  /** The gap signal: asked a lot, still not confirmed by a person. */
  const gaps = useMemo(
    () => ranked.filter((answer) => answer.status !== "verified").slice(0, 3),
    [ranked],
  );

  return (
    <div className="dash-layout">
      <p className="scope-line">
        {user.functionalRole === "admin"
          ? "Reading across every department."
          : `Scoped to ${DEPARTMENT_LABEL[user.department]}.`}
      </p>

      <div className="kpi-row">
        <article className="card kpi accent-amber">
          <span className="accent-bar" aria-hidden="true" />
          <h2 className="kpi-label">Verified coverage</h2>
          <p className="kpi-value">{totals.coverage}%</p>
          <p className="kpi-sub">
            {totals.verified} of {totals.total} tracked answers confirmed by a person
          </p>
        </article>

        <article className="card kpi accent-coral">
          <span className="accent-bar" aria-hidden="true" />
          <h2 className="kpi-label">Re-verification backlog</h2>
          <p className="kpi-value">{totals.stale}</p>
          <p className="kpi-sub">verified answers whose source has since changed</p>
        </article>

        <article className="card kpi accent-teal">
          <span className="accent-bar" aria-hidden="true" />
          <h2 className="kpi-label">Awaiting review</h2>
          <p className="kpi-value">{totals.drafts}</p>
          <p className="kpi-sub">drafts no expert has signed off yet</p>
        </article>
      </div>

      <section className="card accent-amber">
        <span className="accent-bar" aria-hidden="true" />
        <header className="card-head">
          <h2>Coverage by department</h2>
        </header>
        <ul className="bars">
          {stats.map((row) => (
            <li key={row.department}>
              <span className="bar-label">{DEPARTMENT_LABEL[row.department]}</span>
              <span
                className="bar-track"
                role="progressbar"
                aria-valuenow={row.coverage}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`${DEPARTMENT_LABEL[row.department]} verified coverage`}
              >
                <span className="bar-fill" style={{ width: `${row.coverage}%` }} />
              </span>
              <span className="bar-value">{row.coverage}%</span>
              <span className="bar-note">
                {row.stale > 0 && `${row.stale} stale`}
                {row.stale > 0 && row.drafts > 0 && " · "}
                {row.drafts > 0 && `${row.drafts} draft${row.drafts === 1 ? "" : "s"}`}
                {row.stale === 0 && row.drafts === 0 && "all confirmed"}
              </span>
            </li>
          ))}
        </ul>
      </section>

      {gaps.length > 0 && (
        <section className="card accent-coral">
          <span className="accent-bar" aria-hidden="true" />
          <header className="card-head">
            <h2>Asked most, confirmed least</h2>
          </header>
          <p className="lede">
            High traffic with no human sign-off. These are the documentation gaps worth closing
            first.
          </p>
          <ul className="gap-list">
            {gaps.map((answer) => (
              <li key={answer.id}>
                <span className="gap-count">{answer.askedCount}</span>
                <span className="gap-copy">
                  <span className="gap-question">{answer.question}</span>
                  <span className="gap-meta">{DEPARTMENT_LABEL[answer.department]}</span>
                </span>
                <TrustBadge status={answer.status} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card">
        <header className="card-head">
          <h2>Every tracked answer</h2>
          <span className="count">{ranked.length}</span>
        </header>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Question</th>
                <th scope="col">Department</th>
                <th scope="col">Trust</th>
                <th scope="col" className="num">
                  Asked
                </th>
                <th scope="col">Signed off by</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((answer) => (
                <tr key={answer.id}>
                  <td>{answer.question}</td>
                  <td>{DEPARTMENT_LABEL[answer.department]}</td>
                  <td>
                    <TrustBadge status={answer.status} />
                  </td>
                  <td className="num mono">{answer.askedCount}</td>
                  <td>{answer.verifiedBy ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
