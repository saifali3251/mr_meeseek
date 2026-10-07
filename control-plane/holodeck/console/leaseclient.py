"""Lease-API client — the seam that keeps the console decoupled from the control
plane even when they share a process.

`HttpLeaseClient` talks to the lease API over HTTP (loopback in the co-located
MVP, a remote URL once split out — a config change, not a rewrite). `FakeLeaseClient`
is the in-memory stand-in for tests. The console only ever uses these public
lease operations; it never reaches into the lease store/provider internals.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import time
from typing import Optional, Protocol, runtime_checkable

from holodeck.models import holo_id


class LeaseClientError(RuntimeError):
    pass


@runtime_checkable
class LeaseClient(Protocol):
    def acquire(self, app: str, ticket: str, target_repo: Optional[str] = None,
                base_overrides: Optional[dict[str, str]] = None) -> dict: ...
    def get(self, lease_id: str) -> Optional[dict]: ...
    def release(self, lease_id: str) -> None: ...
    def extend(self, lease_id: str, ttl_s: int = 1800) -> dict: ...
    def finalize(self, lease_id: str, *, agent_summary: Optional[str] = None,
                 ticket_summary: Optional[str] = None, issue_type: Optional[str] = None) -> dict:
        """agent_summary/ticket_summary/issue_type are cosmetic PR title/body
        enrichment ONLY (see service.py's _pr_title/_pr_body) — never fed into
        readiness/tests/diff, which stay 100% host-derived regardless of what's
        passed here. Optional and additive: omitting them falls back to the
        original evidence-only PR exactly as before."""
        ...
    def valid_target_repos(self, app: str) -> set[str]:
        """The app's composite repo names (empty for a single-repo app). Lets a
        caller on this side of the seam (ConsoleManager, JiraBridge) validate a
        target_repo BEFORE starting anything — critical for the OmnigentDriver
        path, where the real acquire() call happens asynchronously inside
        Omnigent's own process and a downstream 422 there would never surface
        back to the Jira thread."""
        ...


class HttpLeaseClient:
    """Real client — HTTP to the lease API (default: this same service, loopback)."""

    def __init__(self, base_url: str, token: str = "") -> None:
        self.base = base_url.rstrip("/")
        self.token = token

    def _req(self, method: str, path: str, body: Optional[dict] = None):
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=630) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, None
        except urllib.error.URLError as e:
            raise LeaseClientError(f"lease API unreachable at {self.base}: {e.reason}")

    def acquire(self, app: str, ticket: str, target_repo: Optional[str] = None,
                base_overrides: Optional[dict[str, str]] = None) -> dict:
        # preview/ttl omitted -> the lease API auto-allocates a port + default TTL.
        body_req = {"app": app, "ticket": ticket}
        if target_repo is not None:
            body_req["target_repo"] = target_repo
        if base_overrides is not None:
            body_req["base_overrides"] = base_overrides
        status, body = self._req("POST", "/leases", body_req)
        if status != 201 or body is None:
            raise LeaseClientError(f"acquire failed for {ticket!r} (HTTP {status})")
        return body

    def get(self, lease_id: str) -> Optional[dict]:
        status, body = self._req("GET", f"/leases/{lease_id}")
        return body if status == 200 else None

    def release(self, lease_id: str) -> None:
        self._req("DELETE", f"/leases/{lease_id}")

    def extend(self, lease_id: str, ttl_s: int = 1800) -> dict:
        status, body = self._req("POST", f"/leases/{lease_id}/extend", {"ttl_s": ttl_s})
        if status != 200 or body is None:
            raise LeaseClientError(f"extend failed for {lease_id!r} (HTTP {status})")
        return body

    def finalize(self, lease_id: str, *, agent_summary: Optional[str] = None,
                 ticket_summary: Optional[str] = None, issue_type: Optional[str] = None) -> dict:
        # The notary: re-derives readiness/tests/diff host-side and (if
        # HOLODECK_PR is enabled) opens the PR itself — never the agent.
        # timeout=630 on _req already covers HOLODECK_FINALIZE_TIMEOUT_S (600s default).
        # The three narrative fields below are PR title/body cosmetics ONLY — the
        # service never lets them touch readiness/tests/diff (see service.py
        # docstrings). omit any that are empty so a caller that never had them
        # (bridge.py's Jira lookup failed, or this ticket has no Jira issue at
        # all) still gets exactly the old evidence-only PR body.
        body_req = {k: v for k, v in {
            "agent_summary": agent_summary, "ticket_summary": ticket_summary,
            "issue_type": issue_type,
        }.items() if v}
        status, body = self._req("POST", f"/leases/{lease_id}/finalize", body_req or None)
        if status != 200 or body is None:
            raise LeaseClientError(f"finalize failed for {lease_id!r} (HTTP {status})")
        return body

    def valid_target_repos(self, app: str) -> set[str]:
        # Reuses /environments (already exposes per-app facts, no auth) rather
        # than a dedicated endpoint.
        status, body = self._req("GET", "/environments")
        if status != 200 or body is None:
            raise LeaseClientError(f"environments lookup failed (HTTP {status})")
        for env in body.get("environments", []):
            if env.get("app") == app:
                return set(env.get("target_repos") or [])
        raise LeaseClientError(f"unknown app {app!r}")


class FakeLeaseClient:
    """In-memory stand-in for tests — no server, no Docker."""

    def __init__(self) -> None:
        self._leases: dict[str, dict] = {}
        self._n = 0
        self.force_evidence: dict[str, dict] = {}  # test knob: lease_id -> evidence override
        # test knob: app -> allowed target_repo values, mirrors FakeProvider.composite_repos
        self.composite_repos: dict[str, set[str]] = {}

    def acquire(self, app: str, ticket: str, target_repo: Optional[str] = None,
                base_overrides: Optional[dict[str, str]] = None) -> dict:
        lid = holo_id(ticket)
        existing = self._leases.get(lid)
        if existing is not None and existing["status"] != "released":
            raise LeaseClientError(f"lease {lid!r} already active")
        self._n += 1
        lease = {"lease_id": lid, "app": app, "ticket": ticket, "status": "ready",
                 "preview_port": 18000 + self._n, "error": None, "target_repo": target_repo,
                 "base_overrides": base_overrides}
        self._leases[lid] = lease
        return lease

    def get(self, lease_id: str) -> Optional[dict]:
        return self._leases.get(lease_id)

    def release(self, lease_id: str) -> None:
        if lease_id in self._leases:
            self._leases[lease_id]["status"] = "released"

    def extend(self, lease_id: str, ttl_s: int = 1800) -> dict:
        if lease_id not in self._leases:
            raise LeaseClientError(f"extend failed for {lease_id!r} (HTTP 404)")
        row = self._leases[lease_id]
        now = time.time()
        base = max(row.get("expires_at") or 0.0, now)
        row["expires_at"] = base + ttl_s
        return row

    def finalize(self, lease_id: str, *, agent_summary: Optional[str] = None,
                 ticket_summary: Optional[str] = None, issue_type: Optional[str] = None) -> dict:
        if lease_id not in self._leases:
            raise LeaseClientError(f"finalize failed for {lease_id!r} (HTTP 404)")
        return self.force_evidence.get(lease_id, {
            "lease_id": lease_id, "readiness": "", "readiness_ok": True, "seed_rows": None,
            "test_cmd": None, "test_exit": None, "test_output": "", "test_timed_out": False,
            "diff": "", "golden_head": None, "schema_rev": None, "services_booted": [],
            "services_absent": [], "finalized_at": 0.0, "pr_url": None,
            "guardrail_passed": True, "guardrail_reason": None,
        })

    def valid_target_repos(self, app: str) -> set[str]:
        return set(self.composite_repos.get(app, set()))
