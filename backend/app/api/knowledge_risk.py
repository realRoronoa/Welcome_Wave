"""Knowledge risk: which code only one person understands, ranked by what new hires are about to work on (GET /api/knowledge-risk)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth import check_project, container, require
from app.services.knowledge_risk import build_knowledge_risk

router = APIRouter()


@router.get("/knowledge-risk")
def knowledge_risk(project_id: str, user=Depends(require("manager")), c=Depends(container)):
    """Per-file risk and per-person exposure for a project. Managers and admins only; read-only. `partial` is true when
    some code searches failed or ran out of time (those demand signals are missing); `index_truncated` (also
    `truncated`) when the index's chunk cap left whole files out. The riskiest 200 files are listed (`files_total`
    counts all of them, as does the summary), each with its first 40 symbols (`symbols_total`)."""
    project = check_project(c, user, project_id)
    return build_knowledge_risk(c.store, c.index, project.id, project.org_id)  # the canonical id, not the query string
