"""Pre-boarding: before day one, the hire opens a link (no sign-in) to acknowledge documents, see their access being
prepared, and check their development environment.

The link is the permission, so it is narrow: a random token (256 bits) shown once to the manager who made it; only its
SHA-256 is stored, and it expires (LINK_DAYS, on the real clock, never the demo one). A new link replaces the old one.
The portal shows names, dates, documents and access *status* only: no code, no recordings, no chat, no other people's
data, and nothing it accepts can change a role, an approval or a routing target. What the hire sends (acknowledgements,
environment results) is stored as data and shown back; it is never interpreted."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from app.lifecycle import templates as T
from app.lifecycle.context import HireCtx
from app.ports import EnvironmentCheck

LINK_DAYS = 14
MAX_RESULTS = 30
TOOLS = {  # what the environment check looks for, per role track: (name, command that prints a version)
    "Backend": [("git", "git --version"), ("python3", "python3 --version"), ("docker", "docker --version"), ("make", "make --version")],
    "Frontend": [("git", "git --version"), ("node", "node --version"), ("npm", "npm --version")],
    "SRE": [("git", "git --version"), ("docker", "docker --version"), ("kubectl", "kubectl version --client"),
            ("terraform", "terraform -version")],
    "Data": [("git", "git --version"), ("python3", "python3 --version"), ("docker", "docker --version")],
}
_NAME = re.compile(r"^[A-Za-z0-9._+-]{1,40}$")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(hc: HireCtx) -> tuple[str, datetime]:
    """A new link for the hire; the token is returned once and never stored."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=LINK_DAYS)
    hc.ctx.lc.set_preboarding(hc.ob.id, token_hash(token), expires)
    return token, expires


def resolve(lc, token: str) -> tuple[str, str] | None:
    """(onboarding id, org id) for a live link, else None: unknown, replaced and expired links look the same."""
    if not token or len(token) > 100:
        return None
    found = lc.program_by_token(token_hash(token))
    if not found or found[2] <= datetime.now(timezone.utc):
        return None
    return found[0], found[1]


def ip_hash(token: str, ip: str) -> str:
    """Which address acknowledged, comparable within one link, never readable: keyed with the token, which is not stored."""
    return hmac.new(token.encode(), (ip or "").encode(), hashlib.sha256).hexdigest()[:32]


def tools_for(hc: HireCtx) -> list[tuple[str, str]]:
    t = hc.ctx.lc.get_template(hc.program.template_id)
    return TOOLS.get(t.role_track if t else "Backend", TOOLS["Backend"])


FIRST_WEEK_DAYS = 5  # the template's items due by this program day, and KT sessions booked in this many working days
MAX_FIRST_WEEK = 12
_MILESTONE_KIND = {"preboarding_complete": "welcome", "environment_ready": "setup", "access_ready": "setup", "meet_team": "meet"}
_PLAN_KIND = {"learn": "learn", "meet": "meet", "code": "task"}
_ORDER = {"welcome": 0, "setup": 1, "meet": 2, "kt": 3, "learn": 4, "task": 5}


def _working_days_end(start: date, n: int) -> date:
    """The date of the n-th working day (Monday to Friday) counting the start date's own day if it is one."""
    d, counted = start, 0
    while True:
        if d.weekday() < 5:
            counted += 1
            if counted == n:
                return d
        d += timedelta(days=1)


def first_week(hc: HireCtx) -> list[dict]:
    """What the hire's first week holds, for the portal: [{date, title, text?, done?, kind?}], oldest first.

    Built from the TEMPLATE (an admin's own words: its milestones and plan items due by day FIRST_WEEK_DAYS, scaled to the
    program's length) and from the KT sessions the hire is invited to in their first five working days (title and day only: no
    join link, no recording). Never from the hire's own tasks, which an agent writes from code and a manager has not approved
    yet, and never from anyone else's data: the link is the only permission the portal has."""
    ctx, ob, program = hc.ctx, hc.ob, hc.program
    template = ctx.lc.get_template(program.template_id)
    day = lambda n: ob.start_date + timedelta(days=int(n))  # noqa: E731
    acknowledged = bool(hc.acknowledgements) and all(a.acknowledged_at for a in hc.acknowledgements)
    items: list[dict] = []
    if hc.milestones:  # the approved plan's own milestones
        milestones = [(m.key, m.title, m.due_day, "", m.status == "done") for m in hc.milestones]
    else:
        milestones = [(m["key"], m["title"], T.scale(int(m["due_day"]), program.duration_days), m.get("description", ""),
                       m["key"] == "preboarding_complete" and acknowledged) for m in (template.milestones if template else [])]
    for key, title, due, text, done in milestones:
        if due <= FIRST_WEEK_DAYS:
            items.append({"date": day(due).isoformat(), "title": title, "kind": _MILESTONE_KIND.get(key, "task"), "done": bool(done),
                          **({"text": text[:200]} if text else {})})
    seen = {i["title"].lower() for i in items}
    finished = {t.title for t in hc.tasks if t.status == "done"}
    for p in (template.plan_items if template else []):
        due = T.scale(int(p.get("due_day", 7)), program.duration_days)
        if due <= FIRST_WEEK_DAYS and p["title"].lower() not in seen:
            seen.add(p["title"].lower())
            items.append({"date": day(due).isoformat(), "title": p["title"], "kind": _PLAN_KIND.get(p.get("kind", "learn"), "learn"),
                          "done": p["title"] in finished, **({"text": p["description"][:200]} if p.get("description") else {})})
    last, email = _working_days_end(ob.start_date, FIRST_WEEK_DAYS), ((hc.hire.email if hc.hire else "") or "").lower()
    for m in ctx.store.list_meetings(ob.project_id):
        if m.status in {"cancelled", "failed"} or not m.starts_at:
            continue
        at = m.starts_at if m.starts_at.tzinfo is None else m.starts_at.astimezone(timezone.utc)
        invited = any(a.user_id == ob.new_hire_id or (email and a.email.lower() == email) for a in m.attendees)
        if invited and ob.start_date <= at.date() <= last:
            items.append({"date": at.date().isoformat(), "title": m.title, "kind": "kt", "text": f"KT session, {at:%H:%M} UTC",
                          "done": m.status in {"processing", "ready", "ended"}})
    items.sort(key=lambda i: (i["date"], _ORDER.get(i["kind"], 9), i["title"]))
    return items[:MAX_FIRST_WEEK]


