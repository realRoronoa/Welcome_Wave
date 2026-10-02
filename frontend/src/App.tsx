import { useEffect, useMemo, useState } from "react";
import type { Answer, User, ViewName } from "./types";
import { ANSWERS, USERS } from "./mockData";
import RoleSelect from "./roleSelect/RoleSelect";
import Sidebar from "./shared/Sidebar";
import RoadmapView from "./roadmap/RoadmapView";
import AskView from "./ask/AskView";
import VerifyQueue from "./verifyQueue/VerifyQueue";
import Dashboard from "./dashboard/Dashboard";
import { VIEWS, canSee, defaultViewFor } from "./shared/navigation";
import { MenuIcon } from "./shared/icons";

/**
 * The only file holding shared state.
 *
 * `currentUser` and `answers` live here and reach every feature view as props.
 * That single rule is what makes a real backend a change to this file alone:
 * `RoadmapView`, `AskView`, `VerifyQueue`, and `Dashboard` never learn whether
 * their data came from `mockData.ts` or from `fetch()`.
 *
 *   // now
 *   const [answers, setAnswers] = useState<Answer[]>(ANSWERS);
 *
 *   // later
 *   const [answers, setAnswers] = useState<Answer[]>([]);
 *   useEffect(() => {
 *     fetch(`/api/v1/answers?department=${currentUser.department}`)
 *       .then((res) => res.json())
 *       .then(setAnswers);
 *   }, [currentUser]);
 */
export default function App() {
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [view, setView] = useState<ViewName>("roadmap");
  const [answers, setAnswers] = useState<Answer[]>(ANSWERS);
  const [menuOpen, setMenuOpen] = useState(false);

  // Close the mobile drawer with Escape, the same key that closes every other
  // overlay a person has ever used.
  useEffect(() => {
    if (!menuOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [menuOpen]);

  const counts = useMemo(() => {
    if (!currentUser) return {};
    const mine = answers.filter((answer) => answer.department === currentUser.department);
    return {
      verifyQueue: mine.filter(
        (answer) => answer.status === "ai_draft" || answer.status === "stale",
      ).length,
    };
  }, [answers, currentUser]);

  function selectUser(user: User) {
    setCurrentUser(user);
    setView(defaultViewFor(user.functionalRole));
    setMenuOpen(false);
  }

  function switchIdentity() {
    setCurrentUser(null);
    setMenuOpen(false);
  }

  if (!currentUser) {
    return <RoleSelect users={USERS} onSelect={selectUser} />;
  }

  // Defensive: a persona switch could leave `view` pointing at a screen the new
  // role is not allowed to see, so fall back rather than render it.
  const activeView: ViewName = canSee(currentUser.functionalRole, view)
    ? view
    : defaultViewFor(currentUser.functionalRole);

  const meta = VIEWS[activeView];

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <Sidebar
        user={currentUser}
        view={activeView}
        onNavigate={setView}
        onSwitchIdentity={switchIdentity}
        counts={counts}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
      />

      {menuOpen && (
        <div className="scrim" onClick={() => setMenuOpen(false)} aria-hidden="true" />
      )}

      <main id="main" className="main">
        <button
          type="button"
          className="menu-toggle"
          onClick={() => setMenuOpen(true)}
          aria-label="Open navigation"
          aria-expanded={menuOpen}
        >
          <MenuIcon />
          Menu
        </button>

        <header className="page-head">
          <h1>{meta.title}</h1>
          <p>{meta.blurb}</p>
        </header>

        {activeView === "roadmap" && <RoadmapView user={currentUser} />}

        {activeView === "ask" && (
          <AskView user={currentUser} answers={answers} setAnswers={setAnswers} />
        )}

        {activeView === "verifyQueue" && (
          <VerifyQueue user={currentUser} answers={answers} setAnswers={setAnswers} />
        )}

        {/* No setAnswers — the Dashboard reports, it never writes. */}
        {activeView === "dashboard" && <Dashboard user={currentUser} answers={answers} />}
      </main>
    </div>
  );
}
