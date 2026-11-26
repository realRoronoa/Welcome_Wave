-- Welcome Wave - legal acceptances (app/legal.py, app/api/legal.py, docs/specs/legal-consent.md)
-- Who accepted which version of which document, and when. One row is one acceptance and is evidence, so the table is
-- append-only for the app, like the audit log (015_audit_append_only.sql):
--   * legal_acceptances: a person's acceptance of the terms of service ('terms') and the privacy policy ('privacy'), their
--     acknowledgement of the recording notice ('recording'), and the organisation's acceptance of the data-processing
--     agreement ('dpa': one row per organisation and version, naming the admin who accepted it).
--   * It names people by id only. No name and no email address is copied in, so erasing a person (POST /api/org/people/erase)
--     leaves the row standing under the erased identity, as it leaves their onboarding records: the organisation can still
--     show that an acceptance happened and when, and no longer who it was.
--   * ip_prefix is the network the acceptance came from, never the full address (IPv4 cut to /24, IPv6 to /48), and
--     user_agent is the browser's own description cut to 200 characters. Both may be NULL.
--   * No foreign keys: organisation deletion empties the tables in any order (app/adapters/lifecycle_pg.py delete_org), and
--     the rows of a deleted organisation go last, through delete_org_legal().
--   * ww_app reads and adds rows; it can never change or remove one. app/migrate.py revokes UPDATE, DELETE and TRUNCATE on
--     every run (APPEND_ONLY_TABLES) and refuses to finish if ww_app holds any of them.
--   * Rows leave the table in one way only: delete_org_legal(), a SECURITY DEFINER function owned by ww_definer, removes the
--     current organisation's rows and only after the organisation row itself was deleted in the same transaction
--     (DELETE /api/org).
-- The table carries org_id under forced row-level security with the tenant policy, as every organisation table does;
-- organisation export finds org_id tables itself.

CREATE TABLE legal_acceptances (
    id           bigserial PRIMARY KEY,
    org_id       uuid NOT NULL,
    user_id      uuid NOT NULL,          -- who accepted; for 'dpa', the admin who accepted for the organisation
    document     text NOT NULL CHECK (document IN ('terms', 'privacy', 'recording', 'dpa')),
    version      text NOT NULL CHECK (version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    method       text NOT NULL CHECK (method IN ('signup', 'invitation', 'prompt', 'organisation')),
    accepted_at  timestamptz NOT NULL DEFAULT now(),
    ip_prefix    text CHECK (ip_prefix IS NULL OR length(ip_prefix) <= 64),
    user_agent   text CHECK (user_agent IS NULL OR length(user_agent) <= 200)
);
-- one acceptance per person, document and version; one data-processing agreement per organisation and version
CREATE UNIQUE INDEX legal_acceptances_person ON legal_acceptances (user_id, document, version) WHERE document <> 'dpa';
CREATE UNIQUE INDEX legal_acceptances_dpa ON legal_acceptances (org_id, version) WHERE document = 'dpa';
CREATE INDEX legal_acceptances_org ON legal_acceptances (org_id, accepted_at DESC, id DESC);

ALTER TABLE legal_acceptances ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_acceptances FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON legal_acceptances
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
-- the definer role sees every tenant (002_production.sql), and may delete here only through the function below
CREATE POLICY definer_all ON legal_acceptances TO ww_definer USING (true) WITH CHECK (true);
GRANT SELECT, DELETE ON legal_acceptances TO ww_definer;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ww_app') THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON legal_acceptances FROM ww_app;
    END IF;
END $$;

CREATE FUNCTION delete_org_legal(p_org uuid)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$
DECLARE
    removed bigint;
BEGIN
    IF p_org IS NULL OR p_org IS DISTINCT FROM nullif(current_setting('app.org_id', true), '')::uuid THEN
        RAISE EXCEPTION 'delete_org_legal: only the current organisation''s rows';
    END IF;
    IF EXISTS (SELECT 1 FROM organizations WHERE id = p_org) THEN
        RAISE EXCEPTION 'delete_org_legal: the organisation must be deleted first';
    END IF;
    DELETE FROM legal_acceptances WHERE org_id = p_org;
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION delete_org_legal(uuid) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION delete_org_legal(uuid) FROM PUBLIC;
