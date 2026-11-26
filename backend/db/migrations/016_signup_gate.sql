-- Welcome Wave - the self-hosted sign-up gate (docs/SECURITY.md)
--   * organization_exists(): whether any organisation exists, across tenants. A self-hosted server is one organisation unless
--     its operator sets SIGNUP_ENABLED=true: POST /api/signup creates the first one, and once it exists people join by
--     invitation (app/api/routes.py _signup_allowed). The question is asked before any tenant is set, and row-level security
--     hides every organisation then, so it is a SECURITY DEFINER function owned by ww_definer.
--     It returns one boolean and nothing about any organisation, so EXECUTE stays with PUBLIC (the app role needs no grant
--     from app/migrate.py).

CREATE FUNCTION organization_exists()
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT EXISTS (SELECT 1 FROM organizations) $$;

GRANT CREATE ON SCHEMA public TO ww_definer;
ALTER FUNCTION organization_exists() OWNER TO ww_definer;
REVOKE CREATE ON SCHEMA public FROM ww_definer;
