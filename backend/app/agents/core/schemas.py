"""Shared data shapes.

Two kinds of models live here:
- *Draft* models are what the LLM must return (structured output). They are validated, never trusted.
- Domain models (Chunk, KTLink, Citation, ...) are what agents hand to storage and the API.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["code", "doc", "meeting", "verified_answer"]


# ---------- knowledge index ----------
class Chunk(BaseModel):
    id: str
    project_id: str
    source_type: SourceType
    ref: dict  # code: path,start_line,end_line,commit,language | doc: document_id,title,page | meeting: meeting_id,title,start_s,end_s
    content: str
    symbol: str | None = None
    stale: bool = False


class Hit(BaseModel):
    chunk: Chunk
    score: float  # fused rank score (reciprocal rank fusion)
    similarity: float = 0.0  # cosine similarity of the vector match, 0 if keyword-only


class Citation(BaseModel):
    chunk_id: str | None
    source_type: str
    label: str
    ref: dict


# ---------- KT-to-code linking (the headline feature) ----------
class KTLink(BaseModel):
    meeting_id: str
    meeting_title: str = ""
    file_path: str
    symbol: str | None = None
    chunk_id: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    start_s: float
    end_s: float
    confidence: float
    method: Literal["path", "symbol", "semantic"]
    code_changed_since: bool = False


# ---------- Q&A ----------
class QADraft(BaseModel):
    answer: str
    cited: list[int] = Field(default_factory=list, description="Numbers of the context blocks the answer uses")
    confidence: float = Field(ge=0, le=1)
    sufficient: bool = Field(description="False if the context does not contain the answer")


class QAResult(BaseModel):
    status: Literal["answered", "not_enough_information"]
    answer: str
    citations: list[Citation] = []
    kt_clips: list[KTLink] = []
    confidence: float = 0.0
    routed_to: str | None = None  # expert user id when the question was handed to a human
    # who wrote an answer: a model, or the offline writer quoting the sources ("extractive"); None when nothing was written.
    # Kept with the chat turn, so the Ask page's label is the same after a reload.
    writer: Literal["model", "extractive"] | None = None
    crew: dict | None = None  # how the evidence was found (the research crew's summary: counts only, never the question)


# ---------- Meetings ----------
class Utterance(BaseModel):
    start_s: float
    end_s: float
    speaker: str
    text: str


class ScreenMoment(BaseModel):
    at_s: float
    description: str
    visible_paths: list[str] = []
    visible_symbols: list[str] = []


class ActionItem(BaseModel):
    text: str
    owner_hint: str | None = None
    at_s: float | None = None


class Term(BaseModel):
    term: str
    definition: str


class SegmentAnalysis(BaseModel):
    utterances: list[Utterance] = []
    screen_moments: list[ScreenMoment] = []
    decisions: list[str] = []
    action_items: list[ActionItem] = []
    terms: list[Term] = []


class MeetingSummary(BaseModel):
    summary: str
    decisions: list[str] = []


class MeetingResult(BaseModel):
    meeting_id: str
    project_id: str
    title: str
    summary: str
    decisions: list[str]
    action_items: list[ActionItem]
    terms: list[Term]
    utterances: list[Utterance]
    screen_moments: list[ScreenMoment]
    kt_links: list[KTLink]


# ---------- Tasks ----------
class ProposedTask(BaseModel):
    title: str
    description: str
    kind: Literal["learn", "code", "meet"]
    due_in_days: int = Field(ge=1, le=365)  # a 30/60/90 plan reaches the program's last day
    acceptance_criteria: list[str] = []
    related_paths: list[str] = []
    source: Literal["plan", "meeting", "coverage_gap", "first_pr"] = "plan"
    issue_number: int | None = Field(None, description="GitHub issue this task resolves (first PR only)")


class FirstPR(BaseModel):
    title: str
    why: str
    issue_number: int | None = None
    files: list[str] = []


class TaskPlan(BaseModel):
    tasks: list[ProposedTask] = []
    first_pr: FirstPR | None = None


# ---------- Reviews ----------
class Finding(BaseModel):
    file: str
    line: int | None = None
    severity: Literal["blocker", "major", "minor", "info"]
    message: str
    evidence: str = Field("", description="Exact text copied from the diff that shows the problem")
    source: Literal["llm", "rule"] = "llm"


class ReviewDraft(BaseModel):
    recommendation: Literal["looks_good", "needs_work", "not_enough_information"]
    summary: str
    findings: list[Finding] = []
    questions: list[str] = Field(default_factory=list, description="Three questions that check the author understands their own change")


class ReviewResult(ReviewDraft):
    dropped_findings: int = 0


# ---------- the lifecycle agents' writing (docs/AGENTS.md) ----------
# Rules decide what the Guide, Planner, Scout, Coach and Keeper do; a model only writes the words. Each call sends the
# deterministic draft with the facts, and gets back text that app/agents/core/registry.write() checks in code (length, no
# delimiters, no link, address or number the facts do not contain) before anything is stored. Otherwise the draft is used.
class AgentText(BaseModel):
    text: str = Field(description="The rewritten text. Keep every fact of the draft; add none.")


class AgentTexts(BaseModel):
    texts: list[str] = Field(default_factory=list, description="One rewritten text per draft, in the same order")


# ---------- the agentic Guide (app/agents/specialists/guide/agent.py; its final_answer parameters) ----------
class GuideAnswer(BaseModel):
    answer: str = Field("", description="The answer in plain sentences, each ending with the [n] numbers of the sources that state "
                                        "it. Empty when the sources are not enough.")
    cited: list[int] = Field(default_factory=list, description="The source numbers [n] the answer relies on")
    sufficient: bool = Field(description="True only when the cited sources state everything the answer says")
    confidence: float = Field(ge=0, le=1, description="Your honest probability that the answer is correct and complete")
    kt: list[int] = Field(default_factory=list, description="Numbers [n] of KT moments worth showing the asker as clips")
    related: list[str] = Field(default_factory=list, description="Ids of verified answers (from verified_answers) that answer the question")
    route_to: str = Field("", description="When not sufficient: the user id, from who_knows, code_owners or kt_moments, of the person "
                                          "best placed to answer; empty when no tool named anyone")
    route_why: str = Field("", description="Why that person, from the evidence (one sentence)")
    owner_draft: str = Field("", description="When not sufficient: a short note for that person: what you found (with [n]) and what is missing")


class ClaimVerdict(BaseModel):
    sentence: int = Field(description="The sentence's number")
    supported: bool = Field(description="True only if one cited source states what the sentence says")
    source: int = Field(0, description="The number of the source that states it; 0 when none does")


class ClaimCheck(BaseModel):
    claims: list[ClaimVerdict] = Field(default_factory=list)


# ---------- the Supervisor's free-form request (ask.free) ----------
class FreeRoute(BaseModel):
    handled_by: Literal["guide", "coach", "planner", "scout"] = Field(
        description="guide: a question about the code, documents or KT calls (the default). coach: the asker's own progress or "
                    "what to do next. planner: booking or planning (a KT session, the 30/60/90 plan). scout: choosing a first task.")
    why: str = Field("", description="One sentence: why this agent")
    proposal: str = Field("", description="For coach, planner or scout: one or two sentences saying what that agent will propose; a "
                                          "person decides")


# ---------- the thinking specialists (Scout, Planner, Coach, Keeper, Reviewer; app/agents/core/thinking.py) ----------
# Each is the final_answer parameters of one agent loop (app/agents/core/runtime.py). Code checks every id, path, number, stage
# and text in it before anything is stored or shown (docs/AGENTS.md, "Guardrails"); what fails a check is dropped, and the
# rules stand in for it.
class ScoutPick(BaseModel):
    candidate_id: str = Field(description="A candidate id exactly as first_task_candidates gives it (issue:<n> or task:<id>)")
    reason: str = Field(description="Why this task fits this hire, from what you read (at most two sentences)")
    related_paths: list[str] = Field(default_factory=list, description="The repository files the change will touch, exactly as the "
                                                                        "tools wrote them")
    sources: list[int] = Field(default_factory=list, description="Numbers [n] of the sources you read that support the reason")


class ScoutShortlist(BaseModel):
    picks: list[ScoutPick] = Field(default_factory=list, description="At most 3 first tasks, the best first")
    rationale: str = Field("", description="One or two sentences on how you chose")


class ScoutBrief(BaseModel):
    summary: str = Field(description="The brief for the hire, under 900 characters: what to change, why this task, who explained the "
                                     "code and who reviews it, and where to start")
    first_steps: list[str] = Field(default_factory=list, description="At most 4 concrete first steps, each naming a file, test or KT "
                                                                     "clip you read")
    related_paths: list[str] = Field(default_factory=list, description="The files the change touches, as the tools wrote them")
    sources: list[int] = Field(default_factory=list, description="Numbers [n] of the sources the brief relies on")


class PlanTaskDraft(BaseModel):
    # no field may be named "title": schema_of drops that key everywhere, so the declaration would require a missing property
    name: str = Field(description="The task's short title")
    description: str = Field(description="What to do, in one or two sentences")
    kind: Literal["learn", "code", "meet"]
    due_day: int = Field(description="The program day it is due, from 1 to the program's last day")
    acceptance_criteria: list[str] = Field(default_factory=list, description="What a manager checks to call it done")
    related_paths: list[str] = Field(default_factory=list, description="Files or folders of the repository it is about, as the tools "
                                                                        "wrote them")
    issue_number: int | None = Field(None, description="An open issue number from the issues tool, for a code task that resolves it")
    milestone: str = Field("", description="The key of the program milestone this task reaches, if any")
    why: str = Field("", description="Why this task, from the evidence (one sentence)")
    sources: list[int] = Field(default_factory=list, description="Numbers [n] of the sources that support it")


class PlanDraft(BaseModel):
    tasks: list[PlanTaskDraft] = Field(default_factory=list, description="The plan's tasks, in the order of their due days")
    rationale: str = Field("", description="Two sentences at most: how the plan builds understanding before the first change")


class CriterionNote(BaseModel):
    criterion: str = Field(description="The criterion's key")
    assessment: str = Field(description="What the evidence says about it (one sentence)")


class CompletionDraft(BaseModel):
    decision: Literal["complete", "extend"] = Field(description="Your recommendation; the manager decides")
    extend_days: int = Field(0, description="For extend: how many days (7 to 30)")
    note: str = Field(description="The sign-off note for the manager, under 600 characters")
    assessments: list[CriterionNote] = Field(default_factory=list, description="One per criterion that is not met: is it really "
                                                                               "blocking, and why")


class PlanItemDraft(BaseModel):
    name: str = Field(description="The plan item's short title")
    kind: Literal["learn", "code", "meet"]
    due_day: int = Field(description="Day of a 90-day program")
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)


class TemplateChangeDraft(BaseModel):
    field: str = Field(description="'plan_items' (add an item), 'milestones.<key>.due_day' (move a milestone) or 'duration_days'")
    to_day: int | None = Field(None, description="For a milestone or the duration: the new day (of a 90-day program for milestones)")
    item: PlanItemDraft | None = Field(None, description="For plan_items: the item to add")
    reason: str = Field(description="One sentence naming the evidence, under 300 characters")
    evidence: list[str] = Field(default_factory=list, description="Ids of the cohort_evidence rows this change rests on")


class TemplateProposal(BaseModel):
    changes: list[TemplateChangeDraft] = Field(default_factory=list, description="At most 4 changes; none when the evidence does not "
                                                                                 "call for one")
    rationale: str = ""


class KTSessionProposal(BaseModel):
    presenter_id: str = Field("", description="The user id, from who_knows, code_owners or kt_moments, of who should present it")
    topic: str = Field(description="What the session covers, in a few words")
    paths: list[str] = Field(default_factory=list, description="The files it walks through, as the tools wrote them")
    agenda: list[str] = Field(default_factory=list, description="At most 5 agenda points")
    minutes: int = Field(30, description="Length in minutes (15 to 90)")
    why: str = Field("", description="Why this presenter and this agenda, from the evidence (one sentence)")


class AlertJudgement(BaseModel):
    alert_id: str = Field(description="The id of an open blocker alert from hire_signals")
    blocking: bool = Field(description="False when the evidence shows it is not really in the hire's way")
    reason: str = Field(description="Why, from the signals (one sentence)")
    stage: Literal["hire", "mentor", "manager"] = Field(description="Who should know now. Code allows a stage only after its "
                                                                     "business-day delay, and in the order hire, mentor, manager")


class Concern(BaseModel):
    signal: str = Field(description="The id of the signal (from hire_signals) that shows it, e.g. task:<id> or milestone:<key>")
    why: str = Field(description="What the signal shows and why it may get in the way (one sentence)")


class CoachAssessment(BaseModel):
    stuck: bool = Field(description="True when something is in the hire's way now")
    why: str = Field("", description="Why, from the signals (one sentence)")
    next_step: str = Field("", description="The one next step that would help most")
    nudge: str = Field("", description="A private, kind note to the hire (under 280 characters, at most 2 sentences); empty when nothing "
                                       "calls for one")
    alerts: list[AlertJudgement] = Field(default_factory=list, description="Your judgement of each open blocker alert")
    concerns: list[Concern] = Field(default_factory=list, description="Anything the alerts miss, each tied to one signal")


class WeeklyOverview(BaseModel):
    overview: str = Field(description="One or two sentences for the hire and their manager, under 400 characters, each ending with the "
                                      "[n] numbers of the counted lines it rests on")
    cited: list[int] = Field(default_factory=list, description="The numbers [n] of the counted lines the overview uses")


class NextStep(BaseModel):
    next_step: str = Field(description="The one next step, concrete (under 300 characters)")
    why: str = Field("", description="Why, from the program and the signals (one sentence)")
    signals: list[str] = Field(default_factory=list, description="Ids of the signals it rests on")


class LinkJudgement(BaseModel):
    link: str = Field(description="A link id exactly as kt_changes gives it")
    outdated: bool = Field(description="True when what the KT call explained no longer matches the code")
    reason: str = Field(description="What changed that makes the explanation wrong or incomplete, or why it still holds (one sentence)")


class KeeperJudgement(BaseModel):
    links: list[LinkJudgement] = Field(default_factory=list, description="One judgement per changed link")


class DocEditDraft(BaseModel):
    proposed_text: str = Field(description="The replacement or added text the owner could paste, under 2000 characters")
    rationale: str = Field("", description="Which reader notes it answers and how (one or two sentences)")
    sources: list[int] = Field(default_factory=list, description="Numbers [n] of the sources the wording relies on")


class HandoverSession(BaseModel):
    name: str = Field(description="The KT session's title")
    paths: list[str] = Field(default_factory=list, description="Files it explains, from person_footprint")
    agenda: list[str] = Field(default_factory=list, description="At most 5 agenda points")
    minutes: int = Field(30, description="Length in minutes (15 to 120)")
    why: str = Field("", description="Why it matters: what only this person explained or owns (one sentence)")


class HandoverDraft(BaseModel):
    sessions: list[HandoverSession] = Field(default_factory=list, description="At most 6 KT sessions to record, the most urgent first")
    successor_id: str = Field("", description="The user id, from who_knows or code_owners, of who should take over; empty if unsure")
    successor_why: str = Field("", description="Why that person (one sentence)")
    note: str = Field("", description="A short note to the person who is leaving, under 500 characters")


class AgentFinding(BaseModel):
    file: str = Field(description="A changed file's path, exactly as pr_diff lists it")
    line: int | None = Field(None, description="The new line number pr_diff shows for the problem")
    severity: Literal["blocker", "major", "minor", "info"]
    message: str = Field(description="What is wrong and why, in one or two sentences")
    evidence: str = Field("", description="The exact text of that line, copied from pr_diff")
    sources: list[int] = Field(default_factory=list, description="Numbers [n] of code, tests or KT moments that back the finding")


class AgentReview(BaseModel):
    recommendation: Literal["looks_good", "needs_work", "not_enough_information"]
    summary: str = Field(description="Two or three sentences on the change")
    findings: list[AgentFinding] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list, description="Three short questions that check the author understands the change")


# ---------- the Guide's research crew (app/rag, docs/specs/rag-crew.md) ----------
class PlannedQuery(BaseModel):
    text: str = Field(description="One search, in the words the project's code and documents would use (names, commands, terms); "
                                  "at most 200 characters")
    kinds: list[Literal["code", "doc", "kt", "verified"]] = Field(
        default_factory=list, description="Only these kinds of source (kt: recorded KT calls); empty: every kind")


class QueryPlan(BaseModel):
    intent: Literal["locate", "explain", "howto", "why", "people", "overview", "other"] = "other"
    queries: list[PlannedQuery] = Field(default_factory=list, description="At most 4 searches that the question's own words would miss")
    hypothetical: str = Field("", description="Two or three sentences an ideal source would contain (at most 400 characters)")


class GradedSource(BaseModel):
    n: int = Field(description="The source's number, as shown")
    g: int = Field(description="3 directly answers the question, 2 answers part of it, 1 related but does not answer, 0 unrelated")


class EvidenceGrades(BaseModel):
    grades: list[GradedSource] = Field(default_factory=list, description="One grade per numbered source")
