# LangGraph in Welcome Wave

What LangGraph does here, where the code is, and how to describe it accurately. Line pointers are written as
`` `code on that line` ([file:line](link)) ``. `tests/test_agents_layout.py` checks that each line still says what this
page claims, so a stale pointer fails the build instead of misleading a reader.

Versions: `langgraph` 1.2 and `langgraph-checkpoint-postgres` 3.2 (`backend/requirements.lock`). LangGraph is used for
orchestration only: the graphs, their state, routing, pauses and checkpoints. Model calls do not go through LangChain.
They use our own model port (`core/llm.py`: Gemini through `google-genai`, or the offline model).

## At a glance

| Graph | Built in | Nodes | Checkpointer | What it is for |
|---|---|---|---|---|
| Supervisor, `graph` | `supervisor/graph.py` | 14 | Postgres (self-hosted, Google Cloud); in memory (demo) | Every event that may pause for a person |
| Supervisor, `chat_graph` | the same builder | 14 | none | Requests a person waits for; they never pause |
| Guide tiers | `specialists/guide/agent.py` | 6 | none (runs inside the `guide` node) | Answer, verified answer, or offer a person |
| Coach steps, Reviewer steps | `supervisor/graph.py` | 2 each | none (inside their nodes) | `check → weekly`, `match → review` |
| Agent loop | `core/runtime.py` | 2 | none | One thinking specialist's `think ⇄ act` loop |
| Research crew | `app/rag/crew.py` | 6 | none | Plan, search in parallel, join, rank |

All paths below are under `backend/app/` unless they start with `backend/`.

## 1. The state

