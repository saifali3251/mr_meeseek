"""Lifecycle: ports (#3), capacity (#8), reaper, release, health."""

from __future__ import annotations

import threading
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from holodeck.service import LeaseConflict


def test_ports_reserved_before_strike_and_freed_on_release(service):
    l = service.acquire("full-stack-application", "CPL-1", None, None)
    port = l.preview_port
    assert port in service.store.ports._in_use          # reserved, held
    service.release("cpl-1")
    assert port not in service.store.ports._in_use      # freed on release


def test_capacity_guard_queues_instead_of_rejecting(client):
    # max_leases=3 from the cfg fixture. Past capacity, a new lease QUEUES
    # (still 201, a real lease_id — Omnigent's own provision() poll loop just
    # waits through it) rather than hard-rejecting with a 503.
    for i in range(3):
        assert client.post("/leases", json={"app": "full-stack-application", "ticket": f"CPL-{i}"}).status_code == 201
    r = client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-9"})
    assert r.status_code == 201
    assert r.json()["status"] == "queued"


def test_released_lease_frees_capacity(client):
    for i in range(3):
        client.post("/leases", json={"app": "full-stack-application", "ticket": f"CPL-{i}"})
    client.delete("/leases/cpl-0")
    assert client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-9"}).status_code == 201


def test_queued_lease_advances_when_capacity_frees(client):
    # A ticket queued purely for max_leases (not concurrency) must actually
    # start striking once an existing lease is released — not just sit queued
    # forever. Sync mode (this fixture's default) drains it synchronously
    # inside the release() call itself via _advance_queue.
    for i in range(3):
        client.post("/leases", json={"app": "full-stack-application", "ticket": f"CPL-{i}"})
    queued = client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-9"})
    assert queued.json()["status"] == "queued"

    client.delete("/leases/cpl-0")

    assert client.get("/leases/cpl-9").json()["status"] == "ready"


def test_concurrent_strikes_bounded_and_queue_drains(cfg, provider):
    # max_concurrent_strikes, not max_leases, is what should queue this —
    # capacity is deliberately generous here so only the concurrency gate is
    # under test. Uses FakeProvider's acquire_block to hold the first strike
    # "in progress" deterministically, instead of racing real timing.
    from holodeck.service import LeaseService
    from holodeck.store import LeaseStore, PortPool

    cfg.provision_async = True
    cfg.max_leases = 10
    cfg.max_concurrent_strikes = 1
    svc = LeaseService(
        cfg, LeaseStore(PortPool(cfg.port_pool_start, cfg.port_pool_end, probe=False)), provider)

    gate = threading.Event()
    provider.acquire_block = gate

    first = svc.acquire("full-stack-application", "CPL-1", None, None)
    assert first.status.value == "pending"  # actively striking (blocked on the gate)

    second = svc.acquire("full-stack-application", "CPL-2", None, None)
    assert second.status.value == "queued"  # concurrency slot taken, not a capacity issue

    gate.set()  # let CPL-1's strike (and CPL-2's, once it starts) finish

    for _ in range(200):
        if (svc.store.get("cpl-1").status.value == "ready"
                and svc.store.get("cpl-2").status.value == "ready"):
            break
        time.sleep(0.01)
    assert svc.store.get("cpl-1").status.value == "ready"
    assert svc.store.get("cpl-2").status.value == "ready"


def test_explicit_preview_port_is_reserved(service):
    l = service.acquire("full-stack-application", "CPL-1", 18500, None)
    assert l.preview_port == 18500
    assert 18500 in service.store.ports._in_use


def test_reaper_releases_expired(service, reaper):
    l = service.acquire("full-stack-application", "CPL-1", None, None)
    l.expires_at = time.time() - 1        # already expired
    service.store.put(l)
    reaped = reaper.sweep_once()
    assert reaped == 1
    assert service.store.get("cpl-1").status.value == "released"


def test_reaper_leaves_live_leases(service, reaper):
    service.acquire("full-stack-application", "CPL-1", None, None)   # ttl far in future
    assert reaper.sweep_once() == 0
    assert service.store.get("cpl-1").status.value == "ready"


