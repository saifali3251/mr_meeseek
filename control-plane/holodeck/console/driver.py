"""How a triggered task is started + iterated.

- `DirectDriver` — the console strikes the lease API itself (Path A / fallback).
  Fully runnable; no agent session, so no reiterate.
- `OmnigentDriver` — tells Omnigent to start a session with the `holodeck`
  sandbox; Omnigent's provider strikes the env (lease shows up under
  `holo_id(ticket)`). Drives Omnigent through the OmnigentClient seam, so it
  runs against the Fake in tests/local and the Http client for real.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from holodeck.console.leaseclient import LeaseClient
from holodeck.console.omnigent import OmnigentClient, SessionStatus
from holodeck.models import holo_id

log = logging.getLogger("holodeck.console.driver")


class DriverError(RuntimeError):
    pass


@dataclass
class StartResult:
    lease_id: str
    session_id: Optional[str]
    preview_url: Optional[str]
    preview_port: Optional[int]
    status: str
    session_url: Optional[str] = None


def _port(lease: Optional[dict]) -> Optional[int]:
    return lease.get("preview_port") if lease else None


def _url(port: Optional[int]) -> Optional[str]:
    return f"http://127.0.0.1:{port}" if port else None


class DirectDriver:
    name = "direct"

    def __init__(self, lease_client: LeaseClient) -> None:
        self.client = lease_client

    def start(self, ticket: str, app: str, prompt: Optional[str] = None,
              target_repo: Optional[str] = None,
              base_overrides: Optional[dict[str, str]] = None) -> StartResult:
        lease = self.client.acquire(app, ticket, target_repo, base_overrides=base_overrides)  # no agent session; prompt is unused
        port = _port(lease)
        return StartResult(lease["lease_id"], None, _url(port), port, lease.get("status", "ready"))

    def session_status(self, session_id: Optional[str]) -> SessionStatus:
        return SessionStatus("ready", False)  # no agent session in the direct path

    def session_waiting(self, session_id: Optional[str]) -> bool:
        return False

    def reiterate(self, session_id: Optional[str], feedback: str) -> None:
        raise DriverError(
            "the direct driver has no agent session to reiterate; use the omnigent driver"
        )

    def answer(self, session_id: Optional[str], elicitation_id: str, accept: bool,
               content: Optional[dict] = None) -> None:
        raise DriverError("the direct driver has no agent session to answer")


class OmnigentDriver:
    name = "omnigent"

    def __init__(self, lease_client: LeaseClient, omni_client: OmnigentClient, cfg) -> None:
        self.lease = lease_client
        self.omni = omni_client
        self.cfg = cfg

    def start(self, ticket: str, app: str, prompt: Optional[str] = None,
              target_repo: Optional[str] = None,
              base_overrides: Optional[dict[str, str]] = None) -> StartResult:
        # The ticket spec is the agent's prompt (from Jira); fall back to a stub.
        prompt = prompt or f"Implement ticket {ticket}."
        session_id = self.omni.start(ticket, app, prompt, target_repo)
        # Omnigent's provider strikes the env under holo_id(ticket); read it back.
        lease_id = holo_id(ticket)
        lease = self.lease.get(lease_id)
        port = _port(lease)
        status = lease.get("status", "provisioning") if lease else "provisioning"
        # The session link is knowable immediately (no status() round-trip needed) —
        # lets the very first Jira comment carry it, instead of waiting for refresh().
        session_url = self.omni.session_url(session_id)
        return StartResult(lease_id, session_id, _url(port), port, status, session_url=session_url)

    def session_status(self, session_id: Optional[str]) -> SessionStatus:
        """Full status incl. any outstanding elicitation (id + question), so the
        bridge can post the real question to Jira and answer it correlated."""
        if not session_id:
            return SessionStatus("unknown", False)
        try:
            return self.omni.status(session_id)
        except Exception as e:
            log.warning("Omnigent session_status failed for %s: %s", session_id, e)
            return SessionStatus("unknown", False)

    def session_waiting(self, session_id: Optional[str]) -> bool:
        return self.session_status(session_id).waiting

    def reiterate(self, session_id: Optional[str], feedback: str) -> None:
        """Open-ended guidance -> a user message event."""
        if not session_id:
            raise DriverError("no agent session to reiterate")
        self.omni.send(session_id, feedback)

    def answer(self, session_id: Optional[str], elicitation_id: str, accept: bool,
               content: Optional[dict] = None) -> None:
        """Yes/no verdict on an outstanding elicitation -> an approval event."""
        if not session_id:
            raise DriverError("no agent session to answer")
        self.omni.approve(session_id, elicitation_id, accept, content)
