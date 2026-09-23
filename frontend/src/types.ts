export type Department = "sales" | "hr" | "engineering" ; //naming a new type department nothing else not string not anything

export type FunctionalRole = 
    |"admin" 
    |"domain_expert" 
    |"manager" 
    |"new_hire";




export interface User {      
    id:string;
    name:string;
    email:string;
    department:Department;
    functionalRole:FunctionalRole;
} //


export interface RoadmapData{
    department : Department,
    overview : string,
    readFirst : string[];
    firstTask : string;
}

export interface RoadmapProgress {
    completed : boolean;
    completedAt:string | null;
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
    staleReason?:string;  //staleReason: "The source document changed yesterday"
    askedCount:number;
}


export type AuditAction = "ASK"|"VERIFY_ANSWER" | "FLAG_STALE" | "TASK_COMPLETED" | "CONNECT_SOURCE" ;


export interface AuditEvent{
    id:string;
    actorId:string;
    action:AuditAction;
    timestamp:string;
}

export type ViewName = "roadmap" | "ask" | "verifyQueue" | "dashboard";