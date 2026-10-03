"""Provider-agnostic data models for the Holodeck lease API.

These types are the seam between the HTTP layer and any WorkspaceProvider
(Compose today, EKS later). Nothing here knows about Docker or Kubernetes.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---- input validation (issue #1: untrusted input flows into shell scripts) ----
#
# `app` reaches `source manifests/$HOLO_APP.sh` and `ticket` reaches strike.sh /
# destroy.sh, which escalate to `sudo rm -rf`. An unvalidated value here is a
# remote-root primitive. Validation lives at the model boundary so no code path
# can construct a request that skipped it.
TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def holo_id(ticket: str) -> str:
    """Mirror of lib.sh holo_id: 'HACK-118' -> 'hack-118'.

    Must match the shell exactly, because the workspace dir / compose project
    are derived from it and we use it to detect ticket->id collisions (issue #7).
    Shell: tr '[:upper:] /' '[:lower:]--' | tr -cd 'a-z0-9-'
    """
    lowered = ticket.lower()
    mapped = "".join("-" if c in (" ", "/") else c for c in lowered)
    # keep only [a-z0-9-] (ASCII), mirroring `tr -cd 'a-z0-9-'` exactly.
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"
    return "".join(c for c in mapped if c in allowed)


def diff_stat(diff: str) -> str:
    """Real content added/removed line counts, e.g. "+15 -0" — matches what a
    PR page's own diff stat shows. A raw newline count over-counts: it also
    tallies `diff --git`/`index`/`@@` hunk-header lines and unchanged context
    lines, which is confusing next to GitHub's own "+15" (evidence for one PR
    showed "26 diff lines" here vs "+15" there — same diff, different metric)."""
    added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    return f"+{added} -{removed}"


class LeaseStatus(str, Enum):
    PENDING = "pending"
    # Created (port + record exist) but its strike hasn't started yet — waiting
    # either for total-workspace capacity (max_leases) or a concurrent-strike
    # slot (max_concurrent_strikes) to free up. See LeaseService._advance_queue.
    QUEUED = "queued"
    READY = "ready"
    FAILED = "failed"
    RELEASED = "released"


@dataclass
class WorkspaceHandle:
    """Opaque, substrate-specific pointer to a live workspace.

    ComposeProvider fills compose_project / ws_dir; EksProvider would fill
    namespace / pod instead. The API stores it verbatim and never introspects
    substrate fields.
    """

    lease_id: str
    app: str
    ticket: str
    preview_port: Optional[int] = None
    # Compose substrate:
    compose_project: Optional[str] = None
    ws_dir: Optional[str] = None
    # captured at acquire time from the golden checkout HEAD (for golden_head..HEAD diff)
    golden_head: Optional[str] = None
    # Which of a composite's repos this lease's branch/diff/PR operate on — validated
    # against the app's HOLO_COMPOSITE_REPOS at acquire time. None for single-repo apps
    # (falls back to the manifest's static HOLO_GIT_SUBDIR) or a composite lease
    # acquired without picking one.
    target_repo: Optional[str] = None
    # For composite apps: map of repo -> git ref/branch to checkout as dependency
    base_overrides: Optional[dict[str, str]] = None
    # seeded-row count captured once at strike (HOLO_SEED_PROOF_SQL) — a live status
    # signal shown on the console without a manual finalize. Best-effort: None if the
    # query didn't run. Does NOT gate provisioning.
    seed_rows: Optional[int] = None
    # EKS substrate (reserved):
    namespace: Optional[str] = None
    pod: Optional[str] = None


@dataclass
class ExecResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class ProviderCapabilities:
    """What a substrate can do. Substrate-truthful (not harness-shaped): the
    Omnigent adapter maps these to `SandboxCapabilities`, adding launch-mode
    flags (cli_bootstrap/managed_launch) that are the adapter's concern."""

    file_copy: bool  # put() — copy a file into the workspace
    one_shot_exec: bool  # POST /exec
    streaming_exec: bool  # WS /exec gateway
    programmatic_terminate: bool  # release()
    preview_port: bool  # exposes an outward host port
    resume_stopped: bool = False  # can resume a stopped workspace in place


@dataclass
class Evidence:
    """The notary bundle. Every field is re-derived host-side at finalize time;
    nothing here is taken from anything the agent wrote."""

    readiness: str  # raw readiness-endpoint body, or "" if not answering
    readiness_ok: bool
    seed_rows: Optional[int]  # HOLO_SEED_PROOF_SQL count, or None if the query failed
    test_cmd: Optional[str]  # the manifest/ticket test actually run (agent never supplies)
    test_exit: Optional[int]
    test_output: str
    test_timed_out: bool
    diff: str  # git diff golden_head..HEAD
    golden_head: Optional[str]
    schema_rev: Optional[str]  # alembic/migration head, freshness stamp
    services_booted: list[str] = field(default_factory=list)
    services_absent: list[str] = field(default_factory=list)
    finalized_at: float = field(default_factory=time.time)
    # E2: the draft PR opened at finalize when HOLODECK_PR is enabled (else None).
    pr_url: Optional[str] = None
    # Set only when the plain golden_head..HEAD diff came back empty and
    # ComposeProvider._recover_stray_branch had something to say about why (either
    # it found and used a stray branch instead, or found an ambiguous multi-branch
    # situation it refused to guess at) — see that method's own docstring. None
    # means the plain diff was trusted as-is, nothing anomalous detected.
    branch_note: Optional[str] = None
    # Phase 4 guardrails: Blast radius audit and AST test integrity verification
    guardrail_passed: bool = True
    guardrail_reason: Optional[str] = None


@dataclass
class Lease:
    lease_id: str
    app: str
    ticket: str
    status: LeaseStatus
    handle: Optional[WorkspaceHandle] = None
    preview_port: Optional[int] = None
    # test command resolved from the TICKET at acquire time (optional). The
    # finalize endpoint has NO field for this — an agent physically cannot
    # supply a command (issue: confirmation #2 / D1 made structural).
    ticket_test_cmd: Optional[str] = None
    # Validated against the app's HOLO_COMPOSITE_REPOS at acquire time (service.py),
    # then read directly off the lease by _strike() when it calls provider.acquire() —
    # not threaded through _start_strike/_strike_safe/_advance_queue as an extra
    # parameter, since it's already available here wherever `lease` already is.
    target_repo: Optional[str] = None
    base_overrides: Optional[dict[str, str]] = None
    evidence: Optional[Evidence] = None
    error: Optional[str] = None
    # per-lease capability token (deck's `teardown_token`): authorizes the
    # scoped exec-gateway WebSocket for THIS lease only. Minted at acquire.
    token: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at
