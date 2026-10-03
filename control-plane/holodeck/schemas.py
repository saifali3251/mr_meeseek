"""HTTP request/response schemas (Pydantic).

Note what is DELIBERATELY ABSENT: neither AcquireRequest nor any finalize
request carries a test-command field. The test comes from the manifest
(HOLO_TEST_CMD), optionally overridden by a value derived from the ticket at
acquire time — an agent physically cannot supply a command to run. An absent
field is a property; an allowlist is a rule someone relaxes (confirmation #2).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from holodeck.models import TICKET_RE


class FinalizeRequest(BaseModel):
    """Optional PR title/body cosmetics only — never touches readiness/tests/
    diff, which stay 100% host-derived (service.py:LeaseService.finalize).
    Omitting the whole body (or any individual field) falls back to the
    original evidence-only PR exactly as before this existed."""
    agent_summary: Optional[str] = Field(
        None, description="agent's own final status narrative (already Jira-posted); "
                          "used as the PR body's implementation description")
    ticket_summary: Optional[str] = Field(
        None, description="the Jira issue's summary/title field; used as the PR title's <desc>")
    issue_type: Optional[str] = Field(
        None, description="the Jira issue's type name (Bug/Task/Story/Epic/...); "
                          "maps to the PR title's conventional-commit prefix")


class AcquireRequest(BaseModel):
    app: str = Field(..., description="app name; must match a manifests/<app>.sh on disk")
    ticket: str = Field(..., description="ticket id, e.g. CPL-1")
    preview: Optional[int] = Field(None, description="explicit preview port; omit to auto-allocate")
    ttl_s: Optional[int] = Field(None, ge=1, description="lease lifetime seconds; omits -> default")
    target_repo: Optional[str] = Field(
        None, description="for a composite app: which of its HOLO_COMPOSITE_REPOS this "
                          "lease's branch/diff/PR operate on; omit to fall back to the "
                          "manifest's static HOLO_GIT_SUBDIR")
    base_overrides: Optional[dict[str, str]] = Field(
        None, description="for a composite app: map of repo_name -> git ref/branch to checkout "
                          "for upstream dependencies (e.g. {'test_backend': 'agent/fsa-10'})")

    @field_validator("ticket")
    @classmethod
    def _ticket_ok(cls, v: str) -> str:
        if not TICKET_RE.match(v):
            raise ValueError("ticket must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
        return v

    # `app` is validated against the on-disk allowlist in the route (it needs
    # Config); the regex here is a cheap first gate against path traversal.
    @field_validator("app")
    @classmethod
    def _app_shape(cls, v: str) -> str:
        if not TICKET_RE.match(v):
            raise ValueError("app has an invalid shape")
        return v

    # target_repo's REAL validation (against the app's HOLO_COMPOSITE_REPOS) needs
    # Config/the provider and happens in the route, same as `app` — this is just the
    # same cheap shape gate, so an unvalidated value never reaches a shell either.
    @field_validator("target_repo")
    @classmethod
    def _target_repo_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not TICKET_RE.match(v):
            raise ValueError("target_repo has an invalid shape")
        return v


class ExtendRequest(BaseModel):
    ttl_s: int = Field(..., ge=1)


class ExecRequest(BaseModel):
    """B3 exec. `cmd` is a list (argv, shell=False) or a string (shlex-split at
    the route). NOTE: this is exec-INTO-the-workspace, not a test command — it's
    the agent's own command, distinct from the notary's finalize test."""

    cmd: list[str] | str = Field(..., description="argv list, or a string that is shlex-split")
    service: Optional[str] = Field(None, description="compose service to exec into; default is the app service")
    workdir: Optional[str] = Field(None, description="working directory inside the container")
    timeout_s: Optional[int] = Field(None, ge=1, description="per-exec wall-clock cap; omit -> server default")
    detach: bool = Field(False, description="fire-and-forget: start the process detached (docker compose exec -d) "
                         "and return immediately. For launching long-running daemons (e.g. `omnigent host`) — "
                         "a normal exec would block on captured output and can wedge the single worker.")


class ExecResponse(BaseModel):
    lease_id: str
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str


class LeaseResponse(BaseModel):
    lease_id: str
    app: str
    ticket: str
    status: str
    preview_port: Optional[int] = None
    compose_project: Optional[str] = None
    ws_dir: Optional[str] = None
    golden_head: Optional[str] = None
    target_repo: Optional[str] = None
    base_overrides: Optional[dict[str, str]] = None
    # exec-gateway handle (deck's exec_url + teardown_token): connect a WebSocket
    # to exec_url and present `token` to run scoped commands in this lease.
    exec_url: Optional[str] = None
    token: Optional[str] = None
    expires_at: float
    error: Optional[str] = None


class EvidenceResponse(BaseModel):
    lease_id: str
    readiness: str
    readiness_ok: bool
    seed_rows: Optional[int]
    test_cmd: Optional[str]
    test_exit: Optional[int]
    test_output: str
    test_timed_out: bool
    diff: str
    golden_head: Optional[str]
    schema_rev: Optional[str]
    services_booted: list[str]
    services_absent: list[str]
    finalized_at: float
    pr_url: Optional[str] = None
    branch_note: Optional[str] = None
    guardrail_passed: bool = True
    guardrail_reason: Optional[str] = None


class LeaseListResponse(BaseModel):
    """B11: list live workspaces (feeds the Console). Released leases are
    excluded by default so the list is the operator's 'what's live right now'."""

    leases: list[LeaseResponse]


class EnvironmentInfo(BaseModel):
    app: str
    ready: bool  # golden built + substrate preflight clean
    problems: list[str] = []
    active_leases: int = 0
    target_repos: list[str] = []  # composite repo names; empty for a single-repo app


class EnvironmentsResponse(BaseModel):
    """B11: list goldens + status (feeds the Console)."""

    provider: str
    environments: list[EnvironmentInfo]


class HealthResponse(BaseModel):
    status: str
    provider: str
    active_leases: int
    max_leases: int


class ReadyResponse(BaseModel):
    ready: bool
    provider: str
    problems: list[str] = []


class CapabilitiesResponse(BaseModel):
    provider: str
    file_copy: bool
    one_shot_exec: bool
    streaming_exec: bool
    programmatic_terminate: bool
    preview_port: bool
    resume_stopped: bool
