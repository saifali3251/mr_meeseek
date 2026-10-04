"""SQLite persistence — leases + console tasks survive a fresh store instance on
the same DB file, and nested dataclasses (handle, evidence) round-trip."""

from __future__ import annotations

from holodeck.console.store import ConsoleStore, TaskRecord
from holodeck.db import Db
from holodeck.models import Evidence, Lease, LeaseStatus, WorkspaceHandle
from holodeck.store import LeaseStore, PortPool


def _pool():
    return PortPool(18000, 18010, probe=False)


def test_lease_survives_new_store_on_same_db(tmp_path):
    path = str(tmp_path / "h.db")
    s1 = LeaseStore(_pool(), Db(path))
    s1.put(Lease(
        lease_id="cpl-1", app="full-stack-application", ticket="CPL-1", status=LeaseStatus.READY,
        handle=WorkspaceHandle(lease_id="cpl-1", app="full-stack-application", ticket="CPL-1",
                               preview_port=18000, compose_project="ws-cpl-1",
                               ws_dir="/x", golden_head="abc"),
        preview_port=18000, token="tok", expires_at=123.0,
    ))
    s2 = LeaseStore(_pool(), Db(path))  # new instance, same file
    got = s2.get("cpl-1")
    assert got is not None
    assert got.status == LeaseStatus.READY
    assert got.handle.compose_project == "ws-cpl-1"
    assert got.token == "tok"
    assert s2.count_active() == 1


def test_evidence_round_trips(tmp_path):
    s = LeaseStore(_pool(), Db(str(tmp_path / "h.db")))
    ev = Evidence(readiness="OK", readiness_ok=True, seed_rows=42, test_cmd="pytest",
                  test_exit=0, test_output="pass", test_timed_out=False, diff="d",
                  golden_head="g", schema_rev="head",
                  services_booted=["webserver", "db"], services_absent=[])
    s.put(Lease(lease_id="cpl-2", app="full-stack-application", ticket="CPL-2",
                status=LeaseStatus.READY, evidence=ev))
    got = s.get("cpl-2")
    assert got.evidence.seed_rows == 42
    assert got.evidence.services_booted == ["webserver", "db"]


def test_delete_and_expired(tmp_path):
    s = LeaseStore(_pool(), Db(str(tmp_path / "h.db")))
    s.put(Lease("a", "x", "A", LeaseStatus.READY, expires_at=1.0))
    s.put(Lease("b", "x", "B", LeaseStatus.READY, expires_at=9_999_999_999.0))
    assert [l.lease_id for l in s.expired(now=100.0)] == ["a"]
    assert s.delete("a").lease_id == "a"
    assert s.get("a") is None
    assert s.count_active() == 1


def test_console_task_survives_new_store(tmp_path):
    path = str(tmp_path / "h.db")
    c1 = ConsoleStore(Db(path))
    c1.put(TaskRecord(ticket="CPL-1", app="full-stack-application", lease_id="cpl-1",
                      session_id="s-1", status="ready", preview_port=18000, waiting=True))
    r = ConsoleStore(Db(path)).get("CPL-1")  # new instance, same file
    assert r is not None
    assert (r.lease_id, r.session_id, r.waiting) == ("cpl-1", "s-1", True)
