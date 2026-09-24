#!/usr/bin/env python
"""Launch the Omnigent server with the community `holodeck` provider wired in as
a MANAGED sandbox — so `POST /v1/sessions {host_type:"managed"}` provisions a
Holodeck workspace.

Why this exists: the server builds its managed-sandbox config via
`omnigent.server.managed_hosts.parse_sandbox_config(cfg["sandbox"])`, which only
recognizes omnigent's BUILT-IN providers (modal/daytona/…). `holodeck` is a
community provider, so the plain `sandbox:` YAML resolves to an unsupported
launcher. The supported extension (per that module's docstring) is to construct a
`ManagedSandboxConfig` directly with our launcher factory.

Rather than edit installed site-packages, this wrapper MONKEYPATCHES
`parse_sandbox_config` before handing off to the normal CLI. `parse_sandbox_config`
is imported lazily *inside* the server command (omnigent/cli.py), so patching the
module attribute here is picked up at call time. Upgrade-safe: nothing under
site-packages is modified.

Run it exactly like `omnigent`, e.g.:
    HOLODECK_URL=http://<control-plane-host>:8099 \
    HOLODECK_APP=compliance \
    python holodeck_server.py server --config /etc/omnigent/config.yaml

with config.yaml containing:
    sandbox:
      provider: holodeck
      server_url: https://<this-omnigent-server-public-url>   # workspace dials back here

The launcher reads HOLODECK_URL / HOLODECK_APP / HOLODECK_TOKEN from the env (the
same three the wheel install documents).
"""

from __future__ import annotations

import sys

# holodeck launch-token lifetime: leases are disposable and short-lived, but keep
# the token comfortably above any single session so a live workspace can
# re-authenticate its host tunnel across reconnects.
_HOLODECK_TOKEN_TTL_S = 7 * 24 * 3600


def _install_holodeck_managed_provider() -> None:
    from omnigent.server import managed_hosts as mh
    from omnigent.community.sandbox.holodeck.launcher import HolodeckSandboxLauncher

    _orig = mh.parse_sandbox_config

    def _parse(raw):
        if isinstance(raw, dict) and raw.get("provider") == "holodeck":
            server_url = raw.get("server_url")
            if not isinstance(server_url, str) or not server_url.strip():
                raise ValueError(
                    "sandbox.server_url is required for the holodeck provider "
                    "(the public URL the provisioned workspace dials back to)"
                )
            return mh.ManagedSandboxConfig(
                server_url=server_url.rstrip("/"),
                launcher_factory=lambda: HolodeckSandboxLauncher(),
                token_ttl_s=_HOLODECK_TOKEN_TTL_S,
                managed_launch_supported=True,
                provider="holodeck",
                host_config=raw.get("host_config"),
            )
        return _orig(raw)

    mh.parse_sandbox_config = _parse


def main() -> int:
    _install_holodeck_managed_provider()
    from omnigent.cli import main as omnigent_main
    return omnigent_main()


if __name__ == "__main__":
    sys.exit(main())