The Supervisor's state is a `TypedDict` with `total=False`: every field is optional, and each node returns only the fields
it changes. `` `class State(TypedDict, total=False):` `` ([state.py:17](../backend/app/agents/supervisor/state.py#L17))

- A run starts with `{"event": ...}`. Nodes add `route`, `result`, `error`, and the fields that carry work to the next
  agent: `action_items` (Note-taker → Planner), `kt_before` (Librarian → Keeper), `proposals`, `tick` and `decision`.
- LangGraph merges each node's returned dict into the state. Every field is last-write-wins except `decided`, which has a
  reducer. `` `decided: Annotated[list[dict], operator.add]` `` ([state.py:29](../backend/app/agents/supervisor/state.py#L29))
  Several approval branches of one run can each append their decision, in any order, without overwriting each other.
- The state holds plain values only: dicts, lists, strings and ids, never an object of our code. A paused run's state is
  stored by the checkpointer and read back days later, so it must not depend on our classes or module layout. We checked
  this on the demo's paused runs: the stored checkpoints name no module of `app`.

Subgraphs have their own small states: the agent loop's `` `class LoopState(TypedDict, total=False):` ``
([runtime.py:288](../backend/app/agents/core/runtime.py#L288)), the Guide's `TierState` and the crew's `CrewState`.

## 2. Nodes

`build_supervisor(deps, checkpointer)` builds and compiles the graph.
`` `def build_supervisor(deps: Deps, checkpointer):` `` ([graph.py:97](../backend/app/agents/supervisor/graph.py#L97)),
`` `g = StateGraph(State)` `` ([graph.py:726](../backend/app/agents/supervisor/graph.py#L726))

| Node | What it does |
|---|---|
| `route` | Looks the event type up in `ROUTES`. `` `g.add_node("route", route)` `` ([graph.py:727](../backend/app/agents/supervisor/graph.py#L727)) |
| `orchestrate` | `ask.free`: decides which specialist takes a free-form request (`supervisor/orchestrator.py`) |
| `librarian` … `keeper` | One node per specialist, each wrapped by `guarded`. `` `g.add_node("librarian", guarded(` `` ([graph.py:728](../backend/app/agents/supervisor/graph.py#L728)) |
| `planner_approval`, `reviewer_approval`, `approval` | Pauses for a person (section 5). `` `g.add_node("approval", await_proposal)` `` ([graph.py:739](../backend/app/agents/supervisor/graph.py#L739)) |
| `escalate` | Tells the project's managers that an agent needs a person |

`guarded` is the wrapper every specialist node shares. `` `def guarded(name, fn` `` ([graph.py:100](../backend/app/agents/supervisor/graph.py#L100))

- It runs the specialist. `` `out = fn(state, config) or {}` `` ([graph.py:121](../backend/app/agents/supervisor/graph.py#L121))
- It retries asynchronous events with backoff. A synchronous request is tried once. Errors no retry can fix (the AI off,
  a size limit, a day's quota) stop at once.
- It writes the run record, with the LangSmith trace id when tracing is on.
- It turns a failure into `{"error": ...}` instead of raising, so the edge out of the node can route to `escalate`. (A
  repository busy with another run is the exception: the event goes back to the queue.)

## 3. Edges and conditional routing

Every conditional edge has a **path map**: a label for each possible next node. The labels are what the Agents page draws.
`GET /api/agents` reads them from the compiled graph itself, so the diagram cannot drift from the code.
`` `for source, specs in compiled.builder.branches` `` ([registry.py:117](../backend/app/agents/core/registry.py#L117))

- `` `g.add_edge(START, "route")` `` ([graph.py:765](../backend/app/agents/supervisor/graph.py#L765))
- `route` → the specialist of the event's type, `ask.free` → `orchestrate`, or `unknown event` → `escalate`.
  `` `g.add_conditional_edges("route"` `` ([graph.py:766](../backend/app/agents/supervisor/graph.py#L766)), using
  `` `ROUTES = {` `` ([graph.py:71](../backend/app/agents/supervisor/graph.py#L71))
- `orchestrate` → `guide`, or `proposal` → END. `` `g.add_conditional_edges("orchestrate"` `` ([graph.py:768](../backend/app/agents/supervisor/graph.py#L768))
- Chains between agents are edges, never calls. `librarian` → `keeper` after a push that touched code explained in a KT
  session. `` `g.add_conditional_edges("librarian"` `` ([graph.py:770](../backend/app/agents/supervisor/graph.py#L770))
  The Note-taker's action items lead to the Planner, and the Coach's due completion reviews lead to the Planner too.
- Proposals lead to pauses. `` `g.add_conditional_edges("planner", after(planner_next)` `` ([graph.py:774](../backend/app/agents/supervisor/graph.py#L774))
- `after(chain)` puts the error check in front of every specialist's own choice: `error` → `escalate`, `done` → END.
  `` `def after(chain):` `` ([graph.py:742](../backend/app/agents/supervisor/graph.py#L742)),
  `` `stop = {"error": "escalate", "done": END}` `` ([graph.py:764](../backend/app/agents/supervisor/graph.py#L764))

## 4. Subgraphs

The Guide's tiers, the Coach's steps and the Reviewer's steps are compiled graphs of their own, invoked inside their
parent's node rather than added to the parent as nodes.

- The Guide: `start → tier1 → tier2 → person → explain`, or `escalation → explain`.
  `` `g = StateGraph(TierState)` `` ([agent.py:396](../backend/app/agents/specialists/guide/agent.py#L396)), invoked by
  `` `done = guide_tiers.invoke(` `` ([graph.py:653](../backend/app/agents/supervisor/graph.py#L653))
- The Coach: `check → weekly` when a week closed. `` `c = StateGraph(CoachState)` `` ([graph.py:608](../backend/app/agents/supervisor/graph.py#L608))
- The Reviewer: `match → review` for a code task. `` `r = StateGraph(ReviewState)` `` ([graph.py:627](../backend/app/agents/supervisor/graph.py#L627))

Each is compiled with `checkpointer=False`. `` `return c.compile(checkpointer=False)` `` ([graph.py:615](../backend/app/agents/supervisor/graph.py#L615))
The parent's checkpoint covers the node as a whole, so `guarded` keeps one place for retries, run records and escalation.
A subgraph never pauses: only the Supervisor's own nodes call `interrupt()`.

## 5. Interrupts: where a person decides

Agents propose; people decide. A proposal parks the run at a node that calls `interrupt()`:

| Node | Waits for | Line |
|---|---|---|
| `planner_approval` | the manager's decision on a 30/60/90 plan (`task_plan`) | `` `decision = interrupt({"kind": "task_plan"` `` ([graph.py:461](../backend/app/agents/supervisor/graph.py#L461)) |
| `reviewer_approval` | the manager's decision on a pull request review | `` `decision = interrupt({"kind": "review"` `` ([graph.py:581](../backend/app/agents/supervisor/graph.py#L581)) |
| `approval` | a re-recording, a doc fix or a template change | `` `decision = interrupt({"kind": kind` `` ([graph.py:337](../backend/app/agents/supervisor/graph.py#L337)) |

How it behaves:

- On the first pass, `interrupt(value)` stops the run. The checkpointer saves the state, and `invoke` returns with
  `__interrupt__` holding `{kind, approval_id}`.
- On resume, LangGraph runs the node again from its first line, and this time `interrupt()` returns the person's decision.
  Code before `interrupt()` therefore only reads. `` `kind = store.get_approval(aid).kind` `` ([graph.py:336](../backend/app/agents/supervisor/graph.py#L336))
- One push can park several re-recording proposals. `park` sends one `Send` per proposal, so each gets its own `approval`
  branch and its own interrupt. `` `return [Send("approval"` `` ([graph.py:756](../backend/app/agents/supervisor/graph.py#L756))
  Each is decided on its own, in any order. The `decided` reducer collects the results (section 1).
- `chat_graph` has no checkpointer, so it never parks. `` `if not ids or not checkpointer:` `` ([graph.py:754](../backend/app/agents/supervisor/graph.py#L754))

## 6. The checkpointer in each mode

The same builder is compiled twice: `graph` with a checkpointer, and `chat_graph` without one.
`` `return g.compile(checkpointer=checkpointer)` `` ([graph.py:783](../backend/app/agents/supervisor/graph.py#L783))

| Mode | `graph` | Where |
|---|---|---|
| `demo` | `MemorySaver`: in memory. A paused run lasts as long as the process, and the demo resets on restart anyway | `` `saver = MemorySaver()` `` ([container.py:323](../backend/app/container.py#L323)) |
| `self_hosted` | `PostgresSaver` on the Postgres in Docker | `` `checkpointer = PostgresSaver(pool)` `` ([container.py:215](../backend/app/container.py#L215)), from `` `def _self_hosted` `` ([container.py:233](../backend/app/container.py#L233)) |
| `gcp` | `PostgresSaver` on Cloud SQL, through the same `_postgres` helper | `container.py`, `build_container` |
| every mode | `chat_graph` and every subgraph: no checkpointer | |

- The app's database role cannot create tables, so the migrator creates LangGraph's tables (`checkpoints`,
  `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`). `` `PostgresSaver(conn).setup()` `` ([migrate.py:130](../backend/app/migrate.py#L130))
- A finished run's checkpoints are deleted, because they are only needed while a run can pause.
  `` `def _forget_if_finished` `` ([container.py:190](../backend/app/container.py#L190))
- Deleting an organisation deletes its threads. `` `c.checkpointer.delete_thread(thread)` `` ([orgdata.py:58](../backend/app/lifecycle/orgdata.py#L58))

## 7. How a run resumes

A run's thread id is its event's dedupe key, so a redelivered event finds its own thread.
`` `claim = self.store.claim_event` `` ([container.py:93](../backend/app/container.py#L93))

**After a person decides.** `POST /api/approvals/{id}/decide` (`app/api/routes.py`) calls `Container.decide`:

1. It claims the approval and stores the decision, so a double click does not resume the run twice.
   `` `if not self.store.claim_approval` `` ([container.py:155](../backend/app/container.py#L155))
2. It reads the paused state and maps each approval to the interrupt waiting on it.
   `` `def _pauses(state)` `` ([container.py:180](../backend/app/container.py#L180))
3. It resumes with `Command(resume=...)`. When the run waits on several interrupts, the value is `{interrupt_id: decision}`,
   so only the one decided continues. `` `value = {interrupt_id: decision} if interrupt_id else decision` ``
   ([graph.py:793](../backend/app/agents/supervisor/graph.py#L793)),
   `` `return graph.invoke(Command(resume=value)` `` ([graph.py:794](../backend/app/agents/supervisor/graph.py#L794))
4. The node continues after its `interrupt()`, applies the decision (tasks approved, review decided, session booked), and
   the run goes on to END. Its checkpoints are then deleted.

Because the state is in Postgres, the API process that resumes a run need not be the one that started it, and a restart
in between changes nothing.

**After a crash mid-run.** The event's lease lapses and the queue delivers it again. `_start` reads the thread first:
`` `def _start(self` `` ([container.py:115](../backend/app/container.py#L115))

- A run parked at an approval is left alone. Deciding the approval resumes it.
  `` `if any(t.interrupts for t in state.tasks)` `` ([container.py:125](../backend/app/container.py#L125))
- A run that stopped between nodes continues from its last checkpoint instead of starting over.
  `` `out = self.graph.invoke(None, cfg)` `` ([container.py:129](../backend/app/container.py#L129))
- Nodes that create things check first whether an earlier attempt already did, so a retried node does not propose twice.
  `` `existing = store.pending_approval_for_thread(thread, "task_plan")` `` ([graph.py:392](../backend/app/agents/supervisor/graph.py#L392))

Tests that hold this: `tests/test_postgres.py::test_golden_path_on_postgres` (a plan paused in Postgres, then resumed),
`tests/test_agents_model.py::test_a_push_parks_one_rerecord_per_call_and_speaker_decided_in_any_order`,
`tests/test_review_fixes.py::test_redelivered_event_does_not_restart_a_run_waiting_for_a_manager`,
`tests/test_round3_api.py::test_deciding_before_the_run_paused_is_still_preparing` and
`tests/test_agents_model.py::test_the_roster_and_its_edges_come_from_the_graph`.

## 8. The agent loop

A thinking specialist runs one bounded loop inside its node: `run_agent`.
`` `def run_agent(ctx` `` ([runtime.py:327](../backend/app/agents/core/runtime.py#L327)) The Planner, Scout, Reviewer, Coach
and Keeper reach it through `core/thinking.think`, which adds the agent's mode, budget and run record.
`` `out = rt.run_agent(` `` ([thinking.py:112](../backend/app/agents/core/thinking.py#L112)) The Guide calls it directly,
for its answer and for its check of that answer.

- `think`: one model turn with the tools bound. The answer arrives through a `final_answer` function whose parameters are
  the output schema, so it is validated before it is accepted.
  `` `def think(state: LoopState)` `` ([runtime.py:364](../backend/app/agents/core/runtime.py#L364))
- `act`: runs the turn's tool calls (read-only tools that re-check permissions) and hands their results back as untrusted
  data. `` `def act(state: LoopState)` `` ([runtime.py:414](../backend/app/agents/core/runtime.py#L414))
- `after_think` chooses `act`, `think` (try again) or `stop`.
  `` `g.add_conditional_edges("think"` `` ([runtime.py:473](../backend/app/agents/core/runtime.py#L473))
- Budgets: tool calls, wall-clock time, tokens and model turns per organisation per day. LangGraph's `recursion_limit` is a
  last guard. `` `out = loop.invoke(init` `` ([runtime.py:482](../backend/app/agents/core/runtime.py#L482))
- No checkpointer: a loop takes seconds and is never resumed from a checkpoint.
  `` `loop = g.compile(checkpointer=False)` `` ([runtime.py:475](../backend/app/agents/core/runtime.py#L475))
  Its steps reach the asker live through our own event stream, and the run record keeps a privacy-filtered copy.
- When no model can run a loop (the offline demo, the AI off, "rules" mode, the daily budget used), `run_agent` raises
  `NoToolUse` before any step, and the specialist runs its rules instead.

The research crew is a LangGraph graph with a fan-out and a join: the rules' searches and the model's search plan run in
the same step, and `more` waits for both. `` `g.add_edge(["retrieve", "model_plan"], "more")` `` ([crew.py:887](../backend/app/rag/crew.py#L887))

## 9. Tracing

When an organisation turns tracing on, LangSmith records every graph run. Each run record keeps its `trace_id`, the link
from `GET /api/runs` to the trace. `` `langsmith.configure(` `` ([container.py:281](../backend/app/container.py#L281)),
`` `def trace_id()` `` ([graph.py:59](../backend/app/agents/supervisor/graph.py#L59))

## 10. What LangGraph does not do here

- **No LangChain.** Models are called through our port (`core/llm.py`). Tool calling and the `final_answer` schema are ours.
- **No prebuilt agent.** `create_react_agent` is not used. The loop is the two nodes in section 8.
- **No agent-to-agent calls.** Specialists never import or call each other (`tests/test_agents_layout.py`). Every hop is an
  edge of the Supervisor graph.
- **No parallel specialists.** One event runs one specialist, plus the chain that follows from it. `Send` fans out only
  the approval waits, and the crew fans out its searches.
- **No LangGraph store or streaming API.** Knowledge lives in our index and stores, and live steps go through our own SSE
  hub (`app/live.py`).

## What to say in a deck or on a resume

These statements are true of the code:

- "A LangGraph supervisor graph routes 21 event types to eight specialist agents, chains them through conditional edges,
  and pauses for human approval with `interrupt()`. Runs are checkpointed in Postgres and resumed with `Command`, even
  days later."
- "Each thinking agent runs a bounded think-and-act loop, built as a LangGraph subgraph, over read-only tools. Its output
  is a Pydantic schema that code validates before anything is kept."
- "Human in the loop by construction: an agent's output is a proposal. Plans, reviews, re-recordings and template changes
  wait at an interrupt until a manager decides."
- "Crash-safe: a run interrupted mid-graph continues from its last checkpoint, and an event delivered twice runs once."

The numbers: 1 Supervisor and 8 specialists; 21 event types; 14 nodes in the Supervisor graph; 3 pause nodes for 5 kinds
of approval; 6 LangGraph graphs in all (the Supervisor, the Guide's tiers, the Coach's and Reviewer's steps, the agent
loop and the research crew).

Do not say:

- "The agents talk to each other." They never do. The Supervisor routes, and chains are graph edges.
- "Built on LangChain." Only LangGraph and LangSmith are used.
- "Autonomous agents that act on their own." Agents propose and people decide. The GitHub access is read-only.
- "Our own model." The models are Gemini, plus an offline model for the demo.

Questions you may get, with short answers:

- **Why one Supervisor graph and not nine services?** An approval can wait for days. One checkpointed graph can stop at
  the approval and continue from the same state, and it gives one place for retries, run records and escalation.
- **What happens if the server dies during a run?** The event is delivered again and continues from its last checkpoint.
  A run waiting for a person is left waiting.
- **Why are chat answers not checkpointed?** The person is waiting for them, and they never pause, so they run on
  `chat_graph`, which has no checkpointer.
- **How do you stop a model from approving something?** It cannot. A model's output is a schema that code checks, and
  only a person's decision resumes an `interrupt()`.
