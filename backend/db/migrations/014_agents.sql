-- Welcome Wave - the multi-agent model (docs/AGENTS.md)
--   * approvals.kind gains the proposals of the new specialists: 'rerecord' (the Keeper: a KT explanation is possibly
--     outdated, re-record it with its speaker), 'doc_edit' (the Keeper: drafted wording for a document or file people
--     flagged; never written to a repository) and 'template_change' (the Planner: a program template change drawn from
--     completion data). Each pauses its Supervisor run until a manager decides, like task_plan and review.
--   * org_settings.agent_modes: per organisation, which agents write their text with the AI ("ai", the default) or with
--     deterministic rules ("rules"). PATCH /api/agents/{key}; audited.
--   * onboarding_programs.completion_draft: the Planner's drafted sign-off note (decision, note, the criteria it saw),
--     shown with the completion review until a manager signs off.
--   * doc_edits: approved doc_edit proposals. For an uploaded document, an accepted edit shown with it; for code, a KT call or
--     a verified answer, a suggestion for its owner. org_id with forced row-level security, as every table.
--   * expert_requests.draft: the Guide's AI draft answer, attached to a question handed to a person (their inbox shows it).
--   * lifecycle_orgs(): the daily tick (POST /internal/lifecycle/tick, the self-hosted loop) runs per organisation and has to
--     find them before any tenant is set: a SECURITY DEFINER function owned by ww_definer that returns ids only.
-- ALTER TYPE ... ADD VALUE runs inside this migration's transaction (PostgreSQL 12+); nothing here uses the new values.

ALTER TYPE approval_kind ADD VALUE IF NOT EXISTS 'rerecord';
ALTER TYPE approval_kind ADD VALUE IF NOT EXISTS 'doc_edit';
ALTER TYPE approval_kind ADD VALUE IF NOT EXISTS 'template_change';

ALTER TABLE org_settings ADD COLUMN agent_modes jsonb NOT NULL DEFAULT '{}';
-- the Guide's AI draft travels with a question handed to a person, so their inbox can show it (it was only in a metric)
ALTER TABLE expert_requests ADD COLUMN draft text CHECK (draft IS NULL OR length(draft) <= 4000);
ALTER TABLE onboarding_programs ADD COLUMN completion_draft jsonb;

CREATE TABLE doc_edits (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    project_id       uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    approval_id      uuid REFERENCES approvals(id) ON DELETE SET NULL,
    target_kind      text NOT NULL CHECK (target_kind IN ('doc', 'code', 'meeting', 'answer')),
    target_ref       text NOT NULL CHECK (length(target_ref) BETWEEN 1 AND 500),
    title            text NOT NULL CHECK (length(title) <= 300),
    proposed_text    text NOT NULL CHECK (length(proposed_text) BETWEEN 1 AND 8000),
    current_excerpt  text NOT NULL DEFAULT '' CHECK (length(current_excerpt) <= 8000),
    feedback_ids     jsonb NOT NULL DEFAULT '[]',
    status           text NOT NULL DEFAULT 'suggested' CHECK (status IN ('accepted', 'suggested')),
    owner_id         uuid REFERENCES users(id) ON DELETE SET NULL,
    accepted_by      uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX doc_edits_target ON doc_edits (project_id, target_kind, target_ref, created_at DESC);
CREATE UNIQUE INDEX doc_edits_one_per_approval ON doc_edits (approval_id) WHERE approval_id IS NOT NULL;

ALTER TABLE doc_edits ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_edits FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON doc_edits
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
CREATE TRIGGER ww_live AFTER INSERT OR UPDATE OR DELETE ON doc_edits FOR EACH ROW EXECUTE FUNCTION ww_live_notify('lifecycle');

-- ---------- the daily tick, before the tenant is known ----------
CREATE FUNCTION lifecycle_orgs()
RETURNS TABLE (org_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT DISTINCT p.org_id FROM onboarding_programs p WHERE p.completed_at IS NULL ORDER BY p.org_id $$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION lifecycle_orgs() OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION lifecycle_orgs() FROM PUBLIC;
