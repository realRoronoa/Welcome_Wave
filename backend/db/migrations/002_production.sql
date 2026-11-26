-- Welcome Wave - production hardening
--   * lease-based idempotency (a crashed worker never loses an event)
--   * approval claims (two clicks never resume the same paused run twice)
--   * self-serve sign-up, invitations, GitHub App installations bound to one organisation
--   * row-level security on the tables 001 left open
--   * narrow SECURITY DEFINER lookups for the moments before the tenant is known

-- ---------- idempotency with leases ----------
-- 001 marked an event "seen" before handling it, so a worker that crashed mid-job made the retry a no-op.
-- Now a handler claims the key ('processing'), heartbeats while it works, and marks it 'done' only on success.
ALTER TABLE processed_events ADD COLUMN status text NOT NULL DEFAULT 'done' CHECK (status IN ('processing', 'done'));
ALTER TABLE processed_events ADD COLUMN claimed_at timestamptz NOT NULL DEFAULT now();

-- ---------- approvals ----------
ALTER TABLE approvals ADD COLUMN claimed_at timestamptz;

-- ---------- people ----------
CREATE TABLE invitations (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    project_id    uuid REFERENCES projects(id) ON DELETE CASCADE,
    email         text NOT NULL CHECK (email = lower(email)),
    role          user_role NOT NULL,
    name          text,
    github_login  text,
    invited_by    uuid REFERENCES users(id) ON DELETE SET NULL,
    accepted_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL DEFAULT now() + interval '14 days'
);
CREATE UNIQUE INDEX invitations_open ON invitations (org_id, email, project_id) WHERE accepted_at IS NULL;
CREATE INDEX invitations_email ON invitations (email) WHERE accepted_at IS NULL;

CREATE UNIQUE INDEX users_email_lower ON users (org_id, lower(email));

-- ---------- GitHub App installations ----------
-- An installation belongs to exactly one organisation. The binding is only made after the installing
-- user's own GitHub token proves they can see the installation (OAuth during install), so a tenant
-- cannot connect someone else's private code by guessing an installation id.
CREATE TABLE github_installations (
    installation_id  text PRIMARY KEY,
    org_id           uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    account_login    text NOT NULL DEFAULT '',
    connected_by     uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE documents ADD COLUMN status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'failed'));
ALTER TABLE meetings ADD COLUMN egress_id text;

-- ---------- row-level security for the remaining tables ----------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['invitations', 'github_installations']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid)$p$, t);
    END LOOP;
    -- operational tables: a tenant reads only its own rows; a row written before the tenant is known may have no org
    FOREACH t IN ARRAY ARRAY['agent_runs', 'audit_log']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id IS NULL OR org_id = nullif(current_setting('app.org_id', true), '')::uuid)$p$, t);
    END LOOP;
