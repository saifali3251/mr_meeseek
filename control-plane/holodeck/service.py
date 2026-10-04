"""LeaseService — provider-agnostic orchestration.

Sits between the HTTP routes and the WorkspaceProvider. Owns lease lifecycle,
port reservation, capacity, TTL, and the finalize test-source policy. It knows
NOTHING about Docker or Kubernetes — only the provider protocol. This is the
layer the FakeProvider tests exercise with no substrate.
"""

from __future__ import annotations

import collections
import logging
import os
import secrets
import threading
import time
from typing import AsyncIterator, Optional

from holodeck.config import Config
from holodeck.providers.base import ProviderError
from holodeck.models import (Evidence, ExecResult, Lease, LeaseStatus,
                             WorkspaceHandle, diff_stat, holo_id)
from holodeck.providers.base import (ProviderError, WorkspaceExistsError,
                                     WorkspaceProvider)
from holodeck.store import LeaseStore

log = logging.getLogger("holodeck.service")


class LeaseConflict(RuntimeError):
    """ticket->id already leased (issue #7) -> API returns 409."""

    def __init__(self, lease_id: str) -> None:
        super().__init__(lease_id)
        self.lease_id = lease_id


class LeaseNotReady(RuntimeError):
    """exec on a lease that isn't READY (no live workspace) -> API returns 409."""

    def __init__(self, lease_id: str, status: str) -> None:
        super().__init__(f"lease '{lease_id}' is {status}, not ready")
        self.lease_id = lease_id


def _pr_body(lease: Lease, ev: Evidence, jira_base_url: str = "",
             agent_summary: Optional[str] = None,
             sandbox_url: Optional[str] = None) -> str:
    """Markdown PR body. The evidence checklist (readiness/tests/diff/etc) that
    used to live here has moved to the console dashboard exclusively — this is
    no longer the place a reviewer checks that; it's a Jira-linked description
    plus the live sandbox to click into. agent_summary is the one piece of
    agent-authored text allowed in here, and it's still not "evidence": it's
    the implementation narrative the agent already posted to Jira (gated by
    _BEHAVIOR_PREAMBLE/_FINISH_MARKER in bridge.py), documentation of what
    changed, not a claim about whether it works — readiness/tests/diff stay
    100% host-derived and untouched by anything passed in here.

    sandbox_url comes from Config.workspace_preview_url() — the SAME helper
    that builds TaskRecord.preview_url for the Jira "started" comment's Preview
    link (console/manager.py:refresh) — so this and that can't drift out of
    sync with each other the way they did before that helper existed."""
    ticket_ref = (f"[{lease.ticket}]({jira_base_url.rstrip('/')}/browse/{lease.ticket})"
                  if jira_base_url else f"`{lease.ticket}`")
    summary_block = f"{agent_summary.strip()}\n\n---\n\n" if agent_summary else ""
    sandbox_line = f"**Live sandbox:** {sandbox_url}\n\n" if sandbox_url else ""
    return (
        f"{summary_block}"
        f"{sandbox_line}"
        f"🤖 Opened by Holodeck for ticket {ticket_ref}."
    )


# Jira issue type (case-insensitive) -> conventional-commit PR title prefix.
# Anything not listed here (including no issue type at all) defaults to "feat" —
# matches this project's existing PR title convention (see other PRs).
_PR_TITLE_TYPE_MAP = {
    "bug": "fix",
    "task": "chore",
    "story": "feat",
    "epic": "feat",
}


def _pr_title(lease: Lease, ticket_summary: Optional[str], issue_type: Optional[str]) -> str:
    """feat: <desc> [CRLT-XYZ] — matches the existing PR convention so Jira's own
    smart-commit/PR-link sync picks it up. <desc> falls back to the bare ticket
    id if the Jira summary lookup failed/was omitted, so this never produces an
    empty description."""
    kind = _PR_TITLE_TYPE_MAP.get((issue_type or "").strip().lower(), "feat")
    desc = (ticket_summary or "").strip() or lease.ticket
    return f"{kind}: {desc} [{lease.ticket}]"


