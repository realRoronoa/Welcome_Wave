"""The organisation's data: its export (GET /api/org/export) and its deletion (DELETE /api/org).

Export is JSON, table by table, with no credentials (link tokens, OAuth refresh tokens and API token hashes are never
written) and with the anonymous survey as AGGREGATES ONLY: the survey rows carry a team and a day, and a program's
`survey_days` says who answered on which day, so together they could be joined back to a person. The export holds the
floored per-team, per-day averages instead (app/lifecycle/checkins.py), and no `survey_days`.

Deletion removes everything of the organisation, and "everything" is more than rows:
  * the database rows, and the outbox events of its projects (the store's delete_org, in one transaction);
  * the stored files, found by the uris its documents and call recordings hold (Cloud Storage or the data folder);
  * the LangGraph checkpoints of the runs its approvals paused (a paused run keeps its saved state until it is decided);
  * in the demo, the Google Calendar connection and the Calendly links (they live next to the rows there).
The files and checkpoints are found BEFORE the rows go, because the rows are what name them."""
from __future__ import annotations

import logging

from app.lifecycle import checkins as CI
from app.ports import SurveyResponse

log = logging.getLogger("orgdata")


def export(c, org_id: str) -> dict:
    data = c.lifecycle.export_org(org_id)
    teams = {t.id: t for t in c.lifecycle.teams(org_id)}
    rows = data.pop("survey_responses", [])
    data["survey_aggregates"] = CI.export_aggregates([SurveyResponse(**{**r, "id": str(r.get("id") or "")}) for r in rows], teams)
    for program in data.get("onboarding_programs", []):
        program.pop("survey_days", None)
    return data


def delete(c, org_id: str) -> dict[str, int]:
    """Everything of the organisation. Returns what was removed, by kind; a failure to remove one file or one thread is
    logged and counted under `stored_files_failed` / `checkpoint_threads_failed`, and never undoes the rest."""
    uris = c.lifecycle.org_blob_uris(org_id)
    threads = c.lifecycle.org_thread_ids(org_id)
    counts = c.lifecycle.delete_org(org_id)  # the rows: one transaction, or nothing was deleted and this raises
    files = failed = 0
    for uri in uris:
        try:
            files += bool(c.deps.blobs.delete(uri))
        except Exception:  # noqa: BLE001 - one unreadable file must not leave the rest behind
            failed += 1
            log.warning("stored file not deleted", extra={"extra_fields": {"org_id": org_id}}, exc_info=True)
    counts["stored_files"] = files
    if failed:
        counts["stored_files_failed"] = failed
    saved = lost = 0
    if c.checkpointer is not None:
        for thread in threads:
            try:
                c.checkpointer.delete_thread(thread)
                saved += 1
            except Exception:  # noqa: BLE001
                lost += 1
                log.warning("checkpoint thread not deleted", extra={"extra_fields": {"org_id": org_id}}, exc_info=True)
    counts["checkpoint_threads"] = saved
    if lost:
        counts["checkpoint_threads_failed"] = lost
    return {k: n for k, n in counts.items() if n}
