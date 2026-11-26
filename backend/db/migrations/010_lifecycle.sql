-- Welcome Wave - the onboarding lifecycle
--   * programs from versioned templates, 1:1 with onboardings (a side table keeps the agents' `onboardings` unchanged)
--   * the timeline: milestones, access items, acknowledgements, environment checks
--   * progress by area, blocker alerts with escalation, weekly summaries, check-ins, completion
--   * documentation feedback, verified answers, expert preferences, survey responses (no person id), handovers
--   * metric events for the leadership report, API tokens (the public API is deferred), organisation settings
--   * a read-only `executive` role
-- Every table carries org_id under forced row-level security, as in 001/002. Organisation export and deletion
-- (GET /api/org/export, DELETE /api/org) find every org_id table themselves, so later tables are covered too.

ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'executive';

-- ---------- organisation settings and teams ----------
CREATE TABLE org_settings (
    org_id                    uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    setup                     jsonb NOT NULL DEFAULT '{}',          -- {"skipped": [step], "completed": [step]}
    minutes_per_interruption  int NOT NULL DEFAULT 15 CHECK (minutes_per_interruption BETWEEN 1 AND 240),
    demo_day_offset           int NOT NULL DEFAULT 0 CHECK (demo_day_offset >= 0)
);

CREATE TABLE teams (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX teams_project ON teams (project_id);

-- ---------- program templates: a change makes a new version ----------
CREATE TABLE program_templates (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key               text NOT NULL,
    version           int NOT NULL DEFAULT 1 CHECK (version >= 1),
    title             text NOT NULL,
    role_track        text NOT NULL,
    level             text NOT NULL,
    duration_days     int NOT NULL DEFAULT 90 CHECK (duration_days BETWEEN 14 AND 365),
    milestones        jsonb NOT NULL DEFAULT '[]',
    acknowledgements  jsonb NOT NULL DEFAULT '[]',
    access_items      jsonb NOT NULL DEFAULT '[]',
    plan_items        jsonb NOT NULL DEFAULT '[]',   -- the plan drafted when no model is set up
    active            boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, key, version)
);
CREATE UNIQUE INDEX program_templates_one_active ON program_templates (org_id, key) WHERE active;

-- ---------- programs: one per onboarding ----------
CREATE TABLE onboarding_programs (
    onboarding_id           uuid PRIMARY KEY REFERENCES onboardings(id) ON DELETE CASCADE,
    org_id                  uuid NOT NULL,
    project_id              uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    template_id             uuid NOT NULL REFERENCES program_templates(id),
    template_version        int NOT NULL,
    duration_days           int NOT NULL DEFAULT 90,
    mentor_id               uuid REFERENCES users(id) ON DELETE SET NULL,
    team_id                 uuid REFERENCES teams(id) ON DELETE SET NULL,
    focus_area              text,
    plan_approved_at        timestamptz,
    preboarding_token_hash  text UNIQUE,                 -- sha256 of the link's token; the token itself is never stored
    preboarding_expires_at  timestamptz,
    access_granted_at       timestamptz,
    first_task              jsonb,
    survey_days             int[] NOT NULL DEFAULT '{}', -- which surveys the hire answered; the answers carry no person id
    completed_at            timestamptz,
    completion              jsonb,
    created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX onboarding_programs_project ON onboarding_programs (project_id);
CREATE INDEX onboarding_programs_mentor ON onboarding_programs (mentor_id) WHERE mentor_id IS NOT NULL;

CREATE TABLE onboarding_milestones (
    id             bigserial PRIMARY KEY,
    org_id         uuid NOT NULL,
    onboarding_id  uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    key            text NOT NULL,
    title          text NOT NULL,
    due_day        int NOT NULL,
    status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'skipped')),
    done_at        timestamptz,
    evidence       jsonb NOT NULL DEFAULT '{}',
    UNIQUE (onboarding_id, key)
);

CREATE TABLE access_items (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         uuid NOT NULL,
    onboarding_id  uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    system         text NOT NULL CHECK (length(system) BETWEEN 1 AND 120),
    owner_id       uuid REFERENCES users(id) ON DELETE SET NULL,
    status         text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'granted', 'not_needed')),
    requested_at   timestamptz NOT NULL DEFAULT now(),
    granted_at     timestamptz
);
CREATE INDEX access_items_onboarding ON access_items (onboarding_id);
CREATE INDEX access_items_owner ON access_items (owner_id) WHERE status = 'requested';

CREATE TABLE acknowledgements (
    id               bigserial PRIMARY KEY,
    org_id           uuid NOT NULL,
    onboarding_id    uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    doc_key          text NOT NULL,
    title            text NOT NULL,
    acknowledged_at  timestamptz,
    ip_hash          text,                               -- a keyed hash of the address, never the address
    UNIQUE (onboarding_id, doc_key)
);

CREATE TABLE environment_checks (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         uuid NOT NULL,
    onboarding_id  uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    ran_at         timestamptz NOT NULL DEFAULT now(),
    ok             boolean NOT NULL,
    results        jsonb NOT NULL DEFAULT '[]'
);
CREATE INDEX environment_checks_onboarding ON environment_checks (onboarding_id, ran_at);

-- ---------- progress, blockers, summaries ----------
CREATE TABLE area_progress (
    org_id         uuid NOT NULL,
    onboarding_id  uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    area           text NOT NULL,
    evidence       jsonb NOT NULL DEFAULT '{}',
    self_level     text CHECK (self_level IN ('not_started', 'exploring', 'working', 'contributed')),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (onboarding_id, area)
);

