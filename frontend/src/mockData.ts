import type {
    Answer,
    AuditEvent,
    Department,
    RoadmapData,
    User,
} from "./types"



export const USERS : User[] = [
    {
        id:"user-1",
        name:"Aman Nautiyal",
        email:"aman@company.com",
        department:"engineering",
        functionalRole:"new_hire",
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
]


export const ROADMAPS: Record<Department, RoadmapData> = {
    engineering: {
        department: "engineering",
        overview: "Start by learning how Welcome Wave retrieves, verifies, and serves trusted knowledge.",
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
        overview: "Learn the employee handbook, people processes, and trusted HR knowledge sources.",
        readFirst: [
            "Read the employee handbook",
            "Review the leave and benefits guide",
            "Learn where HR questions are verified",
        ],
        firstTask: "Review the employee onboarding checklist.",
    },
};

export const ANSWERS: Answer[] = [
    {
        id: "answer-1",
        question: "How do I set up the engineering environment?",
        department: "engineering",
        status: "verified",
        text: "Clone the repository, install the frontend dependencies, and run the development server.",
        evidence: [
            {
                source: "README.md",
                section: "Local development",
            },
        ],
        verifiedBy: "Sneha Kapoor",
        verifiedAt: "2026-09-10T09:00:00Z",
        askedCount: 8,
    },
        {
        id: "answer-2",
        question: "What is the current sales discount limit?",
        department: "sales",
        status: "ai_draft",
        text: "Sales representatives can approve discounts up to 10%; larger discounts require manager approval.",
        evidence: [
            {
                source: "sales-playbook.md",
                section: "Discount approval policy",
            },
        ],
        askedCount: 5,
    },
        {
        id: "answer-3",
        question: "What is the employee leave policy?",
        department: "hr",
        status: "stale",
        text: "Employees should submit leave requests through the HR portal at least two working days before the leave.",
        evidence: [
            {
                source: "employee-handbook.pdf",
                section: "Leave policy",
            },
        ],
        staleReason: "The employee handbook was updated yesterday.",
        askedCount: 3,
    },

];

export const TOPIC_KEYWORDS: Record<
    Department,
    { keywords: string[]; answerId: string }[]
> = {
    engineering: [
        {
            keywords: ["engineering", "environment", "setup", "frontend"],
            answerId: "answer-1",
        },
    ],

    sales: [
        {
            keywords: ["sales", "discount", "limit", "approval"],
            answerId: "answer-2",
        },
    ],

    hr: [
        {
            keywords: ["hr", "leave", "policy", "employee"],
            answerId: "answer-3",
        },
    ],
};

export const AUDIT_EVENTS: AuditEvent[] = [
    {
        id: "event-1",
        actorId: "user-1",
        action: "ASK",
        timestamp: "2026-09-18T09:30:00Z",
    },
    {
        id: "event-2",
        actorId: "user-4",
        action: "VERIFY_ANSWER",
        timestamp: "2026-09-18T10:00:00Z",
    },
];


