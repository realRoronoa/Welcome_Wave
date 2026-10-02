import type { FunctionalRole, ViewName } from "../types";

/**
 * One lookup describing every view: who may see it, what the sidebar calls it,
 * and what the page header says. Sidebar reads `label` and `roles`; App reads
 * `title` and `blurb`. Two components, one source of truth.
 *
 * `roles` is the permission list. In the prototype it only filters the
 * interface; in production the same scoping has to be enforced at retrieval,
 * which is the point the README makes about not merely hiding content in the UI.
 */
export const VIEWS: Record<
  ViewName,
  { label: string; roles: FunctionalRole[]; title: string; blurb: string }
> = {
  roadmap: {
    label: "Roadmap",
    roles: ["new_hire", "domain_expert", "manager", "admin"],
    title: "Your first week",
    blurb:
      "Built from content tagged for your department the moment you were added. Nothing to prompt for.",
  },
  ask: {
    label: "Ask",
    roles: ["new_hire", "domain_expert", "manager", "admin"],
    title: "Ask & evidence",
    blurb:
      "Answers are built only from sources your department is permitted to see, and arrive labelled with who stands behind them.",
  },
  verifyQueue: {
    label: "Verify queue",
    roles: ["domain_expert", "admin"],
    title: "Verify queue",
    blurb: "Turn a correct draft into the canonical answer everyone gets from now on.",
  },
  dashboard: {
    label: "Dashboard",
    roles: ["manager", "admin"],
    title: "Coverage & gaps",
    blurb:
      "Where documentation runs thin, and how much of the knowledge base a person has actually confirmed.",
  },
};

/** Sidebar render order. Object key order is not a contract; this is. */
export const VIEW_ORDER: ViewName[] = ["roadmap", "ask", "verifyQueue", "dashboard"];

export function viewsFor(role: FunctionalRole): ViewName[] {
  return VIEW_ORDER.filter((view) => VIEWS[view].roles.includes(role));
}

export function canSee(role: FunctionalRole, view: ViewName): boolean {
  return VIEWS[view].roles.includes(role);
}

/**
 * Where each role lands at sign-in: experts have a queue waiting, managers and
 * admins want the numbers, everyone else starts on the roadmap.
 */
export function defaultViewFor(role: FunctionalRole): ViewName {
  if (role === "domain_expert") return "verifyQueue";
  if (role === "manager" || role === "admin") return "dashboard";
  return "roadmap";
}
