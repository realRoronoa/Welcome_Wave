# Welcome Wave

**An AI onboarding platform that actually helps new hires - instead of just answering their questions.**

## What Is Welcome Wave?

Every company has the same problem: when a new person joins, the knowledge they need is scattered across old documents, Slack threads, code comments, and tribal knowledge. New hires either interrupt teammates constantly or spend too long figuring things out alone.

Welcome Wave is more than a chatbot over company files. It is an onboarding system that:

1. Automatically gives new hires a role-specific starting roadmap.
2. Clearly labels every answer as either an unverified AI draft or a verified answer.
3. Lets domain experts approve useful answers for everyone else.
4. Detects source changes and flags affected verified answers for re-checking.
5. Enforces role-based access to knowledge at the data level.
6. Shows managers where repeated unanswered questions reveal documentation gaps.

## Who Is It For?

- **New hires:** Guided ramp-up for engineering, sales, HR, support, operations, and other teams.
- **Domain experts and team leads:** Approve a good answer once instead of repeating it.
- **Managers and HR:** See onboarding progress and knowledge gaps.
- **Future engineers:** Add more knowledge connectors without rebuilding the core system.

## How It Works

1. An admin uploads documents or connects a GitHub repository. Sources are read-only.
2. Welcome Wave chunks the content and tags it with role-based access rules.
3. A role-specific onboarding roadmap is generated for each new hire.
4. The new hire starts on the roadmap instead of a blank chat screen.
5. Questions are checked against existing verified answers first.
6. If no verified answer exists, the system searches only permitted content and generates a grounded, cited AI draft.
7. A domain expert can verify the draft, turning it into a canonical answer.
8. Source changes automatically flag dependent verified answers for review.
9. Managers can review recurring unanswered questions, verification coverage, and the re-check backlog.

## What Makes It Different From ChatGPT?

Welcome Wave is designed to:

- Distinguish confirmed facts from AI drafts.
- Enforce role-based access instead of merely hiding content in the interface.
- Detect changes to the documents behind verified answers.
- Proactively guide new hires without requiring them to know what to ask.
- Build institutional trust as experts verify answers for future employees.

## Scope

### Included

- Read-only document upload and GitHub connector
- Role-based tagging, chunking, and retrieval
- Grounded, cited AI answers
- Draft versus Verified labeling
- Expert verification workflow
- Staleness detection for changed sources
- Role-specific onboarding roadmaps
- Manager analytics dashboard
- Roadmap, ask/answer, verification queue, and dashboard views
- Basic Prometheus-style health metrics

### Future Roadmap

- Custom AI or embedding models
- Confluence, Slack, Salesforce, Zendesk, and HR connectors
- Multi-company billing and full SaaS packaging
- Mobile application
- Private or self-hosted enterprise deployment

## Technology

| Component | Technology | Purpose |
| --- | --- | --- |
| Backend/API | FastAPI and Python | Ingestion, search, roadmap, and verification logic |
| Frontend console | React and TypeScript | Web experience for new hires, experts, and managers |
| Semantic search | Chroma | Finds relevant content by meaning, not only keywords |
| Answer generation | Claude/LLM API | Writes grounded answers from retrieved company content |
| Long-term data | PostgreSQL | Stores answers, roles, permissions, and dependencies |
| Health monitoring | Prometheus-style metrics | Tracks latency, errors, and usage |

## Trust Guardrails

- Original documents and connected systems are never modified.
- AI drafts are never promoted automatically to Verified.
- Content is never leaked across roles, including through indirect questions.
- Every answer is explicitly labeled as an AI draft or a verified answer.

## Project Basics

