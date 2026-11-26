"""KT call review: the manager side of a recorded KT call.

A call's transcript belongs to the project's managers, not to the onboarding flow: new hires and seniors get the notes
(summary, action items, code moments, terms) and the recording, managers also get the transcript. When a recorded call's
notes are written, a 'kt_followup' approval asks the managers to review the call: read the transcript, then approve it,
booking the follow-up KT sessions the call itself gave reasons for (its action items, code it named but never showed).
Booking uses the KT planner, so a follow-up is the same session, agenda and presenter the planner would book.

Unlike task plans and reviews, no agent run waits on this approval: deciding it books the chosen sessions directly."""
from __future__ import annotations

import logging

from app.ports import Approval, User
from app.services import kt_planner as P

log = logging.getLogger(__name__)

KIND = "kt_followup"


class ReviewBusy(RuntimeError):
    """Another request is deciding this review right now."""


def can_read_transcript(user: User) -> bool:
    return user.role in {"manager", "admin"}


def open_review(store, project_id: str, meeting_id: str, title: str) -> Approval | None:
    """Ask the project's managers to review a recorded call. One review per call: processing the same recording again
    (a retried run) leaves the first one as it is. Notifications are best effort."""
    if any(a.kind == KIND and a.payload.get("meeting_id") == meeting_id for a in store.approvals(project_id, None)):
        return None
    a = store.create_approval(project_id, KIND, f"kt-review:{meeting_id}", {"meeting_id": meeting_id, "title": title})
    for m in store.managers(project_id):
        try:
            store.notify(m.id, "approval", f"Notes for '{title}' are ready. Review the transcript and book the follow-up KT sessions.",
                         f"/approvals/{a.id}?project={project_id}")
        except Exception:  # noqa: BLE001 - a lost notice must not lose the review
            log.warning("kt review notification failed", exc_info=True)
    return a


def followups(store, index, project_id: str, org_id: str, meeting_id: str) -> list[dict]:
    """The planner's sessions that this call gives a reason for, most urgent first, in the planner's own shape."""
    plan = P._compute(store, index, project_id, org_id)
    return [P._session_dict(s, plan.pace) for s in plan.sessions
            if any((r.ref or {}).get("meeting_id") == meeting_id for i in s.items for r in i.reasons)]


def decide(store, index, settings, approval: Approval, user: User, keys: list[str]) -> dict:
    """Approve the review and book the chosen follow-ups. A session someone booked meanwhile counts as booked; one the plan
    no longer has (its code was explained since) is skipped. Raises ReviewBusy. The follow-ups are booked unscheduled
    ('manual'): each presenter picks a time in KT sessions (docs/specs/scheduling.md)."""
    if not store.claim_approval(approval.id, {"approve": keys, "user_id": user.id}):
        raise ReviewBusy(approval.id)
    booked: list[dict] = []
    skipped: list[str] = []
    try:
        for key in dict.fromkeys(keys):
            try:
                r = P.schedule(store, index, approval.project_id, user.org_id, user, key)
                booked.append({"key": key, "meeting_id": r["meeting"].id, "title": r["session"]["title"]})
            except P.AlreadyBooked as e:
                booked.append({"key": key, "meeting_id": e.args[0] if e.args else None, "title": None})
            except (P.PlanChanged, P.NoPresenter):
                skipped.append(key)
        store.decide_approval(approval.id, "approved", user.id)
    finally:
        store.release_approval(approval.id)
    store.close_notifications(f"/approvals/{approval.id}")
    return {"booked": booked, "skipped": skipped}
