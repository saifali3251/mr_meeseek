"""Lease store (SQLite-backed) + port allocator.

The lease store persists to a SQLite DB so state survives restarts; the API is
still single-worker (one connection + lock). The PortPool stays in-memory
bookkeeping and is re-seeded from surviving leases at startup (see factory).
Swapping SQLite for Postgres later (multi-replica) is a change here only — the
store interface is unchanged (README "Scaling to 10K").
"""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import asdict
from typing import Optional

from holodeck.db import Db
from holodeck.models import Evidence, Lease, LeaseStatus, WorkspaceHandle


def _lease_to_row(l: Lease) -> tuple:
    return (
        l.lease_id, l.app, l.ticket, l.status.value, l.preview_port, l.ticket_test_cmd,
        l.token,
        json.dumps(asdict(l.handle)) if l.handle else None,
        json.dumps(asdict(l.evidence)) if l.evidence else None,
        l.error, l.created_at, l.expires_at,
    )


def _row_to_lease(r) -> Lease:
    handle = WorkspaceHandle(**json.loads(r["handle_json"])) if r["handle_json"] else None
    evidence = Evidence(**json.loads(r["evidence_json"])) if r["evidence_json"] else None
    return Lease(
        lease_id=r["lease_id"], app=r["app"], ticket=r["ticket"],
        status=LeaseStatus(r["status"]), handle=handle, preview_port=r["preview_port"],
        ticket_test_cmd=r["ticket_test_cmd"], evidence=evidence, error=r["error"],
        token=r["token"], created_at=r["created_at"], expires_at=r["expires_at"],
    )


class PortPool:
    """Allocates a preview port and holds it reserved until release.

    Reservation happens BEFORE strike runs and is held until teardown, closing
    the check-free -> strike-binds TOCTOU gap (issue #3). strike.sh does not
    check port availability at all (V13), so the API owns this entirely.
    """

    def __init__(self, start: int, end: int, probe: bool = True) -> None:
        self._start, self._end = start, end
        self._in_use: set[int] = set()
        self._lock = threading.Lock()
        self._probe = probe

    @staticmethod
    def _os_free(port: int, host: str = "127.0.0.1") -> bool:
        """Is the port actually bindable right now?

        The in-use set is BOOKKEEPING; it is empty after a restart and knows nothing
        about ports held by anything other than this process. On the POC box the
        substrate also runs the POC stack (443/4080/4013/8989/4242) and possibly
        another developer's workspaces. Asking the OS is the only check that reflects
        reality — and strike.sh does no port check at all (V13).

        Deliberately no SO_REUSEADDR: a port in TIME_WAIT counts as busy. Skipping a
        few ports out of ~1000 is free; handing out a contended one is not.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return True
            except OSError:
                return False

    def reserve(self) -> int:
        with self._lock:
            for p in range(self._start, self._end + 1):
                if p in self._in_use:
                    continue
                if self._probe and not self._os_free(p):
                    # Something outside this process holds it (orphan workspace from a
                    # previous run, the POC stack, an unrelated process). Remember it so
                    # we don't re-probe every allocation.
                    self._in_use.add(p)
                    continue
                self._in_use.add(p)
                return p
        raise RuntimeError("no free preview port in pool")

    def reserve_specific(self, port: int) -> None:
        """Mark a port used without allocating — for adopting orphans on
        startup so we never hand out a port something already holds (issue #4)."""
        with self._lock:
            self._in_use.add(port)

    def release(self, port: Optional[int]) -> None:
        if port is None:
            return
        with self._lock:
            self._in_use.discard(port)


_COLS = ("lease_id, app, ticket, status, preview_port, ticket_test_cmd, token, "
         "handle_json, evidence_json, error, created_at, expires_at")
_UPSERT = f"""
INSERT INTO leases ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(lease_id) DO UPDATE SET
  app=excluded.app, ticket=excluded.ticket, status=excluded.status,
  preview_port=excluded.preview_port, ticket_test_cmd=excluded.ticket_test_cmd,
  token=excluded.token, handle_json=excluded.handle_json,
  evidence_json=excluded.evidence_json, error=excluded.error,
  created_at=excluded.created_at, expires_at=excluded.expires_at
"""


class LeaseStore:
    def __init__(self, port_pool: PortPool, db: Optional[Db] = None) -> None:
        self.ports = port_pool
        self.db = db or Db()  # default: private in-memory DB (tests / throwaway)

    def get(self, lease_id: str) -> Optional[Lease]:
        rows = self.db.query("SELECT * FROM leases WHERE lease_id=?", (lease_id,))
        return _row_to_lease(rows[0]) if rows else None

    def exists(self, lease_id: str) -> bool:
        return bool(self.db.query("SELECT 1 FROM leases WHERE lease_id=?", (lease_id,)))

    def put(self, lease: Lease) -> None:
        self.db.execute(_UPSERT, _lease_to_row(lease))

    def delete(self, lease_id: str) -> Optional[Lease]:
        lease = self.get(lease_id)
        self.db.execute("DELETE FROM leases WHERE lease_id=?", (lease_id,))
        return lease

    def all(self) -> list[Lease]:
        return [_row_to_lease(r) for r in self.db.query("SELECT * FROM leases")]

    def count_active(self) -> int:
        """Leases occupying a total-workspace capacity slot: pending, queued, or
        ready. QUEUED counts too — it already holds a port + a lease record and
        represents committed intent to strike, so excluding it would let
        unbounded queued leases pile up past max_leases.

        RELEASED leases are torn down; FAILED leases rolled back their substrate
        during acquire — counting either would leak capacity slots that nothing
        holds (a FAILED lease used to wedge the host at MAX_LEASES forever)."""
        rows = self.db.query(
            "SELECT COUNT(*) AS c FROM leases WHERE status IN ('pending','queued','ready')")
        return rows[0]["c"]

    def count_active_for_app(self, app: str) -> int:
        """Leases currently occupying container runtime capacity for this application:
        pending or ready. (Queued leases wait in line and do not consume container capacity)."""
        rows = self.db.query(
            "SELECT COUNT(*) AS c FROM leases WHERE app=? AND status IN ('pending', 'ready')",
            (app,))
        return rows[0]["c"]

    def count_queued_for_app(self, app: str) -> int:
        """Leases currently waiting in queue for this application."""
        rows = self.db.query(
            "SELECT COUNT(*) AS c FROM leases WHERE app=? AND status = 'queued'",
            (app,))
        return rows[0]["c"]

    def expired(self, now: float) -> list[Lease]:
        rows = self.db.query(
            "SELECT * FROM leases WHERE status NOT IN ('released','failed') AND expires_at<=?",
            (now,))
        return [_row_to_lease(r) for r in rows]
