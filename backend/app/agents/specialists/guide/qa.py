"""Project Q&A agent (chat.question): answers with citations, attaches KT clips, and hands low-confidence questions to a human.

The answer is the Guide's deterministic tier 1 (app/lifecycle/routing.answer_from): the research crew's evidence
(app/rag/crew.py), one writer (the model, or without one the offline writer quoting the sources), the same retrieval gate
(routing.answer_gate) and the same citation check on every sentence as POST /api/ask/route, so the chat never publishes a
claim the Ask page would refuse. What it cannot answer goes to a person by the one capped path (routing.deliver)."""
from __future__ import annotations

from app.agents.core.citations import label_for
from app.agents.core.deps import Deps
from app.agents.core.schemas import Chunk, Citation, Hit, QAResult
from app.ports import norm_question
from app.services.codeowners import CATCH_ALL, parse_codeowners, rule_for

NOT_ENOUGH = "I don't have enough information in the code, docs or meeting notes to answer this reliably."


def _citation(c: Chunk) -> Citation:
    return Citation(chunk_id=c.id, source_type=c.source_type, label=label_for(c), ref=c.ref)


def _pick_expert(deps: Deps, project_id: str, hits: list[Hit], asker_id: str | None = None):
    """Who should answer: the code owner of the best-ranked code the search found, never the person asking (nor anyone
    deactivated). An owner from a specific rule ('/webhooks/') beats one from the catch-all ('*'), whatever their files'
    ranks: the catch-all names whoever owns everything else, not someone who knows this code. Then the project's managers."""
    project = deps.store.get_project(project_id)
    rules = parse_codeowners(deps.store.codeowners(project_id))
    code = [h for h in hits if h.chunk.source_type == "code"]
    decided = [(h.chunk.ref["path"], rule_for(rules, h.chunk.ref["path"])) for h in code]
    for specific in (True, False):
        for path, rule in decided:
            if not rule or not rule[1] or (specific and rule[0] in CATCH_ALL):
                continue
            users = [u for u in deps.store.users_by_github(project.org_id, rule[1])
                     if u.id != asker_id and u.role in {"expert", "manager", "admin"} and u.status == "active"
                     and deps.store.is_member(project_id, u.id)]
            if users:
                return users[0], path
    managers = [m for m in deps.store.managers(project_id) if m.id != asker_id and m.status == "active"]  # never a deactivated one
    return (managers[0], None) if managers else (None, None)


def kt_clips_for(deps: Deps, project_id: str, code_chunks: list[Chunk], limit: int = 3):
    """KT moments that explained the cited code: the same chunk, the same function, or the whole file."""
    clips = []
    for c in code_chunks:
        for link in deps.store.kt_links(project_id, path=c.ref["path"]):
            same = link.chunk_id == c.id or (c.symbol and link.symbol == c.symbol) or (link.symbol is None and link.chunk_id is None)
            if same and link.confidence >= 0.5 and link not in clips:
                clips.append(link)
    return sorted(clips, key=lambda l: -l.confidence)[:limit]


def answer(deps: Deps, project_id: str, user_id: str, question: str) -> tuple[QAResult, str]:
    from app.lifecycle import routing  # routing imports this module: imported when called

    ai = routing.answer_from(deps, project_id, question)
    hits: list[Hit] = ai["hits"]
    result: QAResult | None = ai["result"]
    if result is not None:  # who wrote it and how the evidence was found stay with the turn
        result = result.model_copy(update={"writer": ai.get("writer"), "crew": ai.get("crew")})

    asked_before = None
    if result is None:
        # the same question, still waiting for its expert: do not hand it over (and notify them) a second time
        asked_before = next((r for r in deps.store.open_expert_requests(project_id)
                             if r.get("asked_by") == user_id and norm_question(r["question"]) == norm_question(question)), None)
        if asked_before:
            expert = deps.store.get_user(str(asked_before["expert_id"]))
            who = expert.name if expert else "an expert"
            text = (f"{NOT_ENOUGH} You asked this already and it is with {who}. Their answer will appear here and in your "
                    "notifications, and is saved as a verified answer.")
            routed = str(asked_before["expert_id"])
        else:
            expert, path = _pick_expert(deps, project_id, hits, asker_id=user_id)
            if expert:
                why = f", who owns {path}" if path else ""
                text = f"{NOT_ENOUGH} I've passed it to {expert.name}{why}; their answer will be saved as a verified answer."
            else:
                text = f"{NOT_ENOUGH} There is nobody else on this project to pass it to yet."
            routed = expert.id if expert else None
        result = QAResult(status="not_enough_information", answer=text, confidence=0.0, routed_to=routed)

    message_id = deps.store.save_chat(project_id, user_id, question, result)
    if result.routed_to and not asked_before:
        asker = deps.store.get_user(user_id)
        if getattr(deps, "lifecycle", None) is not None and asker:
            # the one capped path to a person (office hours, the daily cap, the digest): app/lifecycle/routing.deliver
            from app.agents.core.registry import lifecycle_ctx

            routing.deliver(lifecycle_ctx(deps, asker.org_id, asker), project_id, question, deps.store.get_user(result.routed_to), asker,
                            message_id, 4, False, "", [])
        else:  # the core agents on their own (no lifecycle wired): the request and its notice, as before
            deps.store.create_expert_request(project_id, question, result.routed_to, user_id, message_id)
            deps.store.notify(result.routed_to, "expert_request", f"{asker.name if asker else 'Someone'} asked: {question}",
                              f"/experts?message={message_id}&project={project_id}")
    return result, message_id
