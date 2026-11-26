-- Welcome Wave - audit log reads and retention
--   * GET /api/audit pages through one organisation's entries, newest first: an index for that order.
--   * Entries older than AUDIT_RETENTION_DAYS are deleted by POST /internal/audit/purge (Cloud Scheduler) or the self-hosted
--     loop. The app role sees one organisation at a time, so the purge is a SECURITY DEFINER function owned by ww_definer
--     (see 002_production.sql). It never deletes anything younger than 30 days, whatever cut-off it is given: an app that
--     is compromised cannot use it to erase recent history.

CREATE INDEX IF NOT EXISTS audit_log_org_recent ON audit_log (org_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS audit_log_created ON audit_log (created_at);

GRANT DELETE ON audit_log TO ww_definer;

CREATE FUNCTION purge_audit_log(p_before timestamptz)
RETURNS TABLE (out_org uuid, removed bigint)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$
    WITH gone AS (
        DELETE FROM audit_log a
         WHERE a.created_at < least(p_before, now() - interval '30 days')
        RETURNING a.org_id
    )
    SELECT gone.org_id, count(*) FROM gone GROUP BY gone.org_id
$$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION purge_audit_log(timestamptz) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION purge_audit_log(timestamptz) FROM PUBLIC;