- **Team:** Vijayabaskar R and Aman Nautiyal, mentored by Sowmiya Mam
- **Timeline:** 12 weeks across foundation, ingestion, search, roadmap, verification, staleness detection, analytics, console polish, hardening, and release
- **Repository:** [github.com/VijayabaskarR-06/welcomewave](https://github.com/VijayabaskarR-06/welcomewave)
- **Definition of done:** A live demo across at least three job roles, architecture documentation, a walkthrough video, and tests proving verification priority, role isolation, and staleness correctness

## Glossary

- **Verified Answer:** An answer personally approved by a domain expert and treated as trusted truth.
- **AI Draft:** A fresh AI-generated answer that has not been confirmed by a human.
- **Staleness Flag:** A warning that the source behind a verified answer changed and needs review.
- **Role Scoping:** Rules that determine which content and roadmaps a person can access.
- **Roadmap:** A role-specific guide that tells a new hire where to start.
- **Ingestion Connector:** A plug-in module that reads a knowledge source without writing back to it.

## Prerequisites

- Docker Desktop with Docker Compose, or Node.js 22+ and Python 3.11+
- An Anthropic API key for future generation features (optional for the current scaffold)

## Quick Start With Docker

```sh
sh scripts/setup-dev-env.sh
docker compose up --build
```

Open the frontend at <http://localhost:3000>. The API is available at
<http://localhost:8000>, with the health check at
<http://localhost:8000/health>. Chroma is exposed at port `8001` and PostgreSQL
at port `5432`.

Stop the services with:

```sh
docker compose down
```

## Local Development

Create the environment file:

```powershell
Copy-Item .env.example .env
```

Start the backend:

```powershell
Push-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
uvicorn main:app --reload --port 8000
Pop-Location
```

In another terminal, start the frontend:

```powershell
Push-Location frontend
npm install
npm run dev
Pop-Location
```

The frontend development server runs at <http://localhost:5173>. Under Docker it
is published on <http://localhost:3000> instead — the container passes
`--port 3000` to match the mapping in `docker-compose.yml`.

## Frontend Console

The console is a React + TypeScript single-page app. It currently runs on the
fixtures in `src/mockData.ts`; no endpoint exists for it to call yet.

### Views

Four screens, each gated by the signed-in person's functional role:

| View | Who sees it | What it does |
| --- | --- | --- |
| Roadmap | everyone | The department's reading list and first task, looked up rather than asked for |
| Ask | everyone | Questions answered from that department's sources only, every answer carrying its trust state and citations |
| Verify queue | domain expert, admin | Edit and sign off a draft, turning it into the canonical answer |
| Dashboard | manager, admin | Verified coverage, the re-check backlog, and which questions are asked most but confirmed least |

Sign-in stands in for the real thing, and asks for one thing: a role.
Production reads it from the company directory. The prototype's dropdown is
there because role is what actually changes the console — which screens open,
and whose sources are in scope. The person behind the role is resolved from the
fixtures and reported in a line of text, since which of several engineers signs
in does not change what an engineer may do.

### How state flows

`App.tsx` is the only file that calls `useState` for shared data. `currentUser`
and `answers` live there and reach every view as props:

```
App.tsx ──┬── RoadmapView   { user }
          ├── AskView       { user, answers, setAnswers }
          ├── VerifyQueue   { user, answers, setAnswers }
          └── Dashboard     { user, answers }          ← no setter: read-only
```

Two consequences worth keeping:

- **Swapping fixtures for the API is a change to `App.tsx` alone.** No view
  knows whether its data came from `mockData.ts` or from `fetch()`.
- **Verifying an answer updates every screen at once**, because all four read
  the same array. Verify a draft in the queue and the Ask transcript already on
  screen re-renders as verified — it stores the answer's id, not a copy.

Components may import *fixed reference data* (`ROADMAPS`) directly, but never
mutable state.

### Wiring it to the backend

`types.ts` is the contract both sides share — the shapes there are what the
FastAPI responses have to match. When the endpoints land, replace the initial
state in `App.tsx`:

```ts
const [answers, setAnswers] = useState<Answer[]>([]);

useEffect(() => {
  fetch(`/api/v1/answers?department=${currentUser.department}`)
    .then((res) => res.json())
    .then(setAnswers);
}, [currentUser]);
```

Requests use relative `/api/...` paths. `vite.config.ts` proxies them to the
backend, so no base URL is hardcoded and dev needs no CORS configuration.

### Trust states

Four states, from `AnswerStatus`. Each is distinguished by typographic form as
well as colour, so they stay apart in greyscale and for colour-blind readers:

| State | Treatment |
| --- | --- |
| `verified` | mono, uppercase, tinted, slightly tilted — a stamp |
| `stale` | the same stamp struck through — stamped once, now undermined |
| `ai_draft` | serif italic with a dashed edge and no fill — provisional by shape |
| `not_found` | dotted and grey — it makes no claim, so it takes no colour |

Colour is never the only carrier of meaning anywhere in the console.

### Colour

Surfaces are near-neutral cool greys. They are deliberately not navy: a
saturated chrome competes with the content, which is what makes a dark UI read
as a corporate intranet rather than a product.

That leaves colour free to mean something, and in the console it means exactly
two kinds of thing:

| Role | Hue | Where it appears |
| --- | --- | --- |
| Proceed | violet (`--brand` filled, `--violet` drawn) | Continue, Mark complete, focus rings, the roadmap screen |
| Retrieval | teal | the Ask screen and its submit |
| Verification | amber | the verify queue, verified stamps, coverage bars |
| Staleness | coral | stale stamps, Flag stale, the re-check backlog |

The rule worth keeping: **a solid accent button is only for the action that
accent names.** Verify is amber because amber means verification; Flag stale is
coral for the same reason. Everything else affirmative is `btn-primary`. Before
this, sign-in and Mark complete both borrowed amber — a button wearing a status
colour teaches the status wrong, and a large amber fill is the cheapest-looking
element a dark UI can have.

`--brand` is the deep stop of the violet ramp rather than a fifth hue, because
violet already means "the proactive half of the product" — which is what the
brand means too. It exists separately only because white text on `--violet` at
its drawing lightness does not reach 4.5:1, and on `--brand` it reaches 4.9:1.

### Source layout

```
frontend/src/
  App.tsx              shared state, routing between views, page shell
  types.ts             the contract the backend must match
  mockData.ts          fixtures standing in for the API
  styles.css           tokens, then components, in that order
  roleSelect/          sign-in: pick a role (stands in for the directory)
  roadmap/             RoadmapView
  ask/                 AskView
  verifyQueue/         VerifyQueue
  dashboard/           Dashboard
  shared/              TrustBadge, Sidebar, icons, labels, navigation
```

`shared/navigation.ts` holds one lookup describing every view — its label, page
title, and permitted roles. The sidebar and the page header both read from it,
so adding a view means editing that lookup rather than touching either
component. No routing library: navigation is `view` state in `App.tsx`.

## Validation

Typecheck and build the frontend with:

```powershell
Push-Location frontend
npm run build
Pop-Location
```

`npm run build` runs `tsc --noEmit` first, so a type error fails the build
before Vite is invoked.

## Structure

- `frontend/` React onboarding console
- `backend/` FastAPI services for ingestion, retrieval, verification, and roadmaps
- `tests/` unit, integration, and resilience tests
- `docs/` architecture and API reference
