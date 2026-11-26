"""Demo sign-in (POST /api/demo/login): every role of the seeded organisation signs in with its email and the demo
password, failures never tell whether an email exists, attempts are limited, and none of it exists outside demo mode."""
import pytest
from fastapi.testclient import TestClient

from app.api import demo
from app.config import get_settings
from app.main import create_app

PID = "proj-demo"
PASSWORD = "welcome-wave"
MANAGER = {"X-Demo-User": "u-priya"}
ACCOUNTS = [
    ("admin@demo.dev", "u-admin", "admin"),
    ("priya@demo.dev", "u-priya", "manager"),
    ("ravi@demo.dev", "u-ravi", "expert"),
    ("meera@demo.dev", "u-meera", "expert"),
    ("vijay@demo.dev", "u-vijay", "new_hire"),
    ("elena@demo.dev", "u-exec", "executive"),  # the onboarding lifecycle's seed (app/seed.py seed_lifecycle)
    ("arjun@demo.dev", "u-arjun", "new_hire"),
    ("sara@demo.dev", "u-sara", "new_hire"),
    ("tom@demo.dev", "u-tom", "new_hire"),
]


@pytest.fixture
def client():
    get_settings.cache_clear()
    demo._login_attempts.hits.clear()  # the limiter lives in the process; every test starts with a clean slate
    with TestClient(create_app()) as c:
        yield c
    demo._login_attempts.hits.clear()
    get_settings.cache_clear()


def login(c, email, password=PASSWORD):
    return c.post("/api/demo/login", json={"email": email, "password": password})


@pytest.mark.parametrize("email,uid,role", ACCOUNTS)
def test_every_role_signs_in_and_the_session_works(client, email, uid, role):
    r = login(client, email)
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert (user["id"], user["role"], user["email"]) == (uid, role, email)
    me = client.get("/api/me", headers={"X-Demo-User": user["id"]}).json()  # the id is the session from here on
    assert me["id"] == uid and [p["id"] for p in me["projects"]] == [PID]


def test_the_seeded_admin_is_a_member_of_the_demo_project(client):
    store = client.app.state.container.store
    admin = store.get_user("u-admin")
    assert (admin.name, admin.email, admin.role, admin.github_login) == ("Alex (Admin)", "admin@demo.dev", "admin", "alex-admin")
    assert store.is_member(PID, "u-admin")


def test_requests_without_a_session_are_not_signed_in(client):
    assert client.get("/api/me").status_code == 401  # no silent default person any more
    assert client.get("/api/me", headers={"X-Demo-User": "u-nobody"}).status_code == 403
    assert client.get("/api/config").status_code == 200  # what the sign-in page needs stays public


def test_email_matching_ignores_case_and_spaces(client):
    r = login(client, "  Priya@Demo.DEV ")
    assert r.status_code == 200 and r.json()["user"]["id"] == "u-priya"


def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(client):
    wrong = login(client, "priya@demo.dev", "welcome-wave ")
    unknown = login(client, "nobody@demo.dev")
    empty = login(client, "priya@demo.dev", "")
    assert wrong.status_code == unknown.status_code == empty.status_code == 401
    assert wrong.json() == unknown.json() == empty.json() == {"detail": demo.BAD_LOGIN}


def test_the_password_comes_from_settings(client):
    client.app.state.container.settings.demo_password = "a-different-one"
    assert login(client, "vijay@demo.dev").status_code == 401
    assert login(client, "vijay@demo.dev", "a-different-one").status_code == 200


def test_attempts_are_limited_per_email_and_client(client):
    for _ in range(demo.LOGIN_ATTEMPTS_PER_MINUTE):
        assert login(client, "ravi@demo.dev", "guess").status_code == 401
    r = login(client, "ravi@demo.dev")  # even the right password waits once the limit is reached
    assert r.status_code == 429 and r.headers["retry-after"] == "60"
    assert login(client, "RAVI@demo.dev").status_code == 429  # the same email in another case is the same key
    assert login(client, "meera@demo.dev").status_code == 200  # other accounts are unaffected


def test_the_sign_in_page_lists_accounts_and_invitations_without_a_session(client):
    people = client.get("/api/demo/people").json()
    assert {(m["email"], m["role"]) for m in people["members"]} == {(e, r) for e, _, r in ACCOUNTS}
    assert people["invited"] == []


def test_an_invited_person_signs_in_after_accepting(client):
    r = client.post(f"/api/projects/{PID}/invitations", json={"email": "Sam.Lee@example.com", "role": "new_hire", "name": "Sam"}, headers=MANAGER)
    assert r.status_code == 201, r.text
    [inv] = client.get("/api/demo/people").json()["invited"]
    assert login(client, "sam.lee@example.com").status_code == 401  # not a member until they accept: the same answer as anyone
    joined = client.post("/api/demo/invitations/accept", json={"project_id": PID, "invitation_id": inv["invitation_id"]})
    assert joined.status_code == 200, joined.text
    r = login(client, "SAM.LEE@example.com")
    assert r.status_code == 200 and r.json()["user"]["id"] == joined.json()["id"] and r.json()["user"]["role"] == "new_hire"
    people = client.get("/api/demo/people").json()
    assert not people["invited"] and "sam.lee@example.com" in {m["email"] for m in people["members"]}
    again = client.post("/api/demo/invitations/accept", json={"project_id": PID, "invitation_id": inv["invitation_id"]})
    assert again.status_code == 404
    assert client.post("/api/demo/invitations/accept", json={"project_id": "nope", "invitation_id": inv["invitation_id"]}).status_code == 404


def test_the_demo_password_is_public_config_only_in_demo_mode(client):
    assert client.get("/api/config").json()["demo_password"] == PASSWORD
    client.app.state.container.settings.mode = "gcp"
    try:
        assert client.get("/api/config").json()["demo_password"] is None
    finally:
        client.app.state.container.settings.mode = "demo"


def test_demo_routes_do_not_exist_outside_demo_mode(monkeypatch):
    monkeypatch.setenv("MODE", "gcp")
    get_settings.cache_clear()
    try:
        app = create_app()  # routes only: the lifespan (and its Google clients) never runs for this check
    finally:
        get_settings.cache_clear()
    assert not [r.path for r in app.routes if getattr(r, "path", "").startswith("/api/demo")]
    c = TestClient(app)
    assert c.post("/api/demo/login", json={"email": "priya@demo.dev", "password": PASSWORD}).status_code == 404
    assert c.get("/api/demo/people").status_code == 404
