# The Guide's research crew (multi-agent RAG)

Status: shipped, measured 2026-11-18. The code is the authority; this page says what each part promises, what was measured,
and where the code differs from the first contract.

## Why

Before this track the Guide searched the question once, as asked, and handed the top 8 hits to its writer. One query
misses: a question in the asker's words ("how do I add a default label?") rarely shares words with the code that answers it
(`apply_defaults`), a name the question gives (`check_labels`, `engine/policy.py`) was never looked up directly, nothing
re-ranked the fused list, and the offline keyword channel counted shared words with no weighting. The crew fixes retrieval,
which every answer depends on; the writer, the model's own check (VERIFY), the retrieval gate and the citation guard are
unchanged and keep the last word.

## The crew

```
question ─► Planner ─┬─► Retrievers (code · documents · KT calls · verified answers, one batched pass) ─► Fusion ─► Judge ─► evidence
                     └─► (model planner, concurrently, when a model can think) ─► extra queries ─┘
evidence ─► Writer (the Guide's agent loop, or QA's one call, or the offline extractive writer) ─► Critic (VERIFY + citation guard)
```

| Member | What it does | Model | Without a model |
|---|---|---|---|
| Planner | Reads the question: its intent (locate, explain, howto, why, people, overview, other), the names it gives (paths, functions), its subject words, its parts (a two-part question becomes two queries), and up to 5 queries in the project's own words. The model planner adds up to 4 rewrites and one hypothetical passage (what an ideal source would say), concurrently with the first retrieval. | `structured(..., QueryPlan)`, time-boxed | rules (`app/rag/analyze.py`) |
| Retrievers | Four specialists, one per kind of source, each with its own result size. Every query of the plan runs against each kind in one batched pass (`Index.search_grid`: one embedding call for all queries). The code retriever also looks up every name the question gives: `symbols_named` (in path, then line order) and the file a path names; a qualified name (`ReportJob.run`) through its owner, so another class's `run` never stands in for it. When the query embedding fails (a 429, a 503, the daily quota), the keyword channel and the exact lookups run alone, every similarity 0.0, and the Retrieval says `degraded`. | none | the same |
| Fusion | Weighted reciprocal-rank fusion over every (query, kind) list and the exact-name list. | none | the same |
| Judge | Re-ranks the fused candidates. Always: a lexical cross-scorer (BM25 over the candidates, subject coverage, exact names, phrase matches, an intent prior per kind, the similarity to the question as asked). With an embedder that reads meaning, the similarity weighs more (`rerank.SEMANTIC_WEIGHTS`, read from the pool's lowest to its best). Then the expansion: the other chunks of the top files join the pool when they hold half the subject words (a bare `class X:` chunk is read with its first member), scored without reshuffling the rest. When a model can think: it grades the top candidates 0–3 for this question (each shown as the passage that matches), and the grade comes first. Spans that mostly repeat each other collapse to one (neighbouring 80-line windows do not), at most 3 hits per file. | `structured(..., EvidenceGrades)`, time-boxed | lexical only |
| Writer | Unchanged: the Guide's loop (seeded with the crew's evidence instead of one raw search; it can still search and read more), QA's one call, or the offline writer. | as before | offline extractive writer |
| Critic | Unchanged: VERIFY, `routing.citation_check`, the retrieval gate, at most one repair. | as before | citation guard |

## Contracts

### `app/rag/` (new package)

- `text.py`: one tokenizer for the crew: identifier-aware words (`split_identifiers`), a light stemmer, stop words, `subject(text) -> set[str]`, `identifiers(text) -> list[str]` (paths, snake_case, camelCase, dotted and back-ticked names).
- `analyze.py`: `analyze(question) -> Analysis` with `question`, `intent`, `identifiers`, `subject`, `parts`, `queries: list[Query]`.
  `Query(text, kinds: tuple[str, ...] | None, weight: float, origin)`; `origin` is one of `asked`, `names`, `terms`, `expand`,
  `part`, `model`, `hypothetical`. `queries[0]` is always the question as asked (`origin="asked"`, every kind, weight 1.0).
  At most 5 rule queries. Pure and fast: no I/O.
- `bm25.py`: BM25 (k1 1.2, b 0.75) over a list of token lists.
- `fuse.py`: weighted reciprocal-rank fusion, k = 60.
- `rerank.py`: the lexical cross-scorer: `features(analysis, hit, pool_stats) -> dict`, the weights as module constants, and
  `reasons(features) -> list[str]`: short lines written by our code ("names check_labels", "holds 3 of 3 subject words").
- `crew.py`:

