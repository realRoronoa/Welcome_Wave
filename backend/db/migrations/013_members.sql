-- Welcome Wave - members administration (GET/PATCH /api/org/members, POST .../deactivate and .../reactivate)
--   * users.deactivated_at: set when an admin deactivates a member, cleared when they reactivate them. The row, its history
--     and its project memberships stay; a deactivated member cannot sign in or call the API (app/api/auth.py refuses them)
--     and nobody routes new work to them. `users` keeps its forced row-level security (001); a new column needs no policy.
--   * auth_lookup also returns deactivated_at, so sign-in refuses a deactivated member before the tenant is known. Its
--     result type changes, so it is dropped and created again, then handed back to ww_definer as in 002.
--   * a change to a member (role, status) is announced live like other changes to people (009_live.sql).

ALTER TABLE users ADD COLUMN deactivated_at timestamptz;

DROP FUNCTION auth_lookup(text);

CREATE FUNCTION auth_lookup(p_uid text)
RETURNS TABLE (id uuid, org_id uuid, email text, name text, role user_role, github_login text, deactivated_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT id, org_id, email, name, role, github_login, deactivated_at FROM users WHERE firebase_uid = p_uid $$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION auth_lookup(text) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION auth_lookup(text) FROM PUBLIC;

CREATE TRIGGER ww_live AFTER INSERT OR UPDATE OR DELETE ON users FOR EACH ROW EXECUTE FUNCTION ww_live_notify('people');
