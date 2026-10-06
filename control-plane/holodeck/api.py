"""FastAPI app factory + routes. Provider-agnostic: every handler talks to
LeaseService, never to a substrate."""

from __future__ import annotations

import hashlib
import hmac
import logging
import shlex
from pathlib import Path
from typing import Optional

from fastapi import (Depends, FastAPI, Header, HTTPException, Query, Request,
                    Response, WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware

from holodeck.config import Config
from holodeck.golden_sync import GoldenSyncManager
from holodeck.models import Lease, LeaseStatus
from holodeck.onboarding.pages import build_onboarding_pages_router
from holodeck.onboarding.routes import build_onboarding_api_router
from holodeck.onboarding.service import OnboardingService
from holodeck.ops_console import build_ops_router
from holodeck.providers.base import ProviderError
from holodeck.schemas import (AcquireRequest, CapabilitiesResponse,
                              EnvironmentInfo, EnvironmentsResponse,
                              EvidenceResponse, ExecRequest, ExecResponse,
                              ExtendRequest, FinalizeRequest, HealthResponse,
                              LeaseListResponse, LeaseResponse, ReadyResponse)
from holodeck.service import LeaseConflict, LeaseNotReady, LeaseService
from holodeck.teams import TeamStore

# leases occupying a capacity slot (not torn down / rolled back)
_ACTIVE = (LeaseStatus.PENDING, LeaseStatus.QUEUED, LeaseStatus.READY)

log = logging.getLogger("holodeck.api")


def _lease_response(l: Lease) -> LeaseResponse:
    h = l.handle
    return LeaseResponse(
        lease_id=l.lease_id, app=l.app, ticket=l.ticket, status=l.status.value,
        preview_port=l.preview_port,
        compose_project=h.compose_project if h else None,
        ws_dir=h.ws_dir if h else None,
        golden_head=h.golden_head if h else None,
        target_repo=l.target_repo,
        base_overrides=l.base_overrides,
        exec_url=f"/leases/{l.lease_id}/exec", token=l.token,
        expires_at=l.expires_at, error=l.error,
    )


def create_app(cfg: Config, service: LeaseService,
              teams: Optional[TeamStore] = None,
              onboarding: Optional[OnboardingService] = None,
              golden_sync: Optional[GoldenSyncManager] = None) -> FastAPI:
    app = FastAPI(title="Holodeck Lease API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if golden_sync is None:
        pool = getattr(service.provider, "pool", None)
        golden_sync = GoldenSyncManager(cfg, pool=pool)
    app.state.golden_sync = golden_sync

    def auth(authorization: Optional[str] = Header(default=None)) -> None:
        # issue #2: shared-token check as a dependency (Phase 2, not Phase 5).
        # Empty token disables it (local/test bring-up only).
        if not cfg.token:
            return
        expected = f"Bearer {cfg.token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="missing or invalid token")

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        # provider name in health so a demo can show compose vs eks (confirmation #4).
        return HealthResponse(
            status="ok", provider=service.provider.name,
            active_leases=service.store.count_active(), max_leases=cfg.max_leases,
        )

    @app.get("/readyz", response_model=ReadyResponse)
    def readyz(response: Response,
               app_q: Optional[str] = Query(default=None, alias="app")) -> ReadyResponse:
        # Deeper than /healthz: substrate preflight (Omnigent's prepare()).
        # ?app=<name> scopes the per-app checks to one app. Without it every
        # manifest is checked, so one unbuilt golden 503s the whole endpoint
        # and blocks callers whose own app is healthy.
        problems = service.preflight(app_q)
        response.status_code = 503 if problems else 200
        return ReadyResponse(ready=not problems, provider=service.provider.name,
                             problems=problems)

    @app.get("/capabilities", response_model=CapabilitiesResponse)
    def capabilities() -> CapabilitiesResponse:
        # What the active substrate supports; the harness adapter maps this.
        c = service.capabilities()
        return CapabilitiesResponse(provider=service.provider.name, **c.__dict__)

    @app.get("/caddy-ask")
    def caddy_ask(domain: str = Query(...)) -> Response:
        """Endpoint for Caddy's on_demand_tls ask check.
        Validates whether TLS certificate should be issued for the domain.
        Ensures certificate is only issued for valid preview subdomains in the port pool.
        """
        import re
        m = re.match(r"^p(?P<port>\d+)\.", domain.lower())
        if m:
            port = int(m.group("port"))
            if cfg.port_pool_start <= port <= cfg.port_pool_end:
                return Response(status_code=200, content="OK")
        return Response(status_code=403, content="Domain not allowed")

    @app.post("/webhooks/github")
    async def github_webhook(
        request: Request,
        app_param: Optional[str] = Query(None, alias="app"),
    ) -> dict:
        """GitHub push webhook endpoint for automated golden image rebuilding.
        Validates X-Hub-Signature-256 HMAC when a secret is configured, debounces rapid merges
        to main, coalesces concurrent pushes, and triggers atomic golden rebuilds.
        """
        body = await request.body()

        if cfg.github_webhook_secret:
            sig = request.headers.get("X-Hub-Signature-256")
            if not sig or not sig.startswith("sha256="):
                raise HTTPException(status_code=401, detail="Missing or invalid X-Hub-Signature-256 header")
            computed = "sha256=" + hmac.new(
                cfg.github_webhook_secret.encode("utf-8"),
                body,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(computed, sig):
                raise HTTPException(status_code=401, detail="Invalid webhook signature")

        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return {"status": "pong"}
        if event != "push":
            return {"status": "ignored", "reason": f"unhandled event '{event}'"}

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        ref = payload.get("ref", "")
        repo_data = payload.get("repository") or {}
        repo_name = repo_data.get("name", "")
        commit_sha = payload.get("after", "")

        target_app = app_param
        if not target_app:
            # Match against known apps and composite component repos
            for candidate in cfg.apps:
                if candidate == repo_name:
                    target_app = candidate
                    break
                mvars = cfg.load_manifest_vars(candidate)
                composite = mvars.get("HOLO_COMPOSITE_REPOS", "").split()
                if repo_name in composite:
                    target_app = candidate
                    break
        if not target_app:
            target_app = cfg.default_app

        res = golden_sync.notify_push(
            app=target_app,
            repo=repo_name,
            branch=ref.replace("refs/heads/", ""),
            commit_sha=commit_sha,
        )
        return res

    @app.get("/ops/golden/state")
    def ops_golden_state(app: Optional[str] = Query(None)) -> dict:
        """Inspect current debounce / building status of golden image synchronizer."""
        return golden_sync.state(app)

    @app.post("/ops/golden/rebuild")
    def ops_golden_rebuild(app: Optional[str] = Query(None), force: bool = Query(False)) -> dict:
        """Manually trigger golden sync and pool refresh."""
        target_app = app or cfg.default_app
        return golden_sync.trigger_sync(target_app, force=force)

    @app.post("/leases", response_model=LeaseResponse, status_code=201,
              dependencies=[Depends(auth)])
    def acquire(req: AcquireRequest) -> LeaseResponse:
        # issue #1: app must be in the on-disk allowlist before any shell-out.
        if req.app not in cfg.apps:
            raise HTTPException(status_code=422,
                                detail=f"unknown app '{req.app}'; valid: {sorted(cfg.apps)}")
        # Same treatment, one level down: target_repo must be one of the app's OWN
        # HOLO_COMPOSITE_REPOS (read from its manifest, never guessed) — a
        # single-repo app has an empty set, so ANY target_repo value 422s for it,
        # not just an unrecognized one.
        if req.target_repo is not None:
            valid_repos = service.provider.valid_target_repos(req.app)
            if req.target_repo not in valid_repos:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown target_repo '{req.target_repo}' for app '{req.app}'; "
                           f"valid: {sorted(valid_repos) or '(none — single-repo app)'}")
        if req.base_overrides:
            valid_repos = service.provider.valid_target_repos(req.app)
            invalid_deps = [r for r in req.base_overrides if r not in valid_repos]
            if invalid_deps:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown dependency repo(s) in base_overrides {invalid_deps} for app '{req.app}'; "
                           f"valid: {sorted(valid_repos)}")
        try:
            lease = service.acquire(
                req.app, req.ticket, req.preview, req.ttl_s, req.target_repo,
                base_overrides=req.base_overrides,
            )
        except LeaseConflict as e:
            raise HTTPException(status_code=409,
                                detail=f"lease '{e.lease_id}' already exists")
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return _lease_response(lease)

    # B11 read endpoints — feed the Console (and any curl/tooling). Reads only;
    # the mutating verbs above keep the shared-token dependency.
    @app.get("/leases", response_model=LeaseListResponse, dependencies=[Depends(auth)])
    def list_leases(include_released: bool = Query(default=False)) -> LeaseListResponse:
        leases = service.store.all()
        if not include_released:
            leases = [l for l in leases if l.status != LeaseStatus.RELEASED]
        leases.sort(key=lambda l: l.created_at, reverse=True)
        return LeaseListResponse(leases=[_lease_response(l) for l in leases])

    @app.get("/environments", response_model=EnvironmentsResponse)
    def environments() -> EnvironmentsResponse:
        # readiness is substrate-level (like /readyz), so no token — but the
        # active-lease count is a cheap, useful roll-up for the Console.
        active: dict[str, int] = {}
        for l in service.store.all():
            if l.status in _ACTIVE:
                active[l.app] = active.get(l.app, 0) + 1
        envs = []
        for name in sorted(cfg.apps):
            problems = service.preflight(name)
            envs.append(EnvironmentInfo(
                app=name, ready=not problems, problems=problems,
                active_leases=active.get(name, 0),
                target_repos=sorted(service.provider.valid_target_repos(name))))
        return EnvironmentsResponse(provider=service.provider.name, environments=envs)

    @app.get("/leases/{lease_id}", response_model=LeaseResponse,
             dependencies=[Depends(auth)])
    def get_lease(lease_id: str) -> LeaseResponse:
        lease = service.store.get(lease_id)
        if lease is None:
            raise HTTPException(status_code=404, detail="no such lease")
        return _lease_response(lease)

    @app.get("/leases/{lease_id}/evidence", response_model=EvidenceResponse,
             dependencies=[Depends(auth)])
    def get_evidence(lease_id: str) -> EvidenceResponse:
        # The stored finalize bundle (re-read; finalize is the POST that derives it).
        lease = service.store.get(lease_id)
        if lease is None:
            raise HTTPException(status_code=404, detail="no such lease")
        if lease.evidence is None:
            raise HTTPException(status_code=404, detail="lease not finalized yet")
        return EvidenceResponse(lease_id=lease_id, **lease.evidence.__dict__)

    @app.post("/leases/{lease_id}/finalize", response_model=EvidenceResponse,
              dependencies=[Depends(auth)])
    def finalize(lease_id: str, req: Optional[FinalizeRequest] = None) -> EvidenceResponse:
        # The agent still cannot supply a test command (confirmation #2) — that field
        # has no request-body path at all. req's three fields are PR title/body
        # cosmetics ONLY (see FinalizeRequest/LeaseService.finalize docstrings);
        # they never reach readiness/tests/diff. Optional so a caller sending no
        # body at all (the original contract) still works unchanged.
        try:
            ev = service.finalize(
                lease_id,
                agent_summary=req.agent_summary if req else None,
                ticket_summary=req.ticket_summary if req else None,
                issue_type=req.issue_type if req else None,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="no such lease")
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return EvidenceResponse(lease_id=lease_id, **ev.__dict__)

    @app.post("/leases/{lease_id}/extend", response_model=LeaseResponse,
              dependencies=[Depends(auth)])
    def extend(lease_id: str, req: ExtendRequest) -> LeaseResponse:
        try:
            return _lease_response(service.extend(lease_id, req.ttl_s))
        except KeyError:
            raise HTTPException(status_code=404, detail="no such lease")

    @app.delete("/leases/{lease_id}", response_model=LeaseResponse,
                dependencies=[Depends(auth)])
    def release(lease_id: str) -> LeaseResponse:
        try:
            return _lease_response(service.release(lease_id))
        except KeyError:
            raise HTTPException(status_code=404, detail="no such lease")
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))

    # exec (B3): one-shot POST runs a command in the lease's workspace via the
    # provider and returns exit/stdout/stderr. The GET+WS (PTY stream) shape stays
    # reserved by the deck -> 501.
    @app.post("/leases/{lease_id}/exec", response_model=ExecResponse,
              dependencies=[Depends(auth)])
    def exec_post(lease_id: str, req: ExecRequest) -> ExecResponse:
        argv = shlex.split(req.cmd) if isinstance(req.cmd, str) else list(req.cmd)
        if not argv:
            raise HTTPException(status_code=422, detail="empty command")
        try:
            r = service.exec(lease_id, argv, service=req.service,
                             workdir=req.workdir, timeout_s=req.timeout_s, detach=req.detach)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such lease")
        except LeaseNotReady as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return ExecResponse(lease_id=lease_id, argv=r.argv, exit_code=r.exit_code,
                            stdout=r.stdout, stderr=r.stderr)

    # file copy into the workspace (Omnigent put()): raw bytes body, `?path=`
    # is the destination inside the container, optional `?service=`.
    @app.put("/leases/{lease_id}/files", status_code=204, dependencies=[Depends(auth)])
    async def put_file(
        lease_id: str,
        request: Request,
        path: str = Query(..., description="destination path inside the workspace"),
        svc: Optional[str] = Query(None, alias="service"),
    ) -> Response:
        content = await request.body()
        try:
            service.put_file(lease_id, path, content, service=svc)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such lease")
        except LeaseNotReady as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return Response(status_code=204)

    # exec gateway (the deck's exec_url): a WebSocket scoped to one lease by its
    # own capability token — NOT the shared admin token, so a lease's exec_url +
    # token can be handed to an agent without granting it anything else.
    #   client -> {"cmd": [...] | "str", "service"?, "workdir"?}
    #   server -> {"channel":"stdout"|"stderr","data":...}*  then {"channel":"exit","code":N}
    @app.websocket("/leases/{lease_id}/exec")
    async def exec_ws(websocket: WebSocket, lease_id: str) -> None:
        token = websocket.query_params.get("token")
        if not token:
            authz = websocket.headers.get("authorization", "")
            if authz.lower().startswith("bearer "):
                token = authz[7:]
        try:
            lease = service.authorize_exec(lease_id, token)
        except (KeyError, PermissionError):
            await websocket.close(code=4401)  # unauthorized
            return
        except LeaseNotReady:
            await websocket.close(code=4409)  # not ready
            return

        await websocket.accept()
        try:
            req = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        cmd = req.get("cmd")
        argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd or [])
        if not argv:
            await websocket.send_json({"channel": "error", "detail": "empty command"})
            await websocket.close()
            return
        try:
            async for channel, text in service.stream_exec(
                lease, argv, service=req.get("service"), workdir=req.get("workdir")
            ):
                if channel == "exit":
                    await websocket.send_json({"channel": "exit", "code": int(text)})
                else:
                    await websocket.send_json({"channel": channel, "data": text})
        except WebSocketDisconnect:
            return
        except ProviderError as e:
            await websocket.send_json({"channel": "error", "detail": str(e)})
        finally:
            await websocket.close()

    # Operator Console (Track E): the single-screen env + workspace + evidence UI.
    # Its browser-facing routes talk to `service` in-process (no shared token in
    # the page), matching the /console task board's loopback posture.
    ops_router = build_ops_router(service, cfg, teams)
    app.include_router(ops_router)
    if hasattr(ops_router, "broadcaster"):
        app.state.broadcaster = ops_router.broadcaster

    # Automatic onboarding (docs/AUTOMATIC_ONBOARDING.md) — off unless the
    # caller wired both a TeamStore and an OnboardingService (see factory.py's
    # HOLODECK_ONBOARDING gate); a bare create_app(cfg, service) — every
    # existing test's call shape — stays byte-identical to before this existed.
    if teams is not None and onboarding is not None:
        app.state.onboarding = onboarding
        app.state.teams = teams
        app.include_router(build_onboarding_api_router(onboarding, cfg))
        app.include_router(build_onboarding_pages_router(onboarding, teams))

    # Mount modern React Console UI if built in ui/dist
    ui_dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
    if ui_dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/console", StaticFiles(directory=str(ui_dist), html=True), name="console")

    return app
