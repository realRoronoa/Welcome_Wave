-- Welcome Wave - fixes from the production review
--   * invitations are accepted explicitly, per organisation, and only live (unexpired) invitations count
--   * GitHub App installations are bound only by the person GitHub says installed them
--   * a project has exactly one repository

-- ---------- invitations ----------
DROP FUNCTION accept_invitations(text, text, text);

-- What a signed-in person without an account has been invited to: one row per organisation, newest live invitation's role.
CREATE FUNCTION pending_invitations(p_email text)
RETURNS TABLE (org_id uuid, org_name text, role user_role, invited_by_name text, projects text[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$
    SELECT DISTINCT ON (i.org_id) i.org_id, o.name, i.role, u.name,
           ARRAY(SELECT p.name FROM invitations j JOIN projects p ON p.id = j.project_id
                 WHERE j.org_id = i.org_id AND j.email = lower(p_email) AND j.accepted_at IS NULL AND j.expires_at > now()
                 ORDER BY p.name)
      FROM invitations i JOIN organizations o ON o.id = i.org_id LEFT JOIN users u ON u.id = i.invited_by
     WHERE i.email = lower(p_email) AND i.accepted_at IS NULL AND i.expires_at > now()
     ORDER BY i.org_id, i.created_at DESC
$$;

-- The person chose `p_org`. Link the login to a user row there (creating it with the newest live invitation's role),
-- join every project they were invited to in that organisation, and mark those invitations accepted.
-- Returns NULL if there is no live invitation, or if the email's user row is already bound to a different login.
CREATE FUNCTION accept_invitations(p_uid text, p_email text, p_name text, p_org uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS
$$
DECLARE inv record; newest record; u uuid; bound text;
BEGIN
    IF EXISTS (SELECT 1 FROM users WHERE firebase_uid = p_uid) THEN
        RAISE EXCEPTION 'login already has an account' USING ERRCODE = 'unique_violation';
    END IF;
    SELECT * INTO newest FROM invitations
     WHERE org_id = p_org AND email = lower(p_email) AND accepted_at IS NULL AND expires_at > now()
     ORDER BY created_at DESC LIMIT 1;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT id, firebase_uid INTO u, bound FROM users WHERE org_id = p_org AND lower(email) = lower(p_email);
    IF u IS NULL THEN
        INSERT INTO users (org_id, firebase_uid, email, name, role, github_login)
        VALUES (p_org, p_uid, lower(p_email), coalesce(nullif(newest.name, ''), nullif(p_name, ''), split_part(p_email, '@', 1)),
                newest.role, newest.github_login)
        RETURNING id INTO u;
    ELSIF bound IS NOT NULL AND bound <> p_uid THEN
        RETURN NULL;
    ELSE
        UPDATE users SET firebase_uid = p_uid WHERE id = u;
    END IF;
    FOR inv IN SELECT * FROM invitations WHERE org_id = p_org AND email = lower(p_email) AND accepted_at IS NULL AND expires_at > now()
    LOOP
        IF inv.project_id IS NOT NULL THEN
            INSERT INTO project_members (project_id, user_id, org_id) VALUES (inv.project_id, u, p_org) ON CONFLICT DO NOTHING;
        END IF;
        UPDATE invitations SET accepted_at = now() WHERE id = inv.id;
    END LOOP;
    INSERT INTO audit_log (org_id, actor_id, action, target) VALUES (p_org, u, 'invitation.accepted', lower(p_email));
    RETURN u;
END $$;

-- ---------- one repository per project ----------
-- The index, the code view and the coverage map are per project. Enforced here, not only in the API, so two
-- concurrent connects (or a case variant of the same name) cannot attach a second repository.
-- 001 allowed several per project, so existing rows are reconciled first:
--   * case variants of one repository collapse into one row (the indexed one, else the newest);
--   * every further repository moves to a new project of its own in the same organisation, with the same members.
-- Both projects' code index mixed the two repositories, so it is dropped and their repositories are marked 'failed'
-- ('pending' would show as queued, with Re-index disabled, for an event that never comes).
-- FORCE ROW LEVEL SECURITY applies to the owner too: it is lifted while the migrator fixes every tenant's rows.
ALTER TABLE repositories NO FORCE ROW LEVEL SECURITY;
ALTER TABLE projects NO FORCE ROW LEVEL SECURITY;
ALTER TABLE project_members NO FORCE ROW LEVEL SECURITY;
ALTER TABLE chunks NO FORCE ROW LEVEL SECURITY;

DELETE FROM repositories r USING repositories keep
 WHERE keep.project_id = r.project_id AND lower(keep.full_name) = lower(r.full_name) AND keep.id <> r.id
   AND (keep.last_indexed_sha IS NOT NULL, keep.created_at, keep.id) > (r.last_indexed_sha IS NOT NULL, r.created_at, r.id);

DO $$
DECLARE r record; moved uuid;
BEGIN
    FOR r IN SELECT x.id, x.org_id, x.project_id, x.full_name, p.name AS project_name
               FROM repositories x JOIN projects p ON p.id = x.project_id
              WHERE EXISTS (SELECT 1 FROM repositories k WHERE k.project_id = x.project_id AND (k.created_at, k.id) < (x.created_at, x.id))
    LOOP
        INSERT INTO projects (org_id, name) VALUES (r.org_id, r.project_name || ' - ' || r.full_name) RETURNING id INTO moved;
        INSERT INTO project_members (project_id, user_id, org_id) SELECT moved, user_id, org_id FROM project_members WHERE project_id = r.project_id;
        DELETE FROM chunks WHERE project_id = r.project_id AND source_type = 'code';
        UPDATE repositories SET status = 'failed', last_indexed_sha = NULL WHERE project_id = r.project_id;
        UPDATE repositories SET project_id = moved WHERE id = r.id;
        RAISE NOTICE 'repository % moved from project % to new project %: re-index both', r.full_name, r.project_id, moved;
    END LOOP;
END $$;

ALTER TABLE repositories FORCE ROW LEVEL SECURITY;
ALTER TABLE projects FORCE ROW LEVEL SECURITY;
ALTER TABLE project_members FORCE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;
CREATE UNIQUE INDEX repositories_one_per_project ON repositories (project_id);

-- ---------- GitHub App installations ----------
-- GitHub's signed `installation.created` webhook says which GitHub user installed the app. Binding an installation to an
-- organisation requires the caller's OAuth identity to be that user: seeing one repository is not enough.
CREATE TABLE github_installation_senders (
    installation_id  text PRIMARY KEY,
    sender_id        bigint NOT NULL,
    sender_login     text NOT NULL,
    account_login    text NOT NULL DEFAULT '',
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION unbind_installation(p_installation text) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public AS
$$
    DELETE FROM github_installations WHERE installation_id = p_installation;
    DELETE FROM github_installation_senders WHERE installation_id = p_installation;
$$;

-- ---------- ownership and grants, as in 002 ----------
GRANT SELECT, INSERT, UPDATE, DELETE ON github_installation_senders TO ww_definer;
GRANT DELETE ON github_installations TO ww_definer;
GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION pending_invitations(text) OWNER TO ww_definer;
ALTER FUNCTION accept_invitations(text, text, text, uuid) OWNER TO ww_definer;
ALTER FUNCTION unbind_installation(text) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION pending_invitations(text), accept_invitations(text, text, text, uuid), unbind_installation(text) FROM PUBLIC;
