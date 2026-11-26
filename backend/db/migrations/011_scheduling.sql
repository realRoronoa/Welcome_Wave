-- Welcome Wave - scheduling: KT sessions go on Google Calendar (with a Google Meet link) or are booked through Calendly,
-- instead of in-app LiveKit calls; their recordings come back from Google Drive or are uploaded (docs/specs/scheduling.md).
--   * a meeting remembers where it is held: provider ('google' | 'calendly' | 'manual'), the link people join with, its
--     calendar event (Google: the event id; Calendly: the scheduled event's URI), its start and end, and who was invited
--     (attendees: [{"email", "name", "user_id"}]). One meeting per calendar event, so a redelivered webhook makes one.
--   * the LiveKit room becomes a plain internal handle (`room`); the LiveKit webhook's lookup and the Egress id go.
--   * calendar_connections: one Google account per organisation (OAuth). The refresh token is encrypted by the application
--     (TOKEN_ENCRYPTION_KEY) before it is stored; the database never sees it in the clear.
--   * scheduling_links: a person's own Calendly link, opened prefilled when a KT session is booked with them.
--   * meetings_awaiting_recording(): the recording poller's list, across organisations (it runs before a tenant is known).

-- ---------- meetings: where a session is held ----------
DROP FUNCTION meeting_by_room(text);
ALTER TABLE meetings RENAME COLUMN livekit_room TO room;
ALTER TABLE meetings RENAME CONSTRAINT meetings_livekit_room_key TO meetings_room_key;
ALTER TABLE meetings DROP COLUMN egress_id;
ALTER TABLE meetings
    ADD COLUMN provider           text NOT NULL DEFAULT 'manual' CHECK (provider IN ('google', 'calendly', 'manual')),
    ADD COLUMN join_url           text,
    ADD COLUMN calendar_event_id  text,
    ADD COLUMN starts_at          timestamptz,
    ADD COLUMN ends_at            timestamptz,
    ADD COLUMN attendees          jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(attendees) = 'array');
CREATE UNIQUE INDEX meetings_calendar_event ON meetings (org_id, provider, calendar_event_id) WHERE calendar_event_id IS NOT NULL;
-- the poller's scan: scheduled sessions by end time
CREATE INDEX meetings_scheduled_end ON meetings (ends_at) WHERE status = 'scheduled' AND ends_at IS NOT NULL;

-- ---------- an organisation's Google Calendar connection ----------
CREATE TABLE calendar_connections (
    org_id             uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    provider           text NOT NULL CHECK (provider IN ('google')),
    account_email      text NOT NULL DEFAULT '',
    refresh_token_enc  text NOT NULL,           -- Fernet token, encrypted with TOKEN_ENCRYPTION_KEY
    scopes             text NOT NULL DEFAULT '',
    connected_by       uuid REFERENCES users(id) ON DELETE SET NULL,
    connected_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, provider)
);

-- ---------- a person's Calendly link ----------
CREATE TABLE scheduling_links (
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider    text NOT NULL DEFAULT 'calendly' CHECK (provider IN ('calendly')),
    url         text NOT NULL CHECK (url ~ '^https://'),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, provider)
);
CREATE INDEX scheduling_links_org ON scheduling_links (org_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['calendar_connections', 'scheduling_links']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid)$p$, t);
    END LOOP;
END $$;

-- ---------- the recording poller's list, across organisations ----------
-- Scheduled sessions that ended at least p_grace_s ago and are Google sessions (Meet may have attached a recording to the
-- event), or that ended more than p_wait_s ago (no recording came: the poller marks them 'ended'). Ids and times only.
CREATE FUNCTION meetings_awaiting_recording(p_grace_s int, p_wait_s int, p_limit int)
RETURNS TABLE (id uuid, org_id uuid, project_id uuid, provider text, calendar_event_id text, ends_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT id, org_id, project_id, provider, calendar_event_id, ends_at
     FROM meetings
    WHERE status = 'scheduled' AND ends_at < now() - make_interval(secs => p_grace_s)
      AND ((provider = 'google' AND calendar_event_id IS NOT NULL) OR ends_at < now() - make_interval(secs => p_wait_s))
    ORDER BY ends_at
    LIMIT p_limit $$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION meetings_awaiting_recording(int, int, int) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION meetings_awaiting_recording(int, int, int) FROM PUBLIC;
