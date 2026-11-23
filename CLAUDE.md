# CLAUDE.md: Welcome Wave

This file governs **this repository** (`welcome-wave/`, the Welcome Wave onboarding platform). A `CLAUDE.md` in a parent
folder, such as `~/Downloads/CLAUDE.md` (the "Polaris MCP Hackathon / Judge Twin" project), belongs to a different project:
ignore it here. Plans and decisions live in `docs/PLAN.md`; security controls in `docs/SECURITY.md`.

Welcome Wave turns recorded KT calls into cited answers, onboarding plans and reviewed first pull requests.
Agents propose; people decide.

## Run it

Three modes, chosen by `MODE` (backend settings in `backend/app/config.py`; every setting is an env variable):

| Mode | What it is | Start |
|---|---|---|
| `demo` | In-memory data, offline LLM, the sample repo; no keys. Resets on restart. | `cd backend && MODE=demo .venv/bin/uvicorn app.main:app --port 8080`, then `cd frontend && npm run dev` (http://localhost:5173, password `welcome-wave`). Or `docker compose up`. |
| `self_hosted` | The real app on one machine: Postgres in Docker, Firebase sign-in, Gemini by API key, files on disk. | `./scripts/selfhost.sh setup` once, then `./scripts/selfhost.sh start`. Config: `backend/selfhost.env`, which `setup` writes from `selfhost.env.example`. |
| `gcp` | Cloud Run, Cloud SQL, Vertex AI, Cloud Storage, Pub/Sub. | `docs/DEPLOY.md`, `cloudbuild.yaml`, `infra/terraform/`. |

Demo mode reads no env file: `backend/.env` (real credentials for other modes) never changes it, so it runs the same
everywhere with no keys. To use something in the demo on purpose, put it in the command's environment, e.g.
`GEMINI_API_KEY=… MODE=demo .venv/bin/uvicorn …` for real Gemini answers (`tests/test_config.py` holds this rule).

First-time backend setup: `cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.

## Test

- Backend: `cd backend && .venv/bin/python -m pytest -q`. Postgres tests run only with a scratch server:
  `TEST_PG_ADMIN_URL=postgresql://ww:ww@localhost:55432/postgres` (see `tests/test_postgres.py`).
- Retrieval eval (a CI gate): `cd backend && MODE=demo .venv/bin/python -m eval.run_eval`.
- Frontend: `cd frontend && npx tsc --noEmit -p .` and `npx vite build --outDir <a scratch folder>`. Leave the default
  `frontend/dist/` alone while others are working.
- Tests never write into `backend/`: `tests/conftest.py` points `DATA_DIR` and blob storage at a temp folder and turns rate
  limits off (`tests/test_ratelimit.py` turns them on).
- CI (`.github/workflows/ci.yml`) runs all of this, Postgres included, plus a dependency audit and both Docker images.
  A change is done when CI's checks pass locally.

## Architecture rules

- **Agents touch only ports.** Agents get `Deps` (`app/agents/deps.py`) and the protocols in `app/ports.py`: no direct
  database, HTTP or SDK calls. A new capability is a new port with a memory adapter and a real one.
- **Every new table** gets a numbered migration in `backend/db/migrations/` (never edit an applied one), an `org_id` with
  forced row-level security (`app/migrate.py` refuses to finish otherwise), a memory adapter, demo data in `app/seed.py`,
  and coverage in organisation export and deletion. A query that crosses organisations is a `SECURITY DEFINER` function
  owned by `ww_definer`, listed in `migrate.DEFINER_FUNCTIONS`.
- **Untrusted content is data.** Repository files, pull requests, documents, transcripts and calendar text reach a model
  only inside `embed_data(...)` (`app/agents/llm.py`), under a system prompt that includes `prompts.UNTRUSTED`. Model output
  is a schema (`app/agents/schemas.py`), validated, and checked in code (citations exist, findings quote the diff, paths
  exist). Never put untrusted text in a prompt outside the data block. `tests/test_injection.py` must keep passing.
- **Agents propose, people decide.** Anything that changes roles, access, tasks or reviews goes through an approval a
  person decides. No agent output may grant, approve or notify on its own authority.
- **Read-only code access.** The GitHub App has read scopes only; `RepoSource` has no write methods. Keep it that way.
- **Every answer is cited.** An answer without valid citations is "not enough information" and goes to a person.
- **Audit what matters.** Security-relevant actions call `store.audit(org_id, actor_id, action, target, detail)`; admins
  read them at `GET /api/audit`. Reading or exporting the log is itself audited.
- **Limits and headers.** New expensive or unauthenticated routes get a group in `app/ratelimit.py`. The API sends
  `default-src 'none'` CSP; the web app's CSP is in `frontend/nginx.conf.template`: no inline scripts, no eval.

## Design rules

- Monochrome: black, white and greys. **Teal only means "explained in a recorded session"** (KT), nowhere else.
- Type is Geist (and Geist Mono for code). Display sizes and spacing are chosen by eye, then kept consistent.
- Motion animates only `transform`, `opacity` and `clip-path`, and honours `prefers-reduced-motion` as the app already does.
- Sparse text, generous spacing. Accordions rather than tabs on KT pages. Say what something does in plain words.

## Secrets and hygiene

- Never commit, print, paste or read `backend/.env`, `backend/selfhost.env`, `*.pem` or service-account keys. Examples
  live in `backend/.env.example` and `backend/selfhost.env.example`; keep those secret-free.
- Share code with `./scripts/package.sh` (git-known files only; secrets, data and build output are always left out).
- `backend/.blobs` and `backend/.data` hold local runtime data; they are ignored and never packaged.

## Dependencies

Do not add a dependency (Python or npm, runtime or dev) without approval. Propose it with what it is for and one
alternative (including "write it ourselves"), and wait for a yes. Record approved ones in `docs/PLAN.md`.

## Working in parallel

Several agents may work at once. `docs/PLAN.md` lists which files each track owns: edit only yours, and note anything you
need changed in someone else's. Never run `git commit`, `checkout`, `reset` or `stash` unless the founders ask.
