-- Welcome Wave - live updates and the self-hosted repository watcher
--   * every change to a table the web app shows is announced on the channel `ww_live`; the API relays it to the signed-in
--     browsers of that organisation (GET /api/live), which reload what they show. The payload names what changed, never
--     its contents: {"o": org id, "t": topic, "p": project id or null, "u": the recipient, for notifications only}.
--     Postgres sends identical payloads of one transaction once, so a bulk update is one message.
--   * self-hosted mode (MODE=self_hosted) reads repositories connected by URL (no GitHub App, so no webhooks) and watches
--     them for pushes and pull requests itself. It lists them, across organisations, through `watched_repositories()`.

CREATE FUNCTION ww_live_notify() RETURNS trigger LANGUAGE plpgsql AS
$$
DECLARE r jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN r := to_jsonb(OLD); ELSE r := to_jsonb(NEW); END IF;
    PERFORM pg_notify('ww_live', json_build_object(
        'o', r ->> 'org_id', 't', TG_ARGV[0], 'p', r ->> 'project_id',
        'u', CASE WHEN TG_ARGV[0] = 'notifications' THEN r ->> 'user_id' END)::text);
    RETURN NULL;
END $$;

DO $$
DECLARE pair text[];
BEGIN
    FOREACH pair SLICE 1 IN ARRAY ARRAY[
        ['notifications', 'notifications'],
        ['approvals', 'approvals'],
        ['tasks', 'tasks'], ['onboardings', 'tasks'], ['reviews', 'tasks'],
        ['meetings', 'meetings'], ['meeting_notes', 'meetings'],
        ['kt_links', 'knowledge'],
        ['repositories', 'sources'], ['documents', 'sources'],
        ['expert_requests', 'inbox'],
        ['invitations', 'people'], ['project_members', 'people'],
        ['projects', 'projects'],
        ['agent_runs', 'runs']
    ] LOOP
        EXECUTE format('CREATE TRIGGER ww_live AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION ww_live_notify(%L)',
                       pair[1], pair[2]);
    END LOOP;
END $$;

CREATE FUNCTION watched_repositories()
RETURNS TABLE (id uuid, org_id uuid, project_id uuid, full_name text, default_branch text, last_indexed_sha text, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT id, org_id, project_id, full_name, default_branch, last_indexed_sha, status::text
     FROM repositories WHERE installation_id IS NULL ORDER BY created_at $$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION watched_repositories() OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION watched_repositories() FROM PUBLIC;
