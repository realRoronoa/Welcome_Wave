-- Welcome Wave - the insight views (knowledge risk, KT planner, code tour) at production scale
--   * a KT call booked from the planner remembers the session it was booked for, and the database allows one live
--     booking per session: the planner needs no lock (and no pooled connection held while it works) to stop a double
--     booking, and a call someone else gives the same title never marks a session booked
--   * a scheduled call can be cancelled, so a call nobody joins does not block its session for good
--   * every code chunk of a project is read in (path, start line) order through an index instead of an on-disk sort

-- ---------- booked KT sessions ----------
ALTER TYPE meeting_status ADD VALUE IF NOT EXISTS 'cancelled';
ALTER TABLE meetings ADD COLUMN kt_session_key text;
-- Only calls that can still happen hold a session: a ready, failed or cancelled call frees it to be booked again.
CREATE UNIQUE INDEX meetings_one_live_kt_session ON meetings (project_id, kt_session_key)
    WHERE kt_session_key IS NOT NULL AND status IN ('scheduled', 'live', 'processing');

-- ---------- code chunks in reading order ----------
-- PgIndex.code_chunks orders by path COLLATE "C" (code points, like the memory adapter), which chunks_project_path
-- (default collation) cannot serve, so a large repository sorted on disk on every read.
CREATE INDEX chunks_code_order ON chunks (project_id, (path COLLATE "C"), ((ref->>'start_line')::int))
    WHERE source_type = 'code' AND NOT stale;
