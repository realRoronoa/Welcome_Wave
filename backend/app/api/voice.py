"""Welcome Wave Voice: employee-scoped Agora sessions and consent-gated tools."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import _set_tenant, check_project, container, current_user
from app.api import lifecycle, routes
from app.agents.core.schemas import ProposedTask
from app.lifecycle import visibility
from app.ports import AccessItem, User
from app.services import scheduling
from app.voice import platform

router = APIRouter(prefix="/voice", tags=["voice"])
log = logging.getLogger("voice")


def _signed(settings, session_id: str, org_id: str, user_id: str) -> str:
    data = {"s": session_id, "o": org_id, "u": user_id, "exp": int(time.time()) + 3600}
    blob = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(settings.agora_app_certificate.encode(), blob.encode(), hashlib.sha256).hexdigest()
    return f"{blob}.{sig}"


def _verified(settings, authorization: str | None) -> dict:
    try:
        blob, sig = (authorization or "").removeprefix("Bearer ").split(".", 1)
        wanted = hmac.new(settings.agora_app_certificate.encode(), blob.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, wanted):
            raise ValueError("signature")
        data = json.loads(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)))
        if int(data["exp"]) < time.time():
            raise ValueError("expired")
        for key in ("s", "o", "u"):
            uuid.UUID(data[key])
        return data
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(401, "Invalid voice tool credential") from None


def _own(c, user: User, session_id: str) -> dict:
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(404, "Voice session not found") from None
    row = c.store.one("SELECT * FROM voice_sessions WHERE id = :s AND org_id = :o AND user_id = :u", s=session_id, o=user.org_id, u=user.id)
    if not row:
        raise HTTPException(404, "Voice session not found")
    check_project(c, user, str(row["project_id"]))
    return row


def _public(row: dict, actions: list[dict] | None = None) -> dict:
    return {"id": str(row["id"]), "project_id": str(row["project_id"]), "onboarding_id": str(row["onboarding_id"]) if row["onboarding_id"] else None,
            "status": row["status"], "channel": row["rtc_channel"], "transcript": row["transcript"],
            "sources": row["sources"], "summary": row["summary"], "actions": actions or [], "created_at": row["created_at"]}


def _actions(c, session_id: str) -> list[dict]:
    return [{**r, "id": str(r["id"]), "voice_session_id": str(r["voice_session_id"]), "decided_by": str(r["decided_by"]) if r["decided_by"] else None}
            for r in c.store.rows("SELECT * FROM voice_actions WHERE voice_session_id = :s ORDER BY created_at, id", s=session_id)]


@router.get("/config")
def config(project_id: str | None = None, user: User = Depends(current_user), c=Depends(container)):
    if c.settings.is_demo or not c.settings.voice_ready:
        return {"available": False, "reason": "Voice needs Agora and Murf credentials and a public HTTPS tool URL. See the Voice setup guide."}
    if project_id:
        check_project(c, user, project_id)
        mine = [o for o in c.store.onboardings(project_id) if o.new_hire_id == user.id and c.lifecycle.get_program(o.id)]
        if user.role != "new_hire" or not mine:
            return {"available": False, "reason": "An active onboarding in this project is needed before you can start a voice conversation."}
    return {"available": True, "reason": None}


class StartIn(BaseModel):
    project_id: str


@router.post("/sessions", status_code=201)
def start(body: StartIn, user: User = Depends(current_user), c=Depends(container)):
    if user.role != "new_hire":
        raise HTTPException(403, "Voice onboarding is available to new hires")
    if c.settings.is_demo or not c.settings.voice_ready:
        raise HTTPException(503, "Voice is not configured. See the Voice setup guide.")
    project = check_project(c, user, body.project_id)
    mine = [o for o in c.store.onboardings(project.id) if o.new_hire_id == user.id and c.lifecycle.get_program(o.id)]
    if not mine:
        raise HTTPException(404, "No active onboarding in this project")
    ob = mine[0]
    session_id = str(uuid.uuid4())
    channel = "wwvoice_" + uuid.uuid4().hex
    uid = secrets.randbelow(2**31 - 2000) + 2001
    c.store.one("""INSERT INTO voice_sessions (id, org_id, project_id, user_id, onboarding_id, rtc_channel, rtc_uid)
                   VALUES (:s, :o, :p, :u, :ob, :ch, :uid) RETURNING id""",
                s=session_id, o=user.org_id, p=project.id, u=user.id, ob=ob.id, ch=channel, uid=uid)
    try:
        token = platform.client_token(c.settings, channel, uid)
    except Exception as exc:
        c.store.run("UPDATE voice_sessions SET status = 'failed', ended_at = now() WHERE id = :s", s=session_id)
        log.warning("voice token failed", extra={"extra_fields": {"session_id": session_id, "error_type": type(exc).__name__}})
        raise HTTPException(502, "Agora could not issue a session token. Check the provider configuration.") from None
    c.store.audit(user.org_id, user.id, "voice.prepared", session_id, {"project_id": project.id, "onboarding_id": ob.id})
    return {"id": session_id, "channel": channel, "uid": uid, "app_id": c.settings.agora_app_id,
            "rtc_token": token, "agent_uid": 1002, "status": "starting"}


@router.post("/sessions/{session_id}/activate")
def activate(session_id: str, user: User = Depends(current_user), c=Depends(container)):
    row = _own(c, user, session_id)
    if row["status"] != "starting":
        raise HTTPException(409, "Session already activated or ended")
    bearer = "Bearer " + _signed(c.settings, session_id, user.org_id, user.id)
    try:
        agora_id = platform.start(c.settings, row["rtc_channel"], row["rtc_uid"], bearer, session_id)
    except Exception as exc:
        c.store.run("UPDATE voice_sessions SET status = 'failed', ended_at = now() WHERE id = :s", s=session_id)
        log.warning("voice start failed", extra={"extra_fields": {"session_id": session_id, "error_type": type(exc).__name__}})
        raise HTTPException(502, "Agora could not start the live voice session. Check the provider configuration.") from None
    c.store.run("UPDATE voice_sessions SET agora_session_id = :a WHERE id = :s", a=agora_id, s=session_id)
    c.store.audit(user.org_id, user.id, "voice.started", session_id, {"project_id": str(row["project_id"]), "onboarding_id": str(row["onboarding_id"])})
    return sync(session_id, user, c)


@router.post("/sessions/{session_id}/sync")
def sync(session_id: str, user: User = Depends(current_user), c=Depends(container)):
    row = _own(c, user, session_id)
    if row["status"] in {"ended", "failed"} or not row["agora_session_id"]:
        return _public(row, _actions(c, session_id))
    try:
        provider = platform.status(c.settings, row["agora_session_id"]).upper()
    except Exception:
        return _public(row, _actions(c, session_id))
    if provider == "RUNNING" and row["status"] != "active":
        c.store.run("UPDATE voice_sessions SET status = 'active' WHERE id = :s", s=session_id)
    elif provider in {"STOPPED", "FAILED"}:
        state = "ended" if provider == "STOPPED" else "failed"
        summary = _summary(row, _actions(c, session_id))
        summary.update(_recap(c, row, summary))
        c.store.run("UPDATE voice_sessions SET status = :st, ended_at = COALESCE(ended_at, now()), summary = CAST(:su AS jsonb) WHERE id = :s",
                    st=state, su=json.dumps(summary), s=session_id)
        c.store.audit(user.org_id, user.id, "voice.ended", session_id, {"onboarding_id": str(row["onboarding_id"]), "by": "provider", "status": state})
    return session(session_id, user, c)


@router.get("/sessions/{session_id}")
def session(session_id: str, user: User = Depends(current_user), c=Depends(container)):
    row = _own(c, user, session_id)
    return _public(row, _actions(c, session_id))


@router.get("/sessions")
def history(project_id: str, user: User = Depends(current_user), c=Depends(container)):
    if c.settings.is_demo or not c.settings.voice_ready:
        return []
    check_project(c, user, project_id)
    return [_public(r) for r in c.store.rows("""SELECT * FROM voice_sessions WHERE org_id = :o AND user_id = :u AND project_id = :p
                                           ORDER BY created_at DESC LIMIT 20""", o=user.org_id, u=user.id, p=project_id)]


class TranscriptIn(BaseModel):
    speaker: Literal["employee", "assistant"]
    text: str = Field(min_length=1, max_length=4000)


@router.post("/sessions/{session_id}/transcript")
def transcript(session_id: str, body: TranscriptIn, user: User = Depends(current_user), c=Depends(container)):
    row = _own(c, user, session_id)
    if row["status"] not in {"active", "starting"}:
        raise HTTPException(409, "Voice session is not active")
    c.store.run("""UPDATE voice_sessions SET transcript = (SELECT COALESCE(jsonb_agg(x), '[]'::jsonb) FROM
                 (SELECT x FROM jsonb_array_elements(transcript) x OFFSET GREATEST(jsonb_array_length(transcript)-199,0)) tail)
                 || CAST(:turn AS jsonb) WHERE id = :s""", s=session_id,
                turn=json.dumps([{"speaker": body.speaker, "text": body.text.strip(), "at": int(time.time())}]))
    return {"saved": True}


@router.post("/sessions/{session_id}/stop")
def stop(session_id: str, user: User = Depends(current_user), c=Depends(container)):
    row = _own(c, user, session_id)
    if row["status"] in {"ended", "failed"}:
        return _public(row, _actions(c, session_id))
    if row["status"] in {"active", "starting"} and row["agora_session_id"]:
        try:
            if platform.status(c.settings, row["agora_session_id"]).upper() not in {"STOPPED", "FAILED"}:
                platform.stop(c.settings, row["agora_session_id"])
        except Exception as exc:
            log.warning("voice stop failed", extra={"extra_fields": {"session_id": session_id, "error_type": type(exc).__name__}})
            raise HTTPException(502, "Agora did not confirm that the session stopped. Try again.") from None
    summary = _summary(row, _actions(c, session_id))
    summary.update(_recap(c, row, summary))
    c.store.run("""UPDATE voice_sessions SET status = 'ended', ended_at = COALESCE(ended_at, now()),
                   summary = CAST(:summary AS jsonb) WHERE id = :s""", s=session_id, summary=json.dumps(summary))
    c.store.audit(user.org_id, user.id, "voice.ended", session_id, {"onboarding_id": str(row["onboarding_id"])})
    return session(session_id, user, c)


def _summary(row: dict, actions: list[dict]) -> dict:
    turns = row["transcript"] or []
    return {"title": "Voice onboarding conversation", "turns": len(turns),
            "topics": [t.get("text", "")[:200] for t in turns if t.get("speaker") == "employee"][-5:],
            "sources": row["sources"] or [],
            "completed_actions": [{"kind": a["kind"], "record": a["result"]} for a in actions if a["status"] == "completed"],
            "outstanding_actions": [{"kind": a["kind"], "status": a["status"], "payload": a["payload"]}
                                    for a in actions if a["status"] in {"pending", "approved", "failed"}]}


class VoiceRecap(BaseModel):
    recap: str = Field(max_length=1200)
    next_steps: list[str] = Field(default_factory=list, max_length=5)


def _recap(c, row: dict, summary: dict) -> dict:
    turns = row["transcript"] or []
    fallback = {"recap": f"Discussed {len(summary['topics'])} onboarding topic(s). "
                         f"Completed {len(summary['completed_actions'])} action(s); {len(summary['outstanding_actions'])} still need attention.",
                "next_steps": [f"Review {a['kind'].replace('_', ' ')}" for a in summary["outstanding_actions"][:5]]}
    if not turns or not c.settings.ai_ready:
        return fallback
    from app.agents.core.llm import call_budget

    prompt = json.dumps({"conversation": turns[-20:], "sources": [s.get("label") for s in summary["sources"][:20]],
                         "completed": summary["completed_actions"], "outstanding": summary["outstanding_actions"]}, default=str)[:10000]
    try:
        with call_budget(8):
            result = c.llm.structured(prompt, VoiceRecap, system="Summarise this employee onboarding conversation in at most three factual sentences. "
                                      "List up to five concrete next steps. Use only the supplied conversation and action statuses. "
                                      "Do not claim an access request was granted, a KT request was booked, or a proposed task was approved.")
        return result.model_dump()
    except Exception:
        return fallback


def _tool_user(c, authorization: str | None):
    if not c.settings.voice_ready or c.settings.is_demo:
        raise HTTPException(503, "Voice is not configured")
    data = _verified(c.settings, authorization)
    _set_tenant(data["o"])
    user = c.store.get_user(data["u"])
    if not user or user.org_id != data["o"] or user.status != "active":
        raise HTTPException(401, "Voice member is no longer active")
    row = _own(c, user, data["s"])
    if row["status"] not in {"active", "starting"}:
        raise HTTPException(409, "Voice session is not active")
    return user, row


class ToolIn(BaseModel):
    question: str | None = Field(None, max_length=2000)
    system: str | None = Field(None, max_length=120)
    topic: str | None = Field(None, max_length=200)
    title: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=1000)
    tool_call_id: str | None = Field(None, min_length=1, max_length=200)


def _needed(value: str | None, label: str) -> str:
    if not value or not value.strip():
        raise HTTPException(422, f"{label} is required")
    return value.strip()


@router.post("/tools/{name}")
def tool_call(name: str, body: ToolIn, authorization: str | None = Header(default=None), c=Depends(container)):
    if name not in {"getEmployeeContext", "searchCompanyKnowledge", "checkAccessStatus",
                    "createAccessRequest", "requestKTSession", "createOnboardingTask"}:
        raise HTTPException(404, "Voice tool not found")
    user, row = _tool_user(c, authorization)
    session_id = str(row["id"])
    onboarding_id = str(row["onboarding_id"])
    hc = lifecycle._hire(c, user, onboarding_id, visibility.HIRE)
    if name == "getEmployeeContext":
        return {"profile": visibility.view(hc, ["profile", "status", "plan_items"]), "project_id": str(row["project_id"])}
    if name == "checkAccessStatus":
        return {"access": visibility.view(hc, ["access"])}
    if name == "searchCompanyKnowledge":
        question = _needed(body.question, "question")
        try:
            result = routes.ask(routes.Question(project_id=str(row["project_id"]), question=question), user, c)
        except HTTPException as exc:
            return {"status": "unavailable", "answer": None, "citations": [], "kt_clips": [], "message": str(exc.detail)}
        citations = result.get("citations") or []
        kt_clips = result.get("kt_clips") or []
        sources = list(citations)
        for clip in kt_clips:
            sources.append({"source_type": "kt", "label": f"{clip.get('meeting_title') or 'KT session'} · {clip.get('file_path') or 'recording'} · {float(clip.get('start_s') or 0):.0f}s",
                            "ref": {"meeting_id": clip.get("meeting_id"), "path": clip.get("file_path"),
                                    "start_s": clip.get("start_s"), "end_s": clip.get("end_s")}})
        if sources:
            c.store.run("""UPDATE voice_sessions SET sources = sources || CAST(:sources AS jsonb) WHERE id = :s""",
                        s=session_id, sources=json.dumps(sources, default=str))
        return {"status": result.get("status"), "answer": result.get("answer"), "citations": citations,
                "kt_clips": kt_clips}
    kind = {"createAccessRequest": "access_request", "requestKTSession": "kt_request", "createOnboardingTask": "onboarding_task"}[name]
    if kind == "access_request":
        payload = {"system": _needed(body.system, "system")}
    elif kind == "kt_request":
        payload = {"topic": _needed(body.topic, "topic")}
    else:
        payload = {"title": _needed(body.title, "title"), "description": _needed(body.description, "description")}
    call_id = _needed(body.tool_call_id, "tool_call_id")
    action = c.store.one("""INSERT INTO voice_actions (org_id, voice_session_id, kind, payload, tool_call_id)
                    VALUES (:o, :s, :k, CAST(:p AS jsonb), :t)
                    ON CONFLICT (voice_session_id, tool_call_id) DO UPDATE SET tool_call_id = EXCLUDED.tool_call_id
                    RETURNING id, status, payload, result""",
                    o=user.org_id, s=session_id, k=kind, p=json.dumps(payload), t=call_id)
    if action["payload"] != payload:
        raise HTTPException(409, "Tool call ID was reused with different input")
    c.store.audit(user.org_id, user.id, "voice.action.proposed", str(action["id"]), {"kind": kind, "session_id": session_id})
    return {"confirmation_required": action["status"] == "pending", "action_id": str(action["id"]),
            "kind": kind, "proposed": payload, "status": action["status"], "result": action["result"]}


class DecideIn(BaseModel):
    confirm: bool


@router.post("/actions/{action_id}/approve")
def approve(action_id: str, body: DecideIn, user: User = Depends(current_user), c=Depends(container)):
    if not body.confirm:
        raise HTTPException(422, "Explicit confirmation is required")
    try:
        uuid.UUID(action_id)
    except ValueError:
        raise HTTPException(404, "Voice action not found") from None
    action = c.store.one("""SELECT a.* FROM voice_actions a JOIN voice_sessions s ON s.id = a.voice_session_id
                         WHERE a.id = :a AND a.org_id = :o AND s.user_id = :u""", a=action_id, o=user.org_id, u=user.id)
    if not action:
        raise HTTPException(404, "Voice action not found")
    row = _own(c, user, str(action["voice_session_id"]))
    if action["status"] == "completed":
        return {"id": str(action["id"]), "status": "completed", "result": action["result"]}
    if action["status"] != "pending":
        raise HTTPException(409, "Action is no longer pending")
    try:
        with c.store.writing():
            claimed = c.store.one("""UPDATE voice_actions SET status = 'approved', decided_by = :u, decided_at = now()
                                   WHERE id = :a AND status = 'pending' RETURNING id""", a=action_id, u=user.id)
            if not claimed:
                raise HTTPException(409, "Action has already been decided")
            result = _execute(c, user, row, action)
            c.store.run("UPDATE voice_actions SET status = 'completed', result = CAST(:r AS jsonb) WHERE id = :a",
                        a=action_id, r=json.dumps(result, default=str))
            c.store.audit(user.org_id, user.id, "voice.action.completed", action_id, {"kind": action["kind"], "result": result})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "The requested action could not be completed. No success was recorded.") from None
    return {"id": action_id, "status": "completed", "result": result}


def _execute(c, user: User, row: dict, action: dict) -> dict:
    ob_id = str(row["onboarding_id"])
    hc = lifecycle._hire(c, user, ob_id, visibility.HIRE)
    payload = action["payload"]
    if action["kind"] == "access_request":
        # A request is visible to the project manager, but does not grant anything.
        item = c.lifecycle.add_access_item(AccessItem(id="", onboarding_id=ob_id, system=payload["system"],
                                                       owner_id=hc.ob.manager_id, requested_at=hc.ctx.clock.now()))
        c.store.audit(user.org_id, user.id, "access.requested", item.id, {"system": item.system, "source": "voice"})
        return {"record_id": item.id, "status": item.status, "system": item.system}
    if action["kind"] == "kt_request":
        request = scheduling.SessionRequest(title="KT: " + payload["topic"], starts_at=None, duration_min=30,
                                            attendee_ids=[], provider="manual", join_url=None)
        meeting = scheduling.book(c, str(row["project_id"]), user.org_id, user, request)
        if not meeting:
            raise RuntimeError("KT request not created")
        c.store.audit(user.org_id, user.id, "meeting.created", meeting.id, {"source": "voice", "provider": "manual"})
        return {"record_id": meeting.id, "status": "unscheduled", "title": meeting.title}
    task = ProposedTask(title=payload["title"], description=payload["description"], kind="learn",
                        due_in_days=7, source="plan")
    created = c.store.create_tasks(ob_id, [task], date.today())[0]
    c.store.audit(user.org_id, user.id, "task.proposed", created.id, {"source": "voice"})
    return {"record_id": created.id, "status": created.status, "title": created.title}


@router.post("/actions/{action_id}/decline")
def decline(action_id: str, user: User = Depends(current_user), c=Depends(container)):
    try:
        uuid.UUID(action_id)
    except ValueError:
        raise HTTPException(404, "Pending voice action not found") from None
    row = c.store.one("""UPDATE voice_actions a SET status = 'declined', decided_at = now(), decided_by = :u
                 FROM voice_sessions s WHERE a.id = :a AND s.id = a.voice_session_id AND a.org_id = :o
                 AND s.user_id = :u AND a.status = 'pending' RETURNING a.id""", a=action_id, o=user.org_id, u=user.id)
    if not row:
        raise HTTPException(404, "Pending voice action not found")
    c.store.audit(user.org_id, user.id, "voice.action.declined", action_id, {})
    return {"id": action_id, "status": "declined"}
