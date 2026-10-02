import type { FunctionalRole, User, ViewName } from "../types";
import { DEPARTMENT_LABEL, ROLE_LABEL, initialsOf } from "./labels";
import { VIEWS, viewsFor } from "./navigation";
import { VIEW_ICON } from "./icons";

interface Props {
  user: User;
  view: ViewName;
  onNavigate: (view: ViewName) => void;
  onSwitchIdentity: () => void;
  /** Counts shown against a view, e.g. how many drafts are waiting. */
  counts?: Partial<Record<ViewName, number>>;
  /** Mobile drawer state. The toggle button lives in the header, so App owns this. */
  open?: boolean;
  onClose?: () => void;
}

/**
 * Navigation that changes depending on who is signed in. The list of links is
 * derived from `VIEWS[view].roles` rather than hardcoded per role, so adding a
 * view means editing one lookup in `navigation.ts` and nothing here.
 */
export default function Sidebar({
  user,
  view,
  onNavigate,
  onSwitchIdentity,
  counts,
  open = false,
  onClose,
}: Props) {
  const allowed = viewsFor(user.functionalRole);

  return (
    <aside className={`sidebar${open ? " open" : ""}`} aria-label="Primary navigation">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <span className="brand-name">Welcome Wave</span>
      </div>
      <p className="brand-sub">Onboarding console</p>

      <nav className="nav-group">
        {allowed.map((name) => {
          const count = counts?.[name];
          const isActive = name === view;
          return (
            <button
              key={name}
              type="button"
              className={`nav-item${isActive ? " active" : ""}`}
              data-view={name}
              aria-current={isActive ? "page" : undefined}
              onClick={() => {
                onNavigate(name);
                onClose?.();
              }}
            >
              {VIEW_ICON[name]}
              <span className="nav-label">{VIEWS[name].label}</span>
              {count !== undefined && count > 0 && <span className="nav-count">{count}</span>}
            </button>
          );
        })}
      </nav>

      <div className="sidebar-identity">
        <div className="identity-row">
          <span className="avatar" aria-hidden="true">
            {initialsOf(user.name)}
          </span>
          <span className="identity-copy">
            <span className="identity-name">{user.name}</span>
            <span className="identity-meta">
              {ROLE_LABEL[user.functionalRole]} · {DEPARTMENT_LABEL[user.department]}
            </span>
          </span>
        </div>
        <button type="button" className="btn btn-ghost btn-block" onClick={onSwitchIdentity}>
          Switch person
        </button>
        <p className="identity-note">
          {scopeNote(user.functionalRole, DEPARTMENT_LABEL[user.department])}
        </p>
      </div>
    </aside>
  );
}

function scopeNote(role: FunctionalRole, department: string): string {
  if (role === "admin") return "Admin — reads across every department.";
  if (role === "manager") return `Sees ${department} analytics only.`;
  if (role === "domain_expert") return `Verifies ${department} answers.`;
  return `Answers limited to ${department} content.`;
}
