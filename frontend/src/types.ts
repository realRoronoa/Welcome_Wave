export type Department = "sales" | "hr" | "engineering" ; //naming a new type department nothing else not string not anything

export type FunctionalRole = 
    | "admin " 
    | "domain-expert" 
    | "manager" 
    | "new-hire";


export interface User {      
    id:string;
    name:string;
    email:string;
    department:Department;
    functionRole:FunctionalRole;
} //

export interface RoadmapProgress {
    completed : boolean;
    completeAt:string | null;
}


export type AnswerStatus = "ai_draft" | "verified" | "stale" | "not_found";


export interface Evidence {
    source : string;
    section : string;
}



export interface Answer {
    id:string;
    question :string;
    department:Department;
    status:AnswerStatus;
    text:string;
    evidence:Evidence[];
    verifiedBy?:string;
    verifiedAt?:string;
    askedCount:number;
}


export type AuditAction = "ASK"|"VERIFY_ANSWER" | "FLAG_STALE" | "TASK_COMPLETED" | "CONNECT_SOURCE" ;


export interface AuditEvents{
    id:string;
    actorId:string;
    action:AuditAction;
    timestamp:string;
}

export type ViewName = "roadmap" | "ask" | "verifyQueue" | "dashboard";