"""A hire sees everything a manager or mentor can see about them (GET /api/onboardings/{id}/visibility).

Every lifecycle GET route about one onboarding is found in the app itself (so a new one is covered the day it is added),
called as the manager and as the mentor, and each field it returns must be in the hire's visibility payload with the same
value. The hire cards on the manager's and the mentor's homes are held to the same rule."""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config import get_settings
from app.lifecycle import visibility as V
from app.main import create_app

MANAGER, MENTOR, HIRE, OTHER = ({"X-Demo-User": u} for u in ("u-priya", "u-ravi", "u-vijay", "u-meera"))


@pytest.fixture(scope="module")
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def hire_routes(app) -> list[str]:
    out = []

    def walk(routes, prefix=""):
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":
                walk(r.original_router.routes, prefix + (r.include_context.prefix or ""))
            elif isinstance(r, APIRoute) and "GET" in r.methods and r.endpoint.__module__ == "app.api.lifecycle" \
                    and (prefix + r.path).startswith("/api/onboardings/{onboarding_id}/") and not r.path.endswith("/visibility"):
                out.append(prefix + r.path)
    walk(app.routes)
    return sorted(out)


def test_every_hire_route_is_found(client):
    assert len(hire_routes(client.app)) >= 8  # program, access, areas, blockers, summaries, checkins, completion, shortlist


def test_manager_and_mentor_see_nothing_the_hire_cannot(client):
    seen: dict[str, object] = {}
    for path in hire_routes(client.app):
        url = path.replace("{onboarding_id}", "ob-vijay")
        for who in (MANAGER, MENTOR):
            r = client.get(url, headers=who)
            if r.status_code in {403, 404}:
                continue  # this person may not read it
            assert r.status_code == 200, (url, r.text)
            body = r.json()
            assert isinstance(body, dict), f"{url} must return registry fields, not a bare list"
            for key, value in body.items():
                assert key in V.FIELDS, f"{url} returns '{key}', which is not in the visibility registry"
                seen[key] = value
    mine = client.get("/api/onboardings/ob-vijay/visibility", headers=HIRE).json()["data"]
    missing = set(seen) - set(mine)
    assert not missing, f"fields managers see that the hire's visibility payload lacks: {missing}"
    for key, value in seen.items():
        assert mine[key]["value"] == value, f"'{key}' differs between what the manager sees and what the hire sees"


def test_home_hire_cards_are_registry_fields(client):
    mine = client.get("/api/onboardings/ob-vijay/visibility", headers=HIRE).json()["data"]
    for who, section in ((MANAGER, "hires"), (MENTOR, "mentees")):
        cards = client.get("/api/home", headers=who).json()["sections"][section]
        card = next(c for c in cards if c["profile"]["onboarding_id"] == "ob-vijay")
        for key, value in card.items():
            assert key in mine and mine[key]["value"] == value, f"home card field '{key}'"


def test_the_payload_says_who_sees_what_and_how(client):
    body = client.get("/api/onboardings/ob-vijay/visibility", headers=HIRE).json()
    assert set(body["data"]) == set(V.FIELDS)
    for key, f in body["data"].items():
        assert f["label"] and f["how"] and f["source"] and set(f["visible_to"]) <= V.AUDIENCES, key
    assert any("pre-submit" in p.lower() for p in body["private"])


def test_only_the_hire_and_managers_read_it(client):
    assert client.get("/api/onboardings/ob-vijay/visibility", headers=MANAGER).status_code == 200
    assert client.get("/api/onboardings/ob-vijay/visibility", headers=MENTOR).status_code == 403
    assert client.get("/api/onboardings/ob-vijay/visibility", headers=OTHER).status_code == 404
    assert client.get("/api/onboardings/ob-arjun/visibility", headers=HIRE).status_code == 404  # another hire's


def test_a_hire_sees_what_is_kept_about_their_acceptances_and_removal_requests(client):
    """Admins read the network and browser of each acceptance (the export); managers read a removal request's reason. The
    hire reads both, with the same values."""
    assert client.post("/api/meetings/mtg-1/recording/removal-requests", json={"reason": "My screen showed a payslip."}, headers=HIRE).status_code == 201
    data = client.get("/api/onboardings/ob-vijay/visibility", headers=HIRE).json()["data"]
    acc, rem = data["acceptances"], data["removal_requests"]
    assert (acc["source"], acc["visible_to"]) == ("legal_acceptances", ["hire", "admin"])
    stored = client.app.state.container.store.legal_acceptances("org-demo", user_id="u-vijay")
    assert stored and [(a["document"], a["version"], a["network"], a["browser"]) for a in acc["value"]] == \
        [(r["document"], r["version"], r["ip_prefix"], r["user_agent"]) for r in stored]  # the same values an admin exports
    assert (rem["source"], rem["visible_to"]) == ("recording_removal_requests", ["hire", "manager"])
    assert [(r["meeting_id"], r["reason"], r["status"]) for r in rem["value"]] == [("mtg-1", "My screen showed a payslip.", "open")]
