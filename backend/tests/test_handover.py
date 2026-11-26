"""Offboarding handover completeness (app/lifecycle/handover.py) in demo mode: the transfer set lists everything a leaver holds,
the transfer moves it to the successor, documents can be attached, completion needs a person's confirmation (and an admin to
deactivate the leaver), and only managers and admins of the project reach any of it. Everything is audited."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.lifecycle import handover as H
from app.main import create_app
from app.ports import Handover, User

PID, ORG = "proj-demo", "org-demo"
ADMIN, MANAGER, RAVI, MEERA, HIRE, EXEC = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-ravi", "u-meera", "u-vijay", "u-exec"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _c(client):
    return client.app.state.container


def _meera(client) -> dict:
    [h] = client.get(f"/api/handovers?project_id={PID}", headers=MANAGER).json()
    assert h["user_id"] == "u-meera"
    return h


def _set(client, hid: str, who=MANAGER) -> dict:
    r = client.get(f"/api/handovers/{hid}/transfer", headers=who)
    assert r.status_code == 200, r.text
    return r.json()


def _section(t: dict, key: str) -> dict:
    return next(s for s in t["sections"] if s["key"] == key)


def _items(t: dict, key: str) -> dict[str, dict]:
    return {i["key"]: i for i in _section(t, key)["items"]}


def _audits(client, action: str) -> list[dict]:
    return [a for a in _c(client).store.audit_rows if a["action"] == action]


def _new_handover(client, user_id: str, who=ADMIN, days: int = 30) -> dict:
    r = client.post("/api/handovers", json={"project_id": PID, "user_id": user_id, "last_day": (date.today() + timedelta(days=days)).isoformat()},
                    headers=who)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- the transfer set
def test_the_transfer_set_lists_everything_meera_holds_with_links_and_gaps(client):
    t = _set(client, _meera(client)["id"])
    assert t["person"]["id"] == "u-meera" and t["successor"]["id"] == "u-priya" and t["can_complete"]
    assert [s["key"] for s in t["sections"]] == ["code", "documents", "kt", "sessions", "people", "questions", "approvals", "access", "feedback"]
    # code CODEOWNERS gives her (/webhooks/ @meera-dev), with a GitHub link at the default branch and a link into the app
    code = _items(t, "code")["code:webhooks/server.py"]
    assert code["url"] == "https://github.com/demo/mini-policy-engine/blob/main/webhooks/server.py"
    assert code["href"] == "#/code?path=webhooks%2Fserver.py" and code["owner_id"] == "u-priya" and "0% explained" in code["detail"]
    # the hires she mentors move with their open work; the access item she still has to grant moves too
    people = _items(t, "people")
    assert set(people) == {"mentee:ob-arjun", "mentee:ob-sara"} and all(i["state"] == "moves" for i in people.values())
    assert "open task" in people["mentee:ob-arjun"]["detail"] and "milestones to go" in people["mentee:ob-arjun"]["detail"]
    assert [i["state"] for i in _items(t, "access").values()] == ["moves"]
    # the KT planner's session for her, still to book before her last day
    assert [i["state"] for i in _items(t, "sessions").values()] == ["todo"]
    # the gaps: webhooks/ has no recorded KT, CODEOWNERS still names her (with the replacement line), and no document
    gaps = {g["key"]: g for g in t["gaps"]}
    assert {"module:webhooks", "codeowners", "no_docs", "sessions"} <= set(gaps) and "successor" not in gaps
    assert t["codeowners"]["lines"] == [{"before": "/webhooks/   @meera-dev", "after": "/webhooks/ @priya-mgr"}]
    c = t["completeness"]
    assert c["total"] == sum(s["count"] for s in t["sections"]) and c["moves"] >= 4 and 0 <= c["percent"] <= 100
    # what can be attached: the repository's docs, with GitHub links
    assert {d["path"] for d in t["attachable"]["repository"]} == {"README.md", "docs/CONTRIBUTING.md"}
    assert not t["can_deactivate"] and _set(client, t["handover_id"], ADMIN)["can_deactivate"]


def test_ravi_hands_over_his_recordings_explanations_questions_and_feedback(client):
    c = _c(client)
    h = _new_handover(client, "u-ravi")
    t = _set(client, h["id"])
    kt = _items(t, "kt")
    assert kt["rec:mtg-1"]["kt"] and kt["rec:mtg-1"]["href"] == "#/meetings/mtg-1" and "min of them speaking" in kt["rec:mtg-1"]["detail"]
    clips = [i for k, i in kt.items() if k.startswith("clip:mtg-1:engine/validate.py")]
    assert clips and all(i["href"].startswith("#/meetings/mtg-1?t=") and i["state"] == "moves" for i in clips)
    assert "#/kt-planner" not in [g.get("href") for g in t["gaps"] if g["key"] == "stale"]  # nothing changed yet
    # the question routed to him, his feedback queue and his mentees
    [q] = _items(t, "questions").values()
    assert q["state"] == "moves" and "staging deploy checklist" in q["title"]
    assert any(i["state"] == "moves" for i in _items(t, "feedback").values())
    assert {"mentee:ob-vijay", "mentee:ob-tom"} <= set(_items(t, "people"))
    # the Keeper's re-recording proposal names him: a decision that waits on him
    assert any(i["key"].startswith("approval:") and i["state"] == "todo" for i in _items(t, "approvals").values())
    # once the code changes, his explanation of it is out of date: a gap until re-recorded
    c.store.mark_kt_code_changed(PID, ["engine/validate.py"])
    t = _set(client, h["id"])
    assert {g["key"] for g in t["gaps"]} >= {"stale"} and any(i["state"] == "todo" for i in _items(t, "kt").values())
    # the transfer: his question, feedback, access items and mentees go to Meera, who is told
    request_id = q["key"].split(":", 1)[1]
    out = client.post(f"/api/handovers/{h['id']}/transfer", json={"successor_id": "u-meera"}, headers=MANAGER).json()
    assert out["moved"]["request"] == 1 and out["moved"]["mentee"] >= 2 and out["moved"]["feedback"] >= 1
    assert c.store.get_expert_request(request_id)["expert_id"] == "u-meera"
    assert c.lifecycle.get_program("ob-vijay").mentor_id == "u-meera"
    assert all(f.owner_id != "u-ravi" for f in c.lifecycle.feedback(PID) if f.status == "open")
    assert [n for n in c.store.notifications if n["user_id"] == "u-meera" and n["kind"] == "expert_request"]
    assert [n for n in c.store.notifications if n["user_id"] == "u-vijay" and "mentor" in n["text"]]
    t = _set(client, h["id"])
    assert _items(t, "questions")[q["key"]]["detail"] == "Now with Meera" and t["new_owner"]["id"] == "u-meera"
    assert all(i["state"] != "moves" for s in t["sections"] for i in s["items"])  # nothing left to move
    assert not [k for k in t["ticked"] if k.startswith(("mentee:", "request:", "access:", "feedback:"))]  # moved, not ticked by a person
    assert _audits(client, "handover.transferred")[-1]["detail"]["to"] == "u-meera"


def test_a_manager_leaving_hands_the_hires_she_manages_to_another_manager(client):
    c = _c(client)
    h = _new_handover(client, "u-priya")
    t = _set(client, h["id"], ADMIN)
    managed = {k: i for k, i in _items(t, "people").items() if k.startswith("managed:")}
    assert managed and all(i["state"] == "moves" for i in managed.values())
    assert _items(t, "approvals")["approvals:decide"]["state"] == "done"  # Alex (admin) decides them too
    # Ravi is an expert: he takes her code and documents; the hires go to the project's other manager, Alex
    client.post(f"/api/handovers/{h['id']}/transfer", json={"successor_id": "u-ravi"}, headers=ADMIN)
    assert all(o.manager_id != "u-priya" for o in c.store.onboardings(PID)) and c.store.get_onboarding("ob-vijay").manager_id == "u-admin"


def test_a_leaver_with_nothing_to_move_and_no_successor_shows_the_blocker(client):
    c = _c(client)
    h = _new_handover(client, "u-ravi")
    plan = {**c.lifecycle.get_handover(h["id"]).plan, "successor_id": None}
    c.lifecycle.update_handover(h["id"], plan=plan)
    t = _set(client, h["id"])
    assert t["gaps"][0]["key"] == "successor" and t["gaps"][0]["severity"] == "blocker" and not t["can_complete"]
    r = client.post(f"/api/handovers/{h['id']}/complete", json={"confirm": "Ravi", "accept_gaps": True}, headers=ADMIN)
    assert r.status_code == 422


# ---------------------------------------------------------------- attachments
def test_attachments_are_checked_owned_and_audited(client):
    c = _c(client)
    hid = _meera(client)["id"]
    url = f"/api/handovers/{hid}/attachments"
    r = client.post(url, json={"kind": "repo", "ref": "/docs/CONTRIBUTING.md", "note": "Running the webhook server"}, headers=MANAGER)
    assert r.status_code == 201 and r.json()["attachment"]["url"].endswith("/blob/main/docs/CONTRIBUTING.md")
    attachment = r.json()["attachment"]
    assert client.post(url, json={"kind": "repo", "ref": "docs/CONTRIBUTING.md"}, headers=MANAGER).status_code == 409  # already attached
    assert client.post(url, json={"kind": "repo", "ref": "docs/NOPE.md"}, headers=MANAGER).status_code == 422  # not in the index
    assert client.post(url, json={"kind": "document", "ref": "no-such-doc"}, headers=MANAGER).status_code == 422
    assert client.post(url, json={"kind": "link", "url": "http://wiki.example.com/x", "title": "x"}, headers=MANAGER).status_code == 422
    assert client.post(url, json={"kind": "link", "url": "javascript:alert(1)", "title": "x"}, headers=MANAGER).status_code == 422
    assert client.post(url, json={"kind": "link", "url": "https://wiki.example.com/x"}, headers=MANAGER).status_code == 422  # no title
    assert client.post(url, json={"kind": "link", "url": "https://wiki.example.com/x", "title": "Runbook", "owner_id": "u-vijay"},
                       headers=MANAGER).status_code == 422  # a new hire cannot own it
    doc = c.store.create_document(PID, "Webhook design notes", "local://notes.pdf", "application/pdf", "u-meera")
    assert client.post(url, json={"kind": "document", "ref": doc, "owner_id": "u-ravi"}, headers=MANAGER).status_code == 409  # hers: listed
    t = _set(client, hid)
    docs = _items(t, "documents")
    assert docs[f"attach:{attachment['id']}"]["state"] == "done" and docs[f"upload:{doc}"]["state"] == "moves"
    assert "no_docs" not in {g["key"] for g in t["gaps"]}  # a document now covers her code
    link = client.post(url, json={"kind": "link", "url": "https://wiki.example.com/webhooks", "title": "Webhook runbook", "owner_id": "u-ravi"},
                       headers=MANAGER).json()["attachment"]
    assert _items(_set(client, hid), "documents")[f"attach:{link['id']}"]["owner_id"] == "u-ravi"
    assert client.delete(f"{url}/{link['id']}", headers=MANAGER).status_code == 200
    assert client.delete(f"{url}/{link['id']}", headers=MANAGER).status_code == 404
    assert [a["detail"]["kind"] for a in _audits(client, "handover.attachment_added")] == ["repo", "link"]
    assert _audits(client, "handover.attachment_removed")[0]["detail"]["attachment_id"] == link["id"]


# ---------------------------------------------------------------- completion
def test_completion_needs_a_typed_name_accepted_gaps_and_an_admin_to_deactivate(client):
    c = _c(client)
    hid = _meera(client)["id"]
    done = f"/api/handovers/{hid}/complete"
    assert client.patch(f"/api/handovers/{hid}", json={"status": "done"}, headers=MANAGER).status_code == 422  # only through completion
    assert client.post(done, json={"confirm": "Mira", "accept_gaps": True}, headers=MANAGER).status_code == 422
    r = client.post(done, json={"confirm": "Meera"}, headers=MANAGER)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "open_gaps" and r.json()["detail"]["gaps"]
    assert c.lifecycle.get_program("ob-arjun").mentor_id == "u-meera"  # a refused completion moved nothing
    assert client.post(done, json={"confirm": "Meera", "accept_gaps": True, "deactivate": True}, headers=MANAGER).status_code == 403
    assert c.store.get_user("u-meera").status == "active"
    r = client.post(done, json={"confirm": "meera (senior)", "accept_gaps": True, "deactivate": True}, headers=ADMIN)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["deactivated"] and out["handover"]["status"] == "done" and out["accepted_gaps"]
    assert c.lifecycle.get_program("ob-arjun").mentor_id == "u-priya"  # moved to the successor on completion
    assert c.store.get_user("u-meera").status == "deactivated" and client.get("/api/me", headers=MEERA).status_code == 403
    [audit] = _audits(client, "handover.completed")
    assert audit["actor_id"] == "u-admin" and audit["detail"]["deactivated"] and audit["detail"]["accepted_gaps"]
    assert _audits(client, "member.deactivated")[-1]["detail"] == {"via": "handover", "handover_id": hid}
    # a completed handover is closed to changes until someone reopens it
    assert client.post(done, json={"confirm": "Meera", "accept_gaps": True}, headers=ADMIN).status_code == 409
    assert client.post(f"/api/handovers/{hid}/transfer", json={"successor_id": "u-ravi"}, headers=ADMIN).status_code == 409
    assert client.post(f"/api/handovers/{hid}/attachments", json={"kind": "repo", "ref": "README.md"}, headers=ADMIN).status_code == 409
    t = _set(client, hid, ADMIN)
    assert t["completed"]["deactivated"] and not t["can_complete"]
    assert client.patch(f"/api/handovers/{hid}", json={"status": "in_progress"}, headers=ADMIN).json()["status"] == "in_progress"
    assert _audits(client, "handover.updated")[-1]["detail"].get("reopened")


def test_completion_with_every_gap_closed_needs_no_acceptance_and_keeps_the_account(client):
    c = _c(client)
    hid = _meera(client)["id"]
    t = _set(client, hid)
    session = next(iter(_items(t, "sessions")))
    # a person closes each gap: the module and the session are recorded, CODEOWNERS changed, a document attached
    client.patch(f"/api/handovers/{hid}", json={"done": [session, "module:webhooks", "codeowners"]}, headers=MANAGER)
    client.post(f"/api/handovers/{hid}/attachments", json={"kind": "repo", "ref": "docs/CONTRIBUTING.md"}, headers=MANAGER)
    assert _set(client, hid)["gaps"] == []
    r = client.post(f"/api/handovers/{hid}/complete", json={"confirm": "Meera"}, headers=MANAGER)
    assert r.status_code == 200 and not r.json()["deactivated"] and r.json()["accepted_gaps"] == []
    assert c.store.get_user("u-meera").status == "active"
    assert [n for n in c.store.notifications if n["user_id"] == "u-meera" and "handover is complete" in n["text"]]
    assert [n for n in c.store.notifications if n["user_id"] == "u-priya" and "handover is complete" in n["text"]]
    assert _set(client, hid)["completeness"]["percent"] == 100


def test_ticks_close_todo_items_and_gaps_a_person_resolved_outside_welcome_wave(client):
    hid = _meera(client)["id"]
    t = _set(client, hid)
    session = next(iter(_items(t, "sessions")))
    client.patch(f"/api/handovers/{hid}", json={"done": [session, "codeowners"]}, headers=MANAGER)
    t = _set(client, hid)
    assert _items(t, "sessions")[session]["state"] == "done" and not {"sessions", "codeowners"} & {g["key"] for g in t["gaps"]}
    client.patch(f"/api/handovers/{hid}", json={"undone": ["codeowners"]}, headers=MANAGER)
    assert "codeowners" in {g["key"] for g in _set(client, hid)["gaps"]}


def test_deactivating_yourself_through_a_handover_is_refused(client):
    h = _new_handover(client, "u-admin", who=MANAGER)
    r = client.post(f"/api/handovers/{h['id']}/complete", json={"confirm": "Alex", "accept_gaps": True, "deactivate": True}, headers=ADMIN)
    assert r.status_code == 409 and _c(client).store.get_user("u-admin").status == "active"


# ---------------------------------------------------------------- who may
ROUTES = [("GET", "/api/handovers/{id}/transfer", None), ("POST", "/api/handovers/{id}/transfer", {"successor_id": "u-ravi"}),
          ("POST", "/api/handovers/{id}/attachments", {"kind": "repo", "ref": "README.md"}),
          ("DELETE", "/api/handovers/{id}/attachments/abc", None),
          ("POST", "/api/handovers/{id}/complete", {"confirm": "Meera", "accept_gaps": True})]


@pytest.mark.parametrize("who", [RAVI, MEERA, HIRE, EXEC])
def test_experts_hires_and_executives_cannot_reach_a_handover(client, who):
    c = _c(client)
    hid = _meera(client)["id"]
    for method, path, body in ROUTES:
        r = client.request(method, path.format(id=hid), json=body, headers=who)
        assert r.status_code == 403, (method, path, r.status_code)
    assert c.lifecycle.get_program("ob-arjun").mentor_id == "u-meera" and not c.lifecycle.get_handover(hid).plan.get("attachments")


def test_a_manager_outside_the_project_and_another_organisation_see_nothing(client):
    c = _c(client)
    c.store.add_user(User(id="u-outsider", org_id=ORG, email="out@demo.dev", name="Omar (Manager)", role="manager"))
    hid = _meera(client)["id"]
    for method, path, body in ROUTES:
        assert client.request(method, path.format(id=hid), json=body, headers={"X-Demo-User": "u-outsider"}).status_code == 404
    other = c.lifecycle.add_handover(Handover(id="", org_id="org-elsewhere", project_id=PID, user_id="u-meera", last_day=date.today(),
                                              plan={"items": []}, created_by="u-admin"))
    for method, path, body in ROUTES:
        assert client.request(method, path.format(id=other.id), json=body, headers=ADMIN).status_code == 404
    assert c.lifecycle.get_program("ob-arjun").mentor_id == "u-meera"


def test_the_successor_must_be_an_active_member_who_can_take_it(client):
    c = _c(client)
    hid = _meera(client)["id"]
    url = f"/api/handovers/{hid}/transfer"
    assert client.post(url, json={"successor_id": "u-meera"}, headers=MANAGER).status_code == 422  # herself
    assert client.post(url, json={"successor_id": "u-vijay"}, headers=MANAGER).status_code == 422  # a new hire
    assert client.post(url, json={"successor_id": "u-exec"}, headers=MANAGER).status_code == 422  # an executive
    c.store.set_member_status(ORG, "u-ravi", "deactivated")
    assert client.post(url, json={"successor_id": "u-ravi"}, headers=MANAGER).status_code == 422  # deactivated
    assert c.lifecycle.get_program("ob-arjun").mentor_id == "u-meera"


# ---------------------------------------------------------------- the CODEOWNERS suggestion
def test_the_codeowners_change_replaces_the_leaver_and_keeps_everyone_else():
    text = "# owners\n*  @lead\n/engine/ @ravi-dev @Meera-Dev  # core\n/docs/ @org/writers @meera-dev\n/web/ @ravi-dev @meera-dev\n/x/ @other\n"
    lines = H.codeowners_change(text, "meera-dev", "ravi-dev")
    assert lines == [{"before": "/engine/ @ravi-dev @Meera-Dev  # core", "after": "/engine/ @ravi-dev  # core"},
                     {"before": "/docs/ @org/writers @meera-dev", "after": "/docs/ @org/writers @ravi-dev"},
                     {"before": "/web/ @ravi-dev @meera-dev", "after": "/web/ @ravi-dev"}]
    assert H.codeowners_change(text, "meera-dev", None)[0]["after"] is None  # a successor with no GitHub login
    assert H.codeowners_change(text, "nobody", "ravi-dev") == []


def test_names_confirm_with_or_without_the_role_suffix():
    u = User(id="u", org_id="o", email="m@x", name="Meera  (Senior)", role="expert")
    assert H.confirms(" meera ", u) and H.confirms("Meera (Senior)", u) and not H.confirms("", u) and not H.confirms("Mee", u)
    assert H.github_url(None, "a.md") is None


# ---------------------------------------------------------------- the stored successor is checked again (security review)
def _store_successor(client, hid: str, successor_id: str) -> None:
    lc = _c(client).lifecycle
    h = lc.get_handover(hid)
    lc.update_handover(hid, plan={**h.plan, "successor_id": successor_id})


def test_reassign_without_a_successor_id_checks_the_stored_successor_again(client):
    c = _c(client)
    hid = _meera(client)["id"]
    _store_successor(client, hid, "u-ravi")  # the Keeper's suggestion, or an earlier choice
    c.store.set_member_status(ORG, "u-ravi", "deactivated")  # ... who has left since
    before = c.lifecycle.get_handover(hid).plan
    r = client.patch(f"/api/handovers/{hid}", json={"reassign": True, "done": [next(iter(_items(_set(client, hid), "sessions")))]},
                     headers=MANAGER)
    assert r.status_code == 422
    after = c.lifecycle.get_handover(hid)
    assert after.plan == before and not after.plan.get("transfer")  # refused before anything was written, ticks included
    assert c.lifecycle.get_program("ob-arjun").mentor_id == "u-meera"


def test_reassign_with_the_stored_successor_moves_the_work_and_says_who_received_it(client):
    c = _c(client)
    hid = _meera(client)["id"]
    _store_successor(client, hid, "u-ravi")
    r = client.patch(f"/api/handovers/{hid}", json={"reassign": True}, headers=MANAGER)
    assert r.status_code == 200 and r.json()["plan"]["transfer"]["to"] == "u-ravi"
    assert c.lifecycle.get_program("ob-arjun").mentor_id == "u-ravi"
    entry = _audits(client, "handover.transferred")[-1]
    assert entry["target"] == hid and entry["detail"]["to"] == "u-ravi" and entry["detail"]["moved"]["mentee"] >= 1


def test_complete_checks_the_stored_successor_is_still_on_the_project(client):
    c = _c(client)
    hid = _meera(client)["id"]
    _store_successor(client, hid, "u-ravi")
    c.store.__dict__.get("_store", c.store).project_members[PID].discard("u-ravi")  # Ravi left the project after being chosen
    r = client.post(f"/api/handovers/{hid}/complete", json={"confirm": "Meera", "accept_gaps": True}, headers=MANAGER)
    assert r.status_code == 422 and "no longer a member" in r.text
    h = c.lifecycle.get_handover(hid)
    assert h.status != "done" and not h.plan.get("transfer") and c.lifecycle.get_program("ob-arjun").mentor_id == "u-meera"
    assert not _set(client, hid).get("completed")


def test_the_transfer_itself_refuses_a_successor_who_may_not_take_over(client):
    from app.lifecycle.context import Ctx

    c = _c(client)

    ctx = Ctx.of(c, c.store.get_user("u-priya"))
    h = c.lifecycle.get_handover(_meera(client)["id"])
    for who in ("u-vijay", "u-exec", "u-meera"):  # a new hire, an executive, the leaver
        with pytest.raises(H.HandoverError) as e:
            H.transfer(ctx, h, c.store.get_user(who))
        assert e.value.status == 422
    assert c.lifecycle.get_program("ob-arjun").mentor_id == "u-meera"


def test_deactivating_the_leaver_is_refused_while_they_hold_work_in_another_project(client):
    c = _c(client)
    inner = c.store.__dict__.get("_store", c.store)
    p2 = inner.create_project(ORG, "Second product", "", "u-admin")
    inner.add_member(p2.id, "u-meera")
    ob2 = inner.create_onboarding(p2.id, "u-arjun", "u-meera", date.today())  # Meera manages a hire there, with no handover
    hid = _meera(client)["id"]
    _store_successor(client, hid, "u-priya")
    r = client.post(f"/api/handovers/{hid}/complete", json={"confirm": "Meera", "accept_gaps": True, "deactivate": True}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "held_elsewhere" and r.json()["detail"]["projects"] == ["Second product"]
    assert c.store.get_user("u-meera").status == "active" and c.lifecycle.get_handover(hid).status != "done"
    # completing without deactivating is fine: her account stays for the other project
    r = client.post(f"/api/handovers/{hid}/complete", json={"confirm": "Meera", "accept_gaps": True}, headers=ADMIN)
    assert r.status_code == 200 and not r.json()["deactivated"] and c.store.get_onboarding(ob2.id).manager_id == "u-meera"


def test_ticks_must_be_keys_of_this_handover(client):
    c = _c(client)
    hid = _meera(client)["id"]
    junk = "session:" + "x" * 1000
    assert client.patch(f"/api/handovers/{hid}", json={"done": [junk]}, headers=MANAGER).status_code == 422
    assert client.patch(f"/api/handovers/{hid}", json={"done": ["x" * 2000]}, headers=MANAGER).status_code == 422  # too long, no kind
    assert client.patch(f"/api/handovers/{hid}", json={"done": ["Not A Key"]}, headers=MANAGER).status_code == 422
    assert junk not in c.lifecycle.get_handover(hid).plan.get("ticked", [])
    # real keys still tick: an item of the transfer set, and a gap a person closed outside Welcome Wave
    session = next(iter(_items(_set(client, hid), "sessions")))
    assert client.patch(f"/api/handovers/{hid}", json={"done": [session, "codeowners"]}, headers=MANAGER).status_code == 200
    plan = c.lifecycle.get_handover(hid).plan
    assert "codeowners" in plan["ticked"] and session in set(plan["ticked"]) | {i["key"] for i in plan["items"] if i["done"]}
