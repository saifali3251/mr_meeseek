"""HTTP-level: team scoping on /ops, and the onboarding wizard + admin review
queue end to end. Builds the full stack (create_app with teams+onboarding
wired) — every existing /ops test still uses the 2-arg create_app(cfg, service)
and is unaffected; this file is the one exercising the new surfaces."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from holodeck.api import create_app
from holodeck.onboarding.service import OnboardingService
from holodeck.onboarding.store import OnboardingStore
from holodeck.onboarding.trial import FakeTrialRunner
from holodeck.service import LeaseService
from holodeck.teams import TeamStore

_COMPOSE = """
services:
  webserver:
    build: .
    ports:
      - "8080:8080"
    depends_on: [db]
  db:
    image: postgres:15
    environment:
      POSTGRES_USER: appuser
      POSTGRES_DB: appdb
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:
"""


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo-src" / "widget-api"
    repo.mkdir(parents=True)
    (repo / "compose.yaml").write_text(_COMPOSE)
    return repo


@pytest.fixture
def teams() -> TeamStore:
    return TeamStore()


@pytest.fixture
def onboarding(cfg, teams) -> OnboardingService:
    return OnboardingService(OnboardingStore(), teams, FakeTrialRunner(),
                             cfg.holo_dir / "manifests", cfg.holo_dir / "onboarding-work")


@pytest.fixture
def full_client(cfg, service, teams, onboarding) -> TestClient:
    return TestClient(create_app(cfg, service, teams, onboarding))


def _new_team(client) -> dict:
    r = client.post("/ops/teams", json={"name": "Widgets", "contact": "eng@widgets.com"})
    assert r.status_code == 200, r.text
    return r.json()


# ---- backward compatibility: unscoped /ops is untouched ----

def test_unscoped_ops_state_sees_everything(full_client):
    s = full_client.get("/ops/state").json()
    assert s["team"] is None
    assert {"compliance", "compliance-ui", "main"} <= set(s["apps"])


def test_teams_endpoint_501_when_onboarding_disabled(cfg, service):
    client = TestClient(create_app(cfg, service))  # no teams/onboarding wired
    r = client.post("/ops/teams", json={"name": "X", "contact": "a@x.com"})
    assert r.status_code == 501


# ---- team creation + scoping ----

def test_create_team_and_scope_ops_state(full_client):
    team = _new_team(full_client)
    assert team["slug"] == "widgets"
    s = full_client.get("/ops/state", params={"team": team["slug"], "token": team["token"]}).json()
    assert s["team"] == {"slug": "widgets", "name": "Widgets"}
    assert s["apps"] == []          # owns nothing yet
    assert s["environments"] == []
    assert s["leases"] == []


def test_bad_token_falls_back_to_unscoped(full_client):
    team = _new_team(full_client)
    s = full_client.get("/ops/state", params={"team": team["slug"], "token": "wrong"}).json()
    assert s["team"] is None
    assert {"compliance", "main"} <= set(s["apps"])   # full admin view, not an error


def test_cookie_persists_scope_after_query_param_drops(full_client):
    team = _new_team(full_client)
    full_client.get("/ops", params={"team": team["slug"], "token": team["token"]})
    s = full_client.get("/ops/state").json()   # no query params this time
    assert s["team"]["slug"] == "widgets"


def test_scoped_strike_rejects_unowned_app(full_client, teams):
    team = _new_team(full_client)
    r = full_client.post("/ops/strike", json={"app": "compliance", "ticket": "CPL-1"},
                         params={"team": team["slug"], "token": team["token"]})
    assert r.status_code == 403


def test_scoped_strike_allows_owned_app(full_client, teams):
    team = _new_team(full_client)
    teams.assign_app("compliance", "widgets")
    r = full_client.post("/ops/strike", json={"app": "compliance", "ticket": "CPL-1"},
                         params={"team": team["slug"], "token": team["token"]})
    assert r.status_code == 200, r.text
    s = full_client.get("/ops/state", params={"team": team["slug"], "token": team["token"]}).json()
    assert [l["lease_id"] for l in s["leases"]] == ["cpl-1"]


def test_scoped_client_cannot_finalize_another_teams_lease(full_client, teams):
    # unscoped strike (admin) creates a lease on 'compliance'
    full_client.post("/ops/strike", json={"app": "compliance", "ticket": "CPL-9"})
    team = _new_team(full_client)  # owns nothing
    r = full_client.post("/ops/leases/cpl-9/finalize",
                         params={"team": team["slug"], "token": team["token"]})
    assert r.status_code == 403


def test_unscoped_admin_can_still_act_on_any_lease(full_client):
    full_client.post("/ops/strike", json={"app": "compliance", "ticket": "CPL-3"})
    assert full_client.post("/ops/leases/cpl-3/finalize").status_code == 200


# ---- onboarding wizard ----

def test_onboard_page_requires_team_scope(full_client):
    r = full_client.get("/ops/onboard/state")
    assert r.status_code == 403


def test_full_onboarding_round_trip_publishes_and_scopes(full_client, fixture_repo, teams, cfg):
    team = _new_team(full_client)
    qs = {"team": team["slug"], "token": team["token"]}

    created = full_client.post("/ops/onboard/requests", params=qs, json={
        "app_name": "widget-api", "contact": "eng@widgets.com",
        "repos": [{"name": "widget-api", "url": str(fixture_repo), "role": "app"}],
    })
    assert created.status_code == 200, created.text
    req = created.json()
    assert req["status"] == "draft"
    assert req["manifest"]["HOLO_APP_SERVICE"]["value"] == "webserver"
    assert req["destructive_unreviewed"] is True

    # trial is blocked until destructive fields are reviewed
    blocked = full_client.post(f"/ops/onboard/requests/{req['request_id']}/trial", params=qs)
    assert blocked.status_code == 409

    edited = full_client.patch(f"/ops/onboard/requests/{req['request_id']}/fields", params=qs,
                               json={"fields": {"HOLO_MIGRATE_CMD": "m", "HOLO_SEED_CMD": "s",
                                                "HOLO_SEED_PROOF_SQL": "SELECT 1;"}})
    assert edited.status_code == 200, edited.text
    assert edited.json()["destructive_unreviewed"] is False

    trial = full_client.post(f"/ops/onboard/requests/{req['request_id']}/trial", params=qs)
    assert trial.status_code == 200
    assert trial.json()["status"] == "trial_passed"

    submitted = full_client.post(f"/ops/onboard/requests/{req['request_id']}/submit", params=qs)
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_approval"

    # another team cannot touch this request
    other = full_client.post("/ops/teams", json={"name": "Other", "contact": "b@b.com"}).json()
    other_qs = {"team": other["slug"], "token": other["token"]}
    forbidden = full_client.post(f"/ops/onboard/requests/{req['request_id']}/trial", params=other_qs)
    assert forbidden.status_code == 403

    pending = full_client.get("/ops/admin/onboarding/state").json()
    assert [r["request_id"] for r in pending["requests"]] == [req["request_id"]]

    approved = full_client.post(f"/ops/admin/onboarding/requests/{req['request_id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "published"
    assert (cfg.holo_dir / "manifests" / "widget-api.sh").is_file()

    # now visible in the team's own scoped state — the publish -> allowlist loop closes
    s = full_client.get("/ops/state", params=qs).json()
    assert "widget-api" in s["apps"]


def test_admin_reject_records_reason(full_client, fixture_repo):
    team = _new_team(full_client)
    qs = {"team": team["slug"], "token": team["token"]}
    req = full_client.post("/ops/onboard/requests", params=qs, json={
        "app_name": "widget-api", "contact": "eng@widgets.com",
        "repos": [{"name": "widget-api", "url": str(fixture_repo), "role": "app"}],
    }).json()
    full_client.patch(f"/ops/onboard/requests/{req['request_id']}/fields", params=qs,
                      json={"fields": {"HOLO_MIGRATE_CMD": "m", "HOLO_SEED_CMD": "s"}})
    full_client.post(f"/ops/onboard/requests/{req['request_id']}/trial", params=qs)
    full_client.post(f"/ops/onboard/requests/{req['request_id']}/submit", params=qs)

    rejected = full_client.post(f"/ops/admin/onboarding/requests/{req['request_id']}/reject",
                                json={"reason": "wrong readiness path"})
    assert rejected.status_code == 200
    state = full_client.get("/ops/onboard/state", params=qs).json()
    row = next(r for r in state["requests"] if r["request_id"] == req["request_id"])
    assert row["status"] == "rejected"
    assert row["reject_reason"] == "wrong readiness path"
