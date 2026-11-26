-- Welcome Wave - initial schema (PostgreSQL 16 + pgvector on Cloud SQL)
-- Multi-tenant: every business table carries org_id and is protected by row-level security.
-- The API sets `app.org_id` per transaction:  SELECT set_config('app.org_id', '<org uuid>', true);

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------- enums ----------
CREATE TYPE user_role        AS ENUM ('manager', 'new_hire', 'expert', 'admin');
CREATE TYPE repo_status      AS ENUM ('pending', 'indexing', 'ready', 'failed');
CREATE TYPE source_type      AS ENUM ('code', 'doc', 'meeting', 'verified_answer');
CREATE TYPE meeting_status   AS ENUM ('scheduled', 'live', 'processing', 'ready', 'failed');
CREATE TYPE link_method      AS ENUM ('path', 'symbol', 'semantic');
CREATE TYPE task_kind        AS ENUM ('learn', 'code', 'meet');
CREATE TYPE task_status      AS ENUM ('proposed', 'approved', 'in_progress', 'submitted', 'changes_requested', 'done', 'rejected');
CREATE TYPE approval_kind    AS ENUM ('task_plan', 'review');
CREATE TYPE approval_status  AS ENUM ('pending', 'approved', 'rejected', 'partially_approved');
CREATE TYPE recommendation   AS ENUM ('looks_good', 'needs_work', 'not_enough_information');
CREATE TYPE review_decision  AS ENUM ('approved', 'changes_requested');
CREATE TYPE run_status       AS ENUM ('running', 'succeeded', 'failed');

-- ---------- tenancy and people ----------
CREATE TABLE organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    firebase_uid  text UNIQUE,                     -- Identity Platform subject
    email         text NOT NULL,
    name          text NOT NULL,
    role          user_role NOT NULL,
    github_login  text,                            -- maps CODEOWNERS handles to people
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, email)
);

CREATE TABLE projects (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name         text NOT NULL,
    description  text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE project_members (
    project_id  uuid REFERENCES projects(id) ON DELETE CASCADE,
    user_id     uuid REFERENCES users(id) ON DELETE CASCADE,
    org_id      uuid NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE onboardings (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL,
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    new_hire_id  uuid NOT NULL REFERENCES users(id),
    manager_id   uuid NOT NULL REFERENCES users(id),
    role_title   text NOT NULL DEFAULT 'Software Engineer',
    start_date   date NOT NULL,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'paused')),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------- knowledge sources ----------
CREATE TABLE repositories (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            uuid NOT NULL,
    project_id        uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    full_name         text NOT NULL,                -- owner/repo
    installation_id   text,                         -- GitHub App installation (read-only scopes)
    default_branch    text NOT NULL DEFAULT 'main',
    last_indexed_sha  text,
    status            repo_status NOT NULL DEFAULT 'pending',
    codeowners        text NOT NULL DEFAULT '',
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, full_name)
);

