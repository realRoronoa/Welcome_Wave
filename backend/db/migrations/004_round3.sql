-- Welcome Wave - third review round
--   * definer functions never resolve a table through the caller's temporary schema
--   * one active onboarding per person and project
--   * a transactional outbox, so an event accepted by the API is published even if Pub/Sub is briefly down
--   * a manager's first decision is stored with the claim; an event's lease length is stored with the claim
--   * the invitation list says who invited the person and how old the organisation is

-- ---------- definer functions: pg_temp last ----------
-- Unless pg_temp is named in search_path, PostgreSQL searches the caller's temporary schema FIRST for tables. The
-- application role could then create a temp table called `invitations` and have accept_invitations (running as
-- ww_definer, which sees every tenant) read it instead of the real one. Naming pg_temp last closes that for every
-- SECURITY DEFINER function, and the migrator refuses to finish if one is ever added without it.
DO $$
DECLARE f regprocedure;
BEGIN
    FOR f IN SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.prosecdef
    LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = public, pg_temp', f);
    END LOOP;
END $$;

-- ---------- idempotency: the lease is stored, so its status can be read without knowing the worker's setting ----------
ALTER TABLE processed_events ADD COLUMN lease_s int NOT NULL DEFAULT 300;

-- ---------- approvals: the decision the claim was made for ----------
ALTER TABLE approvals ADD COLUMN decision jsonb;

-- ---------- one active onboarding per person and project ----------
-- Earlier duplicates (a double click before this index) keep the first one active; the rest are paused.
ALTER TABLE onboardings NO FORCE ROW LEVEL SECURITY;
UPDATE onboardings o SET status = 'paused'
 WHERE o.status = 'active' AND EXISTS (SELECT 1 FROM onboardings k WHERE k.project_id = o.project_id AND k.new_hire_id = o.new_hire_id
                                          AND k.status = 'active' AND (k.created_at, k.id) < (o.created_at, o.id));
ALTER TABLE onboardings FORCE ROW LEVEL SECURITY;
CREATE UNIQUE INDEX onboardings_one_active ON onboardings (project_id, new_hire_id) WHERE status = 'active';

-- ---------- outbox ----------
-- Events the API publishes. Written before the publish and marked sent after it; the scheduled flush publishes the rest.
-- A new row waits a minute before a flush may take it, so the request's own publish attempt is not raced.
-- Not tenant data (the events carry ids only), so no org_id and no row-level security; the migrator grants it to ww_app.
CREATE TABLE outbox (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic            text NOT NULL,
    event            jsonb NOT NULL,
    dedupe           text UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    next_attempt_at  timestamptz NOT NULL DEFAULT now() + interval '1 minute',
    attempts         int NOT NULL DEFAULT 0,
    sent_at          timestamptz,
    last_error       text
);
CREATE INDEX outbox_due ON outbox (next_attempt_at) WHERE sent_at IS NULL;

-- ---------- invitations: who invited you, and since when the organisation exists ----------
DROP FUNCTION pending_invitations(text);
CREATE FUNCTION pending_invitations(p_email text)
RETURNS TABLE (org_id uuid, org_name text, role user_role, invited_by_name text, invited_by_email text, org_created_at date, projects text[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$
    SELECT DISTINCT ON (i.org_id) i.org_id, o.name, i.role, u.name, u.email, o.created_at::date,
           ARRAY(SELECT p.name FROM invitations j JOIN projects p ON p.id = j.project_id
                 WHERE j.org_id = i.org_id AND j.email = lower(p_email) AND j.accepted_at IS NULL AND j.expires_at > now()
                 ORDER BY p.name)
      FROM invitations i JOIN organizations o ON o.id = i.org_id LEFT JOIN users u ON u.id = i.invited_by
     WHERE i.email = lower(p_email) AND i.accepted_at IS NULL AND i.expires_at > now()
     ORDER BY i.org_id, i.created_at DESC
$$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION pending_invitations(text) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION pending_invitations(text) FROM PUBLIC;