END $$;

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON organizations
    USING (id = nullif(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (id = nullif(current_setting('app.org_id', true), '')::uuid);

-- ---------- lookups that run before the tenant is known ----------
CREATE FUNCTION meeting_by_room(p_room text)
RETURNS TABLE (id uuid, org_id uuid, project_id uuid, status meeting_status)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT id, org_id, project_id, status FROM meetings WHERE livekit_room = p_room $$;

DROP FUNCTION repo_by_full_name(text);
CREATE FUNCTION repo_by_full_name(p_full_name text)
RETURNS TABLE (id uuid, org_id uuid, project_id uuid, installation_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT id, org_id, project_id, installation_id FROM repositories WHERE lower(full_name) = lower(p_full_name) $$;

CREATE FUNCTION installation_org(p_installation text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT org_id FROM github_installations WHERE installation_id = p_installation $$;

-- Bind an installation to an organisation. Returns false if another organisation already owns it.
CREATE FUNCTION bind_installation(p_installation text, p_org uuid, p_account text, p_user uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS
$$
DECLARE owner uuid;
BEGIN
    INSERT INTO github_installations (installation_id, org_id, account_login, connected_by)
    VALUES (p_installation, p_org, p_account, p_user)
    ON CONFLICT (installation_id) DO NOTHING;
    SELECT org_id INTO owner FROM github_installations WHERE installation_id = p_installation;
    RETURN owner = p_org;
END $$;

-- Self-serve sign-up: a verified login with no account creates an organisation, its first project,
-- and becomes its admin. Refuses if the login already belongs to someone.
CREATE FUNCTION create_organization(p_uid text, p_email text, p_name text, p_org text, p_project text)
RETURNS TABLE (user_id uuid, org_id uuid, project_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS
$$
DECLARE o uuid; u uuid; p uuid;
BEGIN
    IF EXISTS (SELECT 1 FROM users WHERE firebase_uid = p_uid) THEN
        RAISE EXCEPTION 'login already has an account' USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO organizations (name) VALUES (p_org) RETURNING id INTO o;
    INSERT INTO users (org_id, firebase_uid, email, name, role) VALUES (o, p_uid, lower(p_email), p_name, 'admin') RETURNING id INTO u;
    INSERT INTO projects (org_id, name) VALUES (o, p_project) RETURNING id INTO p;
    INSERT INTO project_members (project_id, user_id, org_id) VALUES (p, u, o);
    INSERT INTO audit_log (org_id, actor_id, action, target) VALUES (o, u, 'organization.created', o::text);
    RETURN QUERY SELECT u, o, p;
END $$;

-- First sign-in of an invited person: link the login to a user row (creating it if needed), join every
-- project they were invited to, and mark the invitations accepted. Email must already be verified by the caller.
CREATE FUNCTION accept_invitations(p_uid text, p_email text, p_name text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS
$$
DECLARE inv record; u uuid; first_org uuid;
BEGIN
    SELECT i.org_id INTO first_org FROM invitations i
     WHERE i.email = lower(p_email) AND i.accepted_at IS NULL AND i.expires_at > now()
     ORDER BY i.created_at LIMIT 1;
    IF first_org IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT id INTO u FROM users WHERE org_id = first_org AND lower(email) = lower(p_email);
    IF u IS NULL THEN
        INSERT INTO users (org_id, firebase_uid, email, name, role, github_login)
        SELECT first_org, p_uid, lower(p_email), coalesce(nullif(i.name, ''), nullif(p_name, ''), split_part(p_email, '@', 1)), i.role, i.github_login
          FROM invitations i WHERE i.org_id = first_org AND i.email = lower(p_email) AND i.accepted_at IS NULL
         ORDER BY i.created_at LIMIT 1
        RETURNING id INTO u;
    ELSE
        UPDATE users SET firebase_uid = p_uid WHERE id = u AND firebase_uid IS NULL;
    END IF;
    FOR inv IN SELECT * FROM invitations WHERE org_id = first_org AND email = lower(p_email) AND accepted_at IS NULL AND expires_at > now()
    LOOP
        IF inv.project_id IS NOT NULL THEN
            INSERT INTO project_members (project_id, user_id, org_id) VALUES (inv.project_id, u, first_org) ON CONFLICT DO NOTHING;
        END IF;
        UPDATE invitations SET accepted_at = now() WHERE id = inv.id;
    END LOOP;
    INSERT INTO audit_log (org_id, actor_id, action, target) VALUES (first_org, u, 'invitation.accepted', lower(p_email));
    RETURN u;
END $$;

-- ---------- who the definer functions run as ----------
-- FORCE ROW LEVEL SECURITY applies to the table owner too, and on Cloud SQL the owner is not a superuser,
-- so a SECURITY DEFINER function owned by it would see no rows before the tenant is set. The functions are
-- owned by `ww_definer` instead: a role that cannot log in, whose own policy lets it see every tenant.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ww_definer') THEN
        CREATE ROLE ww_definer NOLOGIN;
    END IF;
END $$;
GRANT ww_definer TO CURRENT_USER;
GRANT USAGE ON SCHEMA public TO ww_definer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO ww_definer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ww_definer;

DO $$
DECLARE t text;
BEGIN
    FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
    LOOP
        EXECUTE format('CREATE POLICY definer_all ON %I TO ww_definer USING (true) WITH CHECK (true)', t);
    END LOOP;
END $$;

GRANT CREATE ON SCHEMA public TO ww_definer;  -- needed only while ownership changes hands
ALTER FUNCTION auth_lookup(text) OWNER TO ww_definer;
ALTER FUNCTION project_org(uuid) OWNER TO ww_definer;
ALTER FUNCTION repo_by_full_name(text) OWNER TO ww_definer;
ALTER FUNCTION meeting_by_room(text) OWNER TO ww_definer;
ALTER FUNCTION installation_org(text) OWNER TO ww_definer;
ALTER FUNCTION bind_installation(text, uuid, text, uuid) OWNER TO ww_definer;
ALTER FUNCTION create_organization(text, text, text, text, text) OWNER TO ww_definer;
ALTER FUNCTION accept_invitations(text, text, text) OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;

-- Only the application role may call them (the migrator grants EXECUTE to ww_app).
REVOKE EXECUTE ON FUNCTION auth_lookup(text), project_org(uuid), repo_by_full_name(text), meeting_by_room(text),
    installation_org(text), bind_installation(text, uuid, text, uuid), create_organization(text, text, text, text, text),
    accept_invitations(text, text, text) FROM PUBLIC;
