"""In-memory fake provider — lets the API/store/reaper/finalize logic be tested
with no Docker, no scripts, no box. Not used in production."""

from __future__ import annotations

import threading
from typing import AsyncIterator, Optional

from holodeck.models import (Evidence, ExecResult, ProviderCapabilities,
                             WorkspaceHandle)
from holodeck.providers.base import WorkspaceExistsError


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self._live: dict[str, WorkspaceHandle] = {}
        self._files: dict[tuple[str, str], bytes] = {}  # (lease_id, path) -> bytes
        # test knobs
        self.manifest_test_cmd: Optional[str] = None  # what test_cmd(app) returns
        self.raise_exists_for: set[str] = set()
        self.acquire_error: Optional[Exception] = None
        # If set, acquire() blocks until the test sets this Event — lets a test
        # deterministically hold a "strike" in progress (e.g. to exercise
        # max_concurrent_strikes queueing) without racing real wall-clock timing.
        self.acquire_block: Optional[threading.Event] = None
        # test knob: app -> allowed target_repo values (mirrors HOLO_COMPOSITE_REPOS).
        # Empty/absent = single-repo app, same as a real manifest with nothing set.
        self.composite_repos: dict[str, set[str]] = {}

    def acquire(
        self, lease_id: str, app: str, ticket: str, preview_port: Optional[int],
        target_repo: Optional[str] = None,
        base_overrides: Optional[dict[str, str]] = None,
    ) -> WorkspaceHandle:
        if self.acquire_block is not None:
            self.acquire_block.wait()
        if self.acquire_error is not None:
            raise self.acquire_error
        if lease_id in self.raise_exists_for or lease_id in self._live:
            raise WorkspaceExistsError(f"workspace '{lease_id}' already exists")
        h = WorkspaceHandle(
            lease_id=lease_id,
            app=app,
            ticket=ticket,
            preview_port=preview_port,
            compose_project=f"ws-{lease_id}",
            ws_dir=f"/fake/ws/{lease_id}",
            golden_head="deadbeef",
            seed_rows=42,
            target_repo=target_repo,
            base_overrides=base_overrides,
        )
        self._live[lease_id] = h
        return h

    def valid_target_repos(self, app: str) -> set[str]:
        return set(self.composite_repos.get(app, set()))

    def finalize(self, handle: WorkspaceHandle, test_cmd: Optional[str]) -> Evidence:
        guardrail_passed = getattr(self, "_guardrail_passed", True)
        guardrail_reason = getattr(self, "_guardrail_reason", None)
        return Evidence(
            readiness="OK",
            readiness_ok=True,
            seed_rows=42,
            test_cmd=test_cmd,
            test_exit=0 if test_cmd else None,
            test_output="fake test passed" if test_cmd else "",
            test_timed_out=False,
            diff="fake diff",
            golden_head=handle.golden_head,
            schema_rev="head",
            services_booted=["webserver", "db"],
            services_absent=[],
            guardrail_passed=guardrail_passed,
            guardrail_reason=guardrail_reason,
        )

    def open_pr(self, handle: WorkspaceHandle, *, base: str, draft: bool,
                title: str, body: str, label: Optional[str] = None) -> str:
        # deterministic within a run; no network. Records the last body for assertions.
        self.last_pr = {"branch": f"agent/{handle.lease_id}", "base": base,
                        "draft": draft, "title": title, "body": body, "label": label}
        return f"https://github.com/hackathon-org/{handle.app}/pull/{(abs(hash(handle.lease_id)) % 900) + 100}"

    def release(self, handle: WorkspaceHandle) -> None:
        self._live.pop(handle.lease_id, None)

    def cancel_acquire(self, lease_id: str) -> None:
        # Unblock a test-held acquire() so it can observe the cancellation and
        # return/raise on its own, same as the real provider's killed subprocess.
        if self.acquire_block is not None:
            self.acquire_block.set()
        self._live.pop(lease_id, None)

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
        return ExecResult(
            argv=argv,
            exit_code=0,
            stdout="detached" if detach else f"fake exec[{service or 'app'}]: {' '.join(argv)}",
            stderr="",
        )

    async def stream_exec(
        self,
        handle: WorkspaceHandle,
        argv: list[str],
        *,
        service: Optional[str] = None,
        workdir: Optional[str] = None,
    ) -> AsyncIterator[tuple[str, str]]:
        yield ("stdout", f"fake stream[{service or 'app'}]: {' '.join(argv)}\n")
        yield ("stdout", "line 1\n")
        yield ("exit", "0")

    def put(
        self,
        handle: WorkspaceHandle,
        remote_path: str,
        content: bytes,
        *,
        service: Optional[str] = None,
    ) -> None:
        self._files[(handle.lease_id, remote_path)] = content

    def test_cmd(self, app: str, target_repo: Optional[str] = None) -> Optional[str]:
        if target_repo and hasattr(self, "manifest_test_cmd_by_repo"):
            repo_cmd = getattr(self, "manifest_test_cmd_by_repo", {}).get(target_repo)
            if repo_cmd:
                return repo_cmd
        return self.manifest_test_cmd

    def prepare(self, app: Optional[str] = None) -> list[str]:
        return []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            file_copy=True, one_shot_exec=True, streaming_exec=True,
            programmatic_terminate=True, preview_port=True, resume_stopped=False,
        )

    def list_orphans(self) -> list[WorkspaceHandle]:
        return []
