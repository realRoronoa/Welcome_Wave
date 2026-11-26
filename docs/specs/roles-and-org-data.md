# The executive role, the permission matrix, and the organisation's data

**Executive.** A read-only role (`users.role` enum value `executive`, migration 010). Admins invite executives.
`current_user` refuses an executive every non-GET request except `POST /api/me/leave`, `POST /api/notifications/read` and
`POST /api/legal/accept` (their own acceptance of the terms: [legal-consent.md](legal-consent.md)), so
read-only holds for every route, including future ones. Executives read reports, survey aggregates and their home; not
individual hires' programs (404).

**Permission matrix.** `docs/PERMISSIONS.md`: every route × admin, manager, expert, new hire, executive.
`tests/test_permissions_matrix.py` lists every route of the running app, fails if one has no row (or a row has no route),
and calls each route as each demo user with valid bodies, so checks made inside handlers are reached.

**Export.** `GET /api/org/export` (admin) → a JSON download: `{<table>: [rows]}` for every table with an `org_id` column, read
from the catalog (Postgres) so later tables are included, plus the organisation row. Left out: derived search data
(embeddings, search text) and anything that is or protects a credential (`*token*`, `*secret*`, `firebase_uid`, address
hashes).

**Deletion.** `DELETE /api/org {confirm: "<organisation name>"}` (admin) → `{deleted: {<table>: rows}}`. One transaction under
row-level security: every org table is emptied in rounds (a table still referenced waits for the next round), then the
organisation row. A final audit entry with no organisation records who deleted it. Not removed: files in blob storage
(documents, recordings) and LangGraph checkpoints of finished runs; deferred.

**Tests.** `test_lifecycle_api.py` (export, deletion, executives), `test_lifecycle_postgres.py::test_export_and_delete_cover_every_table`.
