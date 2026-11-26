-- Welcome Wave - recording consent, removal requests and self-hosted recording retention (docs/SECURITY.md 5 and 8)
--   * meetings: each KT session carries its recording consent: who confirmed that every participant was told the session
--     is recorded and agreed, when, and the version of the recording notice in force at that moment. A recording is not
--     uploaded or imported without it (app/api/uploads.py, app/services/scheduling.py). The first confirmation is kept.
--   * recording_removal_requests: someone on the project asks for a session's recording to be removed. The request goes
--     to the project's managers; a manager removes the recording (DELETE /api/meetings/{id}/recording) or declines, and
--     whoever asked is told. Nothing is removed by the request itself.
--   * stored_recordings(): the sessions that have a stored recording, across organisations, for the self-hosted retention
--     loop (RECORDING_RETENTION_DAYS). Ids, the file's storage path and the status only.
-- The new table carries org_id under forced row-level security, as in 001/010/018; organisation export and deletion find
-- org_id tables themselves.

ALTER TABLE meetings
    ADD COLUMN recording_consent_by      uuid REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN recording_consent_at      timestamptz,
    ADD COLUMN recording_notice_version  text CHECK (recording_notice_version IS NULL OR length(recording_notice_version) <= 40);

CREATE TABLE recording_removal_requests (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL,
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    meeting_id    uuid NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    requested_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    reason        text NOT NULL DEFAULT '' CHECK (length(reason) <= 1000),
    status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'removed', 'declined')),
    decided_by    uuid REFERENCES users(id) ON DELETE SET NULL,
    decided_at    timestamptz,
    note          text NOT NULL DEFAULT '' CHECK (length(note) <= 1000),   -- the manager's words when declining
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recording_removal_requests_meeting ON recording_removal_requests (meeting_id, created_at);
-- one open request per person and session: asking twice changes nothing
CREATE UNIQUE INDEX recording_removal_requests_one_open ON recording_removal_requests (meeting_id, requested_by) WHERE status = 'open';

ALTER TABLE recording_removal_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE recording_removal_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON recording_removal_requests
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid);
-- live updates, as in 009: open KT session pages reload when a request is made or decided
CREATE TRIGGER ww_live AFTER INSERT OR UPDATE OR DELETE ON recording_removal_requests
    FOR EACH ROW EXECUTE FUNCTION ww_live_notify('meetings');

-- ---------- the retention loop's list, across organisations ----------
CREATE FUNCTION stored_recordings()
RETURNS TABLE (id uuid, org_id uuid, project_id uuid, recording_uri text, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT id, org_id, project_id, recording_uri, status::text
     FROM meetings
    WHERE recording_uri IS NOT NULL
    ORDER BY started_at NULLS LAST, id $$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION stored_recordings() OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION stored_recordings() FROM PUBLIC;
