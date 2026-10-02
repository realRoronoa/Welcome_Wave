import type { Answer, AuditEvent, Department, RoadmapData, User } from "./types";

/**
 * Every hardcoded value the app needs, in one place.
 *
 * Shape discipline: each object here is shaped exactly like the JSON the
 * backend will eventually return — no convenience fields a real API wouldn't
 * send, and no missing fields it will have to provide. Treat this file as a
 * stand-in for `backend/`'s future responses, not as arbitrary test data.
 */

export const USERS: User[] = [
  {
    id: "user-1",
    name: "Aman Nautiyal",
    email: "aman@company.com",
    department: "engineering",
    functionalRole: "new_hire",
  },
  {
    id: "user-2",
    name: "Priya Sharma",
    email: "priya@company.com",
    department: "hr",
    functionalRole: "manager",
  },
  {
    id: "user-3",
    name: "Rahul Mehta",
    email: "rahul@company.com",
    department: "sales",
    functionalRole: "domain_expert",
  },
  {
    id: "user-4",
    name: "Sneha Kapoor",
    email: "sneha@company.com",
    department: "engineering",
    functionalRole: "domain_expert",
  },
  {
    id: "user-5",
    name: "Arjun Verma",
    email: "arjun@company.com",
    department: "sales",
    functionalRole: "manager",
  },
  {
    id: "user-6",
    name: "Neha Singh",
    email: "neha@company.com",
    department: "hr",
    functionalRole: "domain_expert",
  },
  {
    id: "user-7",
    name: "Vikram Reddy",
    email: "vikram@company.com",
    department: "engineering",
    functionalRole: "admin",
  },
  {
    id: "user-8",
    name: "Ananya Iyer",
    email: "ananya@company.com",
    department: "hr",
    functionalRole: "new_hire",
  },
  {
    id: "user-9",
    name: "Karan Malhotra",
    email: "karan@company.com",
    department: "sales",
    functionalRole: "domain_expert",
  },
  {
    id: "user-10",
    name: "Riya Nair",
    email: "riya@company.com",
    department: "engineering",
    functionalRole: "new_hire",
  },
];

export const ROADMAPS: Record<Department, RoadmapData> = {
  engineering: {
    department: "engineering",
    overview:
      "Start by learning how Welcome Wave retrieves, verifies, and serves trusted knowledge.",
    readFirst: [
      "Read the platform README",
      "Review the system architecture",
      "Understand the verification workflow",
    ],
    firstTask: "Run the project locally and ask your first grounded question.",
  },

  sales: {
    department: "sales",
    overview: "Learn the sales knowledge sources, approval rules, and escalation paths.",
    readFirst: [
      "Read the sales playbook",
      "Review the discount approval policy",
      "Learn the customer escalation path",
    ],
    firstTask: "Review the current discount approval limits.",
  },

  hr: {
    department: "hr",
    overview:
      "Learn the employee handbook, people processes, and trusted HR knowledge sources.",
    readFirst: [
      "Read the employee handbook",
      "Review the leave and benefits guide",
      "Learn where HR questions are verified",
    ],
    firstTask: "Review the employee onboarding checklist.",
  },
};

