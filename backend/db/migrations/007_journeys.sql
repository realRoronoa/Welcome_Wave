-- Welcome Wave - end-to-end journey fixes
--   * a KT call held where nothing can record it (no LiveKit Egress) ends as 'ended' once its room empties, instead of
--     staying 'scheduled' (and cancellable) for good. 'ended' holds no KT planner session: nothing was captured.
ALTER TYPE meeting_status ADD VALUE IF NOT EXISTS 'ended';
