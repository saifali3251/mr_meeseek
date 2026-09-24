"""HolodeckSandboxLauncher — Omnigent sandbox provider backed by the Holodeck
lease API.

Omnigent (the harness) is swappable; Holodeck is the product. This launcher is
the thin adapter that lets Omnigent treat a Holodeck lease as one of its
sandboxes: every method is an HTTP call to the control plane's REST contract —
no Docker, no strike.sh here. It subclasses the *real* ExecModelHostLauncher so
capabilities are auto-derived (overriding put/terminate advertises file_copy +
programmatic_terminate) and `start_host`/`materialize_workspace` come for free.

Config via env (read by whoever runs the provider — the omnigent CLI):
  HOLODECK_URL    control-plane base URL (default http://127.0.0.1:8099)
  HOLODECK_TOKEN  shared admin token for the REST API (optional)
  HOLODECK_APP    app to strike (default "compliance")

NOTE: `start_host` (the default from ExecModelHostLauncher) backgrounds
`omnigent host` *inside* the workspace — that's gated by the "where does the
agent run" (A/B/C) decision and needs the golden. This adapter's transport
primitives (provision/run/put/terminate) work today against the control plane.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import click

from omnigent.host.identity import (HOST_ID_ENV_VAR, HOST_NAME_ENV_VAR,
                                    HOST_TOKEN_ENV_VAR)
from omnigent.onboarding.sandboxes.base import (ExecModelHostLauncher,
                                                RemoteCommandResult,
                                                render_host_config_write_command)

log = logging.getLogger("holodeck.sandbox")

# Mirror of the control plane's TICKET_RE (holodeck/models.py). Validate here so
# a bad value fails with a clear message instead of a 422 from the API.
_TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# What a Jira issue key looks like (PROJ-123). Not enforced — plenty of valid
# tickets won't match — but a mismatch is worth shouting about: see provision().
_JIRA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")

# Set by sitecustomize.py's managed-session ticket-passthrough patch, from the
# session's `labels.holodeck_ticket` — lets provision() use the REAL ticket
# instead of Omnigent's own randomly-generated "managed-<uuid8>" host name
# (confirmed from source: managed_hosts.launch_managed_host always calls
# provision(host_name) where host_name = f"managed-{uuid4().hex[:8]}", never
# anything derived from session labels). None in every other path (external
# host, CLI bootstrap, or a managed session with no holodeck_ticket label) —
# provision() then falls back to whatever it was actually called with,
# unchanged. Propagates correctly through asyncio.to_thread (contextvars are
# copied into the thread's context), which is how provision() ends up called
# from Omnigent's async launch path.
_managed_ticket_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_holodeck_managed_ticket_override", default=None
)


class HolodeckSandboxLauncher(ExecModelHostLauncher):
    # short name used in `--provider` and error messages
    provider = "holodeck"

    # capability class-vars (the rest are derived from overridden methods)
    supports_local_port_forward = False  # we expose OUTWARD (preview port), not local->sandbox
    can_resume = False  # leases are disposable; no resume-in-place
    supports_cli_bootstrap = True
    supports_managed_launch = True

    def __init__(self) -> None:
        super().__init__()
        self.base = os.environ.get("HOLODECK_URL", "http://127.0.0.1:8099").rstrip("/")
        self.token = os.environ.get("HOLODECK_TOKEN", "")
        self.app = os.environ.get("HOLODECK_APP", "compliance")
        # Which compose SERVICE inside the app's composite the agent's shell/host
        # process actually runs in — NOT the same thing as `self.app` (which
        # manifest to strike) or the manifest's own HOLO_APP_SERVICE (which is
        # tuned for readiness/finalize, e.g. compliance-ui's front-door "qong"
        # gateway, not the code container). Composite apps need this set to the
        # actual code container or run()/run_background() silently fall back to
        # HOLO_APP_SERVICE and exec into the wrong one.
        #
        # Defaults to "cpl-webserver" — the only app in scope right now
        # (compliance-ui's composite, compliance-backend bind-mounted in; see
        # manifests/compliance-ui.sh's HOLO_GIT_SUBDIR comment). Override via env
        # once a second app/service is in play; empty disables the override
        # entirely (falls back to HOLO_APP_SERVICE, correct for single-service
        # apps like plain "compliance").
        self.exec_service = os.environ.get("HOLODECK_EXEC_SERVICE", "backend")
        # Comma-separated env var NAMES to forward from THIS process's own
        # environment (the Omnigent server pod's — already populated from
        # omnigent-secrets) into the sandbox at launch time, so the runner's
        # harness (e.g. claude-sdk) can authenticate without ever baking a
        # credential into the golden image. Mirrors Omnigent's own built-in
        # `cwsandbox` provider, which supports the identical pattern via a
        # server-config `sandbox.cwsandbox.env: [...]` list (see
        # managed_hosts.py's own error message: "e.g. ['ANTHROPIC_API_KEY',
        # 'GIT_TOKEN']") — holodeck bypasses that provider's config parsing
        # entirely (sitecustomize.py intercepts before it), so we replicate
        # the same behavior here rather than the golden image needing its own
        # copy of a secret that's already managed on the Omnigent side.
        # Rotating the value in omnigent-secrets + restarting the server pod
        # is sufficient — no golden rebuild, ever, for this.
        self.forward_env = [
            v.strip() for v in os.environ.get("HOLODECK_FORWARD_ENV", "ANTHROPIC_API_KEY").split(",")
            if v.strip()
        ]

    # ---- HTTP helper -------------------------------------------------------
    def _req(self, method: str, path: str, *, body=None, raw: bytes | None = None):
        url = self.base + path
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if raw is not None:
            data = raw
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=630) as resp:
                payload = resp.read()
                if payload and resp.headers.get_content_type() == "application/json":
                    return resp.status, json.loads(payload)
                return resp.status, payload
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise click.ClickException(f"holodeck {method} {path} -> {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise click.ClickException(f"holodeck control plane unreachable at {url}: {e.reason}")

    # ---- SandboxLifecycle --------------------------------------------------
    def prepare(self) -> None:
        """Local preflight -> the control plane's substrate preflight (/readyz).

        SCOPED to self.app. Unscoped, /readyz checks every manifest on the box,
        so one app with an unbuilt golden made this raise and blocked sessions
        for apps that were perfectly healthy."""
        qs = urllib.parse.urlencode({"app": self.app}) if self.app else ""
        _, body = self._req("GET", "/readyz" + (f"?{qs}" if qs else ""))
        if isinstance(body, dict) and not body.get("ready", True):
            raise click.ClickException(
                f"holodeck substrate not ready for app '{self.app}': {body.get('problems')}"
            )

    def provision(self, ticket: str) -> str:
        """Create a lease (strike) for a JIRA TICKET; return its id as the sandbox id.

        The argument is the ticket key ("JSQ-118"), not an arbitrary sandbox
        name. That is load-bearing, not cosmetic: the control plane derives the
        lease id as holo_id(ticket) — and the workspace dir, compose project
        (ws-<id>) and git branch (agent/<id>) all follow from it. The console
        correlates a task back to its lease by recomputing holo_id(ticket), so
        if this value is anything other than the ticket, the lookup misses and
        the task pins at "provisioning" forever.

        Pass the KEY, not a browse URL — TICKET_RE rejects ':' and '/'.

        Omnigent's base class calls this parameter `name`. We name it `ticket`
        because that is what it must contain; the call site is positional, so
        the rename is transparent. If a future Omnigent passes name=... by
        keyword this raises TypeError — a loud failure, which is the right
        outcome, since a mismatched name silently breaks correlation instead.

        The warning below is the cheapest possible detector for that mismatch:
        if Omnigent hands us a session id or a generated slug rather than the
        ticket, it shows up in the pod log the first time a real session runs.

        For managed sessions specifically, `ticket` here is Omnigent's own
        `f"managed-{uuid4().hex[:8]}"` host name, NOT the real ticket — see
        `_managed_ticket_override` above. If sitecustomize.py's ticket
        passthrough resolved a real one from the session's
        `labels.holodeck_ticket`, prefer it over whatever we were actually
        called with.
        """
        override = _managed_ticket_override.get()
        if override and _TICKET_RE.match(override):
            if override != ticket:
                log.info(
                    "holodeck: provision() using the real ticket %r from "
                    "session labels in place of Omnigent's generated name %r",
                    override, ticket,
                )
            ticket = override
        if not _TICKET_RE.match(ticket or ""):
            raise click.ClickException(
                f"holodeck: {ticket!r} is not a valid ticket id. Pass the Jira KEY "
                "(e.g. 'JSQ-118'), not a URL or a generated sandbox name."
            )
        if not _JIRA_KEY_RE.match(ticket):
            log.warning(
                "holodeck: provision(%r) does not look like a Jira key — if this "
                "came from Omnigent rather than the console, ticket->lease "
                "correlation will not work. Expected e.g. 'JSQ-118'.", ticket
            )
        _, body = self._req("POST", "/leases", body={"app": self.app, "ticket": ticket})
        lease_id = body["lease_id"]
        # The control plane strikes the workspace in the BACKGROUND and returns
        # immediately — `pending` (striking now) or `queued` (waiting on a
        # concurrency/capacity slot if too many strikes are already running; see
        # LeaseService._advance_queue) — so a minutes-long or backlogged strike
        # never holds its single request worker. Poll until READY before
        # returning: Omnigent next calls start_host, which execs into the
        # workspace and needs it live. A sync control plane returns READY on
        # the first response and exits immediately.
        #
        # 180s, not Omnigent's own full launch budget (~240s covers provision +
        # start_host + host registration together — see the design notes) —
        # deliberately shorter so THIS call fails and cleans up on its own terms
        # before Omnigent's outer timeout abandons the attempt without ever
        # giving us the chance to. On timeout, destroy the lease rather than
        # leaving a half-provisioned (or still-queued) workspace behind for a
        # retry to collide with.
        deadline = time.monotonic() + 180
        while True:
            status = (body.get("status") or "").lower()
            if status == "ready":
                return lease_id
            if status in ("failed", "released"):
                raise click.ClickException(
                    f"holodeck lease {lease_id} provisioning {status}: {body.get('error') or ''}")
            if time.monotonic() >= deadline:
                self._cleanup_stuck_lease(lease_id, status)
                raise click.ClickException(
                    f"holodeck lease {lease_id} not READY after 180s (status={status!r}); "
                    "destroyed for a clean retry")
            time.sleep(3)
            _, body = self._req("GET", f"/leases/{lease_id}")

    def _cleanup_stuck_lease(self, lease_id: str, last_status: str) -> None:
        """Best-effort teardown for a lease that never reached READY in time —
        never let a cleanup failure mask the original timeout as the reported
        error; that's the actionable one."""
        try:
            self.terminate(lease_id)
            log.warning(
                "holodeck: destroyed lease %s after it failed to reach READY "
                "in time (last status=%r)", lease_id, last_status,
            )
        except Exception:
            log.exception(
                "holodeck: cleanup of stuck lease %s also failed — may need "
                "manual destroy.sh", lease_id,
            )

    def terminate(self, sandbox_id: str) -> None:
        """Release the lease (destroy the workspace, free the port)."""
        self._req("DELETE", f"/leases/{sandbox_id}")

    # ---- SandboxExecTransport ---------------------------------------------
    def run(self, sandbox_id: str, command: str, *, check: bool = True) -> RemoteCommandResult:
        """Run a command in the workspace. Omnigent passes a shell command
        string, so we exec it under `sh -lc` (a list -> our API runs argv with
        shell semantics via sh, not a naive split)."""
        body_req = {"cmd": ["sh", "-lc", command]}
        if self.exec_service:
            body_req["service"] = self.exec_service
        _, body = self._req(
            "POST", f"/leases/{sandbox_id}/exec",
            body=body_req,
        )
        result = RemoteCommandResult(
            returncode=body["exit_code"], stdout=body["stdout"], stderr=body["stderr"]
        )
        if check and result.returncode != 0:
            raise click.ClickException(
                f"command failed (rc={result.returncode}): {command}\n{result.stderr[:500]}"
            )
        return result

    def put(self, sandbox_id: str, local_path: Path, remote_path: str) -> None:
        """Copy a local file into the workspace (Omnigent ships wheels/config
        this way before start_host)."""
        data = Path(local_path).read_bytes()
        qs_params = {"path": remote_path}
        if self.exec_service:
            qs_params["service"] = self.exec_service
        qs = urllib.parse.urlencode(qs_params)
        self._req("PUT", f"/leases/{sandbox_id}/files?{qs}", raw=data)

    def run_background(
        self, sandbox_id: str, command: str, *, log_path: str = "/tmp/omnigent-host.log"
    ) -> RemoteCommandResult:
        """Launch a long-running process (start_host's `omnigent host`) DETACHED.

        The base default backgrounds via `setsid nohup … &` over an ordinary exec
        — but our control plane's exec captures output and waits, so a daemon that
        never exits blocks the call (and, single-worker, wedges the whole control
        plane). We instead ask the lease API to start it detached
        (`docker compose exec -d`), which returns immediately. Output is redirected
        to `log_path` inside the workspace so `omnigent host` failures are
        inspectable there. Env-prefixed commands (`ENV=val omnigent host …`) are
        re-parsed under `sh -lc` so the assignments apply.

        Also prefixes `self.forward_env`'s names, read live from THIS process's
        own environment (see __init__) — `omnigent host` is a long-running
        daemon that later spawns the actual runner as its own child, so
        anything in its env here is what the harness sees too. Skips any name
        that's unset/empty in our own env rather than exporting an empty
        assignment."""
        prefix = "".join(
            f"{name}={shlex.quote(value)} "
            for name in self.forward_env
            if (value := os.environ.get(name))
        )
        body_req = {"cmd": ["sh", "-lc", f"{prefix}{command} > {log_path} 2>&1 < /dev/null"],
                    "detach": True}
        if self.exec_service:
            body_req["service"] = self.exec_service
        self._req(
            "POST", f"/leases/{sandbox_id}/exec",
            body=body_req,
        )
        return RemoteCommandResult(returncode=0, stdout="launched\n", stderr="")

    # ---- SandboxHostLauncher ------------------------------------------------
    def start_host(
        self,
        sandbox_id: str,
        *,
        token: str,
        host_id: str,
        host_name: str,
        server_url: str,
        repo_url: str | None = None,
        repo_branch: str | None = None,
        repo_name: str | None = None,
        host_config: dict[str, object] | None = None,
        on_stage=None,
    ) -> str:
        """Copy of ExecModelHostLauncher.start_host (base class) with ONE
        change: materialize_workspace() is called UNCONDITIONALLY instead of
        only `if repo_url is not None`.

        Why: the base class only calls materialize_workspace() when a caller
        supplies a repo_url on the session-create request (SessionCreateRequest
        .workspace, per its own docstring, optionally a git URL like
        "https://github.com/org/repo#branch" for host_type=managed) — which
        we deliberately never want callers to have to set. The whole point of
        the holodeck provider is that strike.sh already CoW-cloned a warm,
        already-migrated, already-seeded checkout from the golden at a KNOWN
        git HEAD; actually cloning fresh from GitHub at session-launch time
        would fetch whatever's on the remote's default branch right now,
        which can drift from what the golden was built against and produce a
        checkout the golden's own migration/seed state doesn't correspond to
        — exactly the mismatch we don't want. So: always call
        materialize_workspace() (our override below, which ignores whatever
        repo_url/branch/name it's given and returns "/app" — the CoW-cloned
        checkout already on disk from strike time) regardless of what, if
        anything, the caller passed — no request field, no GitHub reachability,
        ever required for this to resolve correctly.

        Everything else here is unchanged from the base implementation —
        this is a full copy specifically to remove that one `if`, not a
        reimplementation of the $HOME probe / host_config / run_background
        sequencing, which we still rely on being exactly what Omnigent does
        for every other provider."""
        home = self.run(sandbox_id, 'printf %s "$HOME"').stdout.strip()
        if not home:
            raise click.ClickException(
                f"could not resolve $HOME inside sandbox '{sandbox_id}' — "
                "the configured image must provide a usable shell environment"
            )
        workspace = f"{home}/workspace"
        self.run(sandbox_id, f"mkdir -p {shlex.quote(workspace)}")
        workspace = self.materialize_workspace(
            sandbox_id,
            workspace=workspace,
            repo_url=repo_url,
            repo_branch=repo_branch,
            repo_name=repo_name,
            on_stage=on_stage,
        )
        if on_stage is not None:
            on_stage("starting")
        if host_config is not None or self.capabilities.resume_stopped:
            self.run(sandbox_id, render_host_config_write_command(host_config or {}))
        env_prefix = " ".join(
            f"{key}={shlex.quote(value)}"
            for key, value in (
                (HOST_TOKEN_ENV_VAR, token),
                (HOST_ID_ENV_VAR, host_id),
                (HOST_NAME_ENV_VAR, host_name),
            )
        )
        self.run_background(
            sandbox_id,
            f"{env_prefix} omnigent host --server {shlex.quote(server_url)}",
        )
        return workspace

    def materialize_workspace(
        self,
        sandbox_id: str,
        *,
        workspace: str,
        repo_url: str | None,
        repo_branch: str | None,
        repo_name: str | None,
        on_stage=None,
    ) -> str:
        """The lease is already the seeded, bind-mounted checkout strike.sh
        produced — skip the base class's default git-clone-into-workspace and
        point straight at it instead of cloning a second, unseeded copy.
        `repo_url` is typed Optional here (the base class's own signature
        says non-Optional, but start_host above now always calls this even
        when the caller supplied none) — ignored either way, so it makes no
        functional difference, just an honest signature."""
        return "/app"
