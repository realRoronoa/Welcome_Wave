-- Welcome Wave - the audit log is append-only for the app
--   * ww_app reads and adds audit entries; it can never change or remove one. A compromised API process, a leaked ww_app
--     password or a query bug cannot rewrite or erase recent history. app/migrate.py grant_app() grants the app role its
--     data privileges on every run and then revokes UPDATE and DELETE on audit_log again; verify() refuses to finish if
--     ww_app holds either.
--   * Entries leave the table in two ways only, both SECURITY DEFINER functions owned by ww_definer:
--       purge_audit_log()   entries older than the retention, never younger than 30 days (012_audit_retention.sql);
--       delete_org_audit()  the current organisation's entries, and only after the organisation row itself was deleted in
--                           the same transaction (DELETE /api/org: app/adapters/lifecycle_pg.py delete_org).
--   * The table keeps its org_id, forced row-level security and tenant policy; nothing else about it changes.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ww_app') THEN
        REVOKE UPDATE, DELETE ON audit_log FROM ww_app;
    END IF;
END $$;

CREATE FUNCTION delete_org_audit(p_org uuid)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$
DECLARE
    removed bigint;
BEGIN
    IF p_org IS NULL OR p_org IS DISTINCT FROM nullif(current_setting('app.org_id', true), '')::uuid THEN
        RAISE EXCEPTION 'delete_org_audit: only the current organisation''s entries';
    END IF;
    IF EXISTS (SELECT 1 FROM organizations WHERE id = p_org) THEN
        RAISE EXCEPTION 'delete_org_audit: the organisation must be deleted first';
    END IF;
    DELETE FROM audit_log WHERE org_id = p_org;
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION delete_org_audit(uuid) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION delete_org_audit(uuid) FROM PUBLIC;
