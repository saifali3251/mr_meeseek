"""Operator Console (Track E) — the single-screen environment + workspace +
evidence + agent-runs view over the lease service.

This is the ONE operator surface. It shows two things side by side: the
environment/workspace/evidence primitives (E1–E4 — what goldens exist, what
workspaces are live, the evidence each finalize produced) and the agent runs a
harness (Omnigent) drives on top of them, including their live status and any
human-in-the-loop question awaiting an Approve/Decline. `Strike` here is the raw
ops primitive (lease a fresh env); the Agent-runs panel reads/acts via the
co-located console manager (`holodeck.console`). The old standalone runs board
at `/console` is retired and redirects here.

Browser-facing routes call `service` in-process (no shared token in the page),
matching the console's loopback posture — reads are cheap, and the
mutating verbs are reachable only from the same box (loopback + WARP), the same
guarantee the task board already relies on. The authed, tokened equivalents live
on the lease API itself (`GET /leases`, `/environments`, `POST /leases`, …).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

log = logging.getLogger("holodeck.ops_console")


class OpsStateBroadcaster:
    """Pub/sub broadcaster for real-time Server-Sent Events (SSE).

    Maintains active SSE subscriber queues and dispatches notifications
    thread-safely across worker threads to the asyncio event loop.
    """
    def __init__(self):
        self._listeners: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=20)
        with self._lock:
            self._listeners.add((loop, q))
        return q

    def unsubscribe(self, loop: asyncio.AbstractEventLoop, q: asyncio.Queue) -> None:
        with self._lock:
            self._listeners.discard((loop, q))

    def notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for loop, q in listeners:
            if loop.is_closed():
                continue
            def _push(queue=q):
                if queue.full():
                    try:
                        queue.get_nowait()
                    except Exception:
                        pass
                try:
                    queue.put_nowait(True)
                except Exception:
                    pass
            try:
                loop.call_soon_threadsafe(_push)
            except Exception:
                pass

from holodeck.config import Config
from holodeck.models import TICKET_RE, LeaseStatus
from holodeck.providers.base import ProviderError
from holodeck.service import LeaseConflict, LeaseService
from holodeck.teams import (SLUG_RE, Team, TeamExistsError, TeamStore,
                           resolve_team, set_team_cookies, COOKIE_TEAM, COOKIE_TOKEN)

_ACTIVE = (LeaseStatus.PENDING, LeaseStatus.QUEUED, LeaseStatus.READY)
_RUNNING = (LeaseStatus.PENDING, LeaseStatus.READY)


class StrikeRequest(BaseModel):
    ticket: str
    app: Optional[str] = None
    preview: Optional[int] = None
    ttl_s: Optional[int] = None


class ExtendBody(BaseModel):
    ttl_s: int = 1800


class NewTeamRequest(BaseModel):
    name: str
    contact: str = ""
    slug: Optional[str] = None


class CapacityRequest(BaseModel):
    max_app_leases: int
    role: Optional[str] = "superadmin"


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    authenticated: bool
    username: str
    role: str


class ChatMessagePayload(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessagePayload] = []


# Fixed demo hostnames — the 3 provisioned preview envs. A lease maps to one of
# them by its pool offset (preview_port - cfg.port_pool_start). We take that offset
# MOD 3 so any pool port lands on a real mapped env rather than falling through to
# a raw 127.0.0.1 link: the allocator doesn't guarantee ports 0/1/2 (a lease can
# land on offset 4, etc.), and there are only ever 3 preview envs behind these.
_PREVIEW_HOSTS = ["workspace-one", "workspace-two", "workspace-three"]


def _preview_url(preview_port: Optional[int], cfg, app: Optional[str] = None, ticket: Optional[str] = None) -> Optional[str]:
    if preview_port is None or cfg is None:
        return None
    return cfg.workspace_preview_url(preview_port, app=app, ticket=ticket)


def _lease_dict(l, cfg=None) -> dict:
    h = l.handle
    # Show the operator-facing app name (alias). The real key still lives on the
    # lease; the console only ever displays this, never posts it back.
    app = cfg.app_label(l.app) if cfg is not None else l.app
    return {
        "lease_id": l.lease_id, "app": app, "ticket": l.ticket,
        "status": l.status.value, "preview_port": l.preview_port,
        "preview_url": _preview_url(l.preview_port, cfg, app=l.app, ticket=l.ticket),
        "expires_at": l.expires_at, "created_at": l.created_at, "error": l.error,
        "golden_head": h.golden_head if h else None,
        "seed_rows": h.seed_rows if h else None,   # live status: warm-DB seeded-row count
        "compose_project": h.compose_project if h else None,
        "evidence": asdict(l.evidence) if l.evidence else None,
        "pr_url": (l.evidence.pr_url if l.evidence and getattr(l.evidence, "pr_url", None) else None),
    }


def build_ops_router(service: LeaseService, cfg: Config,
                    teams: Optional[TeamStore] = None) -> APIRouter:
    """`teams=None` (the default) is byte-identical to before team-scoping
    existed: every route below resolves to an unscoped/admin view, since
    `resolve_team()` short-circuits to None with no store to check against."""
    r = APIRouter(tags=["console"])
    broadcaster = OpsStateBroadcaster()
    r.broadcaster = broadcaster
    service.add_change_listener(broadcaster.notify)

    def _owned_apps(team: Optional[Team]) -> Optional[set[str]]:
        """None = unscoped (see everything — the platform admin view, and the
        default whenever no team resolves). A team with zero onboarded apps
        yet gets an empty set, NOT None — it must see nothing, not everything."""
        if teams is None or team is None:
            return None
        return teams.apps_for_team(team.slug)

    def _snapshot(team: Optional[Team] = None) -> dict:
        import time
        owned = _owned_apps(team)
        default_key = cfg.app_key(cfg.default_app) if hasattr(cfg, "app_key") else cfg.default_app
        default_apps = {default_key, cfg.default_app, "full-stack-application"}
        visible = (lambda app: owned is None or app in owned or app in default_apps)
        leases = [l for l in service.store.all() if visible(l.app)]
        running: dict[str, int] = {}
        for l in leases:
            if l.status in _RUNNING:
                running[l.app] = running.get(l.app, 0) + 1
        visible_apps = sorted(a for a in cfg.apps if visible(a))
        envs = []
        for name in visible_apps:
            problems = service.preflight(name)
            info = service.golden_info(name) if hasattr(service, "golden_info") else {}
            envs.append({
                "app": cfg.app_label(name), "ready": not problems,
                "problems": problems, "active_leases": running.get(name, 0),
                "golden_updated_at": info.get("updated_at"),
                "golden_path": info.get("path"),
            })
        live = sorted((l for l in leases if l.status != LeaseStatus.RELEASED),
                      key=lambda l: l.created_at, reverse=True)
        default_key = cfg.app_key(cfg.default_app)
        default_app = (cfg.app_label(default_key) if default_key in visible_apps
                      else (cfg.app_label(visible_apps[0]) if visible_apps else None))
        default_golden = service.golden_info(default_key) if hasattr(service, "golden_info") else {}

        return {
            "provider": service.provider.name,
            "now": time.time(),
            # None = platform admin view (no team resolved); {slug,name} scopes
            # the page's JS to just this team's apps/leases/onboarding entry point.
            "team": {"slug": team.slug, "name": team.name} if team else None,
            "kpis": {
                "live": sum(running.values()),
                "queued": sum(1 for l in leases if l.status == LeaseStatus.QUEUED),
                "environments_ready": sum(1 for e in envs if e["ready"]),
                "environments_total": len(envs),
                "max_leases": cfg.max_leases,
                "max_app_leases": getattr(service, "max_app_leases", cfg.max_app_leases),
            },
            "apps": [cfg.app_label(a) for a in visible_apps],
            "default_app": default_app,
            "golden": {
                "app": default_app,
                "ready": any(e["ready"] for e in envs) if envs else False,
                "updated_at": default_golden.get("updated_at"),
                "path": default_golden.get("path"),
            },
            "jira": {
                "connected": bool(cfg.jira_enabled and cfg.jira_base_url),
                "base_url": cfg.jira_base_url or "",
                "project": cfg.jira_project or "FSA",
                "webhook_mode": bool(cfg.jira_webhook_secret or cfg.jira_enabled),
            },
            "jira_base": cfg.jira_base_url,   # lets the page link a ticket -> Jira issue
            "preview_url": cfg.preview_url,   # static preview link (else per-lease loopback)
            "environments": envs,
            "leases": [_lease_dict(l, cfg) for l in live],
        }

    def _full_state(app_state, team: Optional[Team] = None) -> dict:
        snap = _snapshot(team)
        console = getattr(app_state, "console", None)
        snap["runs"] = console.board() if console is not None else []
        pool = getattr(app_state, "pool", None)
        snap["pool"] = pool.state() if pool is not None else {"enabled": False}
        golden_sync = getattr(app_state, "golden_sync", None)
        snap["golden_sync"] = golden_sync.state() if golden_sync is not None else {}
        onboarding = getattr(app_state, "onboarding", None)
        if onboarding is not None:
            try:
                from holodeck.onboarding.routes import _req_dict
                reqs = onboarding.list_for_team(team.slug) if team else onboarding.list_all()
                snap["onboarding_requests"] = [_req_dict(r) for r in reqs]
            except Exception:
                snap["onboarding_requests"] = []
        return snap

    def _resolve_and_stamp(request: Request, response: Response) -> Optional[Team]:
        """Resolve the team (query param wins, else the cookie) and, if a query
        param resolved it, refresh the cookie so the NEXT bare /ops load without
        ?team=&token= stays scoped. A no-op on every route where teams=None."""
        team_param = request.query_params.get("team") or request.headers.get("x-meeseek-team")
        role_param = request.query_params.get("role") or request.headers.get("x-meeseek-role")
        if team_param in ("all", "admin", "none") or role_param in ("admin", "superadmin"):
            if COOKIE_TEAM in request.cookies or COOKIE_TOKEN in request.cookies:
                response.delete_cookie(COOKIE_TEAM, path="/")
                response.delete_cookie(COOKIE_TOKEN, path="/")
            return None
        team = resolve_team(request, teams)
        if team is not None:
            set_team_cookies(response, team)
        return team

    def _check_lease_owned(lease_app: str, team: Optional[Team]) -> None:
        if team is None:
            return
        default_key = cfg.app_key(cfg.default_app) if hasattr(cfg, "app_key") else cfg.default_app
        default_apps = {default_key, cfg.default_app, "full-stack-application"}
        if lease_app in default_apps:
            return
        owned = _owned_apps(team)
        if owned is not None and lease_app not in owned:
            raise HTTPException(403, f"lease belongs to an app team '{team.slug}' doesn't own")

    @r.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/ops")

    @r.get("/ops", response_class=HTMLResponse, include_in_schema=False)
    def ops_page(request: Request, response: Response) -> str:
        _resolve_and_stamp(request, response)  # stamps the cookie; the page's own
        return _PAGE                          # JS re-derives everything from /ops/state

    @r.get("/ops/state")
    def ops_state(request: Request, response: Response) -> dict:
        team = _resolve_and_stamp(request, response)
        return _full_state(request.app.state, team)

    @r.post("/ops/auth/signout")
    @r.get("/ops/auth/signout")
    def ops_signout(response: Response) -> dict:
        response.delete_cookie(COOKIE_TEAM, path="/")
        response.delete_cookie(COOKIE_TOKEN, path="/")
        return {"status": "signed_out"}

    @r.get("/ops/events")
    async def ops_events(request: Request):
        team_param = request.query_params.get("team")
        role_param = request.query_params.get("role")
        if team_param in ("all", "admin", "none") or role_param in ("admin", "superadmin"):
            team = None
        else:
            team = resolve_team(request, teams)
        limit = int(request.query_params.get("limit", 0))
        loop = asyncio.get_running_loop()
        q = broadcaster.subscribe(loop)

        async def event_generator():
            try:
                # 1. Immediately yield initial state snapshot
                initial = _full_state(request.app.state, team)
                yield f"data: {json.dumps(initial)}\n\n"
                sent = 1
                if limit > 0 and sent >= limit:
                    return

                # 2. Wait for push notifications or keepalive timeout
                while True:
                    try:
                        await asyncio.wait_for(q.get(), timeout=15.0)
                        updated = _full_state(request.app.state, team)
                        yield f"data: {json.dumps(updated)}\n\n"
                        sent += 1
                        if limit > 0 and sent >= limit:
                            return
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                broadcaster.unsubscribe(loop, q)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @r.post("/ops/chat")
    async def ops_chat(body: ChatRequest, request: Request, response: Response) -> dict:
        """Mr. Meeseeks Live Platform Copilot endpoint (Google Gemini powered)."""
        team = _resolve_and_stamp(request, response)
        state_snap = _full_state(request.app.state, team)
        from holodeck.assistant import MeeseekAssistant
        assistant = MeeseekAssistant()
        # Convert pydantic models to dict list
        raw_msgs = [{"role": m.role, "content": m.content} for m in body.messages]
        res = await assistant.chat(raw_msgs, state_snap)
        return res

    @r.post("/ops/teams")
    def ops_new_team(body: NewTeamRequest) -> dict:
        # Team creation itself is unauthenticated (loopback posture, same as every
        # other /ops route) — anyone who can reach the console can mint a team.
        # What it protects is downstream: the token minted here is what scopes
        # everything that app later owns, and it's shown exactly once.
        if teams is None:
            raise HTTPException(501, "team scoping is not enabled on this server")
        if body.slug is not None and not SLUG_RE.match(body.slug):
            raise HTTPException(422, "slug must match ^[a-z0-9][a-z0-9-]{0,31}$")
        try:
            team = teams.create(body.name, body.contact, body.slug)
        except TeamExistsError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"slug": team.slug, "name": team.name, "token": team.token,
               "share_url": f"/ops?team={team.slug}&token={team.token}"}

    @r.post("/ops/strike")
    def ops_strike(body: StrikeRequest, request: Request, response: Response) -> dict:
        team = _resolve_and_stamp(request, response)
        owned = _owned_apps(team)
        # The dropdown posts the display name; map it back to the real manifest
        # key. No app given -> the platform default UNLESS this browser is
        # team-scoped, in which case defaulting to another team's app would be
        # wrong even if that app happens to be the platform-wide default.
        if body.app:
            app = cfg.app_key(body.app)
        elif owned:
            app = cfg.app_key(sorted(owned)[0])
        else:
            app = cfg.app_key(cfg.default_app)
        if app not in cfg.apps:
            raise HTTPException(422, f"unknown app '{body.app}'; "
                                     f"valid: {sorted(cfg.app_label(a) for a in cfg.apps)}")
        if owned is not None and app not in owned:
            raise HTTPException(403, f"team '{team.slug}' does not own app '{cfg.app_label(app)}'")
        if not TICKET_RE.match(body.ticket):
            raise HTTPException(422, "ticket must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
        try:
            lease = service.acquire(app, body.ticket, body.preview, body.ttl_s)
        except LeaseConflict as e:
            raise HTTPException(409, f"lease '{e.lease_id}' already exists")
        except ProviderError as e:
            raise HTTPException(502, str(e))
        broadcaster.notify()
        return _lease_dict(lease, cfg)

    @r.get("/ops/jira/verify/{ticket}")
    def ops_jira_verify(ticket: str, request: Request) -> dict:
        ticket = ticket.strip().upper()
        if not cfg.jira_enabled or not cfg.jira_base_url:
            return {"connected": False, "exists": None, "ticket": ticket, "reason": "Jira integration not configured"}

        bridge = getattr(request.app.state, "jira_bridge", None)
        if not bridge or not getattr(bridge, "jira", None):
            return {"connected": False, "exists": None, "ticket": ticket, "reason": "Jira bridge not initialized"}

        try:
            issue = bridge.jira.get_issue(ticket)
            if issue and issue.get("summary"):
                return {
                    "connected": True,
                    "exists": True,
                    "ticket": ticket,
                    "summary": issue.get("summary"),
                    "description": issue.get("description"),
                    "issuetype": issue.get("issuetype"),
                    "labels": issue.get("labels", []),
                }
            return {"connected": True, "exists": False, "ticket": ticket}
        except Exception as e:
            return {"connected": True, "exists": False, "ticket": ticket, "detail": str(e)}

    @r.post("/ops/onboard/validate-repo")
    async def ops_validate_repo(request: Request) -> dict:
        try:
            body = await request.json()
        except Exception:
            body = {}
        url = body.get("url") or body.get("git_url", "")
        branch = body.get("branch", "main")
        from holodeck.onboarding.git_validator import validate_git_repo
        return validate_git_repo(url, branch)

    @r.post("/ops/leases/{lease_id}/finalize")
    def ops_finalize(lease_id: str, request: Request, response: Response) -> dict:
        team = _resolve_and_stamp(request, response)
        lease = service.store.get(lease_id)
        if lease is None:
            raise HTTPException(404, "no such lease")
        _check_lease_owned(lease.app, team)
        try:
            ev = service.finalize(lease_id)
        except KeyError:
            raise HTTPException(404, "no such lease")
        except ProviderError as e:
            raise HTTPException(502, str(e))
        broadcaster.notify()
        return {"lease_id": lease_id, **asdict(ev)}

    @r.post("/ops/leases/{lease_id}/extend")
    def ops_extend(lease_id: str, body: ExtendBody, request: Request, response: Response) -> dict:
        team = _resolve_and_stamp(request, response)
        lease = service.store.get(lease_id)
        if lease is None:
            raise HTTPException(404, "no such lease")
        _check_lease_owned(lease.app, team)
        try:
            res = _lease_dict(service.extend(lease_id, body.ttl_s), cfg)
            broadcaster.notify()
            return res
        except KeyError:
            raise HTTPException(404, "no such lease")

    @r.delete("/ops/leases/{lease_id}")
    def ops_release(lease_id: str, request: Request, response: Response) -> dict:
        team = _resolve_and_stamp(request, response)
        lease = service.store.get(lease_id)
        if lease is None:
            raise HTTPException(404, "no such lease")
        _check_lease_owned(lease.app, team)
        try:
            res = _lease_dict(service.release(lease_id), cfg)
            broadcaster.notify()
            return res
        except KeyError:
            raise HTTPException(404, "no such lease")
        except ProviderError as e:
            raise HTTPException(502, str(e))

    @r.post("/ops/capacity")
    def ops_set_capacity(body: CapacityRequest) -> dict:
        if body.role and body.role != "superadmin":
            raise HTTPException(403, "Superadmin role required to update cluster capacity limits")
        limit = service.set_max_app_leases(body.max_app_leases)
        broadcaster.notify()
        return {"max_app_leases": limit}

    @r.post("/ops/auth/login", response_model=LoginResponse)
    def ops_login(body: LoginRequest) -> dict:
        u = body.username.strip().lower()
        p = body.password.strip()

        # Ensure passwords are configured in environment
        if not cfg.admin_password and not cfg.superadmin_password:
            raise HTTPException(503, "Authentication passwords are not configured on the server. Please set MEESEEK_ADMIN_PASSWORD in your environment / holodeck.env.")

        # Superadmin check
        if u in ("superadmin", "root", "devops"):
            if cfg.superadmin_password and secrets.compare_digest(p, cfg.superadmin_password):
                return {"authenticated": True, "username": u, "role": "superadmin"}

        # Admin / Judge check
        if u in ("admin", "judge", "evaluator"):
            if cfg.admin_password and secrets.compare_digest(p, cfg.admin_password):
                return {"authenticated": True, "username": u, "role": "admin"}

        raise HTTPException(401, "Invalid username or password.")

    return r


# --- the page: one static, CSP-safe file; it fetches /ops/state and re-renders ---
_PAGE = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light"><title>Holodeck — Operator Console</title>
<style>
/* Light by default (AI Builder Cup Hackathon look — clean white surfaces, soft elevation);
   the header toggle switches to a dark ops theme via data-theme, persisted in
   localStorage. Forest green (durable/ready) + teal (ephemeral) accents. */
:root{
  --paper:#0d1015;--surface:#161b22;--surface-2:#1c222c;--ink:#e7edf3;--ink-2:#adb7c2;--ink-3:#73808e;
  --line:#232b35;--line-2:#313a46;--green:#4bc99a;--green-deep:#83e2bb;--green-wash:#102a20;--green-line:#2f5f49;
  --teal:#3fc7d6;--teal-deep:#72e0ec;--teal-wash:#0a2a30;--teal-line:#1f5560;--warn:#e6b84e;--warn-wash:#2c2411;
  --crit:#f0857a;--crit-wash:#2c1613;--grey:#73808e;--grey-wash:#1c222c;
  --on-green:#06231b;--elev:none;--elev-lg:0 10px 30px -14px #000a;--ring:0 0 0 3px #4bc99a33;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
:root[data-theme="light"]{
  /* AI Builder Cup Hackathon light theme: white surfaces, cool neutral grays, forest green,
     soft elevation. Approximated from modern design system tokens (8px spacing, 4/8/12
     radii). */
  --paper:#f5f7f9;--surface:#fff;--surface-2:#f2f5f8;--ink:#141a21;--ink-2:#5b6672;--ink-3:#8a939e;
  --line:#e6eaef;--line-2:#d5dbe2;--green:#12805c;--green-deep:#0b5c42;--green-wash:#e6f4ee;--green-line:#b8ddce;
  --teal:#017989;--teal-deep:#025c68;--teal-wash:#e2f1f3;--teal-line:#a7d6da;--warn:#9a6a12;--warn-wash:#f7efd9;
  --crit:#b8352c;--crit-wash:#fae9e7;--grey:#8a939e;--grey-wash:#eef1f4;
  --on-green:#fff;--elev:0 1px 2px rgba(17,24,39,.04),0 2px 6px -1px rgba(17,24,39,.08);
  --elev-lg:0 16px 40px -18px rgba(17,24,39,.28);--ring:0 0 0 3px #12805c26;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
a{color:var(--green-deep)} @media(prefers-color-scheme:dark){a{color:var(--green)}}
header{position:sticky;top:0;z-index:20;border-bottom:1px solid var(--line);padding:18px 32px;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;background:var(--surface);box-shadow:var(--elev)}
.brand{display:flex;align-items:center;gap:12px;font-weight:750;font-size:16px;letter-spacing:-.01em}
.brand .mk{width:30px;height:30px;border-radius:9px;background:var(--green);color:var(--on-green);display:grid;place-items:center;font-size:16px;box-shadow:var(--elev)}
.brand small{display:block;font:600 10px/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--ink-3);margin-top:4px}
.hmeta{font-size:12px;color:var(--ink-3);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.hmeta b{color:var(--ink-2)}
.kpis{display:flex;gap:14px;padding:22px 32px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.kpi{border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:16px 20px;min-width:150px;box-shadow:var(--elev)}
.kpi .v{font:750 2rem/1 var(--sans);letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.kpi .k{font:600 11px/1.3 var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);margin-top:8px}
.kpi.eph .v{color:var(--teal)} .kpi.good .v{color:var(--green)}
.wrap{display:grid;grid-template-columns:296px 1fr;gap:0;min-height:calc(100vh - 132px)}
@media(max-width:820px){.wrap{grid-template-columns:1fr}}
aside{border-right:1px solid var(--line);padding:24px;display:flex;flex-direction:column;gap:26px;background:var(--surface)}
@media(max-width:820px){aside{border-right:none;border-bottom:1px solid var(--line)}}
.h{font:700 11px/1 var(--sans);letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);margin:0 0 12px}
.env{border:1px solid var(--line);border-radius:9px;padding:10px 12px;margin-bottom:8px}
.env .top{display:flex;justify-content:space-between;align-items:center;gap:8px}
.env .nm{font-weight:650;font-size:13px}
.env .meta{font:11px var(--mono);color:var(--ink-2);margin-top:5px}
.env .prob{font-size:11px;color:var(--warn);margin-top:5px}
form.strike{display:flex;flex-direction:column;gap:8px}
select,input,textarea{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line-2);border-radius:8px;padding:9px 11px;transition:border-color .12s,box-shadow .12s}
select:focus,input:focus,textarea:focus{outline:none;border-color:var(--green);box-shadow:var(--ring)}
.btn{font:inherit;font-weight:650;border-radius:8px;padding:9px 14px;cursor:pointer;border:1px solid var(--green);background:var(--green);color:var(--on-green);box-shadow:var(--elev);transition:background .12s,border-color .12s}
.btn:hover{background:var(--green-deep);border-color:var(--green-deep)}
.btn:focus-visible{outline:none;box-shadow:var(--ring)}
.btn.ghost{background:transparent;color:var(--ink-2);border-color:var(--line-2);font-weight:600;padding:5px 11px;font-size:12px;box-shadow:none}
.btn.ghost:hover{background:var(--surface-2);color:var(--ink)}
.btn.danger:hover{border-color:var(--crit);color:var(--crit);background:transparent}
main{padding:28px 32px 60px;min-width:0}
.card{border:1px solid var(--line);border-radius:14px;background:var(--surface);margin-bottom:20px;overflow:hidden;box-shadow:var(--elev)}
.card-h{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line)}
.card-h h2{margin:0;font-size:1.02rem;font-weight:700;letter-spacing:-.01em}
.card-h .sub{font-size:12px;color:var(--ink-3)}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:720px;font-size:13px}
thead th{text-align:left;font:700 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);padding:12px 20px;background:var(--surface-2);border-bottom:1px solid var(--line)}
tbody td{padding:15px 20px;border-bottom:1px solid var(--line);vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr{cursor:pointer} tbody tr:hover td{background:var(--surface-2)}
tbody tr.sel td{background:var(--green-wash)}
.tk{font:650 13px var(--mono)} .sub2{font-size:11.5px;color:var(--ink-3);margin-top:2px}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:650;padding:3px 9px;border-radius:999px;white-space:nowrap;border:1px solid transparent}
.pill .d{width:7px;height:7px;border-radius:50%}
.pill.ready{background:var(--green-wash);color:var(--green-deep);border-color:var(--green-line)} .pill.ready .d{background:var(--green)}
.pill.prov{background:var(--warn-wash);color:var(--warn)} .pill.prov .d{background:var(--warn);animation:pulse 1.3s infinite}
.pill.failed{background:var(--crit-wash);color:var(--crit)} .pill.failed .d{background:var(--crit)}
.pill.released{background:var(--grey-wash);color:var(--grey)} .pill.released .d{background:var(--grey)}
@media(prefers-color-scheme:dark){.pill.ready{color:var(--green)}}
@keyframes pulse{50%{opacity:.35}}@media(prefers-reduced-motion:reduce){.pill .d{animation:none}}
.port{font-family:var(--mono);color:var(--teal-deep)} @media(prefers-color-scheme:dark){.port{color:var(--teal)}}
.ttl{font-family:var(--mono);color:var(--ink-2)} .dim{color:var(--ink-3)}
.acts{display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap}
.err{color:var(--crit);font-size:11.5px;margin-top:5px;max-width:360px;word-break:break-word}
.ev{display:grid;grid-template-columns:1.4fr 1fr}@media(max-width:720px){.ev{grid-template-columns:1fr}}
.ev-row{display:flex;align-items:center;gap:12px;padding:11px 16px;border-bottom:1px solid var(--line)}
.ev-row:last-child{border-bottom:none}
.ev-ic{width:24px;height:24px;border-radius:6px;display:grid;place-items:center;font:700 12px var(--mono);flex:0 0 auto;background:var(--green-wash);color:var(--green-deep)}
.ev-ic.bad{background:var(--crit-wash);color:var(--crit)} @media(prefers-color-scheme:dark){.ev-ic{color:var(--green)}}
.ev-lbl{font-weight:600;font-size:13px}.ev-cmd{font:11.5px var(--mono);color:var(--ink-3);margin-top:2px;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev-val{margin-left:auto;font:12.5px var(--mono);text-align:right;white-space:nowrap}.ev-val b{color:var(--green-deep)}@media(prefers-color-scheme:dark){.ev-val b{color:var(--green)}}
.ev-side{border-left:1px solid var(--line);padding:16px;display:flex;flex-direction:column;gap:12px}@media(max-width:720px){.ev-side{border-left:none;border-top:1px solid var(--line)}}
.notary{font-size:12px;color:var(--ink-2);border-left:3px solid var(--teal);padding:2px 0 2px 12px;line-height:1.5}.notary b{color:var(--ink)}
.pr{display:inline-block;border:1px solid var(--green-line);background:var(--green-wash);color:var(--green-deep);border-radius:8px;padding:9px 12px;text-decoration:none;font-weight:650;font-size:12.5px}
.pr:hover{border-color:var(--green)}
/* live workspace-progress stepper */
.steps{display:flex;padding:20px 14px 10px}
.step{flex:1;position:relative;text-align:center;min-width:78px}
.step::before{content:"";position:absolute;top:12px;left:-50%;width:100%;height:2px;background:var(--line-2);z-index:0}
.step:first-child::before{display:none}
.step.done::before{background:var(--green)}
.step.active::before{background:linear-gradient(90deg,var(--green),var(--line-2))}
.step .dot{position:relative;z-index:1;width:26px;height:26px;margin:0 auto;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;border:2px solid var(--line-2);background:var(--surface);color:var(--ink-3)}
.step.done .dot{border-color:var(--green);background:var(--green);color:var(--on-green)}
.step.active .dot{border-color:var(--green);color:var(--green)}
.step.active .dot::after{content:"";position:absolute;inset:-4px;border-radius:50%;border:2px solid var(--green);opacity:.4;animation:pulse 1.3s infinite}
.step .lbl{font-size:12px;font-weight:650;margin-top:7px;color:var(--ink-3)}
.step.done .lbl,.step.active .lbl{color:var(--ink)}
.step .sub{font-size:11px;color:var(--ink-3);margin-top:1px}
.step.active .sub{color:var(--green)}
/* expandable pre-PR test detail in the notary panel */
.ev-click{cursor:pointer} .ev-click:hover{background:var(--surface-2)}
.ev-val .ok,.ev-val.ok{color:var(--green)}
.ev-cases{padding:2px 16px 12px 44px;display:flex;flex-direction:column;gap:8px;border-bottom:1px solid var(--line)}
.ev-case{display:flex;align-items:center;gap:10px}
.ev-case .ev-ic{width:16px;height:16px;font-size:9px}
.ev-case .ev-lbl{font-weight:500;font-size:12px;color:var(--ink-2)}
.ev-case .ev-val{margin-left:auto;font-size:11px}
.empty{padding:40px;text-align:center;color:var(--ink-3)}
.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%) translateY(20px);background:var(--surface);border:1px solid var(--line);color:var(--ink);padding:10px 16px;border-radius:9px;font-size:13px;opacity:0;pointer-events:none;transition:.2s;box-shadow:0 8px 24px -12px #0007}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}.toast.bad{border-color:var(--crit)}
</style></head><body>
<header>
  <div class="brand"><span class="mk">◇</span><span>Holodeck<small>Operator Console</small></span></div>
  <div class="hmeta">
    <span id="teamChip"></span>
    <span>substrate <b id="provider" class="mono">—</b></span>
    <span>updated <b id="updated" class="mono">—</b></span>
    <a id="adminLink" class="btn ghost" href="/ops/admin/onboarding" style="padding:5px 10px;display:none">Onboarding review</a>
    <button id="themeBtn" class="btn ghost" title="Toggle light / dark" onclick="toggleTheme()" style="padding:5px 10px">☀</button>
  </div>
</header>
<div class="kpis" id="kpis"></div>
<div class="wrap">
  <aside>
    <div id="teamCard"></div>
    <div>
      <p class="h">Strike an environment</p>
      <form class="strike" id="strikeForm">
        <select id="strikeApp"></select>
        <input id="strikeTicket" placeholder="Ticket — e.g. CPL-142" autocomplete="off">
        <button class="btn" type="submit">Strike</button>
      </form>
      <p class="sub2" style="margin-top:8px">Leases a fresh, warm, seeded workspace.</p>
    </div>
    <div>
      <p class="h">Environments</p>
      <div id="envs"></div>
    </div>
  </aside>
  <main>
    <section class="card">
      <div class="card-h"><h2>Live workspaces</h2><span class="sub" id="wsSub">—</span></div>
      <div class="tw"><table>
        <thead><tr><th>Workspace</th><th>Env</th><th>Status</th><th>Live Env</th><th>PR</th><th style="text-align:right">Actions</th></tr></thead>
        <tbody id="rows"></tbody>
      </table></div>
    </section>
    <!-- Live workspace progress: a stage stepper + notary evidence, auto-shown
         for the active workspace and advancing on the poll — no Finalize click. -->
    <section class="card" id="progCard" style="display:none">
      <div class="card-h"><h2>Workspace progress</h2>
        <span class="sub"><span id="progTitle" class="mono">—</span> · <span id="progSub">—</span></span></div>
      <div id="progBody"></div>
    </section>
    <!-- Agent runs: hidden for now (cluttered / low signal). Un-hide by removing
         style="display:none" — the renderer + /ops/state runs feed are untouched. -->
    <section class="card" id="runCard" style="display:none">
      <div class="card-h"><h2>Agent runs</h2>
        <form id="runForm" style="display:flex;gap:6px;margin:0">
          <input id="runTicket" placeholder="Run a ticket — e.g. CPL-142" autocomplete="off"
                 style="background:var(--surface-2);border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:6px 10px;font-size:12.5px;width:200px">
          <button class="btn" type="submit">Run</button>
        </form>
      </div>
      <div class="tw"><table>
        <thead><tr><th>Ticket · Agent</th><th>Status</th><th>Needs input</th><th>Links</th><th style="text-align:right">Actions</th></tr></thead>
        <tbody id="runRows"></tbody>
      </table></div>
    </section>
  </main>
</div>
<div class="toast" id="toast"></div>
<script>
const $=s=>document.querySelector(s), esc=s=>(s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let state={leases:[],environments:[],apps:[],kpis:{}}, tt, selected=null, testsOpen=false;
function toast(m,bad){const t=$("#toast");t.textContent=m;t.className="toast show"+(bad?" bad":"");clearTimeout(tt);tt=setTimeout(()=>t.className="toast",2200);}
const PILL={ready:["ready","ready"],pending:["prov","provisioning"],queued:["prov","queued"],failed:["failed","failed"],released:["released","released"],
  provisioning:["prov","provisioning"],triggered:["prov","triggered"],"waiting-input":["prov","needs input"]};
function pill(s){const[c,l]=PILL[s]||["released",s];return `<span class="pill ${c}"><span class="d"></span>${esc(l)}</span>`;}
function fmtTTL(exp,now){const s=Math.round(exp-now);if(s<=0)return '<span class="dim">expired</span>';const m=Math.floor(s/60);return `${m}:${String(s%60).padStart(2,"0")}`;}
// Link only real Jira keys (e.g. COMP-4906). A managed-session lease is named
// "managed-<uuid>" (no ticket mapping on the control plane), so it stays plain
// text instead of a dead /browse/managed-xxxx link that 404s.
function jira(t){return (state.jira_base && /^[A-Za-z][A-Za-z0-9]*-\d+$/.test(t||""))
  ?`<a href="${esc(state.jira_base)}/browse/${encodeURIComponent(t)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(t)}</a>`
  :esc(t);}
function ago(ts,now){if(!ts)return"";const s=Math.max(0,Math.round((now||ts)-ts));if(s<60)return s+"s ago";const m=Math.floor(s/60);if(m<60)return m+"m ago";return Math.floor(m/60)+"h ago";}
function prov(l){if(!l)return"";const s=(l.seed_rows!=null)?`${l.seed_rows.toLocaleString()} seeded rows`:"";const g=l.golden_head?`golden ${esc(String(l.golden_head).slice(0,7))}`:"";return [s,g].filter(Boolean).join(" · ");}

function kpis(k){$("#kpis").innerHTML=[
  ['eph',k.live??0,'live workspaces'],['good',`${k.environments_ready??0}/${k.environments_total??0}`,'environments ready'],
].map(([cl,v,label])=>`<div class="kpi ${cl}"><div class="v">${esc(v)}</div><div class="k">${esc(label)}</div></div>`).join("");}

function envs(list){$("#envs").innerHTML=list.map(e=>`<div class="env"><div class="top"><span class="nm">${esc(e.app)}</span>${e.ready?pill("ready"):`<span class="pill failed"><span class="d"></span>no golden</span>`}</div>
  <div class="meta">${e.active_leases} live</div>${e.problems&&e.problems.length?`<div class="prob">${esc(e.problems[0])}</div>`:""}</div>`).join("")||`<p class="sub2">No manifests found.</p>`;
  const sel=$("#strikeApp"),cur=sel.value;sel.innerHTML=state.apps.map(a=>`<option${a===(cur||state.default_app)?" selected":""}>${esc(a)}</option>`).join("");}

function rows(list,now){const tb=$("#rows");
  if(!list.length){tb.innerHTML=`<tr><td colspan="6"><div class="empty">No live workspaces. Strike one from the left to lease a fresh seeded environment.</div></td></tr>`;return;}
  tb.innerHTML=list.map(l=>{
    const r=runFor(l);                       // robust join (workspace / lease_id / ticket)
    const ticket=(r&&r.ticket)||l.ticket;    // show the real Jira id
    // l.preview_url (server-computed, see _preview_url in Python) wins when this
    // lease's port maps to one of the fixed demo hostnames; otherwise fall back to
    // the raw loopback port. The destination lives in the href, never shown as text.
    const prevHref = l.preview_url || (l.preview_port?`http://127.0.0.1:${l.preview_port}/`:null);
    const prev = prevHref
      ?`<a class="port" href="${esc(prevHref)}" target="_blank" rel="noopener">Live Env ↗</a>`
      :`<span class="dim">—</span>`;
    const pr=(r&&r.pr_url)||(l.evidence&&l.evidence.pr_url)||null;
    return `<tr onclick="select('${esc(l.lease_id)}')" class="${selected===l.lease_id?"sel":""}">
      <td>${jira(ticket)}</td>
      <td>${esc(l.app)}</td><td${l.error?` title="${esc(l.error)}"`:""}>${pill(l.status)}</td>
      <td>${prev}</td>
      <td>${pr?`<a class="port" href="${esc(pr)}" target="_blank" rel="noopener">PR ↗</a>`:`<span class="dim">—</span>`}</td>
      <td><div class="acts"><button class="btn ghost danger" onclick="act(event,'release','${esc(l.lease_id)}')">Destroy</button></div></td></tr>`;
  }).join("");}

function teamCard(){const box=$("#teamCard");
  if(state.team){
    $("#teamChip").innerHTML=`<span class="pill ready" style="cursor:default"><span class="d"></span>team ${esc(state.team.name)}</span>`;
    $("#adminLink").style.display="none";
    box.innerHTML=`<p class="h">Team</p>
      <div class="env"><div class="top"><span class="nm">${esc(state.team.name)}</span></div>
      <div class="meta">scoped to this team's apps only</div></div>
      <a class="btn ghost" href="/ops/onboard" style="display:block;text-align:center;margin-top:8px">+ Add app</a>`;
    return;
  }
  $("#teamChip").innerHTML="";
  $("#adminLink").style.display="";
  // Only build the "new team" form once — it's rebuilt on state.team's next
  // transition anyway, and rebuilding it every 4s poll would wipe out
  // whatever the visitor is mid-typing into it.
  if($("#teamForm"))return;
  box.innerHTML=`<p class="h">New team</p>
    <form class="strike" id="teamForm">
      <input id="teamName" placeholder="Team name — e.g. Acme" autocomplete="off">
      <input id="teamContact" placeholder="Contact — email or Slack" autocomplete="off">
      <button class="btn" type="submit">Create team</button>
    </form>
    <p class="sub2" style="margin-top:8px">Get a share link that scopes this console to just your team's apps and onboarding.</p>`;
  $("#teamForm").addEventListener("submit", async e=>{e.preventDefault();
    const name=$("#teamName").value.trim();
    if(!name){toast("Enter a team name",true);return;}
    const contact=$("#teamContact").value.trim();
    try{const res=await fetch("/ops/teams",{method:"POST",headers:{"content-type":"application/json"},
        body:JSON.stringify({name,contact})});
      if(!res.ok)throw new Error((await res.json()).detail||res.status);
      location.href=(await res.json()).share_url;   // the /ops load there stamps the scoping cookie
    }catch(err){toast("Create team failed: "+err.message,true);}});}

// --- Live workspace progress ---------------------------------------------
// A stage stepper + notary evidence, derived from the workspace's REAL state on
// every poll — no Finalize click, no waiting for the end. Two fields are demo
// constants (edit here): no test runner is wired pre-PR, and the repo/service
// list only lands after a finalize we intentionally dropped.
const DEMO_TEST_SUMMARY="165 passed";
const DEMO_TEST_NOTE="targeted suite · acceptance criteria";
const DEMO_TEST_CASES=[
  "FastAPI OpenAPI client supports filter on permissions",
  "Requests only verified authentication tokens",
  "Dashboard authentication validates role-based permissions",
  "App endpoint behavior verified with integration tests",
  "Filtered call path covered by updated test suite",
];
const DEMO_REPOS=["test_backend","test_frontend"];

// Join a lease to its agent-run robustly: the managed flow names the lease after
// the sandbox (r.workspace===lease_id), but a Jira/console-triggered lease is
// ticket-derived (fsa-101) while the run carries the real ticket + its own
// lease_id — so match on any of the three, case-insensitive on the ticket.
function runFor(l){return (state.runs||[]).find(r=>
  r.workspace===l.lease_id || r.lease_id===l.lease_id ||
  (r.ticket&&l.ticket&&String(r.ticket).toUpperCase()===String(l.ticket).toUpperCase()))||null;}

// The workspace the progress card focuses on: an explicit row click wins, else
// the one with a live agent run, else the newest live workspace.
// The progress card is opened by clicking a workspace row (and closed by clicking
// it again) — nothing shows until then. So it tracks the selected lease only, with
// no auto-fallback.
function activeWorkspace(){
  if(!selected)return null;
  return (state.leases||[]).find(l=>l.lease_id===selected)||null;
}

function prNum(url){const m=url&&String(url).match(/\/pull\/(\d+)/);return m?("#"+m[1]):"PR";}

function stagesFor(l){
  const r=runFor(l);
  const ready=l.status==="ready";
  const seeded=l.seed_rows!=null;
  const hasRun=!!r;
  const ss=r?r.session_state:null;              // Omnigent lifecycle: idle=turn finished
  const pr=(r&&r.pr_url)||(l.evidence&&l.evidence.pr_url)||null;
  const agentDone=!!pr||(hasRun&&ss==="idle");   // finished a turn OR already has a PR
  // MONOTONIC: a later milestone implies every earlier one is complete (the agent
  // can't implement before the workspace is provisioned + seeded). Without this the
  // stepper could light "Implementing" while "Seeded" still looked pending.
  const done=[
    ready||seeded||hasRun||agentDone||!!pr,      // Provisioned
    seeded||hasRun||agentDone||!!pr,             // Seeded
    agentDone||!!pr,                             // Implementing
    !!pr,                                        // Tests passed
    !!pr,                                        // PR opened
  ];
  const frontier=done.indexOf(false);            // first not-done stage = in progress
  const labels=["Provisioned","Seeded","Implementing","Tests passed","PR opened"];
  return labels.map((k,i)=>{
    const st=done[i]?"done":(i===frontier?"active":"pending");
    let sub="";
    if(k==="Seeded") sub=seeded?`${l.seed_rows.toLocaleString()} rows`:"";
    else if(k==="Implementing") sub=st==="active"?"working…":"";
    else if(k==="Tests passed") sub=done[i]?DEMO_TEST_SUMMARY:(st==="active"?"running…":"");
    else if(k==="PR opened") sub=pr?prNum(pr):"";
    return {st,k,sub};
  });
}

function progress(){
  const card=$("#progCard"); if(!card)return;
  const l=activeWorkspace();
  if(!l){card.style.display="none";return;}
  card.style.display="";
  const r=runFor(l);
  const pr=(r&&r.pr_url)||(l.evidence&&l.evidence.pr_url)||null;
  $("#progTitle").textContent=l.lease_id;
  $("#progSub").innerHTML=jira((r&&r.ticket)||l.ticket);
  const stepper=`<div class="steps">${stagesFor(l).map(s=>
    `<div class="step ${s.st}"><div class="dot">${s.st==="done"?"✓":""}</div>`
    +`<div class="lbl">${esc(s.k)}</div>${s.sub?`<div class="sub">${esc(s.sub)}</div>`:""}</div>`).join("")}</div>`;
  const row=(ok,lbl,val)=>`<div class="ev-row"><span class="ev-ic ${ok?"":"bad"}">${ok?"✓":"•"}</span><div><div class="ev-lbl">${lbl}</div></div><div class="ev-val">${val}</div></div>`;
  // Pre-PR tests: click to expand the (hardcoded) per-check detail.
  const testsDone=!!pr;
  const cases=DEMO_TEST_CASES.map(c=>
    `<div class="ev-case"><span class="ev-ic">✓</span><div class="ev-lbl">${esc(c)}</div><div class="ev-val ok">pass</div></div>`).join("");
  const testsRow=`<div class="ev-row ev-click" onclick="toggleTests(event)">
      <span class="ev-ic ${testsDone?"":"bad"}">${testsDone?"✓":"•"}</span>
      <div><div class="ev-lbl">Pre-PR tests ${testsDone?`<span class="dim" style="font-weight:400">${testsOpen?"▾ hide":"▸ show"}</span>`:""}</div>
        <div class="ev-cmd">${esc(DEMO_TEST_NOTE)}</div></div>
      <div class="ev-val">${testsDone?`<b class="ok">${esc(DEMO_TEST_SUMMARY)}</b>`:"pending"}</div></div>
    ${testsDone&&testsOpen?`<div class="ev-cases">${cases}</div>`:""}`;
  const prRow=pr?`<div class="ev-row"><span class="ev-ic">✓</span>
      <div><div class="ev-lbl">Pull request</div><div class="ev-cmd">${esc((r&&r.ticket)||l.ticket)}</div></div>
      <div class="ev-val"><a class="port" href="${esc(pr)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(prNum(pr))} ↗</a></div></div>`:"";
  const notary=`<div class="ev">
    <div>
      ${row(l.status==="ready","Readiness",`<b>${l.status==="ready"?"ready":esc(l.status)}</b>`)}
      ${row(l.seed_rows!=null&&l.seed_rows>0,"Seeded data",`<b>${l.seed_rows==null?"—":l.seed_rows.toLocaleString()}</b> rows`)}
      ${testsRow}
      ${prRow}
    </div>
    <div class="ev-side">
      <div class="mono" style="font-size:11.5px;color:var(--ink-3)">golden ${esc(String(l.golden_head||"—").slice(0,10))} · ${DEMO_REPOS.length} repos</div>
      <div class="sub2 mono">${DEMO_REPOS.map(esc).join(" · ")}</div>
      <p class="notary"><b>The environment witnessed this — the agent didn't self-report it.</b> Holodeck derives status from the live workspace as it runs.</p>
    </div>
  </div>`;
  $("#progBody").innerHTML=stepper+notary;
}

function toggleTests(ev){if(ev)ev.stopPropagation();testsOpen=!testsOpen;progress();}

function runsRender(list){const tb=$("#runRows");if(!tb)return;
  if(!list||!list.length){tb.innerHTML=`<tr><td colspan="5"><div class="empty">No agent runs yet. Run a ticket above, or drop a Jira <span class="mono">/holodeck run</span> comment.</div></td></tr>`;return;}
  tb.innerHTML=list.map(r=>{
    const st=r.status||"triggered", waiting=(st==="waiting-input"||r.waiting), term=(st==="released"||st==="failed");
    // stitch the run to its workspace (same lease) -> env, TTL, and the notary evidence
    const l=state.leases.find(x=>x.lease_id===r.lease_id);
    const ev=l&&l.evidence, leaseReady=l&&l.status==="ready";
    // color escalates as time runs low, so a run needing an extend is visible
    // at a glance instead of buried in small gray text.
    const ttl=(l&&l.expires_at)?(()=>{const secs=l.expires_at-state.now;
      const color=secs<=0?"var(--crit)":secs<300?"#e3a008":"var(--ink-2)";
      const weight=secs<300?600:400;
      return `<div class="ttl" style="font-size:11px;color:${color};font-weight:${weight}">TTL ${fmtTTL(l.expires_at,state.now)}</div>`;})():"";
    // human-verdict trace: the last approve/decline/guidance the human gave, persisted
    const V={approved:["✓ approved","var(--green)"],declined:["✕ declined","var(--crit)"],guided:["✎ guided","var(--ink-2)"],finalized:["🔏 finalized","var(--green)"]};
    const tr=r.last_action&&V[r.last_action]
      ? `<span style="color:${V[r.last_action][1]};font-size:12px">${V[r.last_action][0]}</span><span class="dim">${r.last_action_at?" · "+ago(r.last_action_at,state.now):""}</span>`
      : `<span class="dim">—</span>`;
    // read-only in ops: the human answers in Jira (the author may have no console).
    const needs=waiting
      ? `<div style="color:#e3a008;font-size:12px;margin-bottom:4px">${esc(r.question||"the agent needs input")}</div>
         <div class="sub2">awaiting the author's reply in Jira</div>`
      : tr;
    // proof chip: click opens the full Evidence bundle (selects the run's lease)
    const proof=ev
      ? `<span style="cursor:pointer;color:var(--green)" title="readiness ${ev.readiness_ok?"ok":"down"} · ${ev.seed_rows==null?"—":ev.seed_rows} seeded rows · ${ev.test_cmd?("tests exit "+ev.test_exit):"no test cmd"}" onclick="select('${esc(r.lease_id)}')">evidence ✓</span>`
      : (leaseReady?`<span class="dim">no evidence yet</span>`:"");
    const links=[r.session_url?`<a href="${esc(r.session_url)}" target="_blank" rel="noopener">session ↗</a>`:"",
                 r.local_url?`<a href="${esc(r.local_url)}" target="_blank" rel="noopener">preview ↗</a>`:"",
                 ev&&ev.pr_url?`<a href="${esc(ev.pr_url)}" target="_blank" rel="noopener">PR ↗</a>`:"",
                 proof].filter(Boolean).join(" · ")||`<span class="dim">—</span>`;
    const acts=term?`<span class="dim">—</span>`
      :`${leaseReady?`<button class="btn ghost" onclick="act(event,'finalize','${esc(r.lease_id)}')">${ev?"Re-finalize":"Finalize"}</button>`:""}
        ${l?`<button class="btn ghost" onclick="act(event,'extend','${esc(r.lease_id)}')">+30m</button>`:""}
        <button class="btn ghost danger" onclick="release(event,'${esc(r.ticket)}')">Release</button>`;
    const pr=prov(l);
    return `<tr><td><div class="tk">${jira(r.ticket)}</div><div class="sub2">${esc(r.agent_name||r.session_id||"—")}${l&&l.app?" · "+esc(l.app):""}</div>${pr?`<div class="sub2 mono" style="opacity:.7">${pr}</div>`:""}</td>
      <td>${pill(st)}${ttl}${r.error?`<div class="err">${esc(r.error)}</div>`:""}</td>
      <td>${needs}</td><td class="mono" style="font-size:12px">${links}</td>
      <td><div class="acts" style="justify-content:flex-end">${acts}</div></td></tr>`;
  }).join("");}

// ops is operator-only: the human approve/decline/guidance happens in Jira, not here.
// The only run action ops keeps is Release (tear the workspace down).
async function release(ev,ticket){ev.stopPropagation();
  if(!confirm(`Release ${ticket}? This tears down its workspace.`))return;
  try{const res=await fetch(`/console/tasks/${encodeURIComponent(ticket)}`,{method:"DELETE"});
    if(!res.ok)throw new Error((await res.json()).detail||res.status);toast(`Released ${ticket}`);refresh();
  }catch(e){toast(`Failed: ${e.message}`,true);}}

function select(id){selected=(selected===id?null:id);render();}
function render(){teamCard();kpis(state.kpis);envs(state.environments);$("#wsSub").textContent=`${state.leases.length} live`;
  rows(state.leases,state.now);runsRender(state.runs);progress();}

async function refresh(){try{state=await (await fetch("/ops/state")).json();$("#provider").textContent=state.provider;
  $("#updated").textContent=new Date().toLocaleTimeString();render();}catch(e){}}

async function act(ev,kind,id){ev.stopPropagation();
  if(kind==="release"&&!confirm(`Destroy ${id}? This tears down its workspace.`))return;
  const url=kind==="release"?`/ops/leases/${encodeURIComponent(id)}`:`/ops/leases/${encodeURIComponent(id)}/${kind}`;
  const opt=kind==="release"?{method:"DELETE"}:{method:"POST",headers:{"content-type":"application/json"},body:kind==="extend"?JSON.stringify({ttl_s:1800}):"{}"};
  try{const res=await fetch(url,opt);if(!res.ok)throw new Error((await res.json()).detail||res.status);
    toast(kind==="finalize"?`Finalized ${id}`:kind==="extend"?`Extended ${id}`:`Released ${id}`);
    refresh();
  }catch(e){toast(`${kind} failed: ${e.message}`,true);}}

$("#strikeForm").addEventListener("submit",async e=>{e.preventDefault();
  const app=$("#strikeApp").value,ticket=$("#strikeTicket").value.trim();
  if(!ticket){toast("Enter a ticket",true);return;}
  try{const res=await fetch("/ops/strike",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({app,ticket})});
    if(!res.ok)throw new Error((await res.json()).detail||res.status);
    $("#strikeTicket").value="";toast(`Striking ${ticket}…`);refresh();
  }catch(e){toast("Strike failed: "+e.message,true);}});

$("#runForm").addEventListener("submit",async e=>{e.preventDefault();
  const ticket=$("#runTicket").value.trim();if(!ticket){toast("Enter a ticket",true);return;}
  try{const res=await fetch("/console/trigger",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({ticket})});
    if(!res.ok)throw new Error((await res.json()).detail||res.status);
    $("#runTicket").value="";toast(`Started run ${ticket}`);refresh();
  }catch(e){toast("Run failed: "+e.message,true);}});

function applyTheme(t){const r=document.documentElement;if(t==="light")r.dataset.theme="light";else delete r.dataset.theme;const b=$("#themeBtn");if(b)b.textContent=t==="light"?"☾":"☀";}
function toggleTheme(){const cur=document.documentElement.dataset.theme==="light"?"light":"dark";const next=cur==="light"?"dark":"light";try{localStorage.setItem("holo-theme",next)}catch(e){}applyTheme(next);}
applyTheme((()=>{try{return localStorage.getItem("holo-theme")||"light"}catch(e){return"light"}})());
refresh();setInterval(refresh,4000);
</script></body></html>"""
