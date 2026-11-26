-- Welcome Wave - who wrote a chat answer (app/agents/schemas.py QAResult.writer, .crew)
--   * writer: "model", or "extractive" when the offline writer quoted the sources (no model on the server); NULL when nothing
--     was written (a turn handed to a person). The Ask page labels an answer by it, so it is kept with the turn and the label
--     is the same after a reload.
--   * crew: how the evidence was found, the research crew's summary (counts, intent, planner, times: never the question, a
--     query or a model's words).
-- Columns of chat_messages, which already has its org_id and forced row-level security: export and deletion take them along.

ALTER TABLE chat_messages ADD COLUMN writer text CHECK (writer IN ('model', 'extractive'));
ALTER TABLE chat_messages ADD COLUMN crew jsonb;
