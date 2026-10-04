"""Console HTTP surface + the mount hook that co-locates it in the control-plane
app. Routes are provider-agnostic — they only talk to ConsoleManager."""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from holodeck.console.bridge import JiraBridge
from holodeck.console.driver import DirectDriver, OmnigentDriver
from holodeck.console.jira import build_jira_client, parse_webhook
from holodeck.console.leaseclient import HttpLeaseClient
from holodeck.console.manager import ConsoleError, ConsoleManager
from holodeck.console.poller import ConsolePoller
from holodeck.console.store import ConsoleStore

log = logging.getLogger("holodeck.console.routes")


class TriggerRequest(BaseModel):
    ticket: str
    app: Optional[str] = None


class ReiterateRequest(BaseModel):
    feedback: str


class AnswerRequest(BaseModel):
    text: str  # a human reply: "yes"/"no" resolves an approval, else it's guidance



def build_router(manager: ConsoleManager) -> APIRouter:
    r = APIRouter()

    @r.post("/console/trigger", tags=["console"])
    def trigger(body: TriggerRequest):
        try:
            return manager.trigger(body.ticket, body.app).as_dict()
        except ConsoleError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))

    @r.get("/console/board", tags=["console"])
    def board():
        return manager.board()

    @r.post("/console/tasks/{ticket}/reiterate", tags=["console"])
    def reiterate(ticket: str, body: ReiterateRequest):
        try:
            return manager.reiterate(ticket, body.feedback).as_dict()
        except ConsoleError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))

    @r.post("/console/tasks/{ticket}/answer", tags=["console"])
    def answer(ticket: str, body: AnswerRequest):
        """Human reply: a yes/no on a pending elicitation resolves it as an
        approval; anything else is sent as open-ended guidance (a message)."""
        try:
            rec, verdict = manager.answer(ticket, body.text)
        except ConsoleError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))
        d = rec.as_dict()
        d["verdict"] = verdict  # True=approved, False=declined, None=message
        return d

    @r.delete("/console/tasks/{ticket}", tags=["console"])
    def release(ticket: str):
        try:
            rec = manager.release(ticket)
            manager.store.delete(ticket)
            return rec.as_dict()
        except ConsoleError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))

    @r.post("/console/tasks/{ticket}/finalize", tags=["console"])
    def finalize(ticket: str):
        """The notary, human-triggered: re-derives evidence host-side (never
        trusting the agent's own report) and opens the PR itself if enabled."""
        try:
            rec, evidence = manager.finalize(ticket)
        except ConsoleError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))
        d = rec.as_dict()
        d["evidence"] = evidence
        return d

    # The standalone runs-board page is retired — the runs view is folded into
    # the single operator console at /ops. Old links (incl. the /ops "Runs board"
    # link and bookmarks) redirect there. The /console/* JSON + action endpoints
    # above stay: they're what /ops (and the Jira bridge) drive.
    @r.get("/console", include_in_schema=False)
    def console_redirect():
        return RedirectResponse(url="/ops")

    return r


def build_jira_router(bridge: JiraBridge, cfg) -> APIRouter:
    r = APIRouter()

    # SYNC route on purpose: it runs in a threadpool, so the blocking loopback
    # call to /leases inside bridge.handle() can't deadlock the event loop (an
    # async handler doing sync loopback HTTP would).
    @r.post("/jira/webhook", tags=["jira"])
    def jira_webhook(request: Request, payload: dict = Body(...)):
        # shared-secret gate (Jira sends it as ?secret= or the header). Empty
        # secret disables the check (local only).
        if cfg.jira_webhook_secret:
            provided = (request.query_params.get("secret")
                        or request.headers.get("x-holodeck-jira-secret", ""))
            if provided != cfg.jira_webhook_secret:
                raise HTTPException(status_code=401, detail="bad webhook secret")
        res = bridge.handle(parse_webhook(payload))
        broadcaster = getattr(request.app.state, "broadcaster", None)
        if broadcaster is not None:
            broadcaster.notify()
        return {"result": res}

    return r


def _build_driver(cfg, lease_client, omni_client):
    if cfg.console_driver == "omnigent":
        from holodeck.console.omnigent import FakeOmnigentClient, HttpOmnigentClient
        if omni_client is None:
            omni_client = (FakeOmnigentClient(lease_client) if cfg.omnigent_fake
                           else HttpOmnigentClient(cfg))
        return OmnigentDriver(lease_client, omni_client, cfg)
    return DirectDriver(lease_client)


def mount_console(app, cfg, *, db=None, lease_client=None, omni_client=None, jira_client=None,
                  start_poller: bool = True) -> ConsoleManager:
    """Add the console (+ optional Jira bridge) onto an existing app. Console tasks
    persist in `db` (the shared service DB). The console reaches the lease API via
    `lease_client` (default: loopback HTTP), Omnigent via `omni_client`, and Jira
    via `jira_client` — all seams, so it splits out later with only config changes."""
    client = lease_client or HttpLeaseClient(cfg.console_lease_url, cfg.token)
    driver = _build_driver(cfg, client, omni_client)
    manager = ConsoleManager(cfg, ConsoleStore(db), client, driver)
    app.include_router(build_router(manager))
    app.state.console = manager

    broadcaster = getattr(app.state, "broadcaster", None)
    if broadcaster is not None:
        manager.add_change_listener(broadcaster.notify)

    bridge = None
    if cfg.jira_enabled:
        jc = jira_client or build_jira_client(cfg)
        bridge = JiraBridge(manager, jc, cfg)
        app.include_router(build_jira_router(bridge, cfg))
        app.state.jira_bridge = bridge

    if start_poller:
        # board refresh (+ outbound "needs input" comments) on the fast cadence
        poller = ConsolePoller(manager, cfg.console_poll_interval_s,
                               on_tick=(bridge.sync if bridge else None))
        poller.start()
        app.state.console_poller = poller
        # inbound Jira polling (no webhook): trigger labelled tickets + pull replies,
        # on its own slower cadence. Only when a project to scan is configured AND interval > 0.
        if bridge is not None and cfg.jira_project and cfg.jira_poll_interval_s > 0:
            jira_poller = ConsolePoller(manager, cfg.jira_poll_interval_s,
                                        on_tick=bridge.poll_inbound, refresh=False,
                                        name="holo-jira-inbound")
            jira_poller.start()
            app.state.jira_inbound_poller = jira_poller
        else:
            log.info("Jira inbound background polling disabled (operating in webhook mode).")
    return manager
