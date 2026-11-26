"""Documentation feedback: anyone on a project can flag a document, code, a KT call or a verified answer as unclear or
outdated. It goes to the target's owner (each owner sees their queue), with the reason they were chosen:

  code     the CODEOWNERS owner of the file (a person, not a team handle)
  doc      whoever uploaded the document
  meeting  whoever booked or started the call
  answer   whoever verified the answer
and, when none is found, the project's first manager. The target must exist in the project; the note is stored as text and
shown as text."""
from __future__ import annotations

from app.lifecycle.context import Ctx
from app.ports import DocFeedback
from app.services.codeowners import owners_for, parse_codeowners


class UnknownTarget(LookupError):
    pass


def owner_for(ctx: Ctx, project_id: str, kind: str, ref: str) -> tuple[str | None, str]:
    store = ctx.store
    owner, why = None, ""
    if kind == "code":
        if ref not in set(ctx.index.all_paths(project_id)):
            raise UnknownTarget(ref)
        logins = owners_for(parse_codeowners(store.codeowners(project_id)), ref)
        people = store.users_by_github(store.get_project(project_id).org_id, [l for l in logins if "/" not in l]) if logins else []
        if people:
            owner, why = people[0].id, f"CODEOWNERS gives {ref} to {people[0].name}."
    elif kind == "doc":
        d = store.get_document(ref)
        if not d or str(d["project_id"]) != project_id:
            raise UnknownTarget(ref)
        owner = str(d["uploaded_by"]) if d.get("uploaded_by") else None
        why = "They uploaded the document." if owner else ""
    elif kind == "meeting":
        m = store.get_meeting(ref)
        if not m or m.project_id != project_id:
            raise UnknownTarget(ref)
        owner, why = m.created_by, "They booked the KT call." if m.created_by else ""
    else:
        a = next((a for a in ctx.lc.verified_answers(project_id) if a.id == ref), None)
        if not a:
            raise UnknownTarget(ref)
        owner, why = a.verified_by, "They verified the answer."
    if not owner:
        m = next(iter(store.managers(project_id)), None)
        if not m:
            return None, "Nobody owns it yet."
        owner, why = m.id, f"No owner was found, so it goes to {m.name}, a manager of the project."
    return owner, why


def submit(ctx: Ctx, project_id: str, target_kind: str, target_ref: str, kind: str, note: str) -> dict:
    owner, why = owner_for(ctx, project_id, target_kind, target_ref)
    fb = ctx.lc.add_feedback(DocFeedback(id="", project_id=project_id, target_kind=target_kind, target_ref=target_ref, kind=kind,
                                         note=note, owner_id=owner, created_by=ctx.user.id, created_at=ctx.clock.now()))
    if owner and owner != ctx.user.id:
        ctx.store.notify(owner, "feedback", f"{ctx.user.name} marked {target_ref[:80]} as {kind}.", f"/feedback?project={project_id}")
    return {**fb.model_dump(), "why": [why]}


def visible(ctx: Ctx, project_id: str) -> list[DocFeedback]:
    """Managers see the project's feedback; everyone else sees what they sent and what is in their queue. (Executives get
    counts, never rows: counts() below.)"""
    rows = ctx.lc.feedback(project_id)
    if ctx.user.role in {"manager", "admin"}:
        return rows
    return [f for f in rows if ctx.user.id in (f.created_by, f.owner_id)]


def counts(ctx: Ctx, project_id: str) -> dict:
    """What an executive may see of documentation feedback: how many flags, by what they are about and what they say, and
    where they stand. No note, no author, no target: a hire's words and name stay with the owner and the managers."""
    tally: dict[tuple, dict] = {}
    rows = ctx.lc.feedback(project_id)
    for f in rows:
        row = tally.setdefault((f.target_kind, f.kind), {"target_kind": f.target_kind, "kind": f.kind, "open": 0, "resolved": 0,
                                                         "dismissed": 0})
        row[f.status] += 1
    return {"counts_only": True, "total": len(rows), "counts": sorted(tally.values(), key=lambda r: (r["target_kind"], r["kind"]))}