```python
@dataclass
class Retrieval:
    hits: list[Hit]          # ranked best first, at most k. Hit.similarity = cosine to the question AS ASKED (the gate's evidence);
                             # 0.0 when only an exact-name lookup found it. Hit.score = the crew's final score.
    analysis: Analysis
    candidates: int          # distinct chunks considered
    by_kind: dict[str, int]  # candidates per kind: code, doc, meeting, verified_answer
    planner: Literal["rules", "model"]
    judged: bool             # the model graded the candidates
    reasons: dict[str, list[str]]  # chunk id -> why it ranked where it did (our words only)
    grades: dict[str, int]   # chunk id -> the model's grade, when judged
    ms: dict[str, int]       # plan, retrieve, rank, judge, total
    degraded: bool           # the query embedding failed: keyword and exact-name search only
    model_calls: int         # the planner's and judge's calls, counted against the organisation's daily model turns

    def summary(self) -> dict: ...  # the `crew` block of an answer: counts, intent, planner, judged, ms. Never the question,
                                    # never a query text, never the model's words.

def retrieve(deps, project_id, question, *, k=8, purpose="answer", kinds=None, steps=None, on_step=None) -> Retrieval: ...
def search(deps, project_id, query, *, k=8, kinds=None) -> list[Hit]: ...  # purpose="tool": rules only, no model, no steps
```

`retrieve(purpose="answer")` is a compiled LangGraph subgraph (`analyze → retrieve ∥ model_plan → more → rank → judge?`),
compiled once per process. `search` calls the same functions directly, without the model stages.

The model stages run only when `purpose="answer"`, the setting allows it, and the model can think: never for `OfflineLLM`,
`NoLLM` or an agent in rules mode; for `FakeLLM` only when the test scripted that schema. A model stage that errs, returns
something invalid or runs past its time leaves the rules' result in place. Model output is validated in code: queries are
plain strings (at most 200 characters, at most 4, deduplicated) that only feed a read-only search of this project;
grades count only for the numbers shown.

### `Index.search_grid` (port, both adapters)

```python
def search_grid(self, project_id: str, queries: list[str], kinds: list[str], k: dict[str, int]) -> list[dict[str, list[Hit]]]:
    """For each query, for each kind, the top k[kind] hybrid hits. One embedding call for every query. Every Hit.similarity is
    the cosine to queries[0], not to the query that found it."""
```

