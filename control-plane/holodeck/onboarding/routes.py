"""Programmatic onboarding API — mirrors api.py's /leases shape: tokened
(the shared HOLODECK_TOKEN), for any external caller (a CLI, a script, a
future non-browser client) rather than the /ops browser surface, which has its
own in-process routes in pages.py (no shared token, same loopback posture as
the rest of /ops).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from holodeck.config import Config
from holodeck.onboarding.models import OnboardingRequest, RepoSpec
from holodeck.onboarding.service import (OnboardingConflict, OnboardingNotFound,
                                         OnboardingService)


class RepoSpecIn(BaseModel):
    name: str
    url: str
    branch: str = "main"
    role: str = "app"
    test_cmd: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)
    via: Optional[str] = None
    env_var: Optional[str] = None


class CreateOnboardingRequest(BaseModel):
    team_slug: str
    app_name: str
    contact: str = ""
    jira_project: Optional[str] = None
    test_cmd: Optional[str] = None
    preview_port: Optional[str] = None
    repos: list[RepoSpecIn]


class ValidateRepoBody(BaseModel):
    url: str
    branch: str = "main"


class FieldEdits(BaseModel):
    fields: dict[str, Optional[str]]


class RejectBody(BaseModel):
    reason: str


def _req_dict(req: OnboardingRequest) -> dict:
    d = asdict(req)
    d["status"] = req.status.value  # asdict leaves the Enum instance; pin the plain value
    return d


def build_onboarding_api_router(svc: OnboardingService, cfg: Config) -> APIRouter:
    router = APIRouter(prefix="/onboarding", tags=["onboarding"])

    def auth(authorization: Optional[str] = Header(default=None)) -> None:
        # same shared-token dependency as api.py's /leases — empty token disables it.
        if not cfg.token:
            return
        if authorization != f"Bearer {cfg.token}":
            raise HTTPException(401, "missing or invalid token")

    @router.post("/validate-repo")
    def validate_repo(body: ValidateRepoBody) -> dict:
        from holodeck.onboarding.git_validator import validate_git_repo
        return validate_git_repo(body.url, body.branch)

    @router.post("/requests", status_code=201, dependencies=[Depends(auth)])
    def create(body: CreateOnboardingRequest) -> dict:
        try:
            req = svc.create(body.team_slug, body.app_name, body.contact,
                             [RepoSpec(**r.model_dump()) for r in body.repos],
                             jira_project=body.jira_project,
                             test_cmd=body.test_cmd,
                             preview_port=body.preview_port)
            if body.test_cmd:
                req.manifest["HOLO_TEST_CMD"] = ManifestField(value=body.test_cmd, source="user_input", confidence="high")
            if body.preview_port:
                req.manifest["HOLO_APP_PORT"] = ManifestField(value=str(body.preview_port), source="user_input", confidence="high")
            if body.test_cmd or body.preview_port:
                svc.store.put(req)
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _req_dict(req)

    @router.get("/requests", dependencies=[Depends(auth)])
    def list_requests(team: Optional[str] = None) -> dict:
        if team:
            reqs = svc.list_for_team(team)
        else:
            reqs = svc.list_all()
        return {"requests": [_req_dict(r) for r in reqs]}

    @router.get("/requests/{request_id}", dependencies=[Depends(auth)])
    def get(request_id: str) -> dict:
        try:
            return _req_dict(svc.get(request_id))
        except OnboardingNotFound:
            raise HTTPException(404, "no such onboarding request")

    @router.patch("/requests/{request_id}/fields", dependencies=[Depends(auth)])
    def edit(request_id: str, body: FieldEdits) -> dict:
        try:
            return _req_dict(svc.update_fields(request_id, body.fields))
        except OnboardingNotFound:
            raise HTTPException(404, "no such onboarding request")
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post("/requests/{request_id}/trial", dependencies=[Depends(auth)])
    def trial(request_id: str) -> dict:
        try:
            return _req_dict(svc.run_trial(request_id))
        except OnboardingNotFound:
            raise HTTPException(404, "no such onboarding request")
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))

    @router.post("/requests/{request_id}/submit", dependencies=[Depends(auth)])
    def submit(request_id: str) -> dict:
        try:
            return _req_dict(svc.submit(request_id))
        except OnboardingNotFound:
            raise HTTPException(404, "no such onboarding request")
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))

    @router.get("/admin/pending", dependencies=[Depends(auth)])
    def pending() -> dict:
        return {"requests": [_req_dict(r) for r in svc.list_pending_approval()]}

    @router.post("/admin/requests/{request_id}/approve", dependencies=[Depends(auth)])
    def approve(request_id: str) -> dict:
        try:
            return _req_dict(svc.approve(request_id))
        except OnboardingNotFound:
            raise HTTPException(404, "no such onboarding request")
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))

    @router.post("/admin/requests/{request_id}/reject", dependencies=[Depends(auth)])
    def reject(request_id: str, body: RejectBody) -> dict:
        try:
            return _req_dict(svc.reject(request_id, body.reason))
        except OnboardingNotFound:
            raise HTTPException(404, "no such onboarding request")
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))

    return router
