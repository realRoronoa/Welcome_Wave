# Tiered answer routing, verified answers and expert time

**Problem.** Every unanswered question went straight to a senior. Most could be answered from the sources, from an answer
someone already verified, or by the hire's mentor.

**Users.** Any member asks (executives cannot: read-only); mentors and domain owners answer; experts and managers verify.

**Data.** Chat turns (`chat_messages`), `expert_requests`, `verified_answers` (with `reuse_count`; indexed as chunk
`va-<id>`), `expert_preferences`, `metric_events` (`route.asked`, `route.escalated`, `interruption`, `answer.reused`).

**API.**
- `POST /api/ask/route` `{project_id, question, ask_person=false, urgent=false, escalate_request_id?, message_id?, run_id?}` →
  every answer carries `explain` (the Guide; `docs/AGENTS.md`), `run_id` and `steps` (the trace: `[{n, kind:
  think|tool|verify|final, text, tool, args, sources, ms}]`). `run_id` is a UUID the client makes before asking, so it can
  subscribe to `GET /api/live?topics=agent:<run_id>` and watch the steps arrive (`event: step`). Without it the server makes
  one. A malformed one is a 422, and one already used is a 409:
  - tier 1: `{tier: 1, status: "answered", message_id, answer, citations, kt_clips, confidence, check: {ok, sentences: [{text,
    claim, sources}]}, retrieval: {similarity, rule, names}, reused, escalation, why}`;
  - not answered: `{tier: 1, status: "offered", message_id, check, escalation: {available: true, next_tier: 3|4}, why}`, with
    nothing sent to anyone;
  - tier 2: `{tier: 2, status: "related", message_id, related: [{kind, id, question, answer, similarity}], escalation, why}`;
  - tiers 3 and 4: `{tier, status: "routed", message_id, request_id, routed_to: {id, name, role}, delivery: "now"|"digest",
    escalation: {available, next_tier}, why}`; nobody to ask: `{tier: 4, status: "unrouted", …}`.
  - `ask_person: true` with the `message_id` of an offered turn sends that turn to a person, with its draft and cited code.
- `POST /api/ask/free` `{project_id, question, run_id?}` → `{handled_by: guide|coach|planner|scout, why, result, run_id, steps}`:
  the Supervisor decides who handles a free-form request. The Guide's `result` is the `/ask/route` answer; the others'
  results are `{proposal}`, saying what that agent would propose (nothing is done).
- `GET /api/agents/runs/{run_id}` → `{run, steps}`. The asker reads their own run, and the project's managers read any run.
  The steps are the shared trace: lines the code wrote and the sources found, never the question or the model's words.
- `GET /api/experts/requests`: each row carries `draft`, the AI draft that came with it.
- `POST /api/verified-answers {project_id, question, answer, citations?, source_request_id?}` → 201; `GET
  /api/verified-answers?project_id=` (experts, managers).
- `GET /api/experts/preferences`, `PATCH {max_interruptions_per_day?, office_hours?, digest?}` (experts, managers).

**Logic.** It runs as the Guide's subgraph (`tier1 → tier2 → person`) in the Supervisor's chat graph.
1. *AI answer*: the question reaches the model inside the data block only. When the model can run an agent loop, tier 1 is
   agentic RAG (`app/agents/specialists/guide/agent.py`):
   - The question is searched as asked.
   - The agent then searches again with its own queries, reads files, follows symbols, and checks KT moments and verified
     answers (at most 8 tool calls in 25 s).
   - It answers with [n] citations, or says the evidence is not enough.
   - VERIFY, the model's own claim-by-claim check, and the citation check below both run, then at most one repair turn,
     then the citation check decides.

   Otherwise (the offline demo, the AI off, rules mode) QA writes one answer from the first search's blocks. Either way the
   same two gates hold.
   - Retrieval: the best cited source is at least 0.55 similar to the question, or the question names an identifier (a path,
     a snake_case or camelCase name) that the cited source contains. This is the defined rule for keyword-only matches. The
     model's own confidence is shown, never used as the gate.
   - The citation check: each claim must be supported by ONE cited source on its own. Every identifier it names and every
     number in it must appear in that source, at least half of its subject words must too, and its negation must agree
     (a "never/not/no" sentence needs a source that negates the same thing; a plain one, a source that does not).
   For the agent's sources, similarity is measured against the question as asked (a file read takes that of the search
   hit with the same lines). A source the first search did not find also passes when it holds at least half of the
   question's subject words. If either gate fails, tier 1 says "not enough evidence", with the reason.
2. *Related*: a verified answer or an answered expert question with similarity ≥ 0.80. This includes those the agent
   found with its own `verified_answers` queries and judged to answer the question. It is shown without who asked or
   answered. A verified answer shows its question as the verifier wrote it; an expert's answer shows a paraphrase of the
   question's subject, never another hire's words.
3. *Offer*: when neither tier answers, nothing is sent. The answer offers "Ask a person". The agent decides who should
   answer:
   - It picks a person from what `who_knows`, `code_owners` and `kt_moments` returned.
   - It writes a draft for them: what it found and what is missing.
   - Its suggestion is kept only for a person those tools named. The `why` says who it is, in the tools' own words.
4. *Mentor* (asked for): tier 3, with the draft attached to the request.
5. *Domain owner*: on `escalate_request_id` (the asker's own request), or when there is no mentor. The owner of the code the
   answer CITED (else the best-ranked code found): the agent's suggestion if `routing.suggested_target` finds them eligible
   (an active expert, manager or admin on the project, not the asker), else CODEOWNERS (a specific rule before the
   catch-all), else whoever explained
   it most recently, else the catch-all owner, else a manager. The draft goes with it, on the request. When the owner
   answers, the mentor's copy of the same question is answered too and its notices close.

Expert time: one path for every hand-off to a person (`routing.deliver`; the chat's hand-off uses it too). A question
interrupts its person now only within their office hours (when they set any; UTC) and under their daily cap (3 by
default). Otherwise it waits in their digest, which the daily tick sends, once per question, to everyone with `digest` on.
`urgent` is the asker's to use once a day, and only reaches a mentor at once. Targets never come from the question or the
model.

**UI states.** Answer with sources and clips; "not enough evidence" with the unsupported sentence; related answers with
"ask my mentor"; routed with who and when (now or digest), and "escalate".

**Edge cases.** Empty question 422; per-person question limit shared with `/api/chat`; escalating someone else's request 404;
the domain owner already holding it → `unrouted` with the reason.

**Tests.** `test_guide_agent.py` covers the agentic Guide with scripted turns:
- a multi-hop answer;
- VERIFY and the one repair;
- the suggestion through the capped path, and the guards on it;
- the fallback and rules mode;
- injection;
- the run record;
- live steps and SSE;
- `ask.free`.

`test_agent_runtime.py` covers the loop itself. `eval/agentic_eval.py` is the live evaluation, run with a real key and
never in CI. `test_lifecycle_api.py` (each tier, the offer, the draft on the request, AI off, interruption budget, office hours,
the digest), `test_lifecycle_rules.py` (citation check: numbers, negation, one source; the retrieval gate; office hours),
`test_agents_model.py` (owner from cited code, siblings closed, tier 2 paraphrase), `test_injection.py` (the routed question).
