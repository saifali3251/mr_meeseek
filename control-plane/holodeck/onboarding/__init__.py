"""Automatic app onboarding (Easy Way) — docs/AUTOMATIC_ONBOARDING.md §3.

A team submits repo(s) (+ a dependency graph for composites); a Recon Agent
drafts a Holodeck manifest; the team reviews/edits it; a trial golden-build
proves it in a scratch sandbox; a Holodeck-team human approves before it's
published into the real manifests/ allowlist. Nothing here bypasses that last
human gate — see OnboardingService.approve().
"""
