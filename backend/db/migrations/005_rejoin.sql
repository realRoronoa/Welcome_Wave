-- Welcome Wave - coming back after leaving
--   * someone who left an organisation keeps their user row (their history points at it) with no login bound.
--     Accepting a new invitation binds it again, and the newest invitation sets the role and GitHub login, as it does
--     for a new member: an ex-admin invited back as a new hire is a new hire.

DROP FUNCTION accept_invitations(text, text, text, uuid);

-- As in 003, except that an existing unbound row takes the newest invitation's role (and its name and GitHub login,
-- when the invitation gives them).
CREATE FUNCTION accept_invitations(p_uid text, p_email text, p_name text, p_org uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
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
        UPDATE users SET firebase_uid = p_uid, role = newest.role, name = coalesce(nullif(newest.name, ''), name),
                         github_login = coalesce(newest.github_login, github_login)
         WHERE id = u;
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

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION accept_invitations(text, text, text, uuid) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
REVOKE EXECUTE ON FUNCTION accept_invitations(text, text, text, uuid) FROM PUBLIC;
