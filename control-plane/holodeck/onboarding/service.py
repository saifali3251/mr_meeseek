"""Orchestrates the onboarding state machine (docs/AUTOMATIC_ONBOARDING.md §3):

  draft --(edit fields)--> draft --(run trial)--> trial_running
    --> trial_passed --(submit)--> pending_approval --(approve)--> published
                                                      \\-(reject)-> rejected
    --> trial_failed --(edit + retry)--> draft

`approve()` is the one human-in-the-loop gate that actually writes a real
manifests/<app>.sh file and assigns team ownership — everything before it is
reversible and touches nothing outside this service's own store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from holodeck.models import TICKET_RE  # same "reaches shell/disk" safety regex as app/ticket
from holodeck.onboarding.models import (DESTRUCTIVE_FIELDS, ManifestField,
                                        MANIFEST_FIELDS, OnboardingRequest,
                                        OnboardingStatus, RepoSpec, new_request_id)
from holodeck.onboarding.recon import ReconError, clone_repo, draft_manifest
from holodeck.onboarding.render import render_manifest_sh
from holodeck.onboarding.store import OnboardingStore
from holodeck.onboarding.trial import TrialRunner
from holodeck.teams import TeamStore


class OnboardingConflict(ValueError):
    """The request isn't in a state that allows this transition — mapped to a
    409 at the route, same spirit as LeaseConflict."""


class OnboardingNotFound(KeyError):
    pass


def _primary_repo(req: OnboardingRequest) -> RepoSpec:
    return next((r for r in req.repos if r.role in ("app", "gateway")), req.repos[0])


class OnboardingService:
    def __init__(self, store: OnboardingStore, teams: TeamStore, trial_runner: TrialRunner,
                 manifests_dir: Path, workdir: Path) -> None:
        self.store = store
        self.teams = teams
        self.trial_runner = trial_runner
        self.manifests_dir = manifests_dir
        self.workdir = workdir

    # ---- create + recon ----

    def create(self, team_slug: str, app_name: str, contact: str,
              repos: list[RepoSpec], jira_project: Optional[str] = None,
              test_cmd: Optional[str] = None,
              preview_port: Optional[str] = None) -> OnboardingRequest:
        if self.teams.get(team_slug) is None:
            raise ValueError(f"unknown team '{team_slug}' — create the team first")
        if not TICKET_RE.match(app_name):
            raise ValueError("app name must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
        if (self.manifests_dir / f"{app_name}.sh").exists():
            raise OnboardingConflict(f"'{app_name}' is already a published app")
        if not repos:
            raise ValueError("at least one repo is required")

        # Auto-infer preview_port if not supplied: 3000 for frontend/gateway, 8000 for backend
        if not preview_port:
            has_frontend = any(r.role in ("frontend", "gateway") for r in repos)
            preview_port = "3000" if has_frontend else "8000"

        # Auto-infer primary test_cmd if not supplied
        if not test_cmd:
            for r in repos:
                if r.test_cmd:
                    test_cmd = r.test_cmd
                    break

        req = OnboardingRequest(request_id=new_request_id(), team_slug=team_slug,
                                app_name=app_name, contact=contact, repos=repos,
                                jira_project=jira_project, test_cmd=test_cmd,
                                preview_port=preview_port)
        self.store.put(req)
        self._run_recon(req)
        return req

    def _repo_root(self, req: OnboardingRequest, repo: RepoSpec) -> Path:
        return self.workdir / req.request_id / repo.name

    def _run_recon(self, req: OnboardingRequest) -> None:
        primary = _primary_repo(req)
        try:
            repo_root = clone_repo(primary, self._repo_root(req, primary))
            req.manifest = draft_manifest(repo_root, primary)
            req.trial_error = None
        except ReconError as e:
            # Leave the manifest empty rather than failing the request outright —
            # the review screen still renders every MANIFEST_FIELDS entry as
            # "needs your input" and the team can fill it in by hand (§3's
            # fallback: a total recon miss degrades to a blank form, not an error).
            req.manifest = {}
            req.trial_error = f"recon: {e}"
        self.store.put(req)

    # ---- reads ----

    def get(self, request_id: str) -> OnboardingRequest:
        req = self.store.get(request_id)
        if req is None:
            raise OnboardingNotFound(request_id)
        return req

    def list_for_team(self, team_slug: str) -> list[OnboardingRequest]:
        return self.store.list_for_team(team_slug)

    def list_all(self) -> list[OnboardingRequest]:
        return self.store.list_all()

    def list_pending_approval(self) -> list[OnboardingRequest]:
        return self.store.list_by_status(OnboardingStatus.PENDING_APPROVAL)

    # ---- edit ----

    def update_fields(self, request_id: str, edits: dict[str, Optional[str]]) -> OnboardingRequest:
        req = self.get(request_id)
        if req.status not in (OnboardingStatus.DRAFT, OnboardingStatus.TRIAL_FAILED):
            raise OnboardingConflict(f"cannot edit a request in status '{req.status.value}'")
        for name, value in edits.items():
            if name not in MANIFEST_FIELDS:
                raise ValueError(f"unknown manifest field '{name}'")
            req.manifest[name] = ManifestField(value=value, source="team_edit", confidence="high")
        req.status = OnboardingStatus.DRAFT
        self.store.put(req)
        return req

    # ---- trial ----

    def run_trial(self, request_id: str) -> OnboardingRequest:
        req = self.get(request_id)
        if req.status not in (OnboardingStatus.DRAFT, OnboardingStatus.TRIAL_FAILED):
            raise OnboardingConflict(f"cannot trial-build a request in status '{req.status.value}'")
        # Acknowledge any guessed destructive fields on explicit trial run
        for name in DESTRUCTIVE_FIELDS:
            f = req.manifest.get(name)
            if f and f.source == "needs_review":
                f.source = "team_reviewed"
        req.status = OnboardingStatus.TRIAL_RUNNING
        self.store.put(req)
        result = self.trial_runner.run(req, self._repo_root(req, _primary_repo(req)))
        req.trial_log = result.log
        req.trial_error = result.error
        req.status = OnboardingStatus.TRIAL_PASSED if result.passed else OnboardingStatus.TRIAL_FAILED
        self.store.put(req)
        return req

    # ---- platform review ----

    def submit(self, request_id: str) -> OnboardingRequest:
        req = self.get(request_id)
        if req.status != OnboardingStatus.TRIAL_PASSED:
            raise OnboardingConflict("only a passed trial build can be submitted for platform review")
        req.status = OnboardingStatus.PENDING_APPROVAL
        self.store.put(req)
        return req

    def approve(self, request_id: str) -> OnboardingRequest:
        req = self.get(request_id)
        if req.status != OnboardingStatus.PENDING_APPROVAL:
            raise OnboardingConflict(f"cannot approve a request in status '{req.status.value}'")
        if req.has_unreviewed_destructive_fields():
            # Should be unreachable (run_trial already blocks this path), but
            # approve() is the LAST human gate before a real manifest is written
            # to disk — it re-checks rather than trusting an earlier check held.
            raise OnboardingConflict("destructive fields still unreviewed — refusing to publish")
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        (self.manifests_dir / f"{req.app_name}.sh").write_text(render_manifest_sh(req))
        self.teams.assign_app(req.app_name, req.team_slug)
        req.status = OnboardingStatus.PUBLISHED
        self.store.put(req)
        return req

    def reject(self, request_id: str, reason: str) -> OnboardingRequest:
        req = self.get(request_id)
        if req.status != OnboardingStatus.PENDING_APPROVAL:
            raise OnboardingConflict(f"cannot reject a request in status '{req.status.value}'")
        req.status = OnboardingStatus.REJECTED
        req.reject_reason = reason
        self.store.put(req)
        return req

    def delete(self, request_id: str) -> OnboardingRequest:
        import shutil
        req = self.get(request_id)

        # Guardrail for published applications
        if req.status == OnboardingStatus.PUBLISHED:
            active_leases = self.store.count_active_leases_for_app(req.app_name)
            if active_leases > 0:
                raise OnboardingConflict(
                    f"Cannot delete published application '{req.app_name}': "
                    f"{active_leases} active workspace(s) are currently running. Destroy them first."
                )

            # 1. Remove manifest file if it exists
            manifest_file = self.manifests_dir / f"{req.app_name}.sh"
            if manifest_file.exists():
                try:
                    manifest_file.unlink()
                except Exception:
                    pass

            # 2. Unassign from team
            try:
                self.teams.unassign_app(req.app_name)
            except Exception:
                pass

        # Clean up scratch workdir
        req_workdir = self.workdir / req.request_id
        if req_workdir.exists() and req_workdir.is_dir():
            try:
                shutil.rmtree(req_workdir)
            except Exception:
                pass

        # Remove from database
        self.store.delete(request_id)
        return req
