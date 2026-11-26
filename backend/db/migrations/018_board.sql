-- Welcome Wave - the milestone board (app/lifecycle/board.py, app/api/board.py)
-- A board milestone is a day of the program (15, 30, 45, 60 and 90 by default) and the tasks due by it. In the code it is a
-- "phase", so it never mixes with onboarding_milestones (the checkpoints the product checks itself, 010_lifecycle.sql).
--   * program_templates.phases: a template's board milestones, [{key, title, due_day}] in days of the template's length.
--     Empty means the defaults (15/30/45/60/90 of a 90-day program, scaled to its length).
--   * onboarding_phases: one hire's own board milestones, copied from their template the first time their board is read.
--     A manager or admin adds, renames, re-dates, reorders and deletes them at any time.
--   * task_placements: where a person put a task on the board (its milestone, its order in its column). A task without
--     one sits in the milestone its due date falls in.
--   * task_moves: every move of a task from one milestone to another: who, when, from where to where (with the titles at
--     the time, so a later rename or deletion keeps the history readable). The board's "Moved" state reads it.
-- The board's columns (To do, In progress, Done) are the tasks' own statuses (001_init.sql task_status), so a pull request
-- that moves a task moves its card too. Every new table carries org_id under forced row-level security, as in 001/010;
-- organisation export and deletion find org_id tables themselves.

ALTER TABLE program_templates ADD COLUMN phases jsonb NOT NULL DEFAULT '[]';

CREATE TABLE onboarding_phases (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         uuid NOT NULL,
    onboarding_id  uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    key            text NOT NULL CHECK (key ~ '^[a-z0-9_]{1,40}$'),
    title          text NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
    due_day        int NOT NULL CHECK (due_day BETWEEN 1 AND 730),
    position       int NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (onboarding_id, key)
);

CREATE TABLE task_placements (
    task_id        uuid PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    org_id         uuid NOT NULL,
    onboarding_id  uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    phase_key      text,          -- NULL: the milestone its due date falls in
    position       int,           -- its order in its column; NULL: by due date, after the ordered ones
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX task_placements_onboarding ON task_placements (onboarding_id);

CREATE TABLE task_moves (
    id             bigserial PRIMARY KEY,
    org_id         uuid NOT NULL,
    onboarding_id  uuid NOT NULL REFERENCES onboardings(id) ON DELETE CASCADE,
    task_id        uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    from_phase     text,
    to_phase       text NOT NULL,
    from_title     text,
    to_title       text NOT NULL,
    from_due       date,          -- the task's due date before the move, when the move changed it
    to_due         date,
    actor_id       uuid REFERENCES users(id) ON DELETE SET NULL,
    at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX task_moves_onboarding ON task_moves (onboarding_id, at);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['onboarding_phases', 'task_placements', 'task_moves']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
            USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid)
            WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid)$p$, t);
        -- live updates, as in 009/010: open boards reload when a milestone or a placement changes
        EXECUTE format('CREATE TRIGGER ww_live AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION ww_live_notify(%L)',
                       t, 'lifecycle');
    END LOOP;
END $$;
