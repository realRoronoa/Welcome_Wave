/** The milestone board's API shapes (GET /api/onboardings/{id}/board, GET /api/board/hires; backend app/lifecycle/board.py). */
import type { Person } from "../types";

export type Column = "todo" | "in_progress" | "done";
export type TaskKind = "learn" | "code" | "meet";
export type MilestoneState = "complete" | "overdue" | "current" | "past" | "upcoming";

export type MoveRecord = {
  from_key: string | null; from_title: string | null; to_key: string; to_title: string;
  from_due: string | null; to_due: string | null; at: string; by: Person | null;
};

export type Card = {
  id: string; title: string; description: string; kind: TaskKind;
  status: "approved" | "in_progress" | "changes_requested" | "submitted" | "done";
  column: Column;
  badge: string | null; // "Waiting for sign-off" | "In review" | "Changes requested"
  milestone: string; pinned: boolean; due_date: string | null; due_day: number | null; overdue: boolean;
  kt: boolean; origin: string; pr_number: number | null; pr_url: string | null; acceptance_criteria: string[];
  review_pending: boolean;
  moved: { from_key: string | null; from_title: string | null; from_day: number | null; at: string; by: Person | null } | null;
  history: MoveRecord[];
  moves: Column[]; // the columns this caller may move it to (its own included: a new place in it)
};

export type BoardMilestone = {
  key: string; title: string; due_day: number; due_date: string; from_day: number; position: number;
  state: MilestoneState; total: number; done: number; awaiting: number; overdue: number; counts: Record<Column, number>;
  checkpoints: { key: string; title: string; due_day: number; state: string }[];
  moved_out: { task_id: string; title: string; to_key: string; to_title: string | null; at: string; by: Person | null }[];
};

export type BoardData = {
  onboarding_id: string; project_id: string; status: string; hire: Person | null; role_title: string;
  start_date: string; today: string; day: number; duration_days: number;
  columns: { key: Column; title: string }[];
  milestones: BoardMilestone[]; tasks: Card[]; current: string | null; waiting_approval: number;
  can: { move: boolean; reschedule: boolean; edit: boolean };
};

export type HireSummary = {
  onboarding_id: string; name: string | null; role_title: string; day: number; duration_days: number; status: string;
  total: number; done: number; overdue: number; current: { key: string; title: string; due_day: number } | null;
};

export type BoardHires = { hires: HireSummary[]; can_manage: boolean };

/** A move the web app asks for (POST .../board/tasks/{id}/move). */
export type MoveBody = {
  column?: Column; milestone?: string; before_id?: string | null; changes_requested?: boolean; sign_off?: boolean;
};
