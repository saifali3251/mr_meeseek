"""Runtime configuration for the lease API.

All values are env-overridable so the same code runs on the POC EC2 box and in
tests. The app allowlist is derived FROM DISK (manifests/*.sh) rather than
hardcoded, so it can never drift from what actually exists (issue #1).
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _holo_dir() -> Path:
    """Repo root that holds scripts/ and manifests/.

    Defaults to the repo this package lives in (control-plane/ is a sibling of
    scripts/ and manifests/). Override with HOLO_DIR on the box.
    """
    env = os.environ.get("HOLO_DIR")
    if env:
        return Path(env).resolve()
    # control-plane/holodeck/config.py -> repo root is two parents up from control-plane/
    return Path(__file__).resolve().parents[2]


def valid_apps(holo_dir: Path) -> set[str]:
    """Allowlist of app names = manifests/*.sh basenames minus README.

    Derived from disk, not a static list (issue #1: 'not a hardcoded list that
    drifts'). An `app` not in this set is rejected before any shell-out.
    """
    manifests = holo_dir / "manifests"
    if not manifests.is_dir():
        return set()
    return {p.stem for p in manifests.glob("*.sh")} - {"README"}


def parse_app_aliases(raw: str) -> dict[str, str]:
    """`real=display,real2=display2` -> {real: display}. Used to show a friendlier
    operator-facing name than the manifest basename (e.g. the composite bundle
    `compliance-ui` displayed as `compliance`). Blank/garbage pairs are skipped."""
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        real, _, disp = pair.partition("=")
        real, disp = real.strip(), disp.strip()
        if real and disp:
            out[real] = disp
    return out


def default_env_file() -> Path:
    """control-plane/holodeck.env — the operator's local config (gitignored)."""
    return Path(__file__).resolve().parents[1] / "holodeck.env"


def load_env_file(path) -> list[str]:
    """Populate os.environ from a KEY=VALUE file (only keys not already set, so a
    real environment variable always wins). Blank lines and `#` comments are
    ignored. Secrets (Jira token, etc.) live here, gitignored — never in the repo.
    Returns the keys it set."""
    p = Path(path)
    if not p.is_file():
        return []
    loaded: list[str] = []
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


@dataclass
class Config:
    holo_dir: Path = field(default_factory=_holo_dir)

    # provider selection (issue: confirmation #4) — fail fast on unknown at startup.
    provider: str = field(default_factory=lambda: os.environ.get("HOLODECK_PROVIDER", "compose"))

    # bind loopback-only by default: the process can reach `sudo rm -rf` (issue #2).
    host: str = field(default_factory=lambda: os.environ.get("HOLODECK_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_PORT", "8099")))

    # SQLite file backing both the lease store and the console tables — one DB for
    # the one service. ":memory:" is ephemeral (tests / throwaway runs).
    db_path: str = field(default_factory=lambda: os.environ.get(
        "HOLODECK_DB", str(Path(__file__).resolve().parents[1] / "holodeck.db")))

    # shared-token auth (issue #2: moved up from Phase 5 to Phase 2). Empty = disabled
    # (tests / first local bring-up); set HOLODECK_TOKEN on any shared box.
    token: str = field(default_factory=lambda: os.environ.get("HOLODECK_TOKEN", ""))

    # capacity guard (issue #8): ~890 MB/ws, ~60 per 64 GB host. A new lease past
    # this QUEUES (status=queued) rather than hard-rejecting with a 503 — it
    # starts striking once an existing lease is released/expires/fails and
    # frees a slot. See LeaseService._advance_queue.
    max_leases: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_MAX_LEASES", "50")))

    # Run the strike (docker compose up, ~minutes) in a BACKGROUND thread so
    # POST /leases returns a `pending` lease immediately and never holds the
    # single request worker — callers poll GET /leases/{id} for `ready`. A long
    # or hung strike then can't wedge the whole control plane. Default on;
    # tests set it off so acquire() stays synchronous/deterministic.
    provision_async: bool = field(
        default_factory=lambda: os.environ.get("HOLODECK_PROVISION_ASYNC", "1") not in ("0", "false", "no"))

    # Bound how many strikes (docker compose up — CPU/disk-heavy, minutes long)
    # run AT ONCE, separate from max_leases (the total-workspace ceiling below).
    # A ticket arriving once this is full still gets an instant lease_id back
    # (status=queued, not a 503) — its strike starts as soon as a slot frees.
    # Needs real tuning against actual host resources, not a guess: this bounds
    # concurrent `docker compose up`s of the FULL compliance-ui composite (12
    # containers), which is heavier than the single-repo `compliance` app this
    # default was never calibrated against.
    max_concurrent_strikes: int = field(
        default_factory=lambda: int(os.environ.get("HOLODECK_MAX_CONCURRENT_STRIKES", "3")))

    # --- warm workspace pool (holodeck/pool.py) ---
    # How many pre-booted, seeded stacks to keep per app so a strike is a claim
    # (~sub-second) instead of a cold ~2min boot. 0 = OFF, and off is the default
    # on purpose: with pool_size=0 the acquire path is byte-identical to the
    # pre-pool behaviour, so this flag is also the rollback.
    #
    # Each slot is a full running stack (~0.5GB for `compliance`: webserver + db),
    # so this is bounded by host RAM, not by taste. Do NOT pool compliance-ui —
    # 13 services per slot is 4-6GB.
    pool_size: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_POOL_SIZE", "0")))

    # Which app(s) to keep warm, comma-separated. Empty -> default_app. This is
    # separate from default_app on purpose: the app the console triggers by
    # default and the app worth spending idle RAM on are not necessarily the
    # same, and pinning the pool to default_app silently warms the wrong stack.
    pool_apps_raw: str = field(default_factory=lambda: os.environ.get("HOLODECK_POOL_APPS", ""))

    # Reconcile cadence. The loop boots at most ONE slot per tick, so this also
    # paces the refill: an empty 3-slot pool fills over 3 ticks + 3 boots rather
    # than firing 3 concurrent `compose up`s and starving real strikes.
    pool_interval_s: int = field(
        default_factory=lambda: int(os.environ.get("HOLODECK_POOL_INTERVAL_S", "15")))

    # port pool for preview allocation (issue #3: the API owns port selection).
    port_pool_start: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_PORT_START", "18000")))
    port_pool_end: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_PORT_END", "18999")))

    # Probe the OS before handing out a port. The in-use set is only bookkeeping: it is
    # empty after a restart and blind to ports held by anything else on the host (the
    # POC stack, another dev's workspaces, orphans from a previous run). Disabled in
    # unit tests so they don't depend on which ports happen to be free on a laptop.
    port_probe: bool = field(
        default_factory=lambda: os.environ.get("HOLODECK_PORT_PROBE", "1") not in ("0", "false", "no")
    )

    # lease lifetime + reaper cadence (Phase 4).
    default_ttl_s: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_TTL_S", "3600")))
    reaper_interval_s: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_REAPER_S", "30")))

    # finalize test wall-clock cap (issue #6).
    finalize_timeout_s: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_FINALIZE_TIMEOUT_S", "600")))

    # --- finalize -> PR (gap E2) ---
    # OFF by default: opening a real draft PR is an outward-facing side effect, so
    # it must be explicitly enabled per box. finalize opens/updates the PR only
    # when enabled AND there's a real change (a diff vs the golden). Needs `gh`
    # authed + push access to the app repo on the host.
    pr_enabled: bool = field(
        default_factory=lambda: os.environ.get("HOLODECK_PR", "0") not in ("0", "false", "no"))
    pr_base: str = field(default_factory=lambda: os.environ.get("HOLODECK_PR_BASE", "master"))
    pr_draft: bool = field(
        default_factory=lambda: os.environ.get("HOLODECK_PR_DRAFT", "1") not in ("0", "false", "no"))
    # label added to the PR for human review of the preview env (the demo narrative).
    # Attached best-effort — a missing/failed label never fails PR creation. Empty = none.
    pr_label: str = field(default_factory=lambda: os.environ.get("HOLODECK_PR_LABEL", "holodeck_preview"))

    # per-exec wall-clock cap (B3). A hung command is killed and returns exit 124.
    exec_timeout_s: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_EXEC_TIMEOUT_S", "120")))

    # startup reconcile policy for orphaned workspaces (issue #4, my variant):
    #   "log"  -> log loudly + refuse to reuse their ports (default, safe)
    #   "reap" -> destroy them for a clean slate
    reconcile: str = field(default_factory=lambda: os.environ.get("HOLODECK_RECONCILE", "log"))

    # --- console (trigger + board), co-located but seam-preserved ---
    # One service for the MVP, but the console talks to the lease API over HTTP
    # (loopback) even in-process, so splitting it out later is a URL change.
    console_enabled: bool = field(
        default_factory=lambda: os.environ.get("HOLODECK_CONSOLE", "1") not in ("0", "false", "no"))
    console_driver: str = field(default_factory=lambda: os.environ.get("HOLODECK_CONSOLE_DRIVER", "direct"))
    console_poll_interval_s: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_CONSOLE_POLL_S", "5")))
    # app a trigger uses when none is given (a Jira ticket doesn't name an app).
    default_app: str = field(default_factory=lambda: os.environ.get("HOLODECK_DEFAULT_APP", "compliance"))
    # lease API base URL the console calls. Empty -> loopback to this same service.
    holodeck_url: str = field(default_factory=lambda: os.environ.get("HOLODECK_URL", ""))
    # ssh target the author tunnels through to reach a workspace on localhost
    # (dev-testing preview §14). Empty -> the board prints a "<ec2-box>" placeholder.
    console_ssh_host: str = field(default_factory=lambda: os.environ.get("HOLODECK_SSH_HOST", ""))
    # Omnigent server the OmnigentDriver drives (HttpOmnigentClient). Empty until wired.
    omnigent_url: str = field(default_factory=lambda: os.environ.get("HOLODECK_OMNIGENT_URL", ""))
    # durable agent *id* to bind the session to (SessionCreateRequest.agent_id), not a display name.
    # Defaults to Polly; override with HOLODECK_OMNIGENT_AGENT to bind a different agent.
    omnigent_agent: str = field(default_factory=lambda: os.environ.get(
        "HOLODECK_OMNIGENT_AGENT", "057995d1517418e6839f51d340785dd6"))
    # Bearer token for the Omnigent server (from `omnigent login` or an issued token).
    # If empty, the client falls back to account login with the user/password below.
    omnigent_token: str = field(default_factory=lambda: os.environ.get("HOLODECK_OMNIGENT_TOKEN", ""))
    # Account login (username/password) — Omnigent isn't on OIDC here. When no static
    # token is set, the client POSTs these to the login path, caches the returned token,
    # and re-auths on 401. Path is overridable in case the server's route differs.
    omnigent_user: str = field(default_factory=lambda: os.environ.get("HOLODECK_OMNIGENT_USER", ""))
    omnigent_password: str = field(default_factory=lambda: os.environ.get("HOLODECK_OMNIGENT_PASSWORD", ""))
    omnigent_login_path: str = field(
        default_factory=lambda: os.environ.get("HOLODECK_OMNIGENT_LOGIN_PATH", "/auth/login"))
    # run the omnigent driver against the in-memory fake (no server/golden) — for
    # local end-to-end demo of the Omnigent path, like HOLODECK_PROVIDER=fake.
    omnigent_fake: bool = field(
        default_factory=lambda: os.environ.get("HOLODECK_OMNIGENT_FAKE", "0") not in ("0", "false", "no"))

    # --- Jira bridge (inbound webhook -> start/feedback; outbound comments) ---
    jira_enabled: bool = field(
        default_factory=lambda: os.environ.get("HOLODECK_JIRA", "0") not in ("0", "false", "no"))
    jira_fake: bool = field(
        default_factory=lambda: os.environ.get("HOLODECK_JIRA_FAKE", "0") not in ("0", "false", "no"))
    jira_base_url: str = field(default_factory=lambda: os.environ.get("HOLODECK_JIRA_BASE_URL", ""))
    jira_email: str = field(default_factory=lambda: os.environ.get("HOLODECK_JIRA_EMAIL", ""))
    jira_token: str = field(default_factory=lambda: os.environ.get("HOLODECK_JIRA_TOKEN", ""))
    # our own Jira account id/name — comments by it are ignored (loop guard).
    jira_bot_account: str = field(default_factory=lambda: os.environ.get("HOLODECK_JIRA_BOT", ""))
    jira_command_prefix: str = field(default_factory=lambda: os.environ.get("HOLODECK_JIRA_PREFIX", "/holodeck"))
    jira_trigger_label: str = field(default_factory=lambda: os.environ.get("HOLODECK_JIRA_LABEL", "holodeck"))
    # Swapped onto the ticket (trigger label removed) when the agent's own
    # narration hits the "Your action:" marker with no formal elicitation open —
    # a passive status flag, not an automation trigger. See JiraBridge._check_halt.
    jira_halt_label: str = field(
        default_factory=lambda: os.environ.get("HOLODECK_JIRA_HALT_LABEL", "holodeck:halt"))
    # Swapped in by a human once they're done testing a halted ticket (not automatic —
    # same "human decides" pattern as finalize). JiraBridge._reset() deletes the
    # ConsoleStore record for this ticket so _start()'s one-session-per-ticket guard
    # no longer blocks it, releases the lease if it's somehow still active, and drops
    # this label again once done — it's an edge-trigger, not a standing state.
    jira_reset_label: str = field(
        default_factory=lambda: os.environ.get("HOLODECK_JIRA_RESET_LABEL", "holodeck:destroy"))
    jira_webhook_secret: str = field(default_factory=lambda: os.environ.get("HOLODECK_JIRA_WEBHOOK_SECRET", ""))
    # Maps a lease's published preview port -> the DNS domain that's actually
    # wired to serve it (e.g. the 3 admin.workspace-{one,two,three}.aibuildercup.io
    # provisioned domains for AI Builder Cup Hackathon) — used to build the "Live sandbox"
    # link in the PR body (service.py:_pr_body) instead of the unreachable-from-a-reviewer's-browser
    # http://127.0.0.1:<port> tunnel address. Format: "port=domain,port=domain,...";
    # domain is just the workspace label (e.g. "workspace-one"), not the full host —
    # _pr_body builds "https://admin.<domain>.aibuildercup.io/login" from it.
    # A port with no entry here falls back to the 3-tier domain hierarchy or local loopback line.
    console_workspace_domains: dict = field(default_factory=lambda: {
        int(port): domain for port, domain in (
            pair.split("=", 1) for pair in
            os.environ.get(
                "HOLODECK_WORKSPACE_DOMAINS",
                "",
            ).split(",") if pair.strip()
        )
    })
    # Base domain for workspace preview URLs (Tier 2 in domain hierarchy).
    # e.g., "preview.saifali.dev" or "preview.company.com".
    # Empty -> falls back to OMNIGENT_PUBLIC_HOST (Tier 3), or http://127.0.0.1:<port> locally.
    preview_base_domain: str = field(
        default_factory=lambda: os.environ.get("HOLODECK_PREVIEW_BASE_DOMAIN")
        or os.environ.get("PREVIEW_BASE_DOMAIN", "")
    )
    # URL pattern for formatting preview links. Supports {port}, {base_domain}, {ticket}, {app}.
    preview_url_pattern: str = field(
        default_factory=lambda: os.environ.get(
            "HOLODECK_PREVIEW_URL_PATTERN", "https://p{port}.{base_domain}"
        )
    )
    # Static preview URL shown in the console for every workspace (e.g. a shared
    # reviewer entrypoint). Empty = show the per-lease http://127.0.0.1:<port> link.
    preview_url: str = field(default_factory=lambda: os.environ.get("HOLODECK_PREVIEW_URL", ""))

    # --- golden synchronization & rebuild ---
    # Secret for validating GitHub webhook push events (X-Hub-Signature-256)
    github_webhook_secret: str = field(
        default_factory=lambda: os.environ.get("HOLODECK_GITHUB_WEBHOOK_SECRET", "")
    )
    # Debounce quiet window (seconds) to batch rapid-fire merges into a single build
    golden_debounce_s: int = field(
        default_factory=lambda: int(os.environ.get("HOLODECK_GOLDEN_DEBOUNCE_S", "45"))
    )
    # Optional periodic background cron sweep (seconds). 0 = disabled (webhook only)
    golden_cron_s: int = field(
        default_factory=lambda: int(os.environ.get("HOLODECK_GOLDEN_CRON_S", "0"))
    )
    # Maximum execution time allowed for a single golden build before timeout
    golden_build_timeout_s: int = field(
        default_factory=lambda: int(os.environ.get("HOLODECK_GOLDEN_BUILD_TIMEOUT_S", "900"))
    )
    # inbound polling (no webhook): the project to scan for labelled tickets, and
    # how often. Empty project = polling off (webhook-only). Default 60s per §flow.
    jira_project: str = field(default_factory=lambda: os.environ.get("HOLODECK_JIRA_PROJECT", ""))
    jira_poll_interval_s: int = field(default_factory=lambda: int(os.environ.get("HOLODECK_JIRA_POLL_S", "60")))

    # --- automatic app onboarding (docs/AUTOMATIC_ONBOARDING.md, "Easy Way") ---
    # One flag gates the whole feature (team scoping + the wizard + the admin
    # review queue) — the single rollback lever, same spirit as pool_size=0 or
    # pr_enabled: off leaves /ops byte-identical to before onboarding existed.
    onboarding_enabled: bool = field(
        default_factory=lambda: os.environ.get("HOLODECK_ONBOARDING", "1") not in ("0", "false", "no"))
    # fake = deterministic, no Docker (tests, and any demo without a real box).
    # script = shells the real golden-build.sh in a scratch HOLO_ROOT — needs
    # Docker + a reachable repo, so it's the box-only setting.
    trial_runner: str = field(default_factory=lambda: os.environ.get("HOLODECK_TRIAL_RUNNER", "fake"))
    # scratch clone/build dir for recon + trial builds. Never the real
    # manifests_dir — a trial never touches what control-plane actually serves
    # until OnboardingService.approve() writes the real file.
    onboarding_workdir: str = field(default_factory=lambda: os.environ.get(
        "HOLODECK_ONBOARDING_WORKDIR", str(Path(tempfile.gettempdir()) / "holodeck-onboarding")))

    # Restrict which on-disk manifests are strikeable environments (comma-separated).
    # The manifests repo may carry dependency manifests (e.g. the per-repo compliance
    # / main / main-ui goldens that the compliance-ui composite bundles) that should
    # NOT show as separate environments. Empty = expose every manifest (default).
    apps_allow: set[str] = field(default_factory=lambda: {
        a.strip() for a in os.environ.get("HOLODECK_APPS", "").split(",") if a.strip()})
    # Operator-facing display names: `real=display,...`. Cosmetic only — the real
    # manifest key still drives provisioning; the console reverse-maps on strike.
    app_aliases: dict = field(default_factory=lambda: parse_app_aliases(
        os.environ.get("HOLODECK_APP_ALIASES", "")))

    @property
    def pool_apps(self) -> list[str]:
        """Apps the warm pool keeps slots for. Defaults to default_app.

        Names with no manifest are dropped with a warning rather than crashing
        the service — a typo here should cost you the pool, not the control
        plane."""
        raw = [a.strip() for a in self.pool_apps_raw.split(",") if a.strip()]
        wanted = raw or [self.default_app]
        known = self.apps
        good = [a for a in wanted if a in known]
        for a in wanted:
            if a not in known:
                logging.getLogger("holodeck.config").warning(
                    "HOLODECK_POOL_APPS: no manifest for '%s' — not pooling it "
                    "(known: %s)", a, ", ".join(sorted(known)) or "none")
        return good

    @property
    def apps(self) -> set[str]:
        """Strikeable environments: manifests on disk, narrowed by the allowlist
        if one is set. The allowlist can only subtract — it never invents an app
        that has no manifest."""
        found = valid_apps(self.holo_dir)
        return (found & self.apps_allow) if self.apps_allow else found

    def load_manifest_vars(self, app: Optional[str]) -> dict[str, str]:
        """Read simple KEY=VALUE definitions from a manifest shell script without execution."""
        if not app:
            return {}
        p = self.holo_dir / "manifests" / f"{app}.sh"
        if not p.is_file():
            return {}
        out: dict[str, str] = {}
        try:
            for raw in p.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=["\']?(.*?)["\']?$', line)
                if m:
                    out[m.group(1)] = m.group(2)
                    continue
                m2 = re.match(r'^:\s*["\']?\$\{\s*([A-Za-z_][A-Za-z0-9_]*):=(.*?)\s*\}["\']?$', line)
                if m2 and m2.group(1) not in out:
                    out[m2.group(1)] = m2.group(2).strip('"\'')
        except Exception:
            logging.getLogger("holodeck.config").warning("Failed reading manifest vars for %s", app)
        return out

    def workspace_preview_url(self, port: Optional[int], app: Optional[str] = None,
                              ticket: Optional[str] = None) -> Optional[str]:
        """The one place that turns a preview port into a reviewer-facing URL —
        shared by service.py's PR body, console/manager.py's TaskRecord.preview_url
        (which backs the Jira "started" comment's Preview link), and /ops dashboard.

        Implements the 3-tier domain hierarchy:
        1. Manifest HOLO_PREVIEW_DOMAIN / HOLO_PREVIEW_URL_PATTERN
        2. Config / Environment HOLODECK_PREVIEW_BASE_DOMAIN
        3. Default fallback (OMNIGENT_PUBLIC_HOST e.g. *.8.234.68.172.sslip.io, or 127.0.0.1:<port>)
        """
        if not port:
            return None

        # Tier 1: Manifest override
        domain = None
        pattern = None
        if app:
            mvars = self.load_manifest_vars(app)
            domain = mvars.get("HOLO_PREVIEW_DOMAIN")
            pattern = mvars.get("HOLO_PREVIEW_URL_PATTERN")

        # Tier 2: System / Environment Base Domain
        if not domain:
            domain = self.preview_base_domain

        # Tier 3: Public Host Fallback (e.g. 8.234.68.172.sslip.io)
        if not domain:
            domain = os.environ.get("OMNIGENT_PUBLIC_HOST", "")

        # Legacy fallback if HOLODECK_WORKSPACE_DOMAINS is explicitly defined
        if not domain and port in self.console_workspace_domains:
            legacy_domain = self.console_workspace_domains[port]
            return f"https://admin.{legacy_domain}.aibuildercup.io/login"

        # Local loopback fallback if absolutely no domain or host is configured
        if not domain:
            return f"http://127.0.0.1:{port}"

        # URL format resolution
        pattern = pattern or self.preview_url_pattern or "https://p{port}.{base_domain}"

        # Strip any scheme prefix from base_domain if present
        base_domain = domain.rstrip("/")
        if base_domain.startswith("http://") or base_domain.startswith("https://"):
            base_domain = base_domain.split("://", 1)[1]

        ticket_str = ticket or f"ws-{port}"
        app_str = app or self.default_app

        try:
            return pattern.format(
                port=port,
                base_domain=base_domain,
                ticket=ticket_str,
                app=app_str,
            )
        except Exception:
            return f"https://p{port}.{base_domain}"

    def app_label(self, app: str) -> str:
        """Operator-facing display name for a real manifest key (identity if none)."""
        return self.app_aliases.get(app, app)

    def app_key(self, label: str) -> str:
        """Reverse a display name back to its real manifest key. A value that's
        already a real key (or has no alias) passes through unchanged."""
        if label in self.app_aliases:      # already a real key
            return label
        for real, disp in self.app_aliases.items():
            if disp == label:
                return real
        return label

    @property
    def scripts_dir(self) -> Path:
        return self.holo_dir / "scripts"

    @property
    def console_lease_url(self) -> str:
        """Where the console reaches the lease API. Defaults to this service on
        loopback, so the seam is identical whether co-located or split out."""
        return self.holodeck_url or f"http://127.0.0.1:{self.port}"
