"""Background poller — refreshes the board from the lease API (+ Omnigent) on an
interval. Guarded so a bad tick never takes down the co-located control plane."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from holodeck.console.manager import ConsoleManager

log = logging.getLogger("holodeck.console.poller")


class ConsolePoller:
    def __init__(self, manager: ConsoleManager, interval_s: int,
                 on_tick: Optional[Callable[[], None]] = None,
                 refresh: bool = True, name: str = "holo-console-poller") -> None:
        self.manager = manager
        self.interval_s = interval_s
        self.on_tick = on_tick  # e.g. JiraBridge.sync — runs after each refresh
        self.refresh = refresh  # False for the inbound Jira poller (on_tick only)
        self.name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                if self.refresh:
                    self.manager.refresh()
                if self.on_tick is not None:
                    self.on_tick()
            except Exception:  # belt-and-suspenders; refresh()/on_tick guard themselves too
                log.exception("console poll tick failed")
