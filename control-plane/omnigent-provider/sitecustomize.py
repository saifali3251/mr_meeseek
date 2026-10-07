"""Auto-loaded at interpreter startup (Python imports `sitecustomize` from any
sys.path entry). Ships in this wheel so a `pip install --target /opt/holodeck`
+ `PYTHONPATH=/opt/holodeck` makes it active with NO server code, command, or
config changes.

What it does: teaches the Omnigent server to accept the community `holodeck`
provider for MANAGED sandboxes. The server's built-in
`parse_sandbox_config` only allowlists in-tree providers
(modal/daytona/…), so `holodeck` is otherwise rejected and managed launch is
silently disabled. This monkeypatches that function to return a
`ManagedSandboxConfig` backed by `HolodeckSandboxLauncher` for holodeck — and
delegates every other provider (and any parse error) to the original.

Config sources it honors (first found wins):
  - a YAML `sandbox:` block: {provider: holodeck, server_url: ...}
  - or env: OMNIGENT_SANDBOX_PROVIDER=holodeck + OMNIGENT_SANDBOX_SERVER_URL
            (falls back to OMNIGENT_SERVER_URL)
The launcher itself reads HOLODECK_URL / HOLODECK_APP / HOLODECK_TOKEN from env.

Safe: guarded to holodeck only, idempotent, and a no-op if omnigent / the
launcher aren't importable (so it never breaks an unrelated interpreter).
"""

from __future__ import annotations

import contextvars
import logging
import os

log = logging.getLogger("holodeck.sitecustomize")

_HOLODECK_TOKEN_TTL_S = 7 * 24 * 3600

# Same shape as the launcher's OWN _managed_ticket_override, but defined here
# instead of imported from the wheel: target_repo has no equivalent in
# HolodeckSandboxLauncher at all (it predates target_repo existing), so there is
# no existing contextvar to reuse — this module both sets it (in
# _provision_with_ticket, below) and reads it (in the _req patch further down).
_managed_target_repo_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_holodeck_managed_target_repo_override", default=None
)


def _install_holodeck_managed() -> None:
    try:
        from omnigent.server import managed_hosts as mh
        from omnigent.community.sandbox.holodeck.launcher import HolodeckSandboxLauncher
    except Exception:
        log.info("holodeck sandbox patch: not an omnigent server env "
                  "(or provider missing) — skipping, no-op")
        return  # not an omnigent server env (or provider missing) -> no-op

    if getattr(mh, "_holodeck_patched", False):
        return
    _orig = mh.parse_sandbox_config

    def _parse(raw):
        cfg = raw if isinstance(raw, dict) else None
        provider = (cfg or {}).get("provider") or os.environ.get("OMNIGENT_SANDBOX_PROVIDER")
        if provider in ("holodeck", "meeseek"):
            server_url = (
                (cfg or {}).get("server_url")
                or os.environ.get("OMNIGENT_SANDBOX_SERVER_URL")
                or os.environ.get("OMNIGENT_SERVER_URL")
            )
            if not server_url or not str(server_url).strip():
                raise ValueError(
                    "meeseek managed sandbox needs a server_url "
                    "(sandbox.server_url, OMNIGENT_SANDBOX_SERVER_URL, or OMNIGENT_SERVER_URL) "
                    "— the public URL the provisioned workspace dials back to"
                )
            return mh.ManagedSandboxConfig(
                server_url=str(server_url).rstrip("/"),
                launcher_factory=lambda: HolodeckSandboxLauncher(),
                token_ttl_s=_HOLODECK_TOKEN_TTL_S,
                managed_launch_supported=True,
                provider="meeseek",
                host_config=(cfg or {}).get("host_config"),
            )
        return _orig(raw)

    mh.parse_sandbox_config = _parse
    mh._holodeck_patched = True
    log.info("holodeck sandbox patch: installed (parse_sandbox_config wrapped)")


