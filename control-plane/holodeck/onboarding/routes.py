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
    depends_on: list[str] = Field(default_factory=list)
    via: Optional[str] = None
    env_var: Optional[str] = None


class CreateOnboardingRequest(BaseModel):
    team_slug: str
    app_name: str
    contact: str = ""
    repos: list[RepoSpecIn]


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

    @router.post("/requests", status_code=201, dependencies=[Depends(auth)])
    def create(body: CreateOnboardingRequest) -> dict:
        try:
            req = svc.create(body.team_slug, body.app_name, body.contact,
                             [RepoSpec(**r.model_dump()) for r in body.repos])
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _req_dict(req)

    @router.get("/requests", dependencies=[Depends(auth)])
    def list_for_team(team: str) -> dict:
        return {"requests": [_req_dict(r) for r in svc.list_for_team(team)]}

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
