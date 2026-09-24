"""The TrialRunner seam — docs/AUTOMATIC_ONBOARDING.md §2's "trial-build
sandbox", as a protocol with a Fake and a real implementation, the same shape
as providers/base.py's WorkspaceProvider. The onboarding service is written
against this protocol only, never against golden-build.sh directly, for the
same reason the lease service never calls strike.sh directly: swap the
implementation without touching the caller.

FakeTrialRunner is what the test suite and any Docker-less demo use.
ScriptTrialRunner is real — it shells out to the actual golden-build.sh in a
throwaway HOLO_ROOT — but needs Docker + a reachable repo, so it isn't
exercised by the unit tests here (mirrors ComposeProvider vs FakeProvider).
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from holodeck.onboarding.models import OnboardingRequest
from holodeck.onboarding.render import render_manifest_sh


@dataclass
class TrialResult:
    passed: bool
    log: str
    error: Optional[str] = None


class TrialRunner(Protocol):
    def run(self, req: OnboardingRequest, repo_root: Path) -> TrialResult: ...


class FakeTrialRunner:
    """Deterministic, no Docker. `fail_for` lets a test/demo force a specific
    request through the failure path without needing a real broken build."""

    def __init__(self) -> None:
        self.fail_for: set[str] = set()

    def run(self, req: OnboardingRequest, repo_root: Path) -> TrialResult:
        if req.request_id in self.fail_for:
            return TrialResult(passed=False, error="readiness_timeout",
                               log="fake trial: build ok, but /readiness never went green")
        return TrialResult(passed=True,
                           log="fake trial: build ok · readiness ok · seed-proof rows=42")


class ScriptTrialRunner:
    """Real trial build: writes the drafted manifest into a scratch
    manifests/ dir, points HOLO_SRC at the repo checkout, and runs
    golden-build.sh + strike.sh against a throwaway HOLO_ROOT — never the
    real fleet's manifests/ or golden images. Requires Docker on the host;
    every failure (missing scripts, build/migrate/seed error, readiness
    timeout) comes back as TrialResult(passed=False, ...) rather than raising,
    so a bad draft is just something to edit and retry, not a 500.
    """

    def __init__(self, holo_dir: Path, scratch_root: Optional[Path] = None) -> None:
        self.holo_dir = holo_dir
        self.scratch_root = scratch_root or (holo_dir.parent / "holodeck-onboarding-trials")

    def run(self, req: OnboardingRequest, repo_root: Path) -> TrialResult:
        scratch = Path(tempfile.mkdtemp(prefix=f"trial-{req.app_name}-", dir=self.scratch_root))
        manifest_path = scratch / "manifests" / f"{req.app_name}.sh"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(render_manifest_sh(req))
        # a scratch HOLO_DIR needs scripts/ too — reflink/symlink the real ones in,
        # never copy the real manifests/ dir (this must stay isolated from it).
        (scratch / "scripts").symlink_to((self.holo_dir / "scripts").resolve())

        env_root = scratch / "holo-root"
        env_root.mkdir(exist_ok=True)
        cmd = [
            "bash", str(self.holo_dir / "scripts" / "golden-build.compliance.sh"), "--yes",
        ]
        env = {
            "HOLO_APP": req.app_name,
            "HOLO_DIR": str(scratch),
            "HOLO_ROOT": str(env_root),
            "HOLO_SRC": str(repo_root),
        }
        try:
            proc = subprocess.run(cmd, cwd=scratch, env={**_inherit_path(), **env},
                                  capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired as e:
            return TrialResult(passed=False, error="timeout",
                               log=(e.stdout or "") + "\n[trial build timed out after 15m]")
        log = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return TrialResult(passed=False, error=f"golden-build exited {proc.returncode}", log=log)
        return TrialResult(passed=True, log=log)


def _inherit_path() -> dict:
    import os
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
