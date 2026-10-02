/**
 * The shared contract. Every other file imports its shapes from here.
 *
 * These types are also the contract the FastAPI backend has to match: when
 * `GET /api/v1/answers` exists, it should return JSON shaped exactly like
 * `Answer[]`. Keeping that promise is what makes swapping `mockData.ts` for
 * `fetch()` a change to App.tsx alone.
 */

export type Department = "sales" | "hr" | "engineering";

export type FunctionalRole = "admin" | "domain_expert" | "manager" | "new_hire";

export interface User {
  id: string;
  name: string;
  email: string;
  department: Department;
  functionalRole: FunctionalRole;
}

export interface RoadmapData {
  department: Department;
  overview: string;
  readFirst: string[];
  firstTask: string;
}

export interface RoadmapProgress {
  completed: boolean;
  completedAt: string | null;
}

/**
 * `not_found` is a response state, not a stored one — it describes a question
 * the permitted sources could not answer, so nothing is ever persisted for it.
 */
export type AnswerStatus = "ai_draft" | "verified" | "stale" | "not_found";

export interface Evidence {
  source: string;
  section: string;
}

export interface Answer {
  id: string;
  question: string;
  department: Department;
  status: AnswerStatus;
  text: string;
  evidence: Evidence[];
  verifiedBy?: string;
  verifiedAt?: string;
  /** Why a once-verified answer was flagged, e.g. "The handbook changed yesterday." */
  staleReason?: string;
  askedCount: number;
}

export type AuditAction =
  | "ASK"
  | "VERIFY_ANSWER"
  | "FLAG_STALE"
  | "TASK_COMPLETED"
  | "CONNECT_SOURCE";

export interface AuditEvent {
  id: string;
  actorId: string;
  action: AuditAction;
  timestamp: string;
}

export type ViewName = "roadmap" | "ask" | "verifyQueue" | "dashboard";
