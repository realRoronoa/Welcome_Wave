import { useState } from "react";
import type { FormEvent } from "react";
import type { FunctionalRole, User } from "../types";
import { DEPARTMENT_LABEL, ROLE_LABEL } from "../shared/labels";
import { VIEWS, viewsFor } from "../shared/navigation";
import TrustBadge from "../shared/TrustBadge";

interface Props {
  users: User[];
  onSelect: (user: User) => void;
}

/** Dropdown order — broadest access last. */
const ROLE_ORDER: FunctionalRole[] = ["new_hire", "domain_expert", "manager", "admin"];

const ROLE_BLURB: Record<FunctionalRole, string> = {
  new_hire: "Lands on a department roadmap and asks grounded questions from day one.",
  domain_expert: "Reviews AI drafts and confirms the answers their department owns.",
  manager: "Sees where documentation is thin and how much of it is actually trusted.",
  admin: "Reads across every department, and verifies alongside the experts.",
};

/**
 * Stands in for login, in two halves: what the product does on the left, who
 * you want to be on the right.
 *
 * There is exactly one control. Role is the choice that changes the console —
 * which screens open, which department's sources are in scope — so it is the
 * only thing worth asking. The person behind the role is resolved here and
 * reported in a line of text, because a second dropdown made the screen look
 * like a configuration form for a decision that is really one click.
 *
 * The dropdown state is local on purpose. It is a form's working value, not
 * shared data; nothing outside this component needs it until Continue is
 * pressed and `onSelect` hands the chosen person to App.
 */
export default function RoleSelect({ users, onSelect }: Props) {
  const [role, setRole] = useState<FunctionalRole>("new_hire");

  // First match wins. The fixtures hold several people per role; which one
  // signs in does not change what the role may do, which is the whole point
  // of the screen.
  const person = users.find((user) => user.functionalRole === role);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (person) onSelect(person);
  }

  return (
    <main className="auth">
      {/* ---------- left: what this is ---------- */}
      <section className="auth-pitch">
        <div className="brand brand-lg">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">Welcome Wave</span>
        </div>

        <h1>Every answer traced back to someone who knows.</h1>

        <p className="auth-lede">
          New hires get a roadmap before they think to ask. When they do ask, the answer cites its
          sources, says whether a person has confirmed it, and flags itself the moment that source
          changes.
        </p>

        {/* The clearest way to explain the product is to show what it produces.
            These are the real TrustBadge components, in the order an answer
            actually moves through them — so this list cannot drift away from
            what the console does. */}
        <ul className="auth-states">
          <li data-state="ai_draft">
            <TrustBadge status="ai_draft" />
            <p>
              Written from permitted sources with every claim cited. Useful immediately, trusted by
              nobody yet.
            </p>
          </li>
          <li data-state="verified">
            <TrustBadge status="verified" verifiedBy="Sneha Kapoor" />
            <p>
              A domain expert read it and put their name on it. From now on it is served first, to
              everyone who asks.
            </p>
          </li>
          <li data-state="stale">
            <TrustBadge status="stale" />
            <p>
              The document behind it changed. The answer loses its verified standing automatically,
              before anyone acts on it.
            </p>
          </li>
        </ul>
      </section>

      {/* ---------- right: who you are ---------- */}
      <section className="auth-panel">
        <form className="auth-form" onSubmit={submit}>
          <h2>Sign in</h2>
          <p className="auth-note">
            Production reads your role from the company directory. This prototype lets you pick one,
            so you can see what each role is allowed to do.
          </p>

          <div className="field">
            <label htmlFor="role">Role</label>
            <select
              id="role"
              className="select"
              value={role}
              onChange={(event) => setRole(event.target.value as FunctionalRole)}
            >
              {ROLE_ORDER.map((name) => (
                <option key={name} value={name}>
                  {ROLE_LABEL[name]}
                </option>
              ))}
            </select>
            <p className="field-note">{ROLE_BLURB[role]}</p>
          </div>

          <div className="field">
            <span className="field-label">Opens</span>
            <ul className="access-list">
              {viewsFor(role).map((view) => (
                <li key={view}>{VIEWS[view].label}</li>
              ))}
            </ul>
          </div>

          <button type="submit" className="btn btn-primary btn-block" disabled={!person}>
            Continue as {ROLE_LABEL[role]}
          </button>

          {person && (
            <p className="auth-foot">
              Signed in as <strong>{person.name}</strong>, {DEPARTMENT_LABEL[person.department]}.
              Scoping follows that department.
            </p>
          )}
        </form>
      </section>
    </main>
  );
}
