"""Wiring: build the provider + store + service + reaper + app from a Config.

Also holds the startup reconciler (issue #4). Kept separate from main.py so
tests can build a fully-wired app around a FakeProvider.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from holodeck.config import Config
from holodeck.db import Db
from holodeck.onboarding.service import OnboardingService
from holodeck.onboarding.store import OnboardingStore
from holodeck.onboarding.trial import FakeTrialRunner, ScriptTrialRunner, TrialRunner
from holodeck.providers.base import WorkspaceProvider
from holodeck.providers.compose import ComposeProvider
from holodeck.providers.fake import FakeProvider
from holodeck.reaper import Reaper
from holodeck.service import LeaseService
from holodeck.store import LeaseStore, PortPool
from holodeck.teams import TeamStore

log = logging.getLogger("holodeck.factory")

_PROVIDERS = {"compose", "fake", "eks"}
_TRIAL_RUNNERS = {"fake", "script"}


def build_provider(cfg: Config, ports: PortPool | None = None) -> WorkspaceProvider:
    # confirmation #4: fail fast at startup on an unknown provider, not on first request.
    if cfg.provider not in _PROVIDERS:
        raise SystemExit(
            f"HOLODECK_PROVIDER='{cfg.provider}' is not one of {sorted(_PROVIDERS)}"
        )
    if cfg.provider == "compose":
        # `ports` lets the warm pool reserve each slot's published port in the
        # SAME allocator the leases draw from — without it a cold strike could be
        # handed a port a warm slot is already bound to.
        return ComposeProvider(cfg, ports=ports)
    if cfg.provider == "fake":
        return FakeProvider()
    raise SystemExit("HOLODECK_PROVIDER=eks is reserved (Beta) and not yet implemented")


def build_trial_runner(cfg: Config) -> TrialRunner:
    if cfg.trial_runner not in _TRIAL_RUNNERS:
        raise SystemExit(
            f"HOLODECK_TRIAL_RUNNER='{cfg.trial_runner}' is not one of {sorted(_TRIAL_RUNNERS)}"
        )
    if cfg.trial_runner == "script":
        return ScriptTrialRunner(cfg.holo_dir, Path(cfg.onboarding_workdir) / "trials")
    return FakeTrialRunner()


def build_onboarding(cfg: Config, db: Db) -> tuple[Optional[TeamStore], Optional[OnboardingService]]:
    """None, None when the feature is off (HOLODECK_ONBOARDING=0) — every
    caller must treat that as "not wired up", not "wired up with no teams"."""
    if not cfg.onboarding_enabled:
        return None, None
    teams = TeamStore(db)
    onboarding = OnboardingService(
        store=OnboardingStore(db), teams=teams, trial_runner=build_trial_runner(cfg),
        manifests_dir=cfg.holo_dir / "manifests",
        workdir=Path(cfg.onboarding_workdir) / "recon",
    )
    return teams, onboarding


def build(cfg: Config, db: Db | None = None) -> tuple[LeaseService, Reaper, Optional[TeamStore], Optional[OnboardingService]]:
    db = db or Db(cfg.db_path)
    pool = PortPool(cfg.port_pool_start, cfg.port_pool_end, probe=cfg.port_probe)
    store = LeaseStore(pool, db)
    # re-seed the port pool from leases that survived a restart, so we never hand
    # out a port a still-active workspace holds.
    for lease in store.all():
        if lease.status.value in ("pending", "ready") and lease.preview_port:
            pool.reserve_specific(lease.preview_port)
    provider = build_provider(cfg, ports=pool)
    service = LeaseService(cfg, store, provider)
    reaper = Reaper(service, cfg.reaper_interval_s)
    teams, onboarding = build_onboarding(cfg, db)
    return service, reaper, teams, onboarding


def reconcile(cfg: Config, service: LeaseService) -> None:
    """On startup, an in-memory store knows about zero leases but the substrate
    may still hold workspaces from a previous run — dirs on disk, containers up,
    ports bound (issue #4). Fabricating a TTL for them is fuzzy, so instead:

      reconcile=log  (default) -> log each orphan loudly AND reserve its port so
                                   we never hand out a port something still holds.
      reconcile=reap           -> tear each orphan down for a clean slate.
    """
    try:
        orphans = service.provider.list_orphans()
    except Exception:
        log.exception("orphan enumeration failed; continuing")
        return
    if not orphans:
        return
    log.warning("startup reconcile: found %d orphaned workspace(s)", len(orphans))
    for h in orphans:
        if cfg.reconcile == "reap":
            try:
                service.provider.release(h)
                log.warning("reaped orphan workspace %s", h.lease_id)
            except Exception:
                log.exception("failed to reap orphan %s", h.lease_id)
        else:
            # Actually reserve the port the orphan holds — the docstring promised this
            # but nothing did it, so a restart re-handed out a bound port and the next
            # strike died on "port is already allocated".
            if h.preview_port is not None:
                service.store.ports.reserve_specific(h.preview_port)
            log.warning("orphan workspace NOT in store: %s (app=%s dir=%s port=%s%s) — "
                        "release manually or set HOLODECK_RECONCILE=reap",
                        h.lease_id, h.app or "?", h.ws_dir, h.preview_port,
                        " RESERVED" if h.preview_port is not None else "")
