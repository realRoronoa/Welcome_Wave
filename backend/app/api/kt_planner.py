"""KT planner: the next KT sessions, who presents what and why, booked in one click (GET /api/kt-plan, POST /api/kt-plan/schedule)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import check_project, container, require
from app.ports import User
from app.services import scheduling
from app.services.kt_planner import AlreadyBooked, NotYourSession, NoPresenter, PlanChanged, build_plan, schedule

router = APIRouter()


@router.get("/kt-plan")
def kt_plan(project_id: str, presenter: str | None = Query(None, max_length=64),
            user: User = Depends(require("manager", "expert")), c=Depends(container)):
    """The plan: sessions with their agenda, reasons, estimate and impact, plus the re-record queue and the backlog.
    Experts always get their own queue (scope 'mine'), naming no new hire and describing no task awaiting approval;
    `presenter` filters the team plan for managers. New hires' questions are counts for everyone ("Asked 3 times by new
    hires about check_labels"), never who asked or the words (services/kt_planner.py _reason_dicts). `partial` is
    true when some code searches failed or ran out of time (tasks and questions that name no file may be missing);
    `index_truncated` when the index's chunk cap left files out. At most 50 sessions are returned (`sessions_total`
    counts the view's sessions; `presenters` gives each presenter's share of the team plan)."""
    project = check_project(c, user, project_id)
    return build_plan(c.store, c.index, project.id, project.org_id, user, presenter)  # the canonical id, not the query string


class SchedulePlanIn(BaseModel):
    project_id: str
    key: str = Field(pattern="^[0-9a-f]{12}$")
    # When and how (docs/specs/scheduling.md). No time: an unscheduled session, held whenever it suits ('manual').
    starts_at: datetime | None = None
    duration_min: int = Field(30, ge=5, le=240)
    attendees: list[str] = Field(default_factory=list, max_length=50)  # project members to invite besides the presenter
    provider: Literal["google", "manual"] | None = None  # None: Google Calendar when connected and a time is given
    join_url: str | None = Field(None, max_length=500, pattern=r"^https://\S+$")  # manual: where it is held


@router.post("/kt-plan/schedule")
def schedule_session(body: SchedulePlanIn, user: User = Depends(require("manager", "expert")), c=Depends(container)):
    """Book one session as a KT session (on Google Calendar with a Meet link when connected) and send its presenter the
    agenda (a 'kt_request' notification). Booking with the presenter's Calendly link happens on Calendly instead; its
    webhook creates the session."""
    project = check_project(c, user, body.project_id)

    def create(title, key, presenter):
        req = scheduling.SessionRequest(title=title, starts_at=body.starts_at, duration_min=body.duration_min,
                                        attendee_ids=[presenter.id, *body.attendees], provider=body.provider, join_url=body.join_url,
                                        description=f"KT session booked in Welcome Wave. Presenter: {presenter.name}.")
        return scheduling.book(c, project.id, project.org_id, user, req, kt_key=key)

    try:
        # the canonical id: Postgres accepts any spelling of a UUID, and the booking lock and the notification link use this one
        return schedule(c.store, c.index, project.id, project.org_id, user, body.key, create)
    except scheduling.SchedulingUnavailable:
        raise HTTPException(409, "Connect Google Calendar in Settings, Integrations first.") from None
    except scheduling.CalendarFailed as e:
        raise HTTPException(502, f"Google Calendar did not book the session: {e}"[:300]) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except PlanChanged:
        raise HTTPException(409, "plan_changed") from None
    except AlreadyBooked:
        raise HTTPException(409, "already_booked") from None
    except NotYourSession:
        raise HTTPException(403, "You can book only the sessions you present.") from None
    except NoPresenter:
        raise HTTPException(422, "Nobody can present this session yet. Add a CODEOWNERS entry or a manager to the project.") from None
