"""The anonymous survey and its floor of five PEOPLE (app/lifecycle/checkins.py).

A response carries a team and a survey day and no person; a hire answers each survey day once. So the number of responses
in one (team, day) is a number of people, and the floor is applied there: three hires answering on days 30, 60 and 90 are
nine answers but three people, and must not pass a floor of five. Each question's mean needs five answers too, the export
holds aggregates only, and a resubmission is never counted twice."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.lifecycle import checkins as CI
from app.main import create_app
from app.ports import SurveyResponse

PID, ORG = "proj-demo", "org-demo"
ADMIN, MANAGER, HIRE, EXEC = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-vijay", "u-exec"))
ALL = {"clarity": 4, "support": 4, "docs": 4, "tools": 4, "belonging": 4}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _lc(client):
    return client.app.state.container.lifecycle


def _team(client, name):
    return client.post("/api/teams", json={"project_id": PID, "name": name}, headers=MANAGER).json()["id"]


def _answer(client, team_id, day, n, answers=ALL, skip=None):
    for i in range(n):
        a = dict(answers)
        if skip and i == 0:
            a.pop(skip)
        _lc(client).add_survey_response(SurveyResponse(id="", org_id=ORG, team_id=team_id, cohort="2026-Q4", day=day, answers=a))


def _summary(client, who=EXEC):
    return {t["team"]: t for t in client.get("/api/surveys/summary", headers=who).json()["teams"]}


def test_three_hires_over_three_days_are_nine_answers_but_three_people(client):
    team = _team(client, "Alpha")
    for day in (30, 60, 90):
        _answer(client, team, day, 3)
    alpha = _summary(client)["Alpha"]
    assert alpha["n"] == 3  # people, not the nine answers
    assert alpha["status"] == "not_enough_data" and alpha["scores"] is None
    assert [(d["day"], d["n"], d["status"], d["scores"]) for d in alpha["days"]] == [(30, 3, "not_enough_data", None),
                                                                                    (60, 3, "not_enough_data", None),
                                                                                    (90, 3, "not_enough_data", None)]
    assert "Not enough data" in alpha["how"]


def test_five_distinct_people_on_one_day_pass(client):
    team = _team(client, "Beta")
    _answer(client, team, 30, 5)
    beta = _summary(client)["Beta"]
    assert beta["status"] == "ok" and beta["n"] == 5 and beta["scores"]["clarity"] == 4
    assert [(d["day"], d["status"]) for d in beta["days"]] == [(30, "ok")]


def test_every_question_has_its_own_floor(client):
    team = _team(client, "Gamma")
    _answer(client, team, 30, 5, skip="support")  # four people answered 'support', five answered the rest
    gamma = _summary(client)["Gamma"]
    assert gamma["status"] == "ok" and "support" not in gamma["scores"] and gamma["scores"]["clarity"] == 4
    assert "support" not in gamma["days"][0]["scores"]


def test_a_day_below_the_floor_is_not_pooled_into_the_team_figure(client):
    team = _team(client, "Delta")
    _answer(client, team, 30, 5, answers={**ALL, "clarity": 5})
    _answer(client, team, 60, 4, answers={**ALL, "clarity": 1})  # four people: never shown, never averaged in
    delta = _summary(client)["Delta"]
    assert delta["scores"]["clarity"] == 5 and delta["n"] == 5
    day60 = next(d for d in delta["days"] if d["day"] == 60)
    assert day60["n"] == 4 and day60["scores"] is None and day60["status"] == "not_enough_data"


def test_the_floor_is_per_team(client):
    a, b = _team(client, "Left"), _team(client, "Right")
    _answer(client, a, 30, 3)
    _answer(client, b, 30, 3)  # six people in the organisation, three in each team
    s = _summary(client)
    assert s["Left"]["status"] == s["Right"]["status"] == "not_enough_data"


def test_the_planner_reads_the_same_floor(client):
    team = _team(client, "Planner")
    for day in (30, 60, 90):
        _answer(client, team, day, 3, answers={**ALL, "docs": 1})
    assert CI.floored_scores(_lc(client).survey_responses(ORG)) == {}  # nine answers, three people per day: nothing to read
    _answer(client, team, 30, 2, answers={**ALL, "docs": 1})  # five people on day 30
    scores = CI.floored_scores(_lc(client).survey_responses(ORG))
    assert scores["docs"] == (1.0, 5)  # only the cell that passed counts, not the other days' answers


def test_a_resubmission_is_not_counted_twice(client):
    client.post("/api/demo/advance", json={"days": 18}, headers=MANAGER)
    lc = _lc(client)
    before = len(lc.survey_rows)
    body = {"onboarding_id": "ob-vijay", "day": 30, "answers": {"clarity": 5}}
    assert client.post("/api/surveys", json=body, headers=HIRE).status_code == 204
    assert client.post("/api/surveys", json=body, headers=HIRE).status_code == 409
    assert len(lc.survey_rows) == before + 1
    assert lc.get_program("ob-vijay").survey_days == [30]


def test_a_day_can_be_claimed_once_even_if_the_check_races(client):
    lc = _lc(client)
    assert lc.claim_survey_day("ob-vijay", 30) is True
    assert lc.claim_survey_day("ob-vijay", 30) is False  # the second of two simultaneous submissions finds it taken
    assert lc.get_program("ob-vijay").survey_days == [30]


def test_a_failed_save_gives_the_day_back(client, monkeypatch):
    from app.lifecycle import hires
    from app.lifecycle.context import Ctx

    c = client.app.state.container
    monkeypatch.setattr(c.lifecycle, "add_survey_response", lambda r: (_ for _ in ()).throw(RuntimeError("database down")))
    hc = hires.load(Ctx.of(c, c.store.get_user("u-vijay")), "ob-vijay")
    with pytest.raises(RuntimeError):
        CI.submit_survey(hc, 30, {"clarity": 4})
    assert c.lifecycle.get_program("ob-vijay").survey_days == []  # nothing stored, so the hire may answer again


def test_the_export_holds_survey_aggregates_only(client):
    team = _team(client, "Export")
    for day in (30, 60, 90):
        _answer(client, team, day, 3)
    _answer(client, team, 30, 2)  # five people on day 30 only
    r = client.get("/api/org/export", headers=ADMIN)
    data = r.json()
    assert "survey_responses" not in data and '"cohort"' not in r.text  # no raw rows
    assert all("survey_days" not in p for p in data["onboarding_programs"])  # who answered on which day cannot be joined back
    mine = [a for a in data["survey_aggregates"] if a["team"] == "Export"]
    assert {(a["day"], a["respondents"], a["status"]) for a in mine} == {(30, 5, "ok"), (60, 3, "not_enough_data"), (90, 3, "not_enough_data")}
    assert all(a["scores"] is None for a in mine if a["status"] != "ok")
    assert all(set(a) <= {"team_id", "team", "day", "respondents", "status", "scores"} for a in data["survey_aggregates"])


def test_the_report_and_its_csv_respect_the_floor(client):
    team = _team(client, "Report")
    for day in (30, 60, 90):
        _answer(client, team, day, 3)
    report = client.get("/api/reports/onboarding", headers=EXEC).json()
    row = next(t for t in report["surveys"]["teams"] if t["team"] == "Report")
    assert row["status"] == "not_enough_data" and row["scores"] is None
    csv_text = client.get("/api/reports/onboarding.csv", headers=EXEC).text
    lines = [line for line in csv_text.splitlines() if line.startswith("survey.") and ",Report:" in line]
    assert lines and all(",not enough data," in line for line in lines)  # the value column says so; no average appears
