"""Entrypoint: `python -m holodeck.main` (or uvicorn factory).

Enforces the single-worker invariant, runs startup reconcile, starts the reaper,
and serves the app bound loopback-only by default.
"""

from __future__ import annotations

import logging
import os

import uvicorn

from holodeck.api import create_app
from holodeck.config import Config, default_env_file, load_env_file
from holodeck.db import Db
from holodeck.factory import build, reconcile

logging.basicConfig(
    level=os.environ.get("HOLODECK_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("holodeck.main")


def build_app_for_serving(cfg: Config | None = None):
    cfg = cfg or Config()
    db = Db(cfg.db_path)  # one DB for the one service — leases + console + onboarding tables
    service, reaper, teams, onboarding = build(cfg, db)
    reconcile(cfg, service)
    reaper.start()
    # Warm pool AFTER reconcile: reconcile re-reserves ports held by surviving
    # workspaces, and pool.adopt() then re-attaches the slots among them. Started
    # here rather than in build() so tests get a wired service with no background
    # boots firing. No-op unless HOLODECK_POOL_SIZE > 0.
    pool = getattr(service.provider, "pool", None)
    if pool is not None:
        pool.start()
    app = create_app(cfg, service, teams, onboarding)
    app.state.pool = pool
    app.state.reaper = reaper
    app.state.service = service
    app.state.config = cfg
    app.state.db = db
    app.state.teams = teams
    app.state.onboarding = onboarding
    # Co-locate the console (trigger + board) in the same service for the MVP,
    # sharing the same DB. It talks to the lease API over loopback HTTP
    # (cfg.console_lease_url), so it can still be split out later with a URL change.
    if cfg.console_enabled:
        from holodeck.console import mount_console
        mount_console(app, cfg, db=db)
        log.info("console mounted (driver=%s, board at /console)", cfg.console_driver)
        if cfg.jira_enabled:
            log.info("jira bridge mounted (fake=%s, project=%s, poll=%ss, webhook=%s)",
                      cfg.jira_fake, cfg.jira_project or "-", cfg.jira_poll_interval_s,
                      "on" if cfg.jira_webhook_secret else "no secret set")
        else:
            log.info("jira bridge NOT mounted (HOLODECK_JIRA=0) — "
                      "/jira/webhook will 404 and no Jira comments will be posted")
    return app, cfg


def main() -> None:
    # load control-plane/holodeck.env (or $HOLODECK_ENV_FILE) before reading config,
    # so tokens/secrets can live in a gitignored file the operator edits.
    loaded = load_env_file(os.environ.get("HOLODECK_ENV_FILE") or default_env_file())
    if loaded:
        log.info("loaded %d config key(s) from env file", len(loaded))
    cfg = Config()
    log.info("provider=%s bind=%s:%d max_leases=%d reconcile=%s",
             cfg.provider, cfg.host, cfg.port, cfg.max_leases, cfg.reconcile)
    if cfg.host not in ("127.0.0.1", "localhost", "::1"):
        # issue #2: the process can reach `sudo rm -rf`. Non-loopback is opt-in.
        log.warning("binding NON-loopback host %s — ensure token auth + firewall", cfg.host)
    if not cfg.token:
        log.warning("HOLODECK_TOKEN is empty — auth DISABLED (local/demo only)")

    app, cfg = build_app_for_serving(cfg)
    # issue #3: in-memory store is process-local. >1 worker silently corrupts
    # port allocation + hides leases across processes. Hard single-worker.
    uvicorn.run(app, host=cfg.host, port=cfg.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
