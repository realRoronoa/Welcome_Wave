"""Code tour: a guided walkthrough that follows the real call path, each stop linked to its KT clip or its owner (GET /api/tour)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends

from app.api.auth import check_project, container, current_user
from app.ports import User
from app.services.code_tour import build_tour

router = APIRouter()


@router.get("/tour")
def tour(project_id: str, order: Literal["execution", "reading"] = "execution", user: User = Depends(current_user), c=Depends(container)):
    """Any project member may read it; read-only (reading progress lives in the browser). An unknown order is a 422.
    `partial` is true when a first-PR search failed or ran out of time, so that target may be missing; `index_truncated`
    when the index's chunk cap left files out of the tour. Each stop lists at most 20 calls and callers (`calls_total`,
    `called_by_total` count them all)."""
    p = check_project(c, user, project_id)
    return build_tour(c.store, c.index, p.id, p.org_id, user, order)  # the canonical id, not the query string
