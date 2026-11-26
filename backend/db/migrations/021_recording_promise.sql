-- Welcome Wave - recording consent given at booking, and where a session's transcript came from (docs/SECURITY.md 5)
--   * meetings.recording_consent_kind: which kind of consent the session carries.
--       'confirmed'            someone confirmed AFTER the call that everyone was told and agreed (020_recording_consent.sql).
--       'promised_at_booking'  the person who booked a Google Meet session promised, before it, to tell everyone it is
--                              recorded and to stop the recording if anyone objects. It is a promise, not proof that anyone
--                              agreed, and it is shown that way. The poller and the manual import accept either kind.
--     Who, when and the notice version stay in the three columns 020 added. Rows that already carry a consent are
--     confirmations. A later confirmation replaces a promise (it says more); removing the recording clears either kind.
--   * meetings.transcript_source: 'google_meet' when the notes were written from the transcript Google Meet saved beside the
--     recording, 'recording' when the model listened to the recording itself; NULL until notes exist. The notes pipeline
--     (app/agents/specialists/note_taker) sets it; removing the recording clears it.
-- No new table: both columns sit on meetings, which already carries org_id under forced row-level security, so organisation
-- export and deletion cover them.

ALTER TABLE meetings
    ADD COLUMN recording_consent_kind text CHECK (recording_consent_kind IS NULL OR recording_consent_kind IN ('confirmed', 'promised_at_booking')),
    ADD COLUMN transcript_source      text CHECK (transcript_source IS NULL OR transcript_source IN ('google_meet', 'recording'));

UPDATE meetings SET recording_consent_kind = 'confirmed' WHERE recording_consent_at IS NOT NULL;
