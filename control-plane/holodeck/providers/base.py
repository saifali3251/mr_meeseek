"""The WorkspaceProvider seam.

This is the single most important abstraction in Track B. The HTTP layer, the
lease store, the reaper, and finalize are ALL written against this protocol and
never against a concrete substrate. ComposeProvider (Docker Compose, this repo's
strike.sh/destroy.sh) is one implementation; EksProvider (CSI VolumeSnapshot ->
clone PVC + kubectl exec) is a drop-in second one.

Discipline (enforce in review): the API must NEVER call strike.sh directly. The
moment one endpoint shells out on its own, EKS becomes a rewrite instead of a
new class.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional, Protocol, runtime_checkable

from holodeck.models import (Evidence, ExecResult, ProviderCapabilities,
                             WorkspaceHandle)


class ProviderError(RuntimeError):
    """Substrate operation failed. The API maps this to a 5xx (or 409 for a
    known collision — see WorkspaceExistsError)."""


class WorkspaceExistsError(ProviderError):
    """acquire() found a workspace already present for this id (issue #7).
    The API maps this to 409 Conflict rather than a 500 with shell output."""


@runtime_checkable
class WorkspaceProvider(Protocol):
    name: str

    def prepare(self, app: Optional[str] = None) -> list[str]:
        """Preflight the substrate BEFORE any acquire: tooling present, docker
        reachable, golden built. Return human-readable problems ([] = ready).
        Maps to Omnigent's `prepare()`; surfaced at GET /readyz.

        `app` SCOPES the per-app checks. Unscoped (None) means "check every
        app", which is the right answer for an operator dashboard and the
        WRONG one for a caller that only wants one app: a single unbuilt
        golden then fails the whole substrate closed, blocking apps that are
        perfectly healthy. Callers acting on behalf of one app MUST pass it."""
        ...

    def capabilities(self) -> ProviderCapabilities:
        """What this substrate supports. The Omnigent adapter maps this to
        `SandboxCapabilities`. Surfaced at GET /capabilities."""
        ...

    def put(
        self,
        handle: WorkspaceHandle,
        remote_path: str,
        content: bytes,
        *,
        service: Optional[str] = None,
    ) -> None:
        """Copy `content` to `remote_path` inside the workspace (default: the app
        service). Maps to Omnigent's `put()` — used to ship files/config into a
        sandbox before launching a host."""
        ...

    def acquire(
        self, lease_id: str, app: str, ticket: str, preview_port: Optional[int],
        target_repo: Optional[str] = None,
        base_overrides: Optional[dict[str, str]] = None,
    ) -> WorkspaceHandle:
        """Create a warm workspace and return a handle. Must be idempotent-safe:
        raise WorkspaceExistsError if one already exists for lease_id.

        target_repo (composites only): which of the app's HOLO_COMPOSITE_REPOS this
        lease's branch/diff/PR operate on — already validated by the caller against
        valid_target_repos(app) before this is called. None means "use the manifest's
        static HOLO_GIT_SUBDIR fallback" (single-repo apps, or a composite lease that
        didn't specify one)."""
        ...

    def valid_target_repos(self, app: str) -> set[str]:
        """The app's HOLO_COMPOSITE_REPOS, from the manifest — empty set for a
        single-repo app (nothing to validate a target_repo against, so any
        caller-supplied value is rejected). Read straight from disk, same
        never-guess-a-manifest-value discipline as everything else here."""
        ...

    def finalize(self, handle: WorkspaceHandle, test_cmd: Optional[str]) -> Evidence:
        """Host-side notary. Re-derive readiness, seed proof, the migration head,
        the golden_head..HEAD diff, and run `test_cmd` (from the manifest/ticket,
        never the agent) under a wall-clock cap. Read nothing the agent wrote."""
        ...

    def test_cmd(self, app: str, target_repo: Optional[str] = None) -> Optional[str]:
        """The manifest's HOLO_TEST_CMD for `app` (or repo-specific HOLO_TEST_CMD_<repo>),
        or None. Read at acquire time and captured onto the lease so the agent — who
        only ever touches the workspace, never the manifest — cannot influence it."""
        ...

    def open_pr(self, handle: WorkspaceHandle, *, base: str, draft: bool,
                title: str, body: str, label: Optional[str] = None) -> str:
        """E2: push the lease's `agent/<id>` branch and open a (draft) PR with the
        evidence `body`; return the PR URL. Idempotent — an existing PR for the
        branch is returned, not duplicated. Raises ProviderError on failure (no
        commits, push/auth). `label` (e.g. "Preview environment") is attached
        best-effort — a missing/failed label never fails PR creation. Called by
        finalize only when HOLODECK_PR is set, so the substrate stays PR-agnostic
        by default; EKS would `gh` the same way."""
        ...

    def release(self, handle: WorkspaceHandle) -> None:
        """Tear the workspace down and free all of its resources."""
        ...

    def cancel_acquire(self, lease_id: str) -> None:
        """Stop an in-flight acquire() for lease_id, best-effort.

        Called when release() arrives for a lease that's still PENDING — no
        handle exists yet, so release() can't tear anything down itself. The
        acquire() call in progress is expected to notice and raise; whatever
        partial workspace it leaves behind is the caller's (acquire's own
        except-branch) responsibility to roll back, not this method's. A
        provider with no in-flight state for lease_id is a no-op."""
        ...

    def exec(
        self,
        handle: WorkspaceHandle,
        argv: list[str],
        *,
        service: Optional[str] = None,
        workdir: Optional[str] = None,
        timeout_s: Optional[int] = None,
        detach: bool = False,
    ) -> ExecResult:
        """Run `argv` inside the workspace, scoped to this lease. `service`
        selects the container (default: the app service from the manifest);
        `workdir` and `timeout_s` are optional. Returns exit code + captured
        stdout/stderr — the shape Omnigent's `run()` primitive expects.

        `detach=True` starts the process detached and returns immediately without
        capturing output (for long-running daemons like `omnigent host` that
        start_host backgrounds — a captured exec would block until the process
        exits and can wedge the single-worker control plane)."""
        ...

    def stream_exec(
        self,
        handle: WorkspaceHandle,
        argv: list[str],
        *,
        service: Optional[str] = None,
        workdir: Optional[str] = None,
    ) -> AsyncIterator[tuple[str, str]]:
        """Streaming exec for the WS exec gateway (the deck's `exec_url`). Yields
        ('stdout'|'stderr', chunk) frames as they arrive, then a final
        ('exit', '<code>'). An async generator; the WS route relays each frame."""
        ...

    def list_orphans(self) -> list[WorkspaceHandle]:
        """Enumerate workspaces that exist on the substrate but aren't in the
        store — used by startup reconcile (issue #4)."""
        ...