CREATE TABLE blocker_alerts (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           uuid NOT NULL,
    onboarding_id    uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    rule             text NOT NULL CHECK (rule IN ('repeated_topic', 'stalled_task', 'failing_ci', 'environment', 'unresolved_review')),
    subject          text NOT NULL,
    evidence         jsonb NOT NULL DEFAULT '{}',
    stage            text NOT NULL DEFAULT 'hire' CHECK (stage IN ('hire', 'mentor', 'manager')),
    notified_stage   text,
    opened_at        timestamptz NOT NULL DEFAULT now(),
    resolved_at      timestamptz,
    resolved_by      text                                -- a user id, or 'system' when the condition cleared
);
-- one open alert per rule and subject: detection runs again and again, and must not open it twice
CREATE UNIQUE INDEX blocker_alerts_one_open ON blocker_alerts (onboarding_id, rule, subject) WHERE resolved_at IS NULL;

CREATE TABLE progress_summaries (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         uuid NOT NULL,
    onboarding_id  uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    week_start     date NOT NULL,
    body           jsonb NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (onboarding_id, week_start)
);

CREATE TABLE checkin_reviews (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         uuid NOT NULL,
    onboarding_id  uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    day            int NOT NULL,
    author_role    text NOT NULL CHECK (author_role IN ('hire', 'manager')),
    author_id      uuid REFERENCES users(id) ON DELETE SET NULL,
    answers        jsonb NOT NULL DEFAULT '{}',
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (onboarding_id, day, author_role)
);

-- No person id: survey answers are reported only in aggregate, for groups of five or more.
CREATE TABLE survey_responses (
    id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id   uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    team_id  uuid REFERENCES teams(id) ON DELETE SET NULL,
    cohort   text NOT NULL,
    day      int NOT NULL,
    answers  jsonb NOT NULL
);
CREATE INDEX survey_responses_team ON survey_responses (org_id, team_id);

-- ---------- knowledge: feedback on documentation, verified answers, expert time ----------
CREATE TABLE documentation_feedback (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL,
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    target_kind  text NOT NULL CHECK (target_kind IN ('doc', 'code', 'meeting', 'answer')),
    target_ref   text NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('unclear', 'outdated')),
    note         text NOT NULL DEFAULT '',
    owner_id     uuid REFERENCES users(id) ON DELETE SET NULL,
    status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
    created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documentation_feedback_queue ON documentation_feedback (project_id, owner_id) WHERE status = 'open';

CREATE TABLE verified_answers (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             uuid NOT NULL,
    project_id         uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    question           text NOT NULL,
    answer             text NOT NULL,
    citations          jsonb NOT NULL DEFAULT '[]',
    verified_by        uuid REFERENCES users(id) ON DELETE SET NULL,
    source_request_id  uuid REFERENCES expert_requests(id) ON DELETE SET NULL,
    reuse_count        int NOT NULL DEFAULT 0,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX verified_answers_project ON verified_answers (project_id);

CREATE TABLE expert_preferences (
    user_id                    uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    org_id                     uuid NOT NULL,
    max_interruptions_per_day  int NOT NULL DEFAULT 3 CHECK (max_interruptions_per_day BETWEEN 0 AND 50),
    office_hours               jsonb NOT NULL DEFAULT '{}',
    digest                     boolean NOT NULL DEFAULT true
);

-- ---------- offboarding ----------
CREATE TABLE handovers (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_day    date NOT NULL,
    plan        jsonb NOT NULL DEFAULT '{}',
    status      text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'in_progress', 'done')),
    created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX handovers_org ON handovers (org_id, status);

-- ---------- reporting and the (deferred) public API ----------
CREATE TABLE metric_events (
    id          bigserial PRIMARY KEY,
    org_id      uuid NOT NULL,
    project_id  uuid,
    kind        text NOT NULL,
    subject_id  text,
    at          timestamptz NOT NULL DEFAULT now(),
    props       jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX metric_events_kind ON metric_events (org_id, kind, at);

CREATE TABLE api_tokens (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name          text NOT NULL,
    scopes        text[] NOT NULL DEFAULT '{}',
    token_hash    text NOT NULL UNIQUE,
    created_by    uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);

-- ---------- row-level security, as in 001 ----------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['org_settings', 'teams', 'program_templates', 'onboarding_programs', 'onboarding_milestones',
        'access_items', 'acknowledgements', 'environment_checks', 'area_progress', 'blocker_alerts', 'progress_summaries',
        'checkin_reviews', 'survey_responses', 'documentation_feedback', 'verified_answers', 'expert_preferences', 'handovers',
        'metric_events', 'api_tokens']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid)$p$, t);
    END LOOP;
END $$;

-- ---------- live updates, as in 009: the web app reloads its lifecycle views ----------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['teams', 'program_templates', 'onboarding_programs', 'onboarding_milestones', 'access_items',
        'acknowledgements', 'environment_checks', 'area_progress', 'blocker_alerts', 'progress_summaries', 'checkin_reviews',
        'documentation_feedback', 'verified_answers', 'handovers']
    LOOP
        EXECUTE format('CREATE TRIGGER ww_live AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION ww_live_notify(%L)',
                       t, 'lifecycle');
    END LOOP;
END $$;

-- ---------- the pre-boarding link, before the tenant is known ----------
-- The portal has no sign-in: the link's token is the permission. Its hash finds the onboarding and its organisation;
-- everything after that runs under row-level security for that organisation.
CREATE FUNCTION preboarding_lookup(p_hash text)
RETURNS TABLE (onboarding_id uuid, org_id uuid, expires_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT onboarding_id, org_id, preboarding_expires_at FROM onboarding_programs WHERE preboarding_token_hash = p_hash $$;

GRANT SELECT ON onboarding_programs TO ww_definer;
CREATE POLICY definer_all ON onboarding_programs TO ww_definer USING (true) WITH CHECK (true);
GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION preboarding_lookup(text) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION preboarding_lookup(text) FROM PUBLIC;
