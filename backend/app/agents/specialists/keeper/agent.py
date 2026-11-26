"""The Keeper: keeps explanations current with re-recordings, doc fixes and handovers.

It thinks (app/agents/core/thinking.py):
- After a push (`repo.pushed`, chained by the Supervisor after the Librarian). The lines each KT link on the changed files
  explained were read before the Librarian re-indexed (`snapshot`). For every link whose code changed at all, the Keeper reads
  the diff since the call and what the speaker said (`kt_changes`; read_file, find_symbol and kt_moments for more) and judges
  whether the explanation is now outdated: what was said no longer matches what the code does, not merely that lines moved.
  Code keeps the guardrails: only links of this push can be judged, a link whose file or symbol is gone is always outdated,
  and the proposals are one `rerecord` per (KT call, speaker), which a manager decides.
- On feedback (`feedback.created`). When a target has open "unclear" or "outdated" flags from 2 or more people (or 1
  "outdated" on a KT call), the Keeper reads the target and the notes (`feedback_target`; read_file, search_knowledge,
  kt_moments, verified_answers) and drafts the wording of a `doc_edit` for the owner (documents, files, verified answers), or
  a `rerecord` (KT calls). Nothing is ever written to a repository: code access stays read-only, and an approved edit of code
  is a suggestion the owner applies.
- On a handover (`handover.requested`). The checklist is the rules' (app/lifecycle/handover.py); the Keeper explores what the
  person uniquely explained or owns (`person_footprint`, kt_moments, read_file, kt_coverage, who_knows, code_owners) and plans
  the KT sessions to record (files from their footprint only, the most urgent first), a successor (someone the people tools
  named) and a note to them. The manager decides.

Fallback: with no loop (the AI off, the offline demo, "rules" mode, the daily budget) or no answer, the rules decide: a link
is possibly outdated when 30% or more of its linked lines changed, or its symbol (or file) is gone; the KT planner's sessions;
and the checked writer writes the proposals' summary, the doc edit and the note."""
from __future__ import annotations

import difflib
import logging

from app.agents.core import prompts
from app.agents.core import registry as R
from app.agents.core.schemas import KTLink

log = logging.getLogger("keeper")
KEY = "keeper"
OUTDATED_SHARE = 0.30
FLAGS = 2  # open flags, from different people, before a doc fix is drafted
SNAPSHOT_LINES = 400
EXCERPT_CHARS = 3000
FLAG_KINDS = frozenset({"unclear", "outdated"})


def link_id(link: KTLink | dict) -> str:
    l = link if isinstance(link, dict) else link.model_dump()
    return f"{l['meeting_id']}:{l['file_path']}:{l.get('symbol') or ''}:{float(l['start_s']):g}"


def _tell(store, user_id: str | None, kind: str, text: str, link: str) -> None:
    if not user_id:
        return
    try:
        store.notify(user_id, kind, text[:500], link)
    except Exception:  # noqa: BLE001 - a lost notice must never undo a proposal or a decision
        log.warning("keeper notification failed", exc_info=True)


# ---------------------------------------------------------------- possibly outdated explanations, after a push
def snapshot(deps, repo, paths: list[str]) -> list[dict]:
    """Before the Librarian re-indexes: the lines each KT link on these paths explained, as the indexed commit has them."""
    paths = set(paths)
    links = [l for l in deps.store.kt_links(repo.project_id) if l.file_path in paths]
    if not links:
        return []
    old_sha = repo.last_indexed_sha or repo.default_branch
    texts = {}
    for p in {l.file_path for l in links}:
        try:
            texts[p] = deps.repos.file(repo.full_name, p, old_sha, repo.installation_id)
        except Exception:  # noqa: BLE001 - unreadable: nothing to compare that link with
            texts[p] = None
    out = []
    for l in links:
        text = texts.get(l.file_path)
        if text is None:
            continue
        lines = text.splitlines()
        old = lines[l.start_line - 1:l.end_line] if l.start_line and l.end_line else lines[:SNAPSHOT_LINES]
        out.append({"link": l.model_dump(mode="json"), "old": "\n".join(old)})
    return out


