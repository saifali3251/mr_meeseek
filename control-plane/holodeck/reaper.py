"""TTL reaper — background sweep that releases expired leases (Phase 4)."""

from __future__ import annotations

import logging
import threading
import time

from holodeck.service import LeaseService

log = logging.getLogger("holodeck.reaper")


class Reaper:
    def __init__(self, service: LeaseService, interval_s: int) -> None:
        self.service = service
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="holo-reaper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def sweep_once(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        reaped = 0
        for lease in self.service.store.expired(now):
            try:
                self.service.release(lease.lease_id)
                reaped += 1
                log.info("reaped expired lease %s", lease.lease_id)
            except Exception:
                log.exception("failed to reap lease %s", lease.lease_id)
        return reaped

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.sweep_once()
            except Exception:
                log.exception("reaper sweep failed")