def _install_holodeck_ticket_passthrough() -> None:
    """Resolve a managed session's real Jira ticket and hand it to
    HolodeckSandboxLauncher.provision() in place of Omnigent's own
    randomly-generated host name.

    Confirmed from source: managed_hosts.launch_managed_host always calls
    `launcher.provision(host_name)` where
    `host_name = f"managed-{uuid.uuid4().hex[:8]}"` — never anything derived
    from the session. The real ticket, if the caller set one, lives on the
    session's own `labels.holodeck_ticket` (same label the console/Jira-bridge
    side already uses for correlation) — but nothing threads it down into the
    provision() call today.

    Why this patches `orchestration.py`'s copy of `_provision_managed_sandbox`,
    not `helpers.py`'s (where the function is actually defined): orchestration.py
    imports it via `from .helpers import _provision_managed_sandbox` at module
    load time, which copies the name into orchestration's OWN globals. Patching
    helpers.py's attribute afterward wouldn't change what orchestration.py's
    bare `_provision_managed_sandbox(...)` call resolves to — Python looks that
    up in orchestration's own module dict at call time. Reassigning it there is
    what actually takes effect.

    A ContextVar (not a function-signature change) carries the resolved ticket
    across the async call into `HolodeckSandboxLauncher.provision()` — it
    correctly propagates through `asyncio.to_thread` (contextvars are copied
    into the new thread's context), which is how provision() actually gets
    called under this launch path. This also transparently covers BOTH the
    fresh-launch and relaunch branches, since it wraps the function that
    dispatches to either, rather than patching each separately.

    Safe: any lookup failure (missing DATABASE_URL, no such conversation, no
    such label) silently falls back to Omnigent's own generated name — this
    must never be the thing that breaks a managed launch.
    """
    try:
        from omnigent.community.sandbox.holodeck.launcher import (
            _managed_ticket_override,
        )
        import omnigent.server.routes._sessions.orchestration as _orch
    except Exception:
        log.info("holodeck ticket-passthrough: not an omnigent server env "
                  "(or orchestration module shape changed) — skipping, no-op")
        return  # not an omnigent server env (or provider missing) -> no-op

    if getattr(_orch, "_holodeck_ticket_patched", False):
        return
    _orig_provision = _orch._provision_managed_sandbox

    async def _provision_with_ticket(*, session_id, **kwargs):
        ticket = None
        target_repo = None
        try:
            from omnigent.stores.conversation_store.sqlalchemy_store import (
                SqlAlchemyConversationStore,
            )

            store = SqlAlchemyConversationStore(os.environ["DATABASE_URL"])
            conv = store.get_conversation(session_id)
            if conv is None:
                log.warning(
                    "holodeck ticket-passthrough: no conversation found for "
                    "session %s — provision() will fall back to Omnigent's "
                    "generated host name; ticket<->lease correlation will break",
                    session_id,
                )
            else:
                ticket = conv.labels.get("holodeck_ticket")
                if not ticket:
                    log.warning(
                        "holodeck ticket-passthrough: session %s has no "
                        "labels.holodeck_ticket (labels=%r) — provision() will "
                        "fall back to Omnigent's generated host name; "
                        "ticket<->lease correlation will break",
                        session_id, conv.labels,
                    )
                # Same source, same tolerance as ticket above: absent is normal
                # (a single-repo app, or a composite ticket that didn't pick one) —
                # only worth a log line, never a failure. Read here (not a separate
                # DB round-trip) since we already have `conv` for the ticket lookup.
                target_repo = conv.labels.get("holodeck_target_repo")
                if not target_repo:
                    log.info(
                        "holodeck target-repo passthrough: session %s has no "
                        "labels.holodeck_target_repo — acquire() will fall back "
                        "to the app's manifest default (HOLO_GIT_SUBDIR)",
                        session_id,
                    )
        except Exception:
            # never let this break a managed launch — but make the failure
            # visible, since a silent fallback here is what made the earlier
            # comp-4941 lease-id mismatch invisible in the first place.
            log.exception(
                "holodeck ticket-passthrough: failed to resolve the real "
                "ticket for session %s — falling back to Omnigent's "
                "generated host name", session_id,
            )
            ticket = None
            target_repo = None
        ticket_token = _managed_ticket_override.set(ticket)
        repo_token = _managed_target_repo_override.set(target_repo)
        try:
            return await _orig_provision(session_id=session_id, **kwargs)
        finally:
            _managed_ticket_override.reset(ticket_token)
            _managed_target_repo_override.reset(repo_token)

    _orch._provision_managed_sandbox = _provision_with_ticket
    _orch._holodeck_ticket_patched = True
    log.info("holodeck ticket-passthrough: installed "
              "(orchestration._provision_managed_sandbox wrapped)")


def _install_holodeck_target_repo_passthrough() -> None:
    """Inject target_repo into HolodeckSandboxLauncher.provision()'s /leases POST.

    provision() hardcodes `body={"app": self.app, "ticket": ticket}` (source: the
    0.1.4 wheel, launcher.py ~L209) — there is no parameter for target_repo, and
    unlike `ticket` there is no existing contextvar in the launcher to hang this
    off of (target_repo postdates that wheel). Patching provision() itself would
    mean duplicating its ticket validation / override-resolution logic just to
    change one line deep inside it. Wrapping `_req` instead is surgical: intercept
    exactly the one call shaped `POST /leases`, merge in target_repo when the
    override set by _provision_with_ticket (above) is non-None, and delegate
    everything else — every other verb/path this launcher uses (GET /leases/{id},
    DELETE, /exec, /files) — to the original, untouched.

    Safe: no override set (the common case — most tickets don't specify a
    target_repo) means the body is passed through byte-for-byte unchanged.
    """
    try:
        from omnigent.community.sandbox.holodeck.launcher import HolodeckSandboxLauncher
    except Exception:
        log.info("holodeck target-repo passthrough: not an omnigent server env "
                  "(or provider missing) — skipping, no-op")
        return

    if getattr(HolodeckSandboxLauncher, "_holodeck_target_repo_patched", False):
        return
    _orig_req = HolodeckSandboxLauncher._req

    def _req_with_target_repo(self, method, path, *, body=None, raw=None):
        if method == "POST" and path == "/leases" and isinstance(body, dict):
            target_repo = _managed_target_repo_override.get()
            if target_repo:
                body = {**body, "target_repo": target_repo}
        return _orig_req(self, method, path, body=body, raw=raw)

    HolodeckSandboxLauncher._req = _req_with_target_repo
    HolodeckSandboxLauncher._holodeck_target_repo_patched = True
    log.info("holodeck target-repo passthrough: installed (HolodeckSandboxLauncher._req wrapped)")


_install_holodeck_managed()
_install_holodeck_ticket_passthrough()
_install_holodeck_target_repo_passthrough()