def test_extend_pushes_expiry(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    before = client.get("/leases/cpl-1").json()["expires_at"]
    r = client.post("/leases/cpl-1/extend", json={"ttl_s": 99999})
    assert r.status_code == 200
    assert r.json()["expires_at"] > before


def test_release_is_idempotent(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    assert client.delete("/leases/cpl-1").status_code == 200
    assert client.delete("/leases/cpl-1").status_code == 200  # no error second time


def test_healthz_reports_provider(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "fake"
    assert body["max_leases"] == 3


def test_finalize_produces_evidence(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    r = client.post("/leases/cpl-1/finalize")
    assert r.status_code == 200
    ev = r.json()
    assert ev["readiness_ok"] is True
    assert ev["seed_rows"] == 42
    assert "webserver" in ev["services_booted"]


def test_exec_runs_via_provider(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    r = client.post("/leases/cpl-1/exec", json={"cmd": ["echo", "hi"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit_code"] == 0
    assert body["argv"] == ["echo", "hi"]
    assert "echo hi" in body["stdout"]


def test_async_provision_returns_pending_then_ready(cfg, provider):
    # async mode: acquire() returns immediately with a PENDING lease and strikes
    # in the background, so a slow strike can't hold the single request worker.
    import time as _t

    from holodeck.service import LeaseService
    from holodeck.store import LeaseStore, PortPool

    cfg.provision_async = True
    svc = LeaseService(
        cfg, LeaseStore(PortPool(cfg.port_pool_start, cfg.port_pool_end, probe=False)), provider)
    lease = svc.acquire("full-stack-application", "CPL-1", None, None)
    assert lease.status.value in ("pending", "ready")   # returns without waiting on the strike
    for _ in range(200):                                 # background strike lands READY
        if svc.store.get("cpl-1").status.value == "ready":
            break
        _t.sleep(0.01)
    done = svc.store.get("cpl-1")
    assert done.status.value == "ready" and done.handle is not None


def test_exec_detach_returns_immediately(client):
    # detached exec (start_host's omnigent host launch): fire-and-forget, no
    # captured output — the shape the provider's run_background uses so a
    # long-running daemon can't block the single-worker control plane.
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    r = client.post("/leases/cpl-1/exec",
                    json={"cmd": ["omnigent", "host", "--server", "https://x"], "detach": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit_code"] == 0
    assert body["stdout"] == "detached"


def test_exec_accepts_string_cmd_and_service(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    r = client.post("/leases/cpl-1/exec", json={"cmd": "psql -c 'select 1'", "service": "db"})
    assert r.status_code == 200
    # shlex-split preserves the quoted arg as one token
    assert r.json()["argv"] == ["psql", "-c", "select 1"]
    assert "db" in r.json()["stdout"]


def test_exec_on_missing_lease_404(client):
    assert client.post("/leases/nope/exec", json={"cmd": ["ls"]}).status_code == 404


def test_exec_on_released_lease_409(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    client.delete("/leases/cpl-1")
    r = client.post("/leases/cpl-1/exec", json={"cmd": ["ls"]})
    assert r.status_code == 409


def test_acquire_returns_exec_gateway_handle(client):
    r = client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    body = r.json()
    assert body["exec_url"] == "/leases/cpl-1/exec"
    assert body["token"]  # per-lease capability token is minted


def test_ws_exec_streams_with_lease_token(client):
    tok = client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"}).json()["token"]
    with client.websocket_connect(f"/leases/cpl-1/exec?token={tok}") as ws:
        ws.send_json({"cmd": ["echo", "streamed"]})
        frames = []
        while True:
            msg = ws.receive_json()
            frames.append(msg)
            if msg["channel"] == "exit":
                break
    assert frames[-1] == {"channel": "exit", "code": 0}
    assert any(f["channel"] == "stdout" for f in frames)


def test_ws_exec_rejects_bad_token(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/leases/cpl-1/exec?token=wrong") as ws:
            ws.receive_json()


def test_failed_strike_is_retryable_and_frees_capacity(service, provider):
    # a strike that blows up must not brick the ticket or leak a capacity slot.
    provider.acquire_error = RuntimeError("golden missing")
    try:
        service.acquire("full-stack-application", "CPL-1", None, None)
        assert False, "expected the failed strike to raise"
    except RuntimeError:
        pass
    assert service.store.count_active() == 0            # no leaked slot
    assert service.store.get("cpl-1").status.value == "failed"
    assert service.store.get("cpl-1").error            # reason recorded

    provider.acquire_error = None                       # substrate healthy again
    lease = service.acquire("full-stack-application", "CPL-1", None, None)  # must NOT 409
    assert lease.status.value == "ready"
    assert service.store.count_active() == 1


def test_put_file_copies_into_workspace(client, provider):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    r = client.put("/leases/cpl-1/files?path=/app/patch.txt", content=b"hello")
    assert r.status_code == 204
    assert provider._files[("cpl-1", "/app/patch.txt")] == b"hello"


def test_put_file_missing_lease_404(client):
    assert client.put("/leases/nope/files?path=/x", content=b"y").status_code == 404


def test_put_file_requires_path(client):
    client.post("/leases", json={"app": "full-stack-application", "ticket": "CPL-1"})
    # missing ?path= -> FastAPI 422 (required query param)
    assert client.put("/leases/cpl-1/files", content=b"y").status_code == 422


def test_readyz_ready_on_fake(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["ready"] is True
    assert r.json()["problems"] == []


def test_capabilities_advertises_substrate_features(client):
    c = client.get("/capabilities").json()
    assert c["provider"] == "fake"
    assert c["file_copy"] is True
    assert c["streaming_exec"] is True
    assert c["resume_stopped"] is False
