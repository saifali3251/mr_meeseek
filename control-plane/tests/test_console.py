"""Console tests — trigger / correlate / refresh / release + routes, all against
the FakeLeaseClient (no server, no Docker)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holodeck.config import Config
from holodeck.console.driver import DirectDriver
from holodeck.console.leaseclient import FakeLeaseClient
from holodeck.console.manager import ConsoleConflict, ConsoleManager, ConsoleNotFound
from holodeck.console.routes import mount_console
from holodeck.console.store import ConsoleStore


def _manager():
    cfg = Config(provider="fake", console_driver="direct")
    client = FakeLeaseClient()
    return ConsoleManager(cfg, ConsoleStore(), client, DirectDriver(client)), client


def test_trigger_creates_task_and_correlates():
    mgr, _ = _manager()
    rec = mgr.trigger("CPL-1", "compliance")
    assert rec.lease_id == "cpl-1"          # lease id derived from ticket
    assert rec.status == "ready"
    assert rec.preview_url and rec.preview_url.startswith("http://127.0.0.1:")
    board = mgr.board()
    assert len(board) == 1 and board[0]["ticket"] == "CPL-1"


def test_duplicate_trigger_conflicts():
    mgr, _ = _manager()
    mgr.trigger("CPL-1", "compliance")
    with pytest.raises(ConsoleConflict):
        mgr.trigger("CPL-1", "compliance")


def test_refresh_reflects_release():
    mgr, _ = _manager()
    mgr.trigger("CPL-1", "compliance")
    mgr.release("CPL-1")
    mgr.refresh()
    assert mgr.board()[0]["status"] == "released"


def test_released_ticket_is_retriggerable():
    mgr, _ = _manager()
    mgr.trigger("CPL-1", "compliance")
    mgr.release("CPL-1")
    # terminal -> can trigger the same ticket again
    rec = mgr.trigger("CPL-1", "compliance")
    assert rec.status == "ready"


def test_finalize_calls_lease_client_and_stores_verified_pr():
    mgr, client = _manager()
    rec = mgr.trigger("CPL-1", "compliance")
    rec.pr_url = "https://github.com/org/repo/pull/1"  # a stale, self-reported one
    mgr.store.put(rec)
    client.force_evidence[rec.lease_id] = {
        "lease_id": rec.lease_id, "readiness": "ok", "readiness_ok": True, "seed_rows": 2,
        "test_cmd": "pytest", "test_exit": 0, "test_output": "", "test_timed_out": False,
        "diff": "+1", "golden_head": "abc", "schema_rev": "h1", "services_booted": [],
        "services_absent": [], "finalized_at": 1.0, "pr_url": "https://github.com/org/repo/pull/2",
    }
    updated, evidence = mgr.finalize("CPL-1")
    assert evidence["test_exit"] == 0
    # the notary's PR replaces the agent-scraped one, never the other way around
    assert updated.pr_url == "https://github.com/org/repo/pull/2"
    assert updated.last_action == "finalized"
    assert mgr.store.get("CPL-1").pr_url == "https://github.com/org/repo/pull/2"


def test_finalize_unknown_ticket_raises():
    mgr, _ = _manager()
    with pytest.raises(ConsoleNotFound):
        mgr.finalize("CPL-404")


def test_finalize_route():
    cfg = Config(provider="fake", console_driver="direct")
    client = FakeLeaseClient()
    app = FastAPI()
    mgr = mount_console(app, cfg, lease_client=client, start_poller=False)
    mgr.trigger("CPL-1", "compliance")
    c = TestClient(app)
    r = c.post("/console/tasks/CPL-1/finalize")
    assert r.status_code == 200, r.text
    assert r.json()["evidence"]["readiness_ok"] is True


def test_routes_via_app():
    cfg = Config(provider="fake", console_driver="direct")
    client = FakeLeaseClient()
    app = FastAPI()
    mount_console(app, cfg, lease_client=client, start_poller=False)
    c = TestClient(app)

    r = c.post("/console/trigger", json={"ticket": "CPL-9", "app": "compliance"})
    assert r.status_code == 200, r.text
    assert r.json()["lease_id"] == "cpl-9"

    board = c.get("/console/board").json()
    assert any(x["ticket"] == "CPL-9" for x in board)

    rc = c.get("/console", follow_redirects=False)       # runs board folded into /ops
    assert rc.status_code in (307, 308) and rc.headers["location"] == "/ops"
    assert c.delete("/console/tasks/CPL-9").json()["status"] == "released"


def test_trigger_resolves_app_alias_to_real_manifest_key():
    # regression: the Jira/console-trigger path must reverse-map a display alias
    # ("compliance") to the real manifest key ("compliance-ui") before the lease
    # API's allowlist sees it — otherwise the alias is rejected 422.
    cfg = Config(provider="fake", console_driver="direct")
    cfg.apps_allow = {"compliance-ui"}
    cfg.app_aliases = {"compliance-ui": "compliance"}
    cfg.default_app = "compliance"                  # operator sets the alias, naturally
    client = FakeLeaseClient()
    mgr = ConsoleManager(cfg, ConsoleStore(), client, DirectDriver(client))
    rec = mgr.trigger("CPL-1")                       # no app -> falls back to default_app
    assert rec.app == "compliance-ui"               # resolved to the real key, not the alias


def test_trigger_missing_lease_conflict_maps_to_http():
    cfg = Config(provider="fake", console_driver="direct")
    client = FakeLeaseClient()
    app = FastAPI()
    mount_console(app, cfg, lease_client=client, start_poller=False)
    c = TestClient(app)
    c.post("/console/trigger", json={"ticket": "CPL-1", "app": "compliance"})
    r = c.post("/console/trigger", json={"ticket": "CPL-1", "app": "compliance"})
    assert r.status_code == 409
