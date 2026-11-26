/** Audit actions in plain words. The log stores a code per entry ("member.role_changed"); people read "Role changed".
 *  A code missing here still reads well: "kanban_card.moved" becomes "Kanban card moved". Used by the audit log
 *  (Audit.tsx); any page that lists audit entries can use it too. */

const LABELS: Record<string, string> = {
  "access.requested": "Access requested",
  "access.updated": "Access updated",
  "agent.mode_changed": "Agent mode changed",
  "agents.tick": "Daily agent check ran",
  "answer.verified": "Answer verified",
  "area.recorded": "Progress area recorded",
  "audit.exported": "Audit log exported",
  "audit.purged": "Old entries removed",
  "audit.viewed": "Audit log viewed",
  "blocker.resolved": "Blocker resolved",
  "board.milestone.added": "Milestone added",
  "board.milestone.changed": "Milestone edited",
  "board.milestone.removed": "Milestone removed",
  "board.milestones.reordered": "Milestones reordered",
  "board.task.added": "Task added",
  "board.task.changed": "Task edited",
  "board.task.moved": "Task moved on the board",
  "board.task.removed": "Task removed",
  "board.task.rescheduled": "Task moved to another milestone",
  "checkin.written": "Check-in written",
  "completion.complete": "Onboarding signed off",
  "completion.extend": "Onboarding extended",
  "demo.clock_advanced": "Demo clock moved",
  "demo.reset": "Demo reset",
  "demo.signed_in": "Signed in to the demo",
  "document.deleted": "Document deleted",
  "document.uploaded": "Document uploaded",
  "expert.answered": "Expert answered a question",
  "expert.preferences": "Expert preferences changed",
  "feedback.submitted": "Documentation feedback sent",
  "feedback.updated": "Documentation feedback updated",
  "first_task.selected": "First task chosen",
  "github.installation_bound": "GitHub app connected",
  "handover.attachment_added": "Document attached to a handover",
  "handover.attachment_removed": "Document removed from a handover",
  "handover.completed": "Handover completed",
  "handover.created": "Handover started",
  "handover.requested": "Handover requested",
  "handover.transferred": "Knowledge handed over",
  "handover.updated": "Handover updated",
  "integration.google.connected": "Google Calendar connected",
  "integration.google.disconnected": "Google Calendar disconnected",
  "invitation.accepted": "Invitation accepted",
  "invitation.created": "Invitation sent",
  "invitation.revoked": "Invitation revoked",
  "kt.rerecord_booked": "KT re-recording booked",
  "kt_plan.scheduled": "KT session scheduled",
  "meeting.cancelled": "Meeting cancelled",
  "meeting.created": "Meeting created",
  "meeting.recording_deleted": "Recording deleted",
  "meeting.recording_imported": "Recording imported",
  "meeting.recording_uploaded": "Recording uploaded",
  "meeting.scheduled": "Meeting scheduled",
  "member.added": "Member added",
  "member.deactivated": "Member deactivated",
  "member.erased": "Member's data erased",
  "member.github_changed": "GitHub handle changed",
  "member.left": "Member left",
  "member.reactivated": "Member reactivated",
  "member.role_changed": "Role changed",
  "milestone.updated": "Milestone updated",
  "onboarding.enrolled": "New hire enrolled",
  "onboarding.started": "Onboarding started",
  "organization.created": "Organisation created",
  "organization.deleted": "Organisation deleted",
  "organization.exported": "Organisation data exported",
  "preboarding.acknowledged": "Pre-boarding document acknowledged",
  "preboarding.env_checked": "Environment check ran",
  "preboarding.link_created": "Pre-boarding link created",
  "project.created": "Project created",
  "question.routed": "Question routed",
  "repo.connected": "Repository connected",
  "setup.updated": "Setup changed",
  "summary.written": "Weekly summary written",
  "task.moved": "Task moved",
  "team.created": "Team created",
  "team.renamed": "Team renamed",
  "template.created": "Program template created",
  "template.versioned": "Program template updated",
};

/** The first part of a code, as a menu group. */
const GROUPS: Record<string, string> = {
  access: "Access", agent: "Agents", agents: "Agents", answer: "Answers", approval: "Approvals", area: "Progress", audit: "Audit log",
  blocker: "Blockers", board: "Milestone board", checkin: "Check-ins", completion: "Completion", demo: "Demo", document: "Documents", expert: "Experts",
  feedback: "Feedback", first_task: "First task", github: "GitHub", handover: "Handovers", integration: "Integrations",
  invitation: "Invitations", kt: "KT sessions", kt_plan: "KT sessions", meeting: "Meetings", member: "Members", milestone: "Milestones",
  onboarding: "Onboarding", organization: "Organisation", preboarding: "Pre-boarding", project: "Projects", question: "Questions",
  repo: "Repository", setup: "Setup", summary: "Summaries", task: "Tasks", team: "Teams", template: "Templates",
};

const words = (code: string) => code.replace(/[_-]+/g, " ").replace(/\bgithub\b/gi, "GitHub").replace(/\bkt\b/gi, "KT").trim();
const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** "approval.task_plan.decided" with {decision: "approved"} reads "Task plan approved". */
export function actionLabel(action: string, detail?: unknown): string {
  if (LABELS[action]) return LABELS[action];
  const approval = /^approval\.(.+)\.decided$/.exec(action);
  if (approval) {
    const decision = detail && typeof detail === "object" ? (detail as Record<string, unknown>).decision : undefined;
    const what = sentence(words(approval[1]));
    return typeof decision === "string" && decision ? `${what} ${words(decision)}` : `${what} decided`;
  }
  return sentence(action.split(".").map(words).filter(Boolean).join(" "));
}

/** The menu group of an action: "member.added" → "Members". */
export function actionGroup(action: string): string {
  const head = action.split(".")[0];
  return GROUPS[head] ?? sentence(words(head));
}

/** Whether an action filter (exact, or a prefix ending in *) selects an action. */
export function actionMatches(filter: string, action: string): boolean {
  return filter.endsWith("*") ? action.startsWith(filter.slice(0, -1)) : action === filter;
}