class LeaseService:
    def __init__(self, cfg: Config, store: LeaseStore, provider: WorkspaceProvider) -> None:
        self.cfg = cfg
        self.store = store
        self.provider = provider
        # Bounds concurrent `docker compose up`s (max_concurrent_strikes), separate
        # from max_leases (total workspace ceiling, enforced in acquire()). A
        # BoundedSemaphore, not a plain counter: acquire()/release() are each
        # individually atomic, so two concurrent acquire() calls can't both
        # observe "one slot free" and both proceed (the TOCTOU a naive
        # read-the-count-then-decide check would have).
        self._strike_semaphore = threading.BoundedSemaphore(cfg.max_concurrent_strikes)
        # FIFO of lease_ids waiting on either gate above. A plain deque guarded
        # by _queue_lock — this process is single-worker/single-store, so an
        # in-process lock is sufficient; nothing here needs to survive a restart
        # (a restart's own reconcile pass re-derives reality from the store).
        self._queue: collections.deque[str] = collections.deque()
        self._queue_lock = threading.Lock()
        self.max_app_leases = cfg.max_app_leases
        self._change_listeners: list = []
        # Re-hydrate existing queued leases from the store in FIFO order
        for l in sorted(self.store.all(), key=lambda r: r.created_at):
            if l.status == LeaseStatus.QUEUED:
                self._queue.append(l.lease_id)

    def add_change_listener(self, fn) -> None:
        """Register a callback invoked whenever lease state transitions occur."""
        if fn not in self._change_listeners:
            self._change_listeners.append(fn)

    def _notify_change(self) -> None:
        for fn in list(self._change_listeners):
            try:
                fn()
            except Exception:
                log.exception("lease change listener failed")

    def set_max_app_leases(self, limit: int) -> int:
        """Dynamically update the per-application active workspace capacity limit at runtime."""
        self.max_app_leases = max(1, int(limit))
        self.cfg.max_app_leases = self.max_app_leases
        self._advance_queue()
        self._notify_change()
        return self.max_app_leases

    def _resolve_test_cmd(self, app: str, ticket: str, target_repo: Optional[str] = None) -> Optional[str]:
        """Test source: the app's manifest default (HOLO_TEST_CMD, or repo-specific
        HOLO_TEST_CMD_<repo>, resolved per-app by the provider), with a global env fallback.
        Ticket may override, agent never — ticket-derived overrides are intentionally not
        wired to any request field yet; a future ticket integration sets ticket_test_cmd here."""
        return self.provider.test_cmd(app, target_repo=target_repo) or os.environ.get("HOLO_TEST_CMD") or None

    def acquire(self, app: str, ticket: str, preview: Optional[int],
                ttl_s: Optional[int], target_repo: Optional[str] = None,
                base_overrides: Optional[dict[str, str]] = None) -> Lease:
        # target_repo is validated by the caller (api.py, against
        # provider.valid_target_repos(app)) BEFORE this is called — same boundary
        # `app` itself is already checked at, in api.py's route, not here.
        lease_id = holo_id(ticket)

        # issue #7: collision -> clean 409, not a 500 with shell output.
        # Only an ACTIVE lease (pending/queued/ready) blocks the ticket. A prior
        # RELEASED or FAILED lease is re-acquirable: a failed strike already
        # rolled back its substrate, so bricking the ticket forever (the old
        # `!= RELEASED` check) was a bug — the record is kept only for
        # visibility and is overwritten by this fresh attempt.
        existing = self.store.get(lease_id)
        if existing is not None and existing.status in (
            LeaseStatus.PENDING, LeaseStatus.QUEUED, LeaseStatus.READY
        ):
            raise LeaseConflict(lease_id)

        # issue #3: an EXPLICIT preview port is the caller's own intent for THIS
        # ticket — reserve it immediately regardless of queueing, closing the
        # check-free -> strike-binds TOCTOU gap even while this lease waits its
        # turn. An AUTO-allocated port has no such intent attached to it, so
        # it's deferred until the lease actually starts striking (below, or in
        # _advance_queue) — a QUEUED lease that may wait a while isn't yet
        # running anything a preview port would even point at.
        if preview is not None:
            self.store.ports.reserve_specific(preview)
        port = preview  # may be None; auto-allocated lazily just before striking

        ttl = ttl_s or self.cfg.default_ttl_s
        lease = Lease(
            lease_id=lease_id, app=app, ticket=ticket, status=LeaseStatus.PENDING,
            preview_port=port, ticket_test_cmd=self._resolve_test_cmd(app, ticket, target_repo),
            target_repo=target_repo, base_overrides=base_overrides,
            token=secrets.token_urlsafe(24),  # capability for the exec gateway
            expires_at=time.time() + ttl,
        )

        # Per-application active workspace capacity constraint (default: N=3)
        max_for_app = getattr(self, "max_app_leases", self.cfg.max_app_leases)
        if self.store.count_active_for_app(app) >= max_for_app:
            return self._enqueue(lease)

        # Total host capacity guard past max_leases
        if self.store.count_active() >= self.cfg.max_leases:
            return self._enqueue(lease)

        # Separate gate: bound CONCURRENT strikes (docker compose up — CPU/disk
        # heavy) regardless of total capacity headroom. non-blocking: a full
        # semaphore must not stall this request — it's the exact bug this whole
        # design exists to avoid.
        if not self._strike_semaphore.acquire(blocking=False):
            return self._enqueue(lease)

        if port is None:
            port = self.store.ports.reserve()
            lease.preview_port = port
        self.store.put(lease)
        self._notify_change()
        self._start_strike(lease, app, ticket, port)
        return lease

    def _enqueue(self, lease: Lease) -> Lease:
        """Record `lease` as QUEUED and append it to the FIFO wait list. Called
        from acquire() (new lease, no slot available) and never removes a slot
        that was already acquired — the caller must not have acquired one. No
        auto-allocated port yet either (see acquire()) — _advance_queue reserves
        one lazily, right before this lease's strike actually starts."""
        lease.status = LeaseStatus.QUEUED
        self.store.put(lease)
        with self._queue_lock:
            self._queue.append(lease.lease_id)
        self._notify_change()
        return lease

    def _start_strike(self, lease: Lease, app: str, ticket: str, port: int) -> None:
        """Start (or background) the strike for a lease that has ALREADY secured
        a semaphore slot. Caller owns releasing that slot on completion — async
        via _strike_safe's finally, sync via the finally below."""
        if self.cfg.provision_async:
            threading.Thread(
                target=self._strike_safe, args=(lease, app, ticket, port),
                name=f"strike-{lease.lease_id}", daemon=True,
            ).start()
            return  # PENDING — caller polls for READY
        try:
            self._strike(lease, app, ticket, port)  # raises on conflict/failure
        finally:
            self._release_strike_slot()

    def _strike(self, lease: Lease, app: str, ticket: str, port: int) -> None:
        """Run the provider strike and land the lease in READY, or roll back and
        raise (WorkspaceExistsError -> LeaseConflict; any other -> FAILED + re-raise).

        Either outcome can race a concurrent release() (see LeaseService.release):
        a caller can give up on a still-PENDING lease and call cancel_acquire()
        while we're in here. `_released_underneath` is the single check both
        branches use to detect that and defer to the RELEASED record instead of
        overwriting it — never resurrect to READY, never downgrade to FAILED."""
        lease_id = lease.lease_id
        try:
            handle = self.provider.acquire(lease_id, app, ticket, port, lease.target_repo, lease.base_overrides)
        except WorkspaceExistsError as e:
            self.store.ports.release(port)
            self.store.delete(lease_id)
            raise LeaseConflict(lease_id) from e
        except Exception as exc:
            # A strike that fails midway (e.g. a port collision during container start,
            # or a cancel_acquire() kill) leaves the workspace DIR and any started
            # containers behind. strike.sh does not clean up after itself, so without
            # this the next acquire for the same ticket hits "workspace already
            # exists" and the ticket is permanently 409. Best-effort: never mask the
            # original failure.
            #
            # Deliberately NOT done for WorkspaceExistsError above — that workspace
            # belongs to someone else (an orphan or another lease); tearing it down
            # would destroy work this request never owned.
            try:
                self.provider.release(
                    WorkspaceHandle(lease_id=lease_id, app=app, ticket=ticket,
                                    preview_port=port, compose_project=f"ws-{lease_id}")
                )
                log.warning("rolled back partial workspace for lease %s", lease_id)
            except Exception:
                log.exception("rollback of partial workspace %s failed — "
                              "may need `destroy.sh %s` by hand", lease_id, ticket)
            self.store.ports.release(port)
            if self._released_underneath(lease_id):
                # release() already recorded this as RELEASED (a cancel_acquire()
                # kill, not an organic failure) — leave that status as-is; it's
                # more accurate than FAILED and the workspace is already gone.
                return
            # Keep the FAILED record for observability (with the reason), but it
            # no longer blocks re-acquire (see the conflict check above) and no
            # longer counts toward capacity (store.count_active ignores FAILED).
            lease.status = LeaseStatus.FAILED
            lease.error = str(exc)
            self.store.put(lease)
            self._notify_change()
            raise

        if self._released_underneath(lease_id):
            # Finished successfully just after a caller gave up on it (the strike
            # outran cancel_acquire()'s kill, or finished in the small window
            # before it landed) — nobody's waiting for this anymore; tear it back
            # down rather than resurrecting a released lease as READY.
            try:
                self.provider.release(handle)
                log.warning("strike for %s finished after release() gave up on it "
                            "— tore it back down", lease_id)
            except Exception:
                log.exception("cleanup of late-finishing strike %s failed — "
                              "may need `destroy.sh %s` by hand", lease_id, ticket)
            self.store.ports.release(port)
            return

        # The PROVIDER is the authority on which host port actually got bound —
        # not the port we optimistically reserved above. They diverge whenever the
        # provider serves the lease from a pre-booted warm workspace: that slot
        # published its port when its containers were CREATED, and a running
        # container's published port cannot be rebound. So the reserved port is
        # unhonourable and the lease must adopt the real one, or the preview URL,
        # finalize's readiness probe and the PR body all point at a port nothing
        # is listening on.
        #
        # Releasing the reserved port back is not bookkeeping tidiness: PortPool
        # only ever drops a port on release(), so skipping it burns one port per
        # pooled claim, permanently, until the process restarts.
        if handle.preview_port is not None and handle.preview_port != port:
            log.info("lease %s adopting provider port %s (reserved %s released)",
                     lease_id, handle.preview_port, port)
            self.store.ports.reserve_specific(handle.preview_port)
            self.store.ports.release(port)
            lease.preview_port = handle.preview_port

        lease.handle = handle
        lease.status = LeaseStatus.READY
        self.store.put(lease)
        self._notify_change()

    def _released_underneath(self, lease_id: str) -> bool:
        current = self.store.get(lease_id)
        return current is not None and current.status == LeaseStatus.RELEASED

    def _strike_safe(self, lease: Lease, app: str, ticket: str, port: int) -> None:
        """Background wrapper for the async path: never propagates (the thread has
        no caller). _strike already records FAILED for generic errors; a
        WorkspaceExistsError (409 equivalent) deletes the pending lease, so a
        polling caller sees it disappear."""
        try:
            self._strike(lease, app, ticket, port)
        except LeaseConflict:
            log.warning("async strike: workspace already exists for %s", lease.lease_id)
        except Exception:
            log.exception("async strike failed for %s", lease.lease_id)
        finally:
            self._release_strike_slot()

    def _release_strike_slot(self) -> None:
        """A strike (successful, failed, or conflicted) just freed a
        concurrency slot — give it back and let the next queued lease, if any,
        take its turn."""
        self._strike_semaphore.release()
        self._advance_queue()

    def _advance_queue(self) -> None:
        """Start any QUEUED lease(s) that can now get a strike slot and has per-app capacity.
        Advances all queued leases for which concurrency and per-app capacity are available."""
        with self._queue_lock:
            remaining_queue: collections.deque[str] = collections.deque()
            while self._queue:
                next_id = self._queue.popleft()
                lease = self.store.get(next_id)
                if lease is None or lease.status != LeaseStatus.QUEUED:
                    # released/expired/re-acquired while it waited
                    continue

                max_for_app = getattr(self, "max_app_leases", self.cfg.max_app_leases)
                has_app_capacity = self.store.count_active_for_app(lease.app) < max_for_app

                if has_app_capacity and self._strike_semaphore.acquire(blocking=False):
                    if lease.preview_port is None:
                        lease.preview_port = self.store.ports.reserve()
                    lease.status = LeaseStatus.PENDING
                    self.store.put(lease)
                    self._notify_change()
                    threading.Thread(
                        target=self._strike_safe,
                        args=(lease, lease.app, lease.ticket, lease.preview_port),
                        name=f"strike-{next_id}", daemon=True,
                    ).start()
                else:
                    remaining_queue.append(next_id)
            self._queue = remaining_queue

    def finalize(self, lease_id: str, *, agent_summary: Optional[str] = None,
                 ticket_summary: Optional[str] = None, issue_type: Optional[str] = None) -> Evidence:
        lease = self._require(lease_id)
        target_repo = lease.target_repo or (lease.handle.target_repo if lease.handle else None)
        test_cmd = self._resolve_test_cmd(lease.app, lease.ticket, target_repo) or lease.ticket_test_cmd  # dynamic manifest lookup with lease fallback
        evidence = self.provider.finalize(lease.handle, test_cmd)
        # E2: finalize -> draft PR, guarded. Opening a PR is an outward-facing
        # side effect, so it runs only when enabled AND there is a real change
        # (a diff vs the golden). A PR failure never fails finalize — the evidence
        # is still the truth; the PR is a convenience carried on top of it.
        #
        # Guardrails (Phase 3 & Phase 4):
        # 1. Exit 0 is mandatory! If a test was configured and failed or timed out, PR is blocked.
        # 2. Host Guardrails (Blast radius and AST integrity) must pass! If violated, PR is blocked.
        test_passed = (evidence.test_exit == 0) if evidence.test_cmd else True
        guardrail_passed = getattr(evidence, "guardrail_passed", True)
        if not evidence.test_timed_out and test_passed and guardrail_passed:
            if (self.cfg.pr_enabled and lease.handle is not None
                    and evidence.diff and evidence.diff.strip()):
                try:
                    evidence.pr_url = self.provider.open_pr(
                        lease.handle, base=self.cfg.pr_base, draft=self.cfg.pr_draft,
                        title=_pr_title(lease, ticket_summary, issue_type),
                        body=_pr_body(lease, evidence, self.cfg.jira_base_url, agent_summary,
                                     self.cfg.workspace_preview_url(lease.preview_port, app=lease.app, ticket=lease.ticket)),
                        label=self.cfg.pr_label or None)
                except Exception:  # PR is best-effort — a missing `gh`, a push/auth
                    # failure, anything, must never fail finalize (the evidence is the
                    # truth). Was `except ProviderError`, which let a raw FileNotFoundError
                    # ('gh' not on the host) escape as a 500.
                    log.exception(
                        "finalize: PR creation failed for %s (evidence still stamped)", lease_id)
        else:
            log.warning(
                "finalize: PR creation blocked for %s (test_cmd=%r, test_exit=%s, timed_out=%s, guardrail_passed=%s: %s)\n--- Test Suite Output ---\n%s\n------------------------",
                lease_id, evidence.test_cmd, evidence.test_exit, evidence.test_timed_out,
                guardrail_passed, getattr(evidence, "guardrail_reason", None),
                evidence.test_output.strip() if evidence.test_output else "(no test output)")
        lease.evidence = evidence  # overwrite-on-recall (issue #6)
        self.store.put(lease)
        self._notify_change()
        return evidence

    def exec(self, lease_id: str, argv: list[str], *, service: Optional[str] = None,
             workdir: Optional[str] = None, timeout_s: Optional[int] = None,
             detach: bool = False) -> ExecResult:
        """Run a command inside the lease's workspace (B3), scoped to it via the
        provider. Never shells out here — goes through the WorkspaceProvider so
        EKS (`kubectl exec`) is a drop-in for Compose (`docker compose exec`).

        `detach` fire-and-forgets a long-running process (e.g. the Omnigent host
        the sandbox provider's start_host launches): the provider starts it
        detached and returns immediately, so a captured-output exec can't block
        the single worker."""
        lease = self._require(lease_id)
        if lease.status != LeaseStatus.READY or lease.handle is None:
            raise LeaseNotReady(lease_id, lease.status.value)
        return self.provider.exec(
            lease.handle, argv, service=service, workdir=workdir,
            timeout_s=timeout_s, detach=detach
        )

    def authorize_exec(self, lease_id: str, token: Optional[str]) -> Lease:
        """Gate the exec gateway with the lease's own capability token (deck's
        `bearer=teardown_token`). Constant-time compare; a wrong/absent token is
        indistinguishable from a wrong lease id to a caller."""
        lease = self.store.get(lease_id)
        if lease is None or not lease.token or not token:
            raise PermissionError("invalid lease or token")
        if not secrets.compare_digest(token, lease.token):
            raise PermissionError("invalid lease or token")
        if lease.status != LeaseStatus.READY or lease.handle is None:
            raise LeaseNotReady(lease_id, lease.status.value)
        return lease

    def stream_exec(self, lease: Lease, argv: list[str], *, service: Optional[str] = None,
                    workdir: Optional[str] = None) -> AsyncIterator[tuple[str, str]]:
        """Streaming exec for the WS gateway. Returns the provider's async frame
        iterator ('stdout'|'stderr'|'exit', text) — the route stays substrate-agnostic."""
        return self.provider.stream_exec(lease.handle, argv, service=service, workdir=workdir)

    def put_file(self, lease_id: str, remote_path: str, content: bytes, *,
                 service: Optional[str] = None) -> None:
        """Copy bytes into the lease's workspace (Omnigent `put()`)."""
        lease = self._require(lease_id)
        if lease.status != LeaseStatus.READY or lease.handle is None:
            raise LeaseNotReady(lease_id, lease.status.value)
        if not remote_path or not remote_path.strip():
            raise ValueError("remote path is required")
        self.provider.put(lease.handle, remote_path, content, service=service)

    def preflight(self, app: Optional[str] = None) -> list[str]:
        """Substrate readiness (Omnigent `prepare()`), surfaced at /readyz.

        `app` scopes the per-app checks; None keeps the whole-substrate view.
        A caller that only cares about one app should pass it, or an unrelated
        app's missing golden fails it closed."""
        return self.provider.prepare(app)

    def golden_info(self, app: Optional[str] = None) -> dict:
        """Golden image metadata for `app` (or default app)."""
        app_name = app or self.cfg.default_app
        if hasattr(self.provider, "golden_info"):
            return self.provider.golden_info(app_name)
        return {"exists": False, "updated_at": None, "path": None}

    def capabilities(self):
        """What the active substrate supports, surfaced at /capabilities."""
        return self.provider.capabilities()

    def extend(self, lease_id: str, ttl_s: int) -> Lease:
        lease = self._require(lease_id)
        lease.expires_at = time.time() + ttl_s
        self.store.put(lease)
        self._notify_change()
        return lease

    def release(self, lease_id: str) -> Lease:
        lease = self._require(lease_id)
        if lease.status == LeaseStatus.RELEASED:
            return lease
        if lease.status == LeaseStatus.PENDING:
            # Still striking — provider.acquire() hasn't returned, so there's no
            # handle yet for us to tear down (the gap that used to leave these as
            # orphans: this used to fall through to the branch below, skip
            # provider.release() because handle was None, and free the port while
            # docker compose up kept running unwatched). Mark RELEASED now so a
            # concurrent GET/acquire sees the true intent immediately, then ask
            # the provider to kill the in-flight strike. Deliberately do NOT
            # release the port here — the strike may still be bound to it until
            # the kill actually lands; the strike thread's own except-branch
            # (_strike, below) releases it once the subprocess is confirmed dead,
            # so a new lease can't be handed the same port while the old one is
            # still tearing down.
            lease.status = LeaseStatus.RELEASED
            self.store.put(lease)
            self.provider.cancel_acquire(lease_id)
            self._advance_queue()
            self._notify_change()
            return lease
        if lease.handle is not None:
            self.provider.release(lease.handle)
        self.store.ports.release(lease.preview_port)
        lease.status = LeaseStatus.RELEASED
        self.store.put(lease)
        # This just freed a total-workspace (max_leases) capacity slot — a
        # lease queued for THAT reason (not a concurrency-slot shortage) can
        # now proceed. _advance_queue's own gate is the strike semaphore, so
        # this only actually starts something if a concurrency slot is also
        # free right now — otherwise it's a safe no-op and a later strike
        # completion will retry.
        self._advance_queue()
        self._notify_change()
        return lease

    def _require(self, lease_id: str) -> Lease:
        lease = self.store.get(lease_id)
        if lease is None:
            raise KeyError(lease_id)
        return lease
