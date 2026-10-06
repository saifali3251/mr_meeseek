"""Data shapes for an onboarding request. Mirrors the manifest contract in
../../manifests/README.md field-for-field, so a published draft is literally
that contract with values filled in — nothing new to learn on the review
screen or in the generated manifest file.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# The manifest fields recon can draft, in the same order as manifests/README.md's
# contract table. HOLO_APP/HOLO_GIT_SUBDIR are derived separately (app name, and
# only set for composites), not drafted here.
MANIFEST_FIELDS = (
    "HOLO_COMPOSE_FILE", "WS_SERVICES", "HOLO_PORT_SERVICES",
    "HOLO_APP_SERVICE", "HOLO_APP_PORT",
    "HOLO_PG_SERVICE", "HOLO_PG_USER", "HOLO_PG_DB",
    "HOLO_READINESS_PATH", "HOLO_READINESS_SCHEME", "HOLO_SEED_PROOF_SQL",
    "HOLO_PGDATA_MODE", "HOLO_PGDATA_OVERRIDE",
    "HOLO_MIGRATE_CMD", "HOLO_SEED_CMD",
    "HOLO_BUILD_CMD", "HOLO_TOKEN_CMD",
)

# Fields that run an arbitrary command against a real database. Per
# docs/AUTOMATIC_ONBOARDING.md §4 these NEVER auto-approve, in either the Easy
# or Hard Way — a human always reads the literal command before it can run.
DESTRUCTIVE_FIELDS = ("HOLO_MIGRATE_CMD", "HOLO_SEED_CMD")

REQUEST_ID_RE_PREFIX = "onb-"


def new_request_id() -> str:
    return REQUEST_ID_RE_PREFIX + secrets.token_hex(4)


class OnboardingStatus(str, Enum):
    DRAFT = "draft"                    # recon ran; team is reviewing/editing
    TRIAL_RUNNING = "trial_running"
    TRIAL_PASSED = "trial_passed"
    TRIAL_FAILED = "trial_failed"
    PENDING_APPROVAL = "pending_approval"   # team submitted; awaiting platform review
    REJECTED = "rejected"               # platform team sent it back with a reason
    PUBLISHED = "published"             # manifest written, app registered + team-owned


@dataclass
class RepoSpec:
    """One repo in the app's dependency graph (docs/AUTOMATIC_ONBOARDING.md §1).
    A single-repo app is one RepoSpec with no depends_on."""

    name: str
    url: str
    branch: str = "main"
    role: str = "app"          # app | db-owner | gateway | frontend | worker
    test_cmd: Optional[str] = None  # Automated test / validation command
    depends_on: list[str] = field(default_factory=list)
    via: Optional[str] = None       # rest_api | graphql | module_federation | grpc
    env_var: Optional[str] = None   # how the dependency is wired at runtime


@dataclass
class ManifestField:
    """One drafted manifest field, carrying provenance so the review screen can
    show 'inferred from compose.yaml' vs 'needs your input' instead of a bare
    value the team has no reason to trust or distrust."""

    value: Optional[str]
    source: str                 # e.g. "inferred:compose.yaml" | "needs_review" | "team_edit"
    confidence: str = "medium"  # low | medium | high — a hint for the reviewer, not a gate


@dataclass
class OnboardingRequest:
    request_id: str
    team_slug: str
    app_name: str
    contact: str
    repos: list[RepoSpec]
    status: OnboardingStatus = OnboardingStatus.DRAFT
    manifest: dict[str, ManifestField] = field(default_factory=dict)
    trial_log: str = ""
    trial_error: Optional[str] = None
    reject_reason: Optional[str] = None
    jira_project: Optional[str] = None
    test_cmd: Optional[str] = None
    preview_port: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def is_composite(self) -> bool:
        return len(self.repos) > 1

    def has_unreviewed_destructive_fields(self) -> bool:
        """True until every destructive field has been touched by a human (its
        source is no longer a bare recon guess). Blocks trial/publish — see
        OnboardingService — so a guessed HOLO_SEED_CMD can never run unseen."""
        for name in DESTRUCTIVE_FIELDS:
            f = self.manifest.get(name)
            if f is None or f.source == "needs_review":
                return True
        return False
