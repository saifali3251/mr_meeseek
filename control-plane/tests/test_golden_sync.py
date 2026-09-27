"""Tests for Golden Synchronization: Debounced State Machine, Single-Flight Coalescer,
GitHub Webhook HMAC signature verification, and Ops endpoints.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from holodeck.api import create_app
from holodeck.config import Config
from holodeck.golden_sync import GoldenSyncManager
from holodeck.pool import PoolManager, PoolSlot
from holodeck.service import LeaseService


@pytest.fixture
def composite_manifests(manifests_dir: Path) -> Path:
    """Add a composite manifest with multiple component repos."""
    m = manifests_dir / "manifests"
    (m / "composite-app.sh").write_text(
        'HOLO_APP=composite-app\n'
        'HOLO_COMPOSITE_REPOS="test_backend test_frontend"\n'
    )
    return manifests_dir


def test_golden_sync_state_machine_unit(cfg: Config):
    """Test debounce quiet-window, resets on rapid pushes, and dirty flag when building."""
    cfg.golden_debounce_s = 2
    mock_pool = MagicMock()
    mgr = GoldenSyncManager(cfg, pool=mock_pool)
    mgr._execute_build_script = MagicMock(return_value=True)

    app = "compliance"

    # Push to non-main branch is ignored
    res = mgr.notify_push(app, repo="compliance-repo", branch="feature/new-login")
    assert res["status"] == "ignored"
    assert mgr.state(app)["state"] == "IDLE"

    # Push to main enters DEBOUNCING
    res = mgr.notify_push(app, repo="compliance-repo", branch="main", commit_sha="commit-1")
    assert res["status"] == "debouncing"
    assert mgr.state(app)["state"] == "DEBOUNCING"

    # Rapid second push resets debounce timer
    res = mgr.notify_push(app, repo="compliance-repo", branch="main", commit_sha="commit-2")
    assert res["status"] == "debouncing_reset"
    assert mgr.state(app)["state"] == "DEBOUNCING"

    # Trigger manual sync immediately transitions to BUILDING
    res = mgr.trigger_sync(app)
    assert res["status"] == "started"
    assert mgr.state(app)["state"] == "BUILDING"

    # Push landing while BUILDING marks it dirty
    res = mgr.notify_push(app, repo="compliance-repo", branch="main", commit_sha="commit-3")
    assert res["status"] == "queued_dirty"
    assert mgr.state(app)["dirty"] is True


def test_webhook_ping(client: TestClient):
    """GitHub webhook ping event responds with pong."""
    resp = client.post("/webhooks/github", headers={"X-GitHub-Event": "ping"}, json={"zen": "responsive"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "pong"}


def test_webhook_hmac_signature_verification(cfg: Config, service: LeaseService):
    """Webhook enforces SHA256 HMAC signature when secret is configured."""
    cfg.github_webhook_secret = "secret-key-42"
    app = create_app(cfg, service)
    tc = TestClient(app)

    payload = json.dumps({"ref": "refs/heads/main", "repository": {"name": "compliance"}}).encode("utf-8")

    # Missing header -> 401
    resp = tc.post("/webhooks/github", headers={"X-GitHub-Event": "push"}, content=payload)
    assert resp.status_code == 401
    assert "Missing or invalid" in resp.json()["detail"]

    # Invalid header -> 401
    resp = tc.post(
        "/webhooks/github",
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=invalid"},
        content=payload,
    )
    assert resp.status_code == 401
    assert "Invalid webhook signature" in resp.json()["detail"]

    # Valid HMAC signature -> 200
    valid_sig = "sha256=" + hmac.new("secret-key-42".encode("utf-8"), payload, hashlib.sha256).hexdigest()
    resp = tc.post(
        "/webhooks/github",
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": valid_sig,
            "Content-Type": "application/json",
        },
        content=payload,
    )
    assert resp.status_code == 200
    assert "debouncing" in resp.json()["status"]


def test_webhook_composite_repo_mapping(composite_manifests: Path, cfg: Config, service: LeaseService):
    """Component repos in composite applications map automatically to composite app."""
    app = create_app(cfg, service)
    tc = TestClient(app)

    payload = json.dumps({
        "ref": "refs/heads/main",
        "repository": {"name": "test_frontend"},
        "after": "abc1234",
    }).encode("utf-8")

    resp = tc.post(
        "/webhooks/github",
        headers={"X-GitHub-Event": "push", "Content-Type": "application/json"},
        content=payload,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "composite-app"
    assert "debouncing" in data["status"]


def test_ops_golden_state_and_rebuild_endpoints(client: TestClient):
    """GET /ops/golden/state and POST /ops/golden/rebuild operate as expected."""
    resp = client.get("/ops/golden/state?app=compliance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "compliance"
    assert "state" in data

    resp = client.post("/ops/golden/rebuild?app=compliance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "compliance"
    assert data["status"] in ("started", "already_building")


def test_ops_state_includes_golden_sync_info(client: TestClient):
    """GET /ops/state includes the golden_sync state dictionary."""
    resp = client.get("/ops/state")
    assert resp.status_code == 200
    data = resp.json()
    assert "golden_sync" in data
    assert isinstance(data["golden_sync"], dict)


def test_pool_drain_reaps_idle_slots(cfg: Config):
    """pool.drain reaps idle slots for the specified app."""
    cfg.pool_size = 2
    cfg.pool_apps_raw = "compliance"
    destroyed = []
    pool = PoolManager(
        cfg=cfg,
        boot=lambda slot_id, app, port: None,
        destroy=lambda slot: destroyed.append(slot.slot_id),
        verify=lambda slot: True,
        ws_root=lambda app: None,
    )
    # Add fake ready slots
    slot1 = PoolSlot(slot_id="pool-01", app="compliance", port=18001, ws_dir="/tmp/p1", compose_project="p1")
    slot2 = PoolSlot(slot_id="pool-02", app="other-app", port=18002, ws_dir="/tmp/p2", compose_project="p2")
    pool._ready["compliance"] = [slot1]
    pool._ready["other-app"] = [slot2]

    # Drain compliance
    pool.drain("compliance")

    # Verify slot1 was destroyed and removed, but other-app slot remains
    assert "pool-01" in destroyed
    assert "compliance" not in pool._ready
    assert len(pool._ready["other-app"]) == 1
