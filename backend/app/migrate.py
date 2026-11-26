"""Database migrations. Runs as the owner role, once per deploy (Cloud Run job `ww-migrate`):

    DATABASE_URL_OWNER=postgresql://ww_owner:...@/welcomewave?host=/cloudsql/...  \
    APP_DB_PASSWORD=...  python -m app.migrate

1. Applies every db/migrations/*.sql not yet recorded in schema_migrations, each in its own transaction.
2. Creates or updates the `ww_app` login role the services use: NOBYPASSRLS, data privileges only, no temporary tables.
3. Creates LangGraph's checkpoint tables (the app role cannot create tables) and grants them to ww_app.
4. Refuses to finish if ww_app could bypass row-level security, a table is neither tenant-protected (org_id, forced row-level
   security and a policy) nor listed in GLOBAL_TABLES, or a SECURITY DEFINER function could be pointed at a temp table.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

log = logging.getLogger("migrate")
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
APP_ROLE = "ww_app"
DEFINER_FUNCTIONS = [
    "auth_lookup(text)", "project_org(uuid)", "repo_by_full_name(text)", "meetings_awaiting_recording(int, int, int)", "installation_org(text)",
    "bind_installation(text, uuid, text, uuid)", "create_organization(text, text, text, text, text)",
    "accept_invitations(text, text, text, uuid)", "pending_invitations(text)", "unbind_installation(text)",
    "watched_repositories()",
    "purge_audit_log(timestamptz)",
    "preboarding_lookup(text)",
    "lifecycle_orgs()",
]


def plain_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    conn.commit()
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    conn.commit()  # end the read's implicit transaction, or every file below would be a savepoint of one big transaction
    applied = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.stem in done:
            continue
        log.info("applying %s", path.name)
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        applied.append(path.stem)
    return applied


def ensure_app_role(conn: psycopg.Connection, password: str | None) -> None:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
    with conn.transaction():
        if not exists:
            if not password:
                raise SystemExit(f"Role {APP_ROLE} does not exist; set APP_DB_PASSWORD so the migrator can create it.")
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                sql.Identifier(APP_ROLE), sql.Literal(password)))
        elif password:
            # Only a superuser may name BYPASSRLS in ALTER ROLE, even to turn it off; Cloud SQL's owner is not one.
            # verify() below refuses to finish if the role can bypass row-level security.
            conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(APP_ROLE), sql.Literal(password)))
        grant_app(conn)
        revoke_temp(conn)


def revoke_temp(conn: psycopg.Connection) -> None:
    """Every role may create temporary tables by default (through PUBLIC). The app role never needs one, and without them it
    cannot shadow a real table inside a definer function. Only the database's owner may revoke it; otherwise say so."""
    try:
        with conn.transaction():
            conn.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(conn.info.dbname)))
    except psycopg.errors.InsufficientPrivilege:
        pass
    if conn.execute("SELECT has_database_privilege(%s, current_database(), 'TEMPORARY')", (APP_ROLE,)).fetchone()[0]:
        log.warning("%s can still create temporary tables: run REVOKE TEMPORARY ON DATABASE %s FROM PUBLIC as its owner", APP_ROLE, conn.info.dbname)


def grant_app(conn: psycopg.Connection) -> None:
    role = sql.Identifier(APP_ROLE)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(role))
    conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role))
    conn.execute(sql.SQL("REVOKE ALL ON schema_migrations FROM {}").format(role))
    for fn in DEFINER_FUNCTIONS:
        conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(sql.SQL(fn), role))


def setup_checkpoints(url: str) -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        PostgresSaver(conn).setup()


# Tables that belong to no organisation, and why none of them has row-level security. A table not listed here and not
# tenant data fails verify(): a new table is either org-scoped (an org_id, forced row-level security and a policy) or it is
# added here with a reason a reviewer can check.
GLOBAL_TABLES = {
    "schema_migrations": "the migrator's own bookkeeping; ww_app has no access to it",
    "outbox": "events that carry ids only, published and swept by id (004_round3.sql); the org's rows are deleted by project id",
    "processed_events": "dedupe keys of handled events: ids only",
    "github_installation_senders": "senders allowed per GitHub installation id; emptied with the organisation's installations",
    "checkpoints": "LangGraph's checkpoint tables, keyed by thread id (app/migrate.py setup_checkpoints); an organisation's "
                   "threads are deleted with it (app/lifecycle/orgdata.py)",
    "checkpoint_blobs": "LangGraph's checkpoint tables (see checkpoints)",
    "checkpoint_writes": "LangGraph's checkpoint tables (see checkpoints)",
    "checkpoint_migrations": "LangGraph's own schema version",
}
TENANT_ROOTS = {"organizations"}  # keyed by id instead of org_id: still tenant data


def protection_problems(conn: psycopg.Connection) -> list[str]:
    """Every public table is either tenant data, with forced row-level security AND at least one policy (RLS on with no policy
    hides everything, which is safe but a mistake; RLS without FORCE lets the owner role past it), or listed in GLOBAL_TABLES."""
    rows = conn.execute("""
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped),
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition ORDER BY c.relname""").fetchall()
    problems = []
    for name, rls, forced, has_org, policies in rows:
        if has_org or name in TENANT_ROOTS:
            if not (rls and forced):
                problems.append(f"{name}: org-scoped but without forced row-level security")
            elif not policies:
                problems.append(f"{name}: forced row-level security but no policy")
        elif name not in GLOBAL_TABLES:
            problems.append(f"{name}: no org_id and not listed in migrate.GLOBAL_TABLES")
    return problems


def verify(conn: psycopg.Connection) -> None:
    row = conn.execute("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
    if row and (row[0] or row[1]):
        raise SystemExit(f"{APP_ROLE} can bypass row-level security; refusing to continue.")
    unprotected = protection_problems(conn)
    if unprotected:
        raise SystemExit(f"Tables without tenant protection: {unprotected}")
    exposed = [r[0] for r in conn.execute("""
        SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prosecdef AND NOT coalesce('search_path=public, pg_temp' = ANY(p.proconfig), false)""")]
    if exposed:
        raise SystemExit(f"SECURITY DEFINER functions without `SET search_path = public, pg_temp`: {exposed}")


def run(url: str, app_password: str | None) -> list[str]:
    url = plain_url(url)
    with psycopg.connect(url, prepare_threshold=None) as conn:
        applied = apply_migrations(conn)
    setup_checkpoints(url)
    with psycopg.connect(url, prepare_threshold=None) as conn:
        ensure_app_role(conn, app_password)
        verify(conn)
    return applied


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    url = os.environ.get("DATABASE_URL_OWNER")
    if not url:
        print("DATABASE_URL_OWNER is required", file=sys.stderr)
        return 2
    applied = run(url, os.environ.get("APP_DB_PASSWORD") or None)
    log.info("migrations applied: %s", applied or "none (up to date)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