CREATE TABLE documents (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL,
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title        text NOT NULL,
    gcs_uri      text NOT NULL,
    mime_type    text NOT NULL,
    uploaded_by  uuid REFERENCES users(id),
    page_count   int,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- One row per searchable piece of knowledge. `ref` says exactly where it came from:
--   code:    {"path","start_line","end_line","commit","language"}
--   doc:     {"document_id","title","page"}
--   meeting: {"meeting_id","title","start_s","end_s"}
CREATE TABLE chunks (
    id           text PRIMARY KEY,                 -- deterministic hash: re-indexing is idempotent
    org_id       uuid NOT NULL,
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type  source_type NOT NULL,
    path         text GENERATED ALWAYS AS (ref->>'path') STORED,
    symbol       text,
    ref          jsonb NOT NULL,
    content      text NOT NULL,
    search_text  text NOT NULL,                    -- identifiers split: validateResource -> validate resource
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
    embedding    vector(768) NOT NULL,
    stale        boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_gin       ON chunks USING gin (tsv);
CREATE INDEX chunks_project_type  ON chunks (project_id, source_type) WHERE NOT stale;
CREATE INDEX chunks_project_path  ON chunks (project_id, path);
CREATE INDEX chunks_symbol        ON chunks (project_id, symbol) WHERE symbol IS NOT NULL;

-- ---------- meetings and KT-to-code links ----------
CREATE TABLE meetings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL,
    project_id      uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title           text NOT NULL,
    livekit_room    text NOT NULL UNIQUE,
    status          meeting_status NOT NULL DEFAULT 'scheduled',
    recording_uri   text,
    duration_s      real,
    consent_shown   boolean NOT NULL DEFAULT true,
    started_at      timestamptz,
    ended_at        timestamptz,
    summary         text,
    created_by      uuid REFERENCES users(id)
);

CREATE TABLE meeting_participants (
    meeting_id  uuid REFERENCES meetings(id) ON DELETE CASCADE,
    user_id     uuid REFERENCES users(id) ON DELETE CASCADE,
    org_id      uuid NOT NULL,
    PRIMARY KEY (meeting_id, user_id)
);

CREATE TABLE transcript_segments (
    id          bigserial PRIMARY KEY,
    org_id      uuid NOT NULL,
    meeting_id  uuid NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start_s     real NOT NULL,
    end_s       real NOT NULL,
    speaker     text NOT NULL,
    text        text NOT NULL                      -- already redacted
);
CREATE INDEX transcript_meeting_time ON transcript_segments (meeting_id, start_s);

CREATE TABLE screen_moments (
    id               bigserial PRIMARY KEY,
    org_id           uuid NOT NULL,
    meeting_id       uuid NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    at_s             real NOT NULL,
    description      text NOT NULL,
    visible_paths    text[] NOT NULL DEFAULT '{}',
    visible_symbols  text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE meeting_notes (
    meeting_id  uuid PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    org_id      uuid NOT NULL,
    decisions   jsonb NOT NULL DEFAULT '[]',
    terms       jsonb NOT NULL DEFAULT '[]',
    result      jsonb NOT NULL                      -- full MeetingResult for the notes page
);

CREATE TABLE action_items (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             uuid NOT NULL,
    meeting_id         uuid NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    text               text NOT NULL,
    owner_hint         text,
    at_s               real,
    converted_task_id  uuid
);

-- The headline feature: which moment of which KT call explained which code.
CREATE TABLE kt_links (
    id                  bigserial PRIMARY KEY,
    org_id              uuid NOT NULL,
    project_id          uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    meeting_id          uuid NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    file_path           text NOT NULL,
    symbol              text,
    chunk_id            text REFERENCES chunks(id) ON DELETE SET NULL,
    start_line          int,
    end_line            int,
    start_s             real NOT NULL,
    end_s               real NOT NULL,
    confidence          real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    method              link_method NOT NULL,
    code_changed_since  boolean NOT NULL DEFAULT false,
    verified_by         uuid REFERENCES users(id),   -- a senior can confirm a weak link
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kt_links_file  ON kt_links (project_id, file_path);
CREATE INDEX kt_links_chunk ON kt_links (chunk_id);

-- ---------- tasks, approvals, reviews ----------
CREATE TABLE tasks (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               uuid NOT NULL,
    onboarding_id        uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    title                text NOT NULL,
    description          text NOT NULL,
    kind                 task_kind NOT NULL,
    status               task_status NOT NULL DEFAULT 'proposed',
    origin               text NOT NULL DEFAULT 'agent',   -- plan | meeting | coverage_gap | first_pr | manager
    due_date             date,
    acceptance_criteria  jsonb NOT NULL DEFAULT '[]',
    related_paths        text[] NOT NULL DEFAULT '{}',
    github_issue_number  int,
    pr_number            int,
    pr_url               text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tasks_onboarding ON tasks (onboarding_id, status);

-- Human-in-the-loop queue. thread_id is the LangGraph thread to resume when a manager decides.
CREATE TABLE approvals (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL,
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        approval_kind NOT NULL,
    thread_id   text NOT NULL,
    payload     jsonb NOT NULL,
    status      approval_status NOT NULL DEFAULT 'pending',
    decided_by  uuid REFERENCES users(id),
    decided_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX approvals_pending ON approvals (project_id) WHERE status = 'pending';

CREATE TABLE reviews (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            uuid NOT NULL,
    task_id           uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    pr_number         int NOT NULL,
    pr_url            text NOT NULL,
    head_sha          text NOT NULL,
    recommendation    recommendation NOT NULL,
    summary           text NOT NULL,
    findings          jsonb NOT NULL DEFAULT '[]',   -- [{file,line,severity,message,evidence,source}]
    questions         jsonb NOT NULL DEFAULT '[]',   -- understanding check
    answers           jsonb,
    dropped_findings  int NOT NULL DEFAULT 0,
    manager_decision  review_decision,
    decided_by        uuid REFERENCES users(id),
    decided_at        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, head_sha)                       -- same commit is never reviewed twice
);

-- ---------- chat and experts ----------
CREATE TABLE chat_messages (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL,
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users(id),
    question    text NOT NULL,
    status      text NOT NULL,                       -- answered | not_enough_information
    answer      text NOT NULL,
    citations   jsonb NOT NULL DEFAULT '[]',
    kt_clips    jsonb NOT NULL DEFAULT '[]',
    cited_chunk_ids text[] NOT NULL DEFAULT '{}',
    confidence  real NOT NULL DEFAULT 0,
    stale       boolean NOT NULL DEFAULT false,      -- cited code changed after the answer
    feedback    smallint,                            -- -1 wrong, 1 helpful
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chat_cited ON chat_messages USING gin (cited_chunk_ids);

CREATE TABLE expert_requests (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            uuid NOT NULL,
    project_id        uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    message_id        uuid REFERENCES chat_messages(id) ON DELETE SET NULL,
    question          text NOT NULL,
    asked_by          uuid REFERENCES users(id),
    expert_id         uuid NOT NULL REFERENCES users(id),
    status            text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'dismissed')),
    answer            text,
    verified_chunk_id text REFERENCES chunks(id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    answered_at       timestamptz
);

-- ---------- operations ----------
CREATE TABLE notifications (
    id          bigserial PRIMARY KEY,
    org_id      uuid NOT NULL,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        text NOT NULL,
    text        text NOT NULL,
    link        text NOT NULL DEFAULT '',
    read_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notifications_unread ON notifications (user_id) WHERE read_at IS NULL;

CREATE TABLE agent_runs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            uuid,
    project_id        uuid,
    agent             text NOT NULL,
    trigger           text NOT NULL,
    status            run_status NOT NULL,
    detail            jsonb NOT NULL DEFAULT '{}',
    langsmith_run_id  text,
    cost_usd          numeric(10, 5),
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_runs_recent ON agent_runs (project_id, created_at DESC);

-- Idempotency: Pub/Sub may deliver twice; a webhook may be retried. Same key = processed once.
CREATE TABLE processed_events (
    dedupe_key    text PRIMARY KEY,
    processed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id          bigserial PRIMARY KEY,
    org_id      uuid,
    actor_id    uuid,
    action      text NOT NULL,          -- approval.decided, review.decided, repo.connected, ...
    target      text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------- row-level security ----------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['users','projects','project_members','onboardings','repositories','documents','chunks',
        'meetings','meeting_participants','transcript_segments','screen_moments','meeting_notes','action_items',
        'kt_links','tasks','approvals','reviews','chat_messages','expert_requests','notifications']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid)$p$, t);
    END LOOP;
END $$;

-- Two narrow lookups that must run before the tenant is known (login, and a worker receiving an event).
CREATE FUNCTION auth_lookup(p_uid text)
RETURNS TABLE (id uuid, org_id uuid, email text, name text, role user_role, github_login text)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS
$$ SELECT id, org_id, email, name, role, github_login FROM users WHERE firebase_uid = p_uid $$;

CREATE FUNCTION project_org(p_project uuid) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path = public AS
$$ SELECT org_id FROM projects WHERE id = p_project $$;

CREATE FUNCTION repo_by_full_name(p_full_name text) RETURNS TABLE (id uuid, org_id uuid, project_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS
$$ SELECT id, org_id, project_id FROM repositories WHERE full_name = p_full_name $$;

-- The application connects as `ww_app` (no BYPASSRLS). Migrations run as the owner.
-- CREATE ROLE ww_app LOGIN PASSWORD '...';  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ww_app;
-- LangGraph's PostgresSaver creates its own checkpoint tables on startup (checkpointer.setup()).
