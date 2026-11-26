-- Live voice sessions are scoped to the same organisation and project as the existing onboarding data.
-- Agora/Murf credentials and RTC tokens are never stored here. Tool bearer tokens are signed and expire.
CREATE TABLE voice_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    onboarding_id uuid REFERENCES onboardings(id) ON DELETE SET NULL,
    agora_session_id text UNIQUE,
    rtc_channel text NOT NULL UNIQUE,
    rtc_uid bigint NOT NULL,
    status text NOT NULL DEFAULT 'starting' CHECK (status IN ('starting', 'active', 'ended', 'failed')),
    transcript jsonb NOT NULL DEFAULT '[]',
    sources jsonb NOT NULL DEFAULT '[]',
    summary jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz
);
CREATE INDEX voice_sessions_user ON voice_sessions (user_id, created_at DESC);
CREATE INDEX voice_sessions_onboarding ON voice_sessions (onboarding_id, created_at DESC);
ALTER TABLE voice_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE voice_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON voice_sessions
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);

CREATE TABLE voice_actions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    voice_session_id uuid NOT NULL REFERENCES voice_sessions(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('access_request', 'kt_request', 'onboarding_task')),
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'completed', 'failed', 'declined')),
    result jsonb NOT NULL DEFAULT '{}',
    tool_call_id text NOT NULL CHECK (length(tool_call_id) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    decided_by uuid REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (voice_session_id, tool_call_id)
);
CREATE INDEX voice_actions_session ON voice_actions (voice_session_id, created_at);
ALTER TABLE voice_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE voice_actions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON voice_actions
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
