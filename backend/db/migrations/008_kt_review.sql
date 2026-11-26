-- Welcome Wave - KT call review
--   * a recorded KT call's transcript goes to the project's managers: when its notes are written, a 'kt_followup'
--     approval asks them to review the call and book the follow-up KT sessions it gave reasons for. No agent run
--     waits on it (thread_id is 'kt-review:<meeting id>'); deciding it books the chosen sessions directly.
ALTER TYPE approval_kind ADD VALUE IF NOT EXISTS 'kt_followup';