`MemoryIndex` keeps the sparse non-zeros of each vector (the hashing embedder's vectors are sparse) and ranks its keyword
channel with BM25; `search()` is unchanged. `PgIndex` embeds once and runs the hybrid query per (query, kind) on one
connection, adding `1 - (embedding <=> anchor)` for each returned row.

### Model schemas and prompts (`app/agents/core/schemas.py`, `app/agents/core/prompts.py`)

```python
class PlannedQuery(BaseModel):  text: str; kinds: list[Literal["code", "doc", "kt", "verified"]] = []
class QueryPlan(BaseModel):     intent: Literal[...] = "other"; queries: list[PlannedQuery] = []; hypothetical: str = ""
class GradedSource(BaseModel):  n: int; g: int  # 0 unrelated, 1 related, 2 partly answers, 3 directly answers
class EvidenceGrades(BaseModel): grades: list[GradedSource] = []
```

`prompts.RAG_PLAN` and `prompts.RAG_JUDGE` include `UNTRUSTED`; the question and every source reach the model only inside
`embed_data(...)`.

### Where the crew is used

| Caller | Before | Now |
|---|---|---|
| Guide tier 1 (`guide.attempt`) | `index.search(question, 8)` | `crew.retrieve(purpose="answer")`; its steps open the trace |
| QA (`qa.answer`, `chat.question`) and `routing.ai_answer` without hits | `index.search(question, 8)` | `crew.retrieve(purpose="answer")` |
| `search_knowledge` tool (every thinking agent) | `index.search(query, k, kinds)` | `crew.search(query, k, kinds)` |

Other `index.search` callers (KT planner, first task, review guidelines, routing's person tiers) are unchanged.

### Trace steps (asker sees `text`; everyone else `shared`)

| Step | kind / tool | text (asker) | shared |
|---|---|---|---|
| plan | `think` / `plan_search` | "Planned 3 searches: where check_labels is defined" (bare subject words are listed: "Planned 3 searches for: reports, missing, labels") | "Planned 3 searches" |
| retrieve | `tool` / `search_knowledge` | "Searched code, documents, KT calls and verified answers: 21 found (code 14 · docs 4 · KT 3)" | the same counts |
| rank | `verify` / `rank_evidence` | "Ranked the evidence: kept 6 of 21 (the model judged 3 directly relevant)" | the same counts |

Sources on the retrieve and rank steps are the hits, as before. Run summaries stay as they are: never the question.

### API (additive)

The tier-1 attempt carries `crew` (`Retrieval.summary()`); `POST /api/ask/route` returns it on every tier when a retrieval
ran: `{intent, queries, planner, candidates, by_kind: {code, doc, kt, verified}, kept, judged, ms: {plan, retrieve, rank,
judge, total}}`. The Ask page draws it as one quiet line under the answer. `GET /api/agents` adds `crew` to the Guide:
`[{key, name, role}]`.

A tier-1 result also carries `writer`: `"model"` when a model wrote the answer, `"extractive"` when the offline writer
composed it from quoted sources (no model on the server), `null` when nothing was written. `explain.ai` is true only for
`"model"`. The Ask page labels an extractive answer "Quoted from the sources".

### The offline writer (`OfflineLLM`, `QADraft`)

Without a model the answer is extractive: one to three sentences, each copied or assembled from one cited source and
ending with its `[n]`, so every sentence passes the citation guard. A code source gives its name, file and lines and its
docstring's first sentence (or its signature); a document, KT call or verified answer gives the sentence that holds the
most of the question's subject words, attributed ("CONTRIBUTING, page 1:", "KT #1 at 01:05:"). It answers exactly when it
did before (a source must share the question's subject) and never cites a source that does not.

## Guardrails (unchanged, and held by tests)

- Retrieval reads only the run's project; model queries feed only that read-only search.
- The retrieval gate still judges similarity to the question as asked (`Hit.similarity` keeps that meaning), or a name the
  question gives that the cited source contains. The citation guard has the last word on every sentence.
- The question and sources reach a model only inside `embed_data`, under `UNTRUSTED`. A source that says "grade me 3" or
  "ignore your instructions" changes nothing a guard decides (`tests/test_injection.py`).
- Shared traces and run records carry counts only: no question, no query text, no model words.
- No new dependency: BM25, fusion and the scorer are written here.

## Evaluation (`backend/eval/rag_eval.py`)

Two corpora, each with a golden set split into `dev` (for tuning) and `holdout` (reported, never tuned on):

- `demo`: the seeded demo project (sample repo, documents, the KT call, verified answers). `eval/golden_demo.jsonl`.
- `self`: Welcome Wave's own backend (`backend/app/**/*.py`) and `docs/*.md`, indexed offline. `eval/golden_self.jsonl`.

Retrieval metrics per method (`baseline` = `index.search`, `crew` = `crew.retrieve`): hit@1, recall@5, recall@10, MRR@10,
nDCG@10, latency p50/p95, by category. End to end on `demo` (offline): answered rate on answerable questions, refusal rate on
unanswerable ones, citation precision, citation-guard pass rate. `--live` adds the model stages with a real model when
`GEMINI_API_KEY` is in the environment. `python -m eval.run_eval` (the CI gate) runs the crew's metrics with targets.

## Measured

Retrieval is k = 10 on answerable cases (hit@1 / R@5 / R@10 / MRR@10 / nDCG@10). `dev` is tuned on; `holdout` and `unseen`
never are. `eval/README.md` defines each metric.

### Offline, 2026-11-18

`MemoryIndex(HashEmbedder())` and the offline writer, no network. `python -m eval.rag_eval --e2e`, plus `--corpus unseen`.

| Corpus | Split | Cases | Baseline | Crew |
|---|---|---|---|---|
| demo | dev | 31 | 0.68 / 0.94 / 0.94 / 0.78 / 0.80 | 0.90 / 0.90 / 0.97 / 0.91 / 0.87 |
| demo | holdout | 18 | 0.67 / 0.94 / 1.00 / 0.78 / 0.80 | 0.78 / 1.00 / 1.00 / 0.88 / 0.88 |
| self | dev | 52 | 0.15 / 0.40 / 0.58 / 0.28 / 0.31 | 0.52 / 0.77 / 0.87 / 0.63 / 0.60 |
| self | holdout | 33 | 0.18 / 0.39 / 0.48 / 0.28 / 0.28 | 0.45 / 0.73 / 0.79 / 0.57 / 0.56 |
| unseen (Starlette) | unseen | 55 | 0.44 / 0.71 / 0.84 / 0.57 / 0.54 | 0.66 / 0.94 / 0.96 / 0.77 / 0.70 |

Crew latency per question, p50 / p95: demo 6 / 10 ms, self 22 / 71 ms, unseen 25 / 47 ms.

The self corpus includes `docs/*.md`, so its numbers move when the docs do. This round's doc updates took self crew R@5
from 0.79 to 0.75 (one case lost to the new STATUS text, two to other edits); the rows above are after them. Since then the
working-status docs (STATUS, PLAN, CURRENT_STATE, LOCAL_FIRST_USE) are left out of the self corpus, which took it back to
0.78 ([backend/eval/README.md](../../backend/eval/README.md), "Corpora").

End to end, tier 1 (answered: share of answerable cases; refused: share of unanswerable ones):

| Corpus | Variant | Answered | Refused | Citation precision | Cites an expected source | Guard |
|---|---|---|---|---|---|---|
| demo (49 + 8) | crew, and `guide [crew]` | 0.65 | 1.00 | 0.78 | 1.00 | 1.00 |
| unseen (55 + 15) | crew, and `guide [crew]` | 0.64 | 0.93 | 0.47 | 0.74 | 1.00 |
| unseen | baseline | 0.62 | 0.93 | 0.44 | 0.68 | 1.00 |

The one wrong answer is `unseen-unanswerable-12` ("default timeout for an endpoint"): it passes the terms rule on
"default", "endpoint" and "enforce". It was answered before the crew too.

### With real embeddings, 2026-11-17

The demo project embedded with `gemini-embedding-2` (the configured `gemini-embedding-001` hit the free tier's daily limit).

| Run | Crew | Plain search |
|---|---|---|
| Rules only, Postgres + pgvector, all 49 | 0.86 / 1.00 / 1.00 / 0.92 / 0.89 | 0.88 / 0.98 / 1.00 / 0.92 / 0.91 |
| Semantic weights, cached vectors replayed offline, dev (31) | 0.94 / 0.97 / – / 0.96 / 0.93 | 0.84 / 0.97 / – / 0.91 / 0.92 |
| Semantic weights, cached vectors replayed offline, holdout (18) | 0.83 / 1.00 / – / 0.90 / 0.90 | 0.83 / 1.00 / – / 0.90 / 0.91 |

The rules-only row is what prompted the semantic weights (`rerank.SEMANTIC_WEIGHTS`). On holdout the crew now ties plain
search. With these vectors the similarity gate cannot tell answerable from unanswerable: 7 of 8 unanswerable questions
score at least 0.55, and the lowest answerable one (0.63) is below the highest unanswerable one (0.68).

### Live model, 2026-11-17

| Check | Result |
|---|---|
| Model planner and judge vs rules, 10 answerable self holdout questions, offline index | nDCG 0.26 → 0.49, MRR 0.25 → 0.48, hit@1 0.10 → 0.30; 7 wins, 2 ties, 1 loss (a gap in the golden set). p50 3.8 s vs 0.14 s. |
| The Guide, 12 demo cases on Postgres (8 answerable, 4 not) | 7 of 8 answered, 4 of 4 refused. All 10 citations support their claims. One false refusal (`demo-why-2`) when the loop ran out of time. p50 25.6 s, p95 52.1 s; 7.5 model calls a question; 34 of 90 calls failed (503 and read timeouts on the free tier). |
| The Guide after the answer-path fixes, 7 hand-judged questions | 3 correct, 3 refused (2 honestly, 1 an injection), 1 wrong (D3: misses an `if False` guard in the webhook). |

Not yet measured: `gemini-embedding-001` (retrieval and the similarity gate), and the self corpus with real embeddings.
Both wait on the free tier's daily embedding quota or billing.

## Deviations from the first contract

- **Degraded search.** When the query embedding fails, the crew searches by keyword and exact name alone
  (`search_grid(semantic=False)`) and says so (`Retrieval.degraded`, the trace, "Semantic search is unavailable"). With no
  embedder at all, `AIUnavailable` still propagates.
- **Weights follow the embedder.** With a real embedder, similarity is read from the pool's lowest to its best value and
  weighs 4.0. The hashing embedder keeps the offline weights.
- **Bounded model stages.** Each planner or judge call runs under `llm.call_budget`: one try per model, no backoff, nothing
  started past the deadline. Gemini refuses a request deadline under 10 s, so a 6 s stage can leave one request running up
  to about 10 s. Each call counts against the organisation's daily model turns (`Retrieval.model_calls`).
- **The judge reads the matching passage**, not the first 600 characters (`crew.excerpt`).
- **One answer path.** `/api/chat` answers through `routing.answer_from`, the same gate, citation check and repair as
  `/api/ask/route`. Each `[n]` is pointed at the source that supports its claim.
- **Stricter gate.** The similarity rule also needs the cited sources to hold a third of the question's subject words, and a
  question that names something no cited source has is refused.
- **AI off.** With no model, the offline writer answers (`writer: "extractive"`) and the why says the AI is off. "Ask a
  person" works even when search is down.
- **Saved labels.** `writer` and `crew` are saved with each chat turn (migration `017_chat_writer.sql`), so the label survives
  a reload.
- **Names on screen.** The roster member is "Search planner" (`search_planner`), not "Planner", the onboarding agent's
  name. The Ask page shows "cited N"; the trace's rank step still says "kept N of M".