export const ANSWERS: Answer[] = [
  /* ---------- engineering ---------- */
  {
    id: "answer-1",
    question: "How do I set up the engineering environment?",
    department: "engineering",
    status: "verified",
    text: "Clone the repository, install the frontend dependencies, and run the development server.",
    evidence: [{ source: "README.md", section: "Local development" }],
    verifiedBy: "Sneha Kapoor",
    verifiedAt: "2026-09-10T09:00:00Z",
    askedCount: 8,
  },
  {
    id: "answer-4",
    question: "Who reviews my first pull request?",
    department: "engineering",
    status: "ai_draft",
    text: "A domain expert on your own team reviews it. For platform code that is currently Sneha Kapoor.",
    evidence: [{ source: "team-directory.md", section: "Code review owners" }],
    askedCount: 6,
  },
  {
    id: "answer-5",
    question: "How does staleness detection work?",
    department: "engineering",
    status: "verified",
    text: "Each verified answer stores a dependency link to the source chunks it was built from. When a chunk changes, every answer depending on it is flagged for re-verification.",
    evidence: [{ source: "docs/architecture.md", section: "Verification" }],
    verifiedBy: "Vikram Reddy",
    verifiedAt: "2026-09-16T11:20:00Z",
    askedCount: 11,
  },
  {
    id: "answer-6",
    question: "What is the escalation path for a production incident?",
    department: "engineering",
    status: "stale",
    text: "Page the on-call engineer through the incident channel, then open an incident doc from the template.",
    evidence: [{ source: "runbook.md", section: "Incident response" }],
    verifiedBy: "Vikram Reddy",
    verifiedAt: "2026-09-05T08:15:00Z",
    staleReason: "runbook.md was rewritten on 26 September.",
    askedCount: 14,
  },
  {
    id: "answer-7",
    question: "Which branch should I open a pull request against?",
    department: "engineering",
    status: "ai_draft",
    text: "Open pull requests against `main`. There is no long-lived develop branch.",
    evidence: [{ source: "CONTRIBUTING.md", section: "Branching" }],
    askedCount: 4,
  },

  /* ---------- sales ---------- */
  {
    id: "answer-2",
    question: "What is the current sales discount limit?",
    department: "sales",
    status: "ai_draft",
    text: "Sales representatives can approve discounts up to 10%; larger discounts require manager approval.",
    evidence: [{ source: "sales-playbook.md", section: "Discount approval policy" }],
    askedCount: 5,
  },
  {
    id: "answer-8",
    question: "How do I request access to the CRM?",
    department: "sales",
    status: "ai_draft",
    text: "Ask your manager to add you as a viewer in the CRM connector configuration. There is no self-service request flow yet.",
    evidence: [{ source: "source-access.md", section: "Access requests" }],
    askedCount: 17,
  },
  {
    id: "answer-9",
    question: "What is the customer escalation path?",
    department: "sales",
    status: "verified",
    text: "Escalate to the account manager first. If unresolved within one business day, raise it to the regional sales manager.",
    evidence: [{ source: "sales-playbook.md", section: "Escalation" }],
    verifiedBy: "Karan Malhotra",
    verifiedAt: "2026-09-19T14:05:00Z",
    askedCount: 7,
  },
  {
    id: "answer-10",
    question: "Which deals need legal review?",
    department: "sales",
    status: "stale",
    text: "Any contract above ₹25 lakh in annual value, or any deal with non-standard payment terms, needs legal review.",
    evidence: [{ source: "sales-playbook.md", section: "Legal review" }],
    verifiedBy: "Rahul Mehta",
    verifiedAt: "2026-09-02T10:30:00Z",
    staleReason: "sales-playbook.md was updated on 28 September.",
    askedCount: 9,
  },

  /* ---------- hr ---------- */
  {
    id: "answer-3",
    question: "What is the employee leave policy?",
    department: "hr",
    status: "stale",
    text: "Employees should submit leave requests through the HR portal at least two working days before the leave.",
    evidence: [{ source: "employee-handbook.pdf", section: "Leave policy" }],
    verifiedBy: "Neha Singh",
    verifiedAt: "2026-09-08T09:45:00Z",
    staleReason: "The employee handbook was updated yesterday.",
    askedCount: 3,
  },
  {
    id: "answer-11",
    question: "How do I claim medical reimbursement?",
    department: "hr",
    status: "verified",
    text: "Submit the bill through the benefits portal within 30 days. Reimbursement is processed with the following month's payroll.",
    evidence: [{ source: "benefits-guide.pdf", section: "Medical reimbursement" }],
    verifiedBy: "Neha Singh",
    verifiedAt: "2026-09-22T13:00:00Z",
    askedCount: 12,
  },
  {
    id: "answer-12",
    question: "When is the probation review?",
    department: "hr",
    status: "ai_draft",
    text: "Probation is reviewed at the end of the third month, in a conversation between the new hire and their manager.",
    evidence: [{ source: "employee-handbook.pdf", section: "Probation" }],
    askedCount: 10,
  },
];

/**
 * Stands in for semantic search. `AskView` matches the typed question against
 * these keyword sets, scoped to the asker's own department — which is how role
 * isolation shows up in the prototype: an engineering keyword list is never
 * consulted for a sales user.
 */
export const TOPIC_KEYWORDS: Record<
  Department,
  { keywords: string[]; answerId: string }[]
> = {
  engineering: [
    { keywords: ["environment", "setup", "local", "install", "frontend"], answerId: "answer-1" },
    { keywords: ["review", "pull request", "pr", "reviewer"], answerId: "answer-4" },
    { keywords: ["stale", "staleness", "freshness", "changed"], answerId: "answer-5" },
    { keywords: ["incident", "escalation", "outage", "on-call", "production"], answerId: "answer-6" },
    { keywords: ["branch", "main", "develop"], answerId: "answer-7" },
  ],

  sales: [
    { keywords: ["discount", "limit", "approval"], answerId: "answer-2" },
    { keywords: ["crm", "access", "request"], answerId: "answer-8" },
    { keywords: ["escalation", "customer", "escalate"], answerId: "answer-9" },
    { keywords: ["legal", "contract", "review"], answerId: "answer-10" },
  ],

  hr: [
    { keywords: ["leave", "policy", "holiday", "time off"], answerId: "answer-3" },
    { keywords: ["medical", "reimbursement", "benefits", "insurance"], answerId: "answer-11" },
    { keywords: ["probation", "review", "confirmation"], answerId: "answer-12" },
  ],
};

export const AUDIT_EVENTS: AuditEvent[] = [
  { id: "event-1", actorId: "user-1", action: "ASK", timestamp: "2026-09-18T09:30:00Z" },
  { id: "event-2", actorId: "user-4", action: "VERIFY_ANSWER", timestamp: "2026-09-18T10:00:00Z" },
  { id: "event-3", actorId: "user-7", action: "CONNECT_SOURCE", timestamp: "2026-09-26T08:10:00Z" },
  { id: "event-4", actorId: "user-7", action: "FLAG_STALE", timestamp: "2026-09-26T08:12:00Z" },
];
