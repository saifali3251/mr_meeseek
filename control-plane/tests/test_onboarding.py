"""OnboardingService — the Easy Way's state machine (docs/AUTOMATIC_ONBOARDING.md
§3): recon drafts a manifest, a team edits/trials it, a platform-review human
approves before anything is published. All against FakeTrialRunner — no
Docker — and a local fixture "repo" (a plain directory with a compose file),
which recon.clone_repo() treats as an already-checked-out path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from holodeck.onboarding.models import (DESTRUCTIVE_FIELDS, ManifestField,
                                        OnboardingStatus, RepoSpec)
from holodeck.onboarding.recon import ReconError, draft_manifest
from holodeck.onboarding.render import render_manifest_sh
from holodeck.onboarding.service import OnboardingConflict, OnboardingService
from holodeck.onboarding.store import OnboardingStore
from holodeck.onboarding.trial import FakeTrialRunner
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
    (repo / "app.py").write_text('@app.route("/healthz")\ndef healthz(): return "ok"\n')
    (repo / "Makefile").write_text("seed-db:\n\tpython seed.py\n")
    return repo


@pytest.fixture
def teams() -> TeamStore:
    t = TeamStore()
    t.create("Widgets", "eng@widgets.com", slug="widgets")
    return t


@pytest.fixture
def manifests_out(tmp_path: Path) -> Path:
    d = tmp_path / "manifests"
    d.mkdir()
    return d


@pytest.fixture
def trial() -> FakeTrialRunner:
    return FakeTrialRunner()


@pytest.fixture
def svc(teams, trial, manifests_out, tmp_path) -> OnboardingService:
    return OnboardingService(OnboardingStore(), teams, trial, manifests_out, tmp_path / "work")


def _repos(fixture_repo) -> list:
    return [RepoSpec(name="widget-api", url=str(fixture_repo), role="app")]


# ---- recon (unit) ----

def test_draft_manifest_infers_app_and_db(fixture_repo):
    fields = draft_manifest(fixture_repo, RepoSpec(name="widget-api", url=str(fixture_repo)))
    assert fields["HOLO_APP_SERVICE"].value == "webserver"
    assert fields["HOLO_APP_PORT"].value == "8080"
    assert fields["HOLO_PG_SERVICE"].value == "db"
    assert fields["HOLO_PG_USER"].value == "appuser"
    assert fields["HOLO_PG_DB"].value == "appdb"
    assert fields["HOLO_PGDATA_MODE"].value == "named-volume"
    assert fields["HOLO_READINESS_PATH"].value == "/healthz"
    assert set(fields["WS_SERVICES"].value.split()) == {"webserver", "db"}


def test_draft_manifest_never_trusts_destructive_fields(fixture_repo):
    fields = draft_manifest(fixture_repo, RepoSpec(name="widget-api", url=str(fixture_repo)))
    for name in DESTRUCTIVE_FIELDS:
        assert fields[name].source == "needs_review"  # even though a Makefile guess exists


def test_draft_manifest_raises_without_a_compose_file(tmp_path):
    empty = tmp_path / "empty-repo"
    empty.mkdir()
    with pytest.raises(ReconError):
        draft_manifest(empty, RepoSpec(name="x", url=str(empty)))


# ---- service lifecycle ----

def test_create_runs_recon_and_lands_in_draft(svc, fixture_repo):
    req = svc.create("widgets", "widget-api", "eng@widgets.com", _repos(fixture_repo))
    assert req.status == OnboardingStatus.DRAFT
    assert req.manifest["HOLO_APP_SERVICE"].value == "webserver"


def test_create_rejects_unknown_team(svc, fixture_repo):
    with pytest.raises(ValueError):
        svc.create("no-such-team", "widget-api", "x@x.com", _repos(fixture_repo))


def test_create_rejects_app_name_collision(svc, fixture_repo, manifests_out):
    (manifests_out / "widget-api.sh").write_text("HOLO_APP=widget-api\n")
    with pytest.raises(OnboardingConflict):
        svc.create("widgets", "widget-api", "x@x.com", _repos(fixture_repo))


def test_recon_failure_leaves_a_blank_editable_draft(svc, tmp_path):
    empty = tmp_path / "no-compose-here"
    empty.mkdir()
    req = svc.create("widgets", "blank-app", "x@x.com",
                     [RepoSpec(name="blank-app", url=str(empty))])
    assert req.status == OnboardingStatus.DRAFT
    assert req.manifest == {}
    assert "recon:" in req.trial_error


def test_trial_blocked_while_destructive_fields_unreviewed(svc, fixture_repo):
    req = svc.create("widgets", "widget-api", "eng@widgets.com", _repos(fixture_repo))
    with pytest.raises(OnboardingConflict):
        svc.run_trial(req.request_id)


def test_edit_then_trial_then_submit_then_approve_publishes(svc, fixture_repo, manifests_out, teams):
    req = svc.create("widgets", "widget-api", "eng@widgets.com", _repos(fixture_repo))
    edited = svc.update_fields(req.request_id, {
        "HOLO_MIGRATE_CMD": "python setup.py migrate",
        "HOLO_SEED_CMD": "python seed.py",
        "HOLO_SEED_PROOF_SQL": "SELECT count(*) FROM widgets;",
    })
    assert not edited.has_unreviewed_destructive_fields()
    assert edited.manifest["HOLO_MIGRATE_CMD"].source == "team_edit"

    passed = svc.run_trial(req.request_id)
    assert passed.status == OnboardingStatus.TRIAL_PASSED
    assert "fake trial" in passed.trial_log

    submitted = svc.submit(req.request_id)
    assert submitted.status == OnboardingStatus.PENDING_APPROVAL
    assert [r.request_id for r in svc.list_pending_approval()] == [req.request_id]

    published = svc.approve(req.request_id)
    assert published.status == OnboardingStatus.PUBLISHED
    manifest_path = manifests_out / "widget-api.sh"
    assert manifest_path.is_file()
    text = manifest_path.read_text()
    assert "HOLO_APP=widget-api" in text
    assert "HOLO_APP_SERVICE=webserver" in text
    assert teams.team_for_app("widget-api") == "widgets"


def test_submit_requires_a_passed_trial(svc, fixture_repo):
    req = svc.create("widgets", "widget-api", "eng@widgets.com", _repos(fixture_repo))
    with pytest.raises(OnboardingConflict):
        svc.submit(req.request_id)


def test_approve_requires_pending_approval(svc, fixture_repo):
    req = svc.create("widgets", "widget-api", "eng@widgets.com", _repos(fixture_repo))
    with pytest.raises(OnboardingConflict):
        svc.approve(req.request_id)


def test_failed_trial_can_be_edited_and_retried(svc, fixture_repo, trial):
    req = svc.create("widgets", "widget-api", "eng@widgets.com", _repos(fixture_repo))
    svc.update_fields(req.request_id, {"HOLO_MIGRATE_CMD": "m", "HOLO_SEED_CMD": "s"})
    trial.fail_for.add(req.request_id)
    failed = svc.run_trial(req.request_id)
    assert failed.status == OnboardingStatus.TRIAL_FAILED
    assert failed.trial_error == "readiness_timeout"

    trial.fail_for.discard(req.request_id)
    edited = svc.update_fields(req.request_id, {"HOLO_APP_PORT": "9090"})
    assert edited.status == OnboardingStatus.DRAFT
    passed = svc.run_trial(req.request_id)
    assert passed.status == OnboardingStatus.TRIAL_PASSED


def test_reject_records_reason_and_can_resubmit_after_edit(svc, fixture_repo):
    req = svc.create("widgets", "widget-api", "eng@widgets.com", _repos(fixture_repo))
    svc.update_fields(req.request_id, {"HOLO_MIGRATE_CMD": "m", "HOLO_SEED_CMD": "s"})
    svc.run_trial(req.request_id)
    svc.submit(req.request_id)
    rejected = svc.reject(req.request_id, "wrong readiness path")
    assert rejected.status == OnboardingStatus.REJECTED
    assert rejected.reject_reason == "wrong readiness path"
    with pytest.raises(OnboardingConflict):
        svc.approve(req.request_id)


def test_is_composite_and_git_subdir_in_render(svc, fixture_repo, tmp_path, teams, manifests_out):
    gateway = tmp_path / "repo-src" / "gateway"
    gateway.mkdir(parents=True)
    (gateway / "compose.yaml").write_text(_COMPOSE)
    repos = [
        RepoSpec(name="gateway", url=str(gateway), role="gateway"),
        RepoSpec(name="widget-api", url=str(fixture_repo), role="app", depends_on=["gateway"]),
    ]
    req = svc.create("widgets", "composite-app", "eng@widgets.com", repos)
    assert req.is_composite()
    svc.update_fields(req.request_id, {"HOLO_MIGRATE_CMD": "m", "HOLO_SEED_CMD": "s"})
    svc.run_trial(req.request_id)
    svc.submit(req.request_id)
    svc.approve(req.request_id)
    text = (manifests_out / "composite-app.sh").read_text()
    assert "HOLO_GIT_SUBDIR=gateway" in text  # the first "app"/"gateway"-role repo


# ---- render (shell-safety) ----

def test_render_shlex_quotes_hostile_field_values():
    from holodeck.onboarding.models import OnboardingRequest, new_request_id
    req = OnboardingRequest(
        request_id=new_request_id(), team_slug="widgets", app_name="x", contact="a@x.com",
        repos=[RepoSpec(name="x", url="https://x/x")],
        manifest={"HOLO_PG_DB": ManifestField(value="innocent$(rm -rf /)", source="team_edit")},
    )
    text = render_manifest_sh(req)
    assert "$(rm -rf /)" not in text or "'" in text  # never left as a live substitution
    assert "HOLO_PG_DB='innocent$(rm -rf /)'" in text