def changed_share(old: list[str], new: list[str]) -> float:
    """The share of the old (non-blank) lines that are not in the new text in the same order."""
    a = [x.strip() for x in old if x.strip()]
    b = [x.strip() for x in new if x.strip()]
    if not a:
        return 0.0
    same = sum(m.size for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return round(1 - same / len(a), 2)


def _symbol(refs: dict, name: str | None):
    if not name:
        return None
    return refs.get(name) or next((r for s, r in refs.items() if s.rsplit(".", 1)[-1] == name.rsplit(".", 1)[-1]), None)


def measure(deps, project_id: str, repo_id: str, snap: list[dict]) -> list[dict]:
    """After the Librarian: each snapshot link with the code as it is now, the share of its lines that changed, whether its
    file or symbol is gone, and the rules' reason."""
    repo = deps.store.get_repo(repo_id)
    sha = repo.last_indexed_sha or repo.default_branch
    paths = sorted({s["link"]["file_path"] for s in snap})
    new = {p: deps.repos.file(repo.full_name, p, sha, repo.installation_id) for p in paths}
    refs = {p: {r.symbol: r for r in deps.index.symbols_in(project_id, p) if r.symbol} for p in paths}
    out = []
    for s in snap:
        l, p = s["link"], s["link"]["file_path"]
        target, gone = "", False
        if new[p] is None:
            share, why, gone = 1.0, f"{p} was removed", True
        else:
            lines = new[p].splitlines()
            ref = _symbol(refs[p], l.get("symbol"))
            if l.get("symbol") and ref is None:
                share, why, gone = 1.0, f"{l['symbol']} is gone from {p}", True
            else:
                rows = lines[ref.start_line - 1:ref.end_line] if ref and ref.start_line and ref.end_line else lines
                target = "\n".join(rows[:SNAPSHOT_LINES])
                share = changed_share(s["old"].splitlines(), rows)
                why = f"{round(share * 100)}% of the lines of {l.get('symbol') or p} changed since {l.get('meeting_title') or 'the KT call'}"
        out.append({"link": l, "path": p, "changed_share": share, "reason": why, "gone": gone, "old": s["old"], "new": target})
    return out


def outdated(deps, project_id: str, repo_id: str, snap: list[dict]) -> list[dict]:
    """The rules: the snapshot's links whose code changed by OUTDATED_SHARE or more, or whose symbol or file is gone."""
    return [{k: m[k] for k in ("link", "path", "changed_share", "reason")} for m in measure(deps, project_id, repo_id, snap)
            if m["changed_share"] >= OUTDATED_SHARE]


def _said(deps, project_id: str, link: dict) -> str:
    """What the KT session said around a link's moment, redacted (as kt_moments shows it)."""
    notes = deps.store.meeting_notes(link["meeting_id"])
    lines = []
    for u in (notes.utterances if notes else []):
        if u.end_s >= float(link["start_s"]) - 5 and u.start_s <= float(link["end_s"]) + 5:
            text = deps.redactor.redact(u.text)[0] if deps.redactor is not None else u.text
            lines.append(f"{u.speaker}: {text}")
    return "\n".join(lines[:12])


def judge_push(deps, ev: dict, measured: list[dict]):
    """The Keeper's judgement of the links whose code changed: (the outdated ones as changes, the Thought), or None when no loop
    could run. A link whose file or symbol is gone is outdated whatever the model says."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.schemas import KeeperJudgement
    from app.agents.tools import REGISTRY

    changed = [m for m in measured if m["changed_share"] > 0 or m["gone"]]
    if not changed:
        return [], None
    pid = ev["project_id"]
    notes = [{"id": link_id(m["link"]), "meeting_id": m["link"]["meeting_id"], "meeting_title": m["link"].get("meeting_title") or "KT session",
              "path": m["path"], "symbol": m["link"].get("symbol"), "changed_share": m["changed_share"], "gone": m["gone"],
              "old": m["old"], "new": m["new"], "said": _said(deps, pid, m["link"])} for m in changed[:12]]
    task = ("A push changed code that recorded KT sessions explained. Judge each changed link: is its explanation outdated now? "
            "Start with kt_changes.\n\n" + embed_data({"links": len(notes), "changed_paths": sorted({m["path"] for m in changed})}))
    t = thinking.think(deps, KEY, org_id=R.org_of(deps, pid), project_id=pid, user=None, system=prompts.KEEPER_PUSH_AGENT, task=task,
                       tools=REGISTRY.select("kt_changes", "read_file", "find_symbol", "kt_moments"), output=KeeperJudgement,
                       max_steps=8, timeout_s=40, notes={"kt_changes": notes})
    if t is None:
        return None
    if t.output is None:
        return None, t
    by_id = {link_id(m["link"]): m for m in changed}
    verdict: dict[str, tuple[bool, str]] = {}
    for j in t.output.links:
        if j.link.strip() not in by_id:
            t.note("verify", "Dropped a judgement of a link that is not in this push", shared="Dropped a judgement that failed a check")
            continue
        reason, _ = t.text(j.reason, 300)
        verdict[j.link.strip()] = (j.outdated, reason or by_id[j.link.strip()]["reason"])
    out = []
    for lid, m in by_id.items():
        judged, reason = verdict.get(lid, (m["changed_share"] >= OUTDATED_SHARE, m["reason"]))  # not judged: the rules
        if m["gone"]:
            judged, reason = True, m["reason"]  # the code it explained no longer exists
        if judged:
            out.append({"link": m["link"], "path": m["path"], "changed_share": m["changed_share"], "reason": reason})
    kept = len(out)
    t.note("final", f"Judged {len(by_id)} changed link{'s' if len(by_id) != 1 else ''}: {kept} outdated",
           shared=f"Judged the changed KT links: {kept} outdated")
    return out, t


def _speaker_groups(deps, project_id: str, changes: list[dict]) -> dict:
    from app.services.knowledge import Speakers

    speakers = Speakers(deps.store, deps.store.members(project_id))
    groups: dict = {}
    for ch in changes:
        if ch.get("link"):
            link = KTLink(**ch["link"])
            label, user = speakers.for_link(link)
            mid = link.meeting_id
        else:  # a KT call flagged as a whole, with no link: its main speaker
            mid, label, user = ch["meeting_id"], ch.get("speaker_label"), ch.get("speaker")
        sid = user.id if user else None
        g = groups.setdefault((mid, sid or label or ""), {
            "meeting_id": mid, "speaker": {"id": sid, "name": user.name if user else (label or "Unknown speaker")},
            "paths": {}, "links": [], "reasons": []})
        if ch.get("path"):
            share = ch.get("changed_share")
            old = g["paths"].get(ch["path"])
            g["paths"][ch["path"]] = share if old is None else (max(old, share) if share is not None else old)
        if ch.get("link"):
            g["links"].append(link_id(ch["link"]))
        if ch.get("reason") and ch["reason"] not in g["reasons"]:
            g["reasons"].append(ch["reason"])
    return groups


def propose_rerecords(deps, project_id: str, thread: str, changes: list[dict], trigger: str, feedback_ids: list[str] = (),
                      trace: str | None = None, thought=None) -> list[str]:
    """One rerecord approval per (KT call, speaker) that no pending one covers yet. Returns the ids this run parks on."""
    store = deps.store
    org = R.org_of(deps, project_id)
    existing = [a for a in store.approvals(project_id) if a.kind == "rerecord"]
    ids = []
    for (mid, _), g in _speaker_groups(deps, project_id, changes).items():
        match = next((a for a in existing if a.payload.get("meeting_id") == mid
                      and (a.payload.get("speaker") or {}).get("id") == g["speaker"]["id"]
                      and (a.payload.get("speaker") or {}).get("name") == g["speaker"]["name"]), None)
        if match:
            if match.thread_id == thread:  # a retry after this run saved it
                ids.append(match.id)
            continue
        meeting = store.get_meeting(mid)
        title = meeting.title if meeting else "a KT session"
        paths = [{"path": p, "changed_share": s} for p, s in sorted(g["paths"].items())]
        where = ", ".join(p["path"] for p in paths) or "the call"
        draft = (f"{g['speaker']['name'].split(' (')[0]}'s explanation in '{title}' may be out of date ({where}): "
                 + "; ".join(g["reasons"][:3]) + ".")
        if thought is not None:  # the Keeper's own reasons, already checked: no second model call to reword them
            text, ai = draft[:300], True
            rule = ("The Keeper read the change and what the session said, and judged the explanation outdated (a removed file or "
                    "symbol always is).")
        else:
            text, ai, _ = R.write(deps, KEY, org, system=prompts.KEEPER_RERECORD,
                                  data={"meeting_title": title, "speaker": g["speaker"]["name"], "paths": paths, "reasons": g["reasons"]},
                                  draft=draft, max_len=300)
            rule = ("Rule: a KT link is possibly outdated when 30% or more of its linked lines changed, or its symbol is gone."
                    if trigger == "repo.pushed" else "Rule: one 'outdated' flag on a KT call, or two flags from different people.")
        sources = [R.source(p["path"], "code", path=p["path"], changed_share=p["changed_share"]) for p in paths]
        sources += [R.source(title, "kt", meeting_id=mid)] + [R.source("Reader feedback", "event", feedback_id=f) for f in feedback_ids]
        payload = {"meeting_id": mid, "meeting_title": title, "speaker": g["speaker"], "paths": paths, "links": g["links"],
                   "feedback_ids": list(feedback_ids), "trigger": trigger, "summary": text,
                   "explain": R.explain(KEY, g["reasons"] + [rule, "A manager decides; approving books the re-recording with the speaker."],
                                        sources, None, ai, trace)}
        if thought is not None:
            from app.agents.core.runtime import shared_steps

            payload["agent"] = {"run_id": thought.run_id, "steps": shared_steps(thought.steps)}
        a = store.create_approval(project_id, "rerecord", thread, payload)
        for m in store.managers(project_id):
            _tell(store, m.id, "approval", f"The Keeper suggests re-recording '{title}' with {g['speaker']['name']}.",
                  f"/approvals/{a.id}?project={project_id}")
        ids.append(a.id)
    return ids


def after_push(deps, ev: dict, snap: list[dict], thread: str, trace: str | None = None) -> list[str]:
    return after_push_traced(deps, ev, snap, thread, trace)[0]


def after_push_traced(deps, ev: dict, snap: list[dict], thread: str, trace: str | None = None):
    """(the rerecord approvals this run parks on, the run record's trace or None): the Keeper's judgement, else the 30% rule."""
    measured = measure(deps, ev["project_id"], ev["repository_id"], snap)
    judged = judge_push(deps, ev, measured)
    thought = judged[1] if judged else None
    if judged is None or judged[0] is None:
        changes = [{k: m[k] for k in ("link", "path", "changed_share", "reason")} for m in measured if m["changed_share"] >= OUTDATED_SHARE]
        agentic = None
    else:
        changes, agentic = judged[0], thought
    ids = propose_rerecords(deps, ev["project_id"], thread, changes, "repo.pushed", trace=trace, thought=agentic) if changes else []
    return ids, thought.trace() if thought is not None else None


# ---------------------------------------------------------------- reader feedback
def _excerpt(deps, project_id: str, kind: str, ref: str) -> tuple[str, str]:
    """(title, current excerpt) of a flagged target, read through the tools: the file at the indexed commit, the document's
    first page, the verified answer."""
    store = deps.store
    try:
        if kind == "code":
            repo = next(iter(store.repos_for(project_id)), None)
            text = deps.repos.file(repo.full_name, ref, repo.last_indexed_sha or repo.default_branch, repo.installation_id) if repo else None
            return ref, (text or "")[:EXCERPT_CHARS]
        if kind == "doc":
            from app.services.documents import extract_pages

            d = store.get_document(ref) or {}
            pages = extract_pages(deps.blobs.get(d["uri"]), d["mime"]) if d.get("uri") else []
            return d.get("title") or "Document", (pages[0] if pages else "")[:EXCERPT_CHARS]
        if kind == "answer":
            a = next((a for a in deps.lifecycle.verified_answers(project_id) if a.id == ref), None)
            return (f"Verified answer: {a.question[:80]}" if a else "Verified answer"), (a.answer if a else "")[:EXCERPT_CHARS]
    except Exception:  # noqa: BLE001 - the excerpt is context for the draft: without it the readers' notes still stand
        log.warning("excerpt unreadable", exc_info=True)
    return ref, ""


def _doc_draft(title: str, flags: list) -> str:
    lines = [f"Readers marked {title} as {' and '.join(sorted({f.kind for f in flags}))} ({len({f.created_by for f in flags})} people)."]
    for f in flags[:6]:
        note = " ".join((f.note or "").split())[:240]
        lines.append(f"- {'Update' if f.kind == 'outdated' else 'Clarify'}: {note or '(no note)'}")
    lines.append("Suggested change: answer each point above where the reader stopped; keep the rest as it is.")
    return "\n".join(lines)


def feedback_created(deps, ev: dict, thread: str, trace: str | None = None) -> list[str]:
    """feedback.created {project_id, feedback_id}: a doc_edit or a rerecord once the target has enough flags."""
    return feedback_created_traced(deps, ev, thread, trace)[0]


def feedback_created_traced(deps, ev: dict, thread: str, trace: str | None = None):
    """(the approvals this run parks on, the run record's trace or None)."""
    lc, store = deps.lifecycle, deps.store
    fb = lc.get_feedback(ev["feedback_id"])
    if not fb or fb.status != "open":
        return [], None
    pid = fb.project_id
    flags = [f for f in lc.feedback(pid) if f.target_kind == fb.target_kind and f.target_ref == fb.target_ref and f.status == "open"
             and f.kind in FLAG_KINDS]
    people = {f.created_by for f in flags}
    outdated_ = [f for f in flags if f.kind == "outdated"]
    ids = [f.id for f in flags]
    if fb.target_kind == "meeting":
        if not outdated_ and len(people) < FLAGS:
            return [], None
        return _rerecord_from_feedback(deps, pid, fb.target_ref, flags, thread, trace), None
    if len(people) < FLAGS:
        return [], None
    for a in store.approvals(pid):
        if a.kind == "doc_edit" and a.payload.get("target_kind") == fb.target_kind and a.payload.get("target_ref") == fb.target_ref:
            return ([a.id] if a.thread_id == thread else []), None  # one pending fix per target; approving it resolves every flag
    org = R.org_of(deps, pid)
    title, excerpt = _excerpt(deps, pid, fb.target_kind, fb.target_ref)
    draft = _doc_draft(title, flags)
    data = {"target": {"kind": fb.target_kind, "ref": fb.target_ref, "title": title}, "current_excerpt": excerpt,
            "reader_notes": [{"kind": f.kind, "note": f.note} for f in flags]}
    t = draft_doc_edit(deps, pid, org, fb, title, excerpt, flags)
    agent_sources = []
    if t is not None and t.output is not None:
        text, dropped = t.text(t.output.proposed_text, 2000, draft, numbers=False)
        if text is None:
            t.note("verify", f"The wording was not used: {dropped}", shared="The wording failed a check")
            text, ai = draft, False
        else:
            ai, dropped = True, None
            agent_sources = t.sources(t.output.sources)
    else:
        text, ai, dropped = R.write(deps, KEY, org, system=prompts.KEEPER_DOC_EDIT, data=data, draft=draft, max_len=2000, numbers=False)
    logic = [f"{len(people)} people marked {title} as {' or '.join(sorted({f.kind for f in flags}))}: a fix is drafted at {FLAGS}.",
             "The owner applies it: Welcome Wave never writes to a repository." if fb.target_kind == "code"
             else "Approving stores it as an accepted edit of the document." if fb.target_kind == "doc"
             else "Approving sends it to the answer's owner."]
    if dropped:
        logic.append(f"The AI's wording was not used: {dropped}.")
    kind = {"code": "code", "doc": "doc", "answer": "doc"}[fb.target_kind]
    sources = [R.source(title, kind, target_kind=fb.target_kind, target_ref=fb.target_ref)] + agent_sources
    sources += [R.source(f"{f.kind.capitalize()} flag", "event", feedback_id=f.id) for f in flags]
    payload = {"target_kind": fb.target_kind, "target_ref": fb.target_ref, "title": title, "feedback_ids": ids, "owner_id": fb.owner_id,
               "current_excerpt": excerpt, "proposed_text": text, "explain": R.explain(KEY, logic, sources[:12], None, ai, trace)}
    if t is not None:
        from app.agents.core.runtime import shared_steps

        payload["agent"] = {"run_id": t.run_id, "steps": shared_steps(t.steps)}
        rationale, _ = t.text(t.output.rationale, 400, draft, numbers=False) if t.output is not None else (None, None)
        if rationale:
            payload["rationale"] = rationale
    a = store.create_approval(pid, "doc_edit", thread, payload)
    for m in store.managers(pid):
        _tell(store, m.id, "approval", f"The Keeper drafted a fix for {title[:80]}, which readers flagged.", f"/approvals/{a.id}?project={pid}")
    return [a.id], t.trace() if t is not None else None


def draft_doc_edit(deps, project_id: str, org: str, fb, title: str, excerpt: str, flags: list):
    """The Keeper's loop over a flagged target: its Thought (output a DocEditDraft), or None when no loop could run."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.schemas import DocEditDraft
    from app.agents.tools import REGISTRY

    target = {"kind": fb.target_kind, "ref": fb.target_ref, "title": title, "current_excerpt": excerpt,
              "reader_notes": [{"kind": f.kind, "note": (f.note or "")[:1000]} for f in flags[:10]]}
    task = ("Readers flagged this target. Read it and the notes with feedback_target, check what is true now, and draft the wording "
            "for its owner.\n\n" + embed_data({"target_kind": fb.target_kind, "title": title, "flags": len(flags)}))
    return thinking.think(deps, KEY, org_id=org, project_id=project_id, user=None, system=prompts.KEEPER_FEEDBACK_AGENT, task=task,
                          tools=REGISTRY.select("feedback_target", "read_file", "search_knowledge", "kt_moments", "verified_answers"),
                          output=DocEditDraft, max_steps=5, timeout_s=30, notes={"target": target})


def _rerecord_from_feedback(deps, project_id: str, meeting_id: str, flags: list, thread: str, trace: str | None) -> list[str]:
    from app.services.knowledge import Speakers, dominant_speaker

    n_out = sum(f.kind == "outdated" for f in flags)
    reason = (f"{n_out} reader{'s' if n_out != 1 else ''} marked it outdated" if n_out else
              f"{len({f.created_by for f in flags})} readers marked it unclear")
    links = [l for l in deps.store.kt_links(project_id) if l.meeting_id == meeting_id]
    changes = [{"link": l.model_dump(mode="json"), "path": l.file_path, "changed_share": None, "reason": reason} for l in links]
    if not changes:
        notes = deps.store.meeting_notes(meeting_id)
        label = dominant_speaker(notes.utterances, 0, 10**9) if notes else None
        user = Speakers(deps.store, deps.store.members(project_id)).member(label)
        changes = [{"meeting_id": meeting_id, "speaker_label": label, "speaker": user, "reason": reason}]
    return propose_rerecords(deps, project_id, thread, changes, "feedback.created", [f.id for f in flags], trace)


# ---------------------------------------------------------------- decisions
def _resolve(deps, project_id: str, ids: list[str], target: tuple[str, str] | None, how: str) -> list[str]:
    """Close the flags a decision answered (and any other open flag on the same target), telling each sender."""
    lc, store = deps.lifecycle, deps.store
    rows = {f.id: f for f in lc.feedback(project_id) if f.status == "open"
            and (f.id in set(ids) or (target and (f.target_kind, f.target_ref) == target and f.kind in FLAG_KINDS))}
    for f in rows.values():
        lc.update_feedback(f.id, status="resolved")
        _tell(store, f.created_by, "feedback", f"Your feedback on {f.target_ref[:80]} was resolved: {how}", f"/feedback?project={project_id}")
    return sorted(rows)


def apply_rerecord(deps, approval, user_id: str) -> dict:
    """Approve: a KT session 'Re-record: <title>' with no time set (provider manual), booked for the speaker, who is told."""
    from app.ports import Attendee

    store, p, pid = deps.store, approval.payload, approval.project_id
    speaker = store.get_user(p["speaker"]["id"]) if (p.get("speaker") or {}).get("id") else None
    key = f"rerecord:{approval.id}"
    m = store.create_meeting(pid, f"Re-record: {p['meeting_title']}"[:200], user_id or None, kt_key=key, provider="manual",
                             attendees=[Attendee(email=speaker.email, name=speaker.name, user_id=speaker.id)] if speaker else [])
    if m is None:  # a retried decision: the session exists
        m = next(x for x in store.list_meetings(pid) if x.kt_key == key)
    decider = store.get_user(user_id) if user_id else None
    where = ", ".join(x["path"] for x in p.get("paths", [])) or "the call"
    if speaker and speaker.id != user_id:
        _tell(store, speaker.id, "kt_request",
              f"{decider.name if decider else 'A manager'} asked you to re-record '{p['meeting_title']}' ({where}). Pick a time in KT sessions.",
              f"/meetings?call={m.id}&project={pid}")
    resolved = _resolve(deps, pid, p.get("feedback_ids", []), None, "a re-recording was booked.") if p.get("feedback_ids") else []
    org = R.org_of(deps, pid)
    store.audit(org, user_id or None, "kt.rerecord_booked", m.id, {"approval_id": approval.id, "meeting_id": p["meeting_id"],
                                                                     "speaker_id": (p.get("speaker") or {}).get("id")})
    return {"meeting_id": m.id, "title": m.title, "speaker": p.get("speaker"), "resolved_feedback": resolved}


def apply_doc_edit(deps, approval, user_id: str, text: str | None = None) -> dict:
    """Approve: the wording (as the manager edited it) is stored as an accepted edit of a document, or as a suggestion for the
    owner of code, a KT call or an answer; every flag on the target is resolved. Nothing is written to a repository."""
    from app.ports import DocEdit

    store, p, pid = deps.store, approval.payload, approval.project_id
    org = R.org_of(deps, pid)
    wording = ((text or "").strip() or p["proposed_text"])[:8000]
    edit = deps.lifecycle.add_doc_edit(DocEdit(
        id="", org_id=org, project_id=pid, approval_id=approval.id, target_kind=p["target_kind"], target_ref=p["target_ref"],
        title=p["title"][:300], proposed_text=wording, current_excerpt=(p.get("current_excerpt") or "")[:8000],
        feedback_ids=list(p.get("feedback_ids", [])), status="accepted" if p["target_kind"] == "doc" else "suggested",
        owner_id=p.get("owner_id"), accepted_by=user_id or None))
    how = "an edit was accepted." if edit.status == "accepted" else "the owner has suggested wording to apply."
    resolved = _resolve(deps, pid, edit.feedback_ids, (p["target_kind"], p["target_ref"]), how)
    if p.get("owner_id") and p["owner_id"] != user_id:
        text_ = (f"An edit of {p['title'][:80]} was accepted from reader feedback." if edit.status == "accepted" else
                 f"Suggested wording for {p['title'][:80]}, from reader feedback. Apply it yourself: Welcome Wave never writes to "
                 "the repository.")
        _tell(store, p["owner_id"], "feedback", text_, f"/feedback?project={pid}")
    return {"edit": edit.model_dump(mode="json"), "resolved_feedback": resolved}


# ---------------------------------------------------------------- handovers
def handover(deps, ctx, project_id: str, person, last_day, trace: str | None = None):
    """handover.requested: the plan (handover.create), the KT sessions to record with their agendas, a note to the person."""
    return handover_traced(deps, ctx, project_id, person, last_day, trace)[0]


MAX_SESSIONS = 6


def handover_traced(deps, ctx, project_id: str, person, last_day, trace: str | None = None, run_id: str | None = None):
    """(the handover, the Keeper's Thought or None). The checklist is the rules'; the Keeper plans the sessions, the successor
    and the note when it can think (checked: footprint files only, a successor the people tools named), else the KT planner's
    sessions and the checked writer's note."""
    from app.lifecycle import handover as H
    from app.services.kt_planner import agenda_text, build_plan

    h = H.create(ctx, project_id, person, last_day)
    plan = dict(h.plan)
    items = plan.get("items", [])
    counts = {k: sum(i["kind"] == k for i in items) for k in ("file", "session", "request", "mentee", "access", "feedback")}
    parts = [f"{n} {label}" for label, n in (("KT sessions to record", counts["session"]), ("files to explain", counts["file"]),
                                             ("open questions", counts["request"]), ("mentees to hand over", counts["mentee"]),
                                             ("access items", counts["access"]), ("feedback items", counts["feedback"])) if n]
    first = person.name.split(" (")[0]
    draft = (f"{first}, before your last day ({last_day:%d %b}): " + (", ".join(parts) if parts else "nothing is left to hand over")
             + ". Your successor and manager can see this list.")
    t = think_handover(deps, ctx, project_id, person, last_day, run_id)
    sources = [R.source(f, "code", path=f) for f in plan.get("orphaned_files", [])[:6]]
    if t is not None and t.output is not None:
        out = t.output
        allowed = t.ctx.notes.get("footprint_paths", set())
        sessions = []
        for x in out.sessions[:MAX_SESSIONS]:
            title, _ = t.text(x.name, 200)
            agenda = [a for a in (t.text(y, 200)[0] for y in x.agenda[:5]) if a]
            paths = [p for p in dict.fromkeys(str(q).strip().lstrip("./") for q in x.paths) if p in allowed][:8]
            if not title or not (paths or agenda):
                t.note("verify", "Dropped a session with no file of theirs or no agenda", shared="Dropped a session that failed a check")
                continue
            why, _ = t.text(x.why, 300)
            sessions.append({"key": f"keeper:{len(sessions) + 1}", "title": title, "est_minutes": max(15, min(120, int(x.minutes or 30))),
                             "agenda": "\n".join(f"- {a}" for a in agenda), "paths": paths, "why": why or ""})
        successor = out.successor_id.strip()
        if successor and (successor not in t.ctx.people or successor == person.id):
            t.note("verify", "Kept the rules' successor: the one suggested was not named by a tool", shared="Kept the rules' successor")
            successor = ""
        note, why_not = t.text(out.note, 500, draft)
        if note is None:
            t.note("verify", f"The note's wording was not used: {why_not}", shared="The note's wording failed a check")
        if sessions:
            plan["kt_sessions"] = sessions
        else:
            plan["kt_sessions"] = _planner_sessions(ctx, project_id, person, build_plan, agenda_text)
        if successor:
            sw, _ = t.text(out.successor_why, 300)
            plan["successor_id"] = successor
            plan["why"] = list(plan.get("why", [])) + [f"The Keeper suggests the successor: {sw or 'they know this code.'}"]
        plan["note"] = note or draft
        plan["agent"] = {"run_id": t.run_id, "steps": _shared(t)}
        sources += [R.source(s_["title"], "kt", session_key=s_["key"]) for s_ in plan["kt_sessions"][:6]]
        plan["explain"] = R.explain(KEY, list(plan.get("why", [])) + [f"The Keeper planned {len(sessions)} KT session(s) from what only "
                                                                      f"{first} explained or owns."], sources, None, note is not None, trace)
        return ctx.lc.update_handover(h.id, plan=plan), t
    plan["kt_sessions"] = _planner_sessions(ctx, project_id, person, build_plan, agenda_text)
    text, ai, _ = R.write(deps, KEY, person.org_id, system=prompts.KEEPER_HANDOVER,
                          data={"person_first_name": first, "last_day": f"{last_day:%d %b}", "to_do": parts,
                                "kt_sessions": [s_["title"] for s_ in plan["kt_sessions"]]}, draft=draft, max_len=500)
    plan["note"] = text
    sources += [R.source(s_["title"], "kt", session_key=s_["key"]) for s_ in plan["kt_sessions"][:6]]
    plan["explain"] = R.explain(KEY, plan.get("why", []), sources, None, ai, trace)
    return ctx.lc.update_handover(h.id, plan=plan), t


def _shared(t) -> list[dict]:
    from app.agents.core.runtime import shared_steps

    return shared_steps(t.steps)


def _planner_sessions(ctx, project_id: str, person, build_plan, agenda_text) -> list[dict]:
    """The KT planner's sessions for the person, with their agendas (the rules' sessions)."""
    try:
        sessions = build_plan(ctx.store, ctx.index, project_id, person.org_id, ctx.user, person.id)["sessions"]
    except Exception:  # noqa: BLE001 - the plan's checklist already lists them; the agendas are a nicety
        log.warning("handover sessions unreadable", exc_info=True)
        sessions = []
    return [{"key": s["key"], "title": s["title"], "est_minutes": s.get("est_minutes"), "agenda": agenda_text(s.get("items") or [])}
            for s in sessions]


def think_handover(deps, ctx, project_id: str, person, last_day, run_id: str | None = None):
    """The Keeper's loop over what the person leaving holds: its Thought (output a HandoverDraft), or None."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.schemas import HandoverDraft
    from app.agents.tools import REGISTRY

    task = ("Plan the handover of the person leaving: what only they explained or own, as KT sessions to record before their last "
            "day, and who should take over. Start with person_footprint.\n\n"
            + embed_data({"person": {"user_id": person.id, "name": person.name}, "last_day": f"{last_day:%d %b %Y}"}))
    return thinking.think(deps, KEY, org_id=person.org_id, project_id=project_id, user=ctx.user, system=prompts.KEEPER_HANDOVER_AGENT,
                          task=task, tools=REGISTRY.select("person_footprint", "kt_moments", "read_file", "kt_coverage", "who_knows",
                                                           "code_owners"),
                          output=HandoverDraft, run_id=run_id, max_steps=8, timeout_s=40, clock=ctx.clock,
                          notes={"person_id": person.id})