def portal(hc: HireCtx) -> dict:
    ctx, ob = hc.ctx, hc.ob
    project = ctx.store.get_project(ob.project_id)
    team = ctx.lc.get_team(hc.program.team_id) if hc.program.team_id else None
    first = (hc.hire.name if hc.hire else "").split(" (")[0].split(" ")[0]
    latest = hc.environment_checks[-1] if hc.environment_checks else None
    week = first_week(hc)
    return {
        "hire": {"first_name": first},
        "project": project.name, "team": team.name if team else None, "role_title": ob.role_title,
        "start_date": ob.start_date, "days_until_start": max(0, (ob.start_date - ctx.clock.today()).days),
        "manager": (ctx.person(ob.manager_id) or {}).get("name"), "mentor": (ctx.person(hc.program.mentor_id) or {}).get("name"),
        "acknowledgements": [{"doc_key": a.doc_key, "title": a.title, "acknowledged_at": a.acknowledged_at} for a in hc.acknowledgements],
        "access": [{"system": a.system, "status": a.status} for a in hc.access],
        "environment": {"tools": [n for n, _ in tools_for(hc)],
                        "latest": latest.model_dump(exclude={"id", "onboarding_id"}) if latest else None},
        "expires_at": hc.program.preboarding_expires_at,
        **({"first_week": week} if week else {}),  # absent when the template holds nothing for it: the page shows its generic list
    }


def acknowledge(hc: HireCtx, doc_key: str, token: str, ip: str):
    return hc.ctx.lc.acknowledge(hc.ob.id, doc_key, ip_hash(token, ip), hc.ctx.clock.now())


def script(hc: HireCtx, post_url: str) -> str:
    """A shell script that reads tool versions and posts them back. It changes nothing on the machine."""
    checks = "\n".join(f"check {name} {cmd}" for name, cmd in tools_for(hc))
    return f"""#!/usr/bin/env sh
# Welcome Wave environment check. It only asks each tool for its version, then sends the results to your pre-boarding page.
# It installs nothing and changes nothing. Run it with: sh env-check.sh
results=""
check() {{
  name="$1"; shift
  if ! command -v "$1" >/dev/null 2>&1; then ok=false; out="not installed"
  elif out=$("$@" 2>&1); then ok=true; else ok=false; fi
  out=$(printf '%s' "$out" | head -n 1 | tr -d '"\\\\' | cut -c1-160)
  item="{{\\"name\\":\\"$name\\",\\"ok\\":$ok,\\"detail\\":\\"$out\\"}}"
  if [ -z "$results" ]; then results="$item"; else results="$results,$item"; fi
  echo "$name: $ok ($out)"
}}
{checks}
curl -fsS -X POST -H 'Content-Type: application/json' --data "{{\\"results\\":[$results]}}" '{post_url}' >/dev/null \\
  && echo "Sent. Reload your pre-boarding page." || echo "Could not send the results; paste them to your manager."
"""


def record(hc: HireCtx, results: list[dict]) -> EnvironmentCheck:
    """Stores what the script reported, cleaned: known-length names, short details, a boolean each."""
    clean = []
    for r in results[:MAX_RESULTS]:
        name = str(r.get("name", ""))
        if _NAME.match(name):
            clean.append({"name": name, "ok": r.get("ok") is True, "detail": str(r.get("detail", ""))[:200]})
    ok = bool(clean) and all(r["ok"] for r in clean)
    return hc.ctx.lc.add_environment_check(EnvironmentCheck(id="", onboarding_id=hc.ob.id, ran_at=hc.ctx.clock.now(), ok=ok, results=clean))
