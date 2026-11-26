"""GET /api/live: the signed-in browser's stream of live changes (Server-Sent Events; see app/live.py).
    ?topics=a,b   only these topics; `agent:<run_id>` streams the steps of the caller's own agent run as `event: step`
                  (data: {"t": "agent:<run_id>", "p": project id, "step": {n, kind, text, tool, args, sources, ms}}).
GET /api/blobs/<token>: a file behind a signed link (self-hosted call recordings; Cloud Storage serves its own on Google Cloud).

The stream ends after STREAM_S so the browser reconnects with a fresh sign-in token (Firebase tokens last an hour), and it
sends a comment line every PING_S so proxies keep an idle connection open."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.api.auth import container, current_user
from app.live import AGENT_TOPIC
from app.ports import User

router = APIRouter()
STREAM_S = 15 * 60
PING_S = 20


TOPIC = re.compile(r"[a-z_]{1,40}|\*")
MAX_TOPICS = 10


def _topics(raw: str | None) -> list[str] | None:
    """The topics a stream asked for: change topics by name, agent runs as agent:<uuid>. A malformed one is a 422."""
    if raw is None:
        return None
    topics = [t.strip().lower() for t in raw.split(",") if t.strip()]
    if not topics or len(topics) > MAX_TOPICS or not all(TOPIC.fullmatch(t) or AGENT_TOPIC.fullmatch(t) for t in topics):
        raise HTTPException(422, f"topics: up to {MAX_TOPICS} topic names or agent:<run id>, comma-separated")
    return topics


@router.get("/live")
async def live(request: Request, topics: str | None = Query(None, max_length=600), user: User = Depends(current_user)):
    hub = request.app.state.live
    sub = hub.subscribe(user.org_id, user.id, _topics(topics))
    loop = asyncio.get_running_loop()

    async def events():
        try:
            yield "retry: 3000\n\n"
            yield f"event: ready\ndata: {json.dumps({'user': user.id})}\n\n"
            end = loop.time() + STREAM_S
            while loop.time() < end:
                if await request.is_disconnected():
                    return
                try:
                    change = await asyncio.wait_for(sub.queue.get(), PING_S)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                kind = "step" if "step" in change else "change"
                yield f"event: {kind}\ndata: {json.dumps(change, default=str)}\n\n"
        finally:
            hub.unsubscribe(sub)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.get("/blobs/{token}")
def blob(token: str, c=Depends(container)):
    """The link is the permission (it is signed, names one file and expires), like a Cloud Storage signed URL."""
    verify = getattr(c.deps.blobs, "verify", None)
    uri = verify(token) if verify else None
    if not uri:
        raise HTTPException(404, "This link has expired. Reload the page for a new one.")
    path = c.deps.blobs.path(uri)
    if not os.path.isfile(path):
        raise HTTPException(404, "The file is gone")
    media = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
                        content_disposition_type="inline")
