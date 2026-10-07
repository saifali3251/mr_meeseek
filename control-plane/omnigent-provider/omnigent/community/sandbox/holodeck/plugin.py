"""Registry contribution — declared via the `omnigent.sandbox_providers`
entry point in pyproject.toml. The launcher_class MUST stay under the
`omnigent.community.sandbox` namespace (the registry rejects anything else)."""

from __future__ import annotations

from omnigent.onboarding.sandboxes.registry import (SandboxProviderContribution,
                                                    SandboxProviderMetadata)


def get_contribution() -> SandboxProviderContribution:
    launcher = "omnigent.community.sandbox.holodeck.launcher:HolodeckSandboxLauncher"
    return SandboxProviderContribution(
        name="omnigent-community-sandbox-meeseek",
        providers={
            "holodeck": SandboxProviderMetadata(
                name="holodeck",
                launcher_class=launcher,
            ),
            "meeseek": SandboxProviderMetadata(
                name="meeseek",
                launcher_class=launcher,
            ),
        },
    )
