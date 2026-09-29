"""ComposeProvider — the Docker Compose substrate (demo / EC2).

Shells out to the PROVEN scripts/strike.sh and scripts/destroy.sh; it does not
reimplement the mechanism. All subprocess calls are argv lists with shell=False
and a curated env (issue #1) — never a shell string. The only trusted-caller
assumption is that `app`/`ticket` were validated at the API boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import AsyncIterator, Optional

from holodeck.config import Config
from holodeck.models import (Evidence, ExecResult, ProviderCapabilities,
                             WorkspaceHandle, holo_id)
from holodeck.guardrails import audit_blast_radius, verify_ast_test_integrity
from holodeck.pool import POOL_MARKER, PoolManager, PoolSlot
from holodeck.providers.base import ProviderError, WorkspaceExistsError

log = logging.getLogger("holodeck.compose")

# Marker strike.sh prints when a workspace already exists (see strike.sh).
_EXISTS_MARKER = "already exists"

# How much of stdout/stderr to keep in a log line — enough to see a real error,
# not so much that one noisy command floods the log file.
_LOG_OUTPUT_CHARS = 800


def _base_env(app: str, target_repo: Optional[str] = None) -> dict[str, str]:
    """Curated env for a script call. HOLO_ROOT/HOLO_SRC/HOLO_GOLDEN come from
    the box's holodeck.local.env (sourced by lib.sh); we only inject HOLO_APP
    (and, when a lease specifies one, HOLO_TARGET_REPO — read by strike.sh's
    branch-cutting step; see manifests/control-tower.sh's HOLO_GIT_SUBDIR note)."""
    env = {**os.environ, "HOLO_APP": app}
    if target_repo:
        env["HOLO_TARGET_REPO"] = target_repo
    return env


class ComposeProvider:
    """NEVER guess manifest values from os.environ.

    lib.sh resolves per-app config (local.env -> defaults -> manifests/<app>.sh) inside
    the shell that sources it; those values are NOT in this process's environment.
    Guessing them silently broke finalize: HOLO_COMPOSE_FILE defaulted to
    docker-compose.yaml while compliance uses compose.yaml, WS_SERVICES defaulted to ""
    so the services-booted stamp was always empty, and HOLO_WORKSPACES defaulted to ""
    which produced a RELATIVE cwd and a FileNotFoundError.

    So we ask lib.sh via scripts/holo-env.sh and cache the answer per app. Adding a
    manifest variable means listing it in holo-env.sh once — no per-consumer defaults.
    The same discipline carries to EksProvider: the manifest stays the source of truth
    for app facts, and each provider translates them to its own substrate.
    """

    name = "compose"

    def __init__(self, cfg: Config, ports=None) -> None:
        self.cfg = cfg
        self.scripts = cfg.scripts_dir
        self._manifest_cache: dict[str, dict[str, str]] = {}
        # In-flight strike.sh subprocesses, keyed by lease_id, so cancel_acquire()
        # can kill one that's still running when a caller gives up on it.
        self._inflight: dict[str, subprocess.Popen] = {}
        self._inflight_lock = threading.Lock()
        # The lease PortPool. The pool manager reserves each slot's port here at
        # boot, so the lease allocator can never hand a cold strike a port a warm
        # slot is already bound to. Optional so tests can build a bare provider.
        self._ports = ports
        self.pool = PoolManager(
            cfg, boot=self._pool_boot, destroy=self._pool_destroy,
            verify=self._pool_verify, ws_root=self._pool_ws_root,
        )
        self.pool.adopt_one = self._pool_adopt_one

    # ---- manifest resolution ----------------------------------------------
    def test_cmd(self, app: str, target_repo: Optional[str] = None) -> Optional[str]:
        """HOLO_TEST_CMD from the resolved manifest (via holo-env.sh), or repo-specific
        HOLO_TEST_CMD_<target_repo>, or None."""
        m = self._manifest(app)
        if target_repo:
            repo_cmd = m.get(f"HOLO_TEST_CMD_{target_repo}")
            if repo_cmd:
                return repo_cmd
        return m.get("HOLO_TEST_CMD") or None

    def _test_service(self, app: str, target_repo: Optional[str], m: dict[str, str]) -> str:
        """Resolve which container service to run tests in."""
        if target_repo:
            svc_key = f"HOLO_TEST_SERVICE_{target_repo}"
            if m.get(svc_key):
                return m[svc_key]
            if target_repo in ("test_frontend", "frontend"):
                return "frontend"
            if target_repo in ("test_backend", "backend"):
                return "backend"
        return m.get("HOLO_TEST_SERVICE") or m.get("HOLO_APP_SERVICE", "backend")

    def _manifest(self, app: str) -> dict[str, str]:
        """Resolved manifest config for `app`, straight from lib.sh. Cached per app."""
        cached = self._manifest_cache.get(app)
        if cached is not None:
            return cached

        proc = subprocess.run(
            ["./holo-env.sh"], cwd=self.scripts, env=_base_env(app), shell=False,
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise ProviderError(
                f"holo-env.sh failed for app={app!r} (rc={proc.returncode}): "
                f"{(proc.stderr or proc.stdout)[-500:]}"
            )

        m: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            key, sep, value = line.partition("=")   # first '=' only; values may contain '='
            if sep and (key.startswith("HOLO_") or key == "WS_SERVICES"):
                m[key] = value

        # Fail loudly on the one value that has no safe fallback — an empty
        # HOLO_WORKSPACES is what produced the relative-path crash.
        if not m.get("HOLO_WORKSPACES"):
            raise ProviderError(
                f"holo-env.sh did not resolve HOLO_WORKSPACES for app={app!r}; "
                "check scripts/lib.sh and holodeck.local.env"
            )
        self._manifest_cache[app] = m
        return m

    def _ws_root(self) -> Optional[Path]:
        """$HOLO_WORKSPACES, which is app-independent (derived from HOLO_ROOT in
        lib.sh). Resolved via any valid app so orphan listing works without a lease."""
        app = "main" if "main" in self.cfg.apps else next(iter(sorted(self.cfg.apps)), "")
        if not app:
            return None
        try:
            return Path(self._manifest(app)["HOLO_WORKSPACES"])
        except ProviderError:
            return None

    # ---- warm pool ---------------------------------------------------------
    def _pool_ws_root(self, app: str) -> Optional[Path]:
        try:
            return Path(self._manifest(app)["HOLO_WORKSPACES"])
        except ProviderError:
            return None

    def _pool_boot(self, slot_id: str, app: str) -> Optional[PoolSlot]:
        """Boot one warm slot: an ordinary strike under a synthetic id.

        Reuses strike.sh unchanged — holo_id("pool-01") is idempotent, so the
        workspace lands at ws/pool-01 with project ws-pool-01, exactly as a
        ticket-named one would. The port is reserved in the LEASE allocator
        before the boot so a concurrent cold strike can't be handed the port
        this slot is about to bind."""
        if self._ports is None:
            return None
        port = self._ports.reserve()
        argv = ["./strike.sh", slot_id, "--preview", str(port)]
        log.info("pool: booting slot %s on port %s (app=%s)", slot_id, port, app)
        proc = subprocess.run(argv, cwd=self.scripts, env=_base_env(app), shell=False,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=900)
        if proc.returncode != 0:
            # Roll the partial workspace back ourselves — the pool manager has no
            # handle to do it with, and a leftover dir would permanently burn this
            # slot id ("workspace already exists" on every retry).
            log.warning("pool: strike for %s failed rc=%s: %s", slot_id, proc.returncode,
                        ((proc.stdout or "") + (proc.stderr or ""))[-_LOG_OUTPUT_CHARS:])
            subprocess.run(["./destroy.sh", slot_id], cwd=self.scripts,
                           env=_base_env(app), shell=False, capture_output=True,
                           text=True, timeout=300)
            self._ports.release(port)
            return None
        ws_dir = self._pool_ws_root(app) / slot_id
        # Mark it HERE, while we still hold the means to roll back. The marker is
        # what makes the slot recoverable after a control-plane restart and what
        # keeps reconcile from reaping it, so an unmarked-but-running workspace is
        # a leak: destroy rather than return one.
        try:
            (ws_dir / POOL_MARKER).write_text("holodeck warm pool slot\n")
        except OSError:
            log.exception("pool: could not mark %s — destroying to avoid an untracked "
                          "workspace", slot_id)
            subprocess.run(["./destroy.sh", slot_id], cwd=self.scripts,
                           env=_base_env(app), shell=False, capture_output=True,
                           text=True, timeout=300)
            self._ports.release(port)
            return None
        return PoolSlot(slot_id=slot_id, app=app, port=port, ws_dir=str(ws_dir),
                        compose_project=f"ws-{slot_id}")

    def _pool_adopt_one(self, ws_dir: Path, app: str) -> Optional[PoolSlot]:
        """Rebuild a PoolSlot for a marked workspace that survived a restart.

        The dir does not record its port, so ask Docker which host port the
        project publishes — the same trick list_orphans uses. Re-reserve it, or
        the lease allocator would happily hand it to a cold strike."""
        slot_id = ws_dir.name
        proj = f"ws-{slot_id}"
        held = sorted(self._published_ports_by_project().get(proj, ()))
        if not held:
            return None      # nothing published => not actually up; leave it to reconcile
        port = held[0]
        if self._ports is not None:
            self._ports.reserve_specific(port)
        return PoolSlot(slot_id=slot_id, app=app, port=port, ws_dir=str(ws_dir),
                        compose_project=proj)

    def _golden_stamp(self, root: Path) -> Optional[str]:
        """Fingerprint of a tree's .holodeck-golden. Both golden-build scripts
        write a `date` line into it, so its content changes on every rebuild —
        it is already a generation id and needs no change to those scripts."""
        try:
            return hashlib.sha256((root / ".holodeck-golden").read_bytes()).hexdigest()
        except OSError:
            return None

    def _pool_slot_is_current(self, slot: PoolSlot) -> bool:
        """Was this slot cloned from the CURRENT golden?

        A rebuilt golden makes every existing slot stale, and staleness is
        invisible to a health check: the slot is perfectly alive, it just
        serves last build's code. Without this the pool would keep handing
        agents an old tree and the PRs would be cut against it — wrong in a way
        nothing else in the system would flag.

        Fails OPEN. If either stamp is unreadable we keep the slot: a golden
        that never wrote one would otherwise make every slot permanently stale
        and the pool would churn forever, boiling the box for nothing."""
        golden = self._manifest(slot.app).get("HOLO_GOLDEN", "")
        if not golden:
            return True
        current, mine = self._golden_stamp(Path(golden)), self._golden_stamp(Path(slot.ws_dir))
        if current is None or mine is None:
            return True
        if current != mine:
            log.warning("pool: slot %s was cloned from a previous golden — retiring it",
                        slot.slot_id)
            return False
        return True

    def _pool_verify(self, slot: PoolSlot) -> bool:
        """Is this slot actually serving the CURRENT golden? ~50ms.

        Deliberately NOT a seed-count query: that needs a container exec (~1s of
        compose overhead) on every claim and every reconcile tick. The golden was
        seed-proven at build time and the slot is a clone of it, and for
        `compliance` HOLO_READINESS_PATH (/readiness) runs SELECT 1 — so a 200
        already proves the app is up AND its database is reachable."""
        try:
            m = self._manifest(slot.app)
        except ProviderError:
            return False
        # Staleness first: it's two small file reads, and a stale slot must be
        # retired even when it is perfectly healthy.
        if not self._pool_slot_is_current(slot):
            return False
        scheme = m.get("HOLO_READINESS_SCHEME") or "http"
        url = f"{scheme}://127.0.0.1:{slot.port}{m['HOLO_READINESS_PATH']}"
        argv = ["curl", "-fsS", "-m", "5"] + (["-k"] if scheme == "https" else []) + [url]
        r = self._run(argv, self.scripts, dict(os.environ), timeout=10)
        return bool(r and r.returncode == 0)

    def _pool_destroy(self, slot: PoolSlot) -> None:
        subprocess.run(["./destroy.sh", slot.slot_id], cwd=self.scripts,
                       env=_base_env(slot.app), shell=False, capture_output=True,
                       text=True, timeout=300)
        if self._ports is not None:
            self._ports.release(slot.port)

    def _claim_slot(self, slot: PoolSlot, lease_id: str, app: str, ticket: str,
                    target_repo: Optional[str] = None,
                    base_overrides: Optional[dict[str, str]] = None) -> Optional[WorkspaceHandle]:
        """Bind a warm slot to a ticket. This is the whole fast path.

        `checkout -B`, not `-b`: a retried ticket (a previously FAILED lease) may
        already have its branch here. hooksPath=/dev/null because a repo-level
        post-checkout hook would otherwise run on every claim and is exactly the
        kind of thing that turns a 50ms operation into seconds.

        The branch is cut in _git_root(), NOT in ws_dir. For a composite like
        compliance-ui, ws_dir is a plain directory holding five separate clones
        and is not a git repo at all — branching there fails, and treating that
        as fatal would destroy a good slot on every claim, so the pool would
        thrash (boot 2min, claim, destroy, repeat) instead of ever serving.
        target_repo (already validated by the caller) picks the checkout when the
        lease specifies one; HOLO_GIT_SUBDIR is the fallback otherwise.

        Fetches first, same as strike.sh's cold path, so a warm slot doesn't hand
        out a branch frozen at however-stale the pool slot's own boot was —
        best-effort, same tolerance as the branch cut itself below.

        The pool marker is removed: the workspace now belongs to a lease, so a
        later restart must treat it as a normal workspace, not a free slot.
        """
        ws = Path(slot.ws_dir)
        git_root = self._git_root(app, ws, target_repo)
        fetch = self._run(["git", "-C", str(git_root), "fetch", "--quiet", "origin"],
                          self.scripts, dict(os.environ), timeout=60)
        default_branch = ""
        if fetch is not None and fetch.returncode == 0:
            sym = self._run(["git", "-C", str(git_root), "symbolic-ref", "--short",
                             "refs/remotes/origin/HEAD"],
                            self.scripts, dict(os.environ), timeout=15)
            if sym is not None and sym.returncode == 0:
                default_branch = sym.stdout.strip().removeprefix("origin/")
        checkout_argv = ["git", "-C", str(git_root), "-c", "core.hooksPath=/dev/null",
                          "checkout", "-B", f"agent/{lease_id}"]
        if default_branch:
            checkout_argv.append(f"origin/{default_branch}")
        r = self._run(checkout_argv, self.scripts, dict(os.environ), timeout=60)
        if r is None or r.returncode != 0:
            # Match strike.sh, which warns rather than failing here: a workspace
            # whose branch didn't get cut is still a usable environment, and the
            # cold path has always tolerated it. Failing would be a REGRESSION
            # against the cold path, not a safety improvement.
            log.warning("pool: branch cut for agent/%s failed in %s (%s) — serving the "
                        "slot anyway, as strike.sh does", lease_id, git_root,
                        (r.stderr.strip()[:200] if r else "timeout"))
        if base_overrides:
            for dep_repo, ref in base_overrides.items():
                try:
                    dep_root = self._git_root(app, ws, dep_repo)
                    self._run(["git", "-C", str(dep_root), "fetch", "--quiet", "origin", ref],
                              self.scripts, dict(os.environ), timeout=60)
                    checkout_dep = self._run(
                        ["git", "-C", str(dep_root), "-c", "core.hooksPath=/dev/null", "checkout", "--quiet", ref],
                        self.scripts, dict(os.environ), timeout=30
                    )
                    if checkout_dep and checkout_dep.returncode == 0:
                        log.info("pool: checked out dependency %s at %s for lease %s", dep_repo, ref, lease_id)
                    else:
                        log.warning("pool: failed checking out dependency %s at %s for lease %s", dep_repo, ref, lease_id)
                except Exception as e:
                    log.warning("pool: exception checking out dependency %s: %s", dep_repo, e)
        try:
            (ws / POOL_MARKER).unlink()
        except OSError:
            pass
        return WorkspaceHandle(
            lease_id=lease_id, app=app, ticket=ticket, preview_port=slot.port,
            compose_project=slot.compose_project, ws_dir=str(ws),
            golden_head=self._git_head(git_root), target_repo=target_repo,
            base_overrides=base_overrides,
        )

    # ---- lifecycle ---------------------------------------------------------
    def acquire(
        self, lease_id: str, app: str, ticket: str, preview_port: Optional[int],
        target_repo: Optional[str] = None,
        base_overrides: Optional[dict[str, str]] = None,
    ) -> WorkspaceHandle:
        # Fast path: a pre-booted warm slot turns a ~2min strike into a branch
        # cut + a readiness GET. Falls through to the cold path on any miss —
        # pool disabled, empty, or the slot failed verification — so pooling is
        # a pure optimisation and never load-bearing for correctness.
        slot = self.pool.claim(app)
        if slot is not None:
            t0 = time.monotonic()
            handle = self._claim_slot(slot, lease_id, app, ticket, target_repo, base_overrides=base_overrides)
            if handle is not None:
                log.info("strike SERVED FROM POOL lease=%s slot=%s port=%s in %.2fs",
                         lease_id, slot.slot_id, slot.port, time.monotonic() - t0)
                return handle

        argv = ["./strike.sh", ticket]
        if preview_port is not None:
            argv += ["--preview", str(preview_port)]
        log.info("strike starting lease=%s app=%s ticket=%s preview_port=%s target_repo=%s",
                 lease_id, app, ticket, preview_port, target_repo)
        t0 = time.monotonic()
        # Stream strike.sh output to temp FILES, not capture_output pipes, and
        # feed it /dev/null on stdin. A pipe left open by a daemon child (docker
        # leaves containers holding the inherited fd) makes a captured wait's
        # `timeout` ineffective — the kill fires but the follow-up read of the
        # pipe blocks forever waiting for it to close, so `acquire` never returns
        # and (single-worker) the whole control plane wedges. Files have no such
        # backpressure, so a timeout (ours below, or cancel_acquire's kill) always
        # lets us actually read the output back.
        #
        # Popen, not run(): a lease_id -> Popen registered in self._inflight is
        # what lets cancel_acquire() kill this specific strike from another
        # thread while we're blocked in wait() below — release() has no handle
        # to call provider.release() with yet (that only exists once acquire()
        # returns), so a kill switch on the subprocess itself is the only way to
        # stop a strike a caller has already given up on.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(
                argv, cwd=self.scripts, env=_base_env(app, target_repo), shell=False,
                stdin=subprocess.DEVNULL, stdout=out, stderr=err,
            )
            with self._inflight_lock:
                self._inflight[lease_id] = proc
            try:
                try:
                    proc.wait(timeout=600)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    log.warning("strike TIMED OUT lease=%s after 600s — killed", lease_id)
                    raise ProviderError(
                        f"strike.sh for '{lease_id}' exceeded 600s and was killed "
                        "(check the warm host / docker; see destroy.sh to reclaim)")
            finally:
                with self._inflight_lock:
                    self._inflight.pop(lease_id, None)
            out.seek(0); err.seek(0)
            combined = (out.read() + err.read()).decode(errors="replace")
        elapsed = time.monotonic() - t0
        if proc.returncode != 0:
            log.warning("strike FAILED lease=%s rc=%s (%.1fs): %s",
                       lease_id, proc.returncode, elapsed, combined[-2000:])
            if _EXISTS_MARKER in combined:
                raise WorkspaceExistsError(f"workspace '{lease_id}' already exists")
            raise ProviderError(f"strike.sh failed (rc={proc.returncode}): {combined[-2000:]}")

        # Absolute, manifest-resolved. Guessing this produced a relative path and a
        # FileNotFoundError the moment finalize used it as a cwd.
        ws_dir = Path(self._manifest(app)["HOLO_WORKSPACES"]) / lease_id
        if base_overrides:
            for dep_repo, ref in base_overrides.items():
                try:
                    dep_root = self._git_root(app, ws_dir, dep_repo)
                    self._run(["git", "-C", str(dep_root), "fetch", "--quiet", "origin", ref],
                              self.scripts, dict(os.environ), timeout=60)
                    self._run(
                        ["git", "-C", str(dep_root), "-c", "core.hooksPath=/dev/null", "checkout", "--quiet", ref],
                        self.scripts, dict(os.environ), timeout=30
                    )
                    log.info("cold strike: checked out dependency %s at %s for lease %s", dep_repo, ref, lease_id)
                except Exception as e:
                    log.warning("cold strike: exception checking out dependency %s: %s", dep_repo, e)
        golden_head = self._git_head(self._git_root(app, ws_dir, target_repo))
        log.info("strike succeeded lease=%s ws_dir=%s golden_head=%s target_repo=%s (%.1fs)",
                 lease_id, ws_dir, golden_head, target_repo, elapsed)
        handle = WorkspaceHandle(
            lease_id=lease_id, app=app, ticket=ticket, preview_port=preview_port,
            compose_project=f"ws-{lease_id}", ws_dir=str(ws_dir), golden_head=golden_head,
            target_repo=target_repo, base_overrides=base_overrides,
        )
        # Best-effort seeded-row snapshot for the console status (never fails the
        # strike; the workspace is already up + seeded here).
        try:
            handle.seed_rows = self._seed_count(handle)
        except Exception:
            log.exception("seed-count capture failed for %s (non-fatal)", lease_id)
        return handle

    def _seed_count(self, handle: WorkspaceHandle) -> Optional[int]:
        """Run the manifest's HOLO_SEED_PROOF_SQL and return the count (or None).
        Shared by strike (status snapshot) and finalize (notary)."""
        m = self._manifest(handle.app)
        proof_sql = m.get("HOLO_SEED_PROOF_SQL")
        if not proof_sql:
            return None
        ws = Path(handle.ws_dir) if handle.ws_dir else self.cfg.holo_dir
        env = {**_base_env(handle.app),
               "COMPOSE_PROJECT_NAME": handle.compose_project or f"ws-{handle.lease_id}"}
        r = self._run(self._dc(handle) + ["exec", "-T", m["HOLO_PG_SERVICE"], "psql",
                                          "-U", m["HOLO_PG_USER"], "-d", m["HOLO_PG_DB"],
                                          "-tAc", proof_sql], ws, env, timeout=30)
        if r and r.returncode == 0:
            digits = r.stdout.strip()
            return int(digits) if digits.isdigit() else None
        return None

    def finalize(self, handle: WorkspaceHandle, test_cmd: Optional[str]) -> Evidence:
        """Host-side notary (R2/D1/V7). Re-derives every claim; reads nothing the
        agent wrote. `test_cmd` is resolved by the API from the manifest/ticket —
        finalize has no request field for it, so an agent cannot supply one."""
        proj = handle.compose_project or f"ws-{handle.lease_id}"
        ws = Path(handle.ws_dir) if handle.ws_dir else self.cfg.holo_dir
        env = {**_base_env(handle.app), "COMPOSE_PROJECT_NAME": proj}

        m = self._manifest(handle.app)      # every value below comes from the manifest
        dc = self._dc(handle)

        # seed proof — re-run the manifest query ourselves (shared with strike).
        seed_rows = self._seed_count(handle)

        # readiness — hit the preview port if we have one. Scheme is manifest-driven
        # (HOLO_READINESS_SCHEME): TLS entrypoints (main nginx:443, compliance-ui
        # qong:8989) serve a self-signed cert, so https is polled with -k. Mirrors the
        # scheme-aware poll in strike.sh so the notary and strike agree.
        readiness, readiness_ok = "", False
        if handle.preview_port is not None:
            path = m["HOLO_READINESS_PATH"]
            scheme = m.get("HOLO_READINESS_SCHEME") or "http"
            curl = ["curl", "-fsS"] + (["-k"] if scheme == "https" else [])
            url = f"{scheme}://127.0.0.1:{handle.preview_port}{path}"
            cr = self._run(curl + [url], ws, env, timeout=15)
            if cr and cr.returncode == 0:
                readiness, readiness_ok = cr.stdout.strip(), True

        # migration head (freshness stamp, closes V12).
        schema_rev = None
        sr = self._run(dc + ["exec", "-T", m["HOLO_APP_SERVICE"],
                             "alembic", "current"], ws, env, timeout=30)
        if sr and sr.returncode == 0:
            schema_rev = sr.stdout.strip().splitlines()[-1] if sr.stdout.strip() else None

        # diff = golden_head..HEAD (issue #5) — total change vs the struck snapshot.
        # Scoped to HOLO_GIT_SUBDIR when the app sets one (composites: the workspace
        # root itself isn't a git repo — see _git_root).
        diff = ""
        branch_note = None
        if handle.golden_head:
            git_root = self._git_root(handle.app, ws, handle.target_repo)
            dr = self._run(["git", "-C", str(git_root), "diff", f"{handle.golden_head}..HEAD"],
                           ws, env, timeout=30)
            diff = dr.stdout if dr else ""
            if not diff.strip():
                diff, branch_note = self._recover_stray_branch(
                    git_root, handle.golden_head, handle.lease_id, ws, env)

        # services booted vs absent (D11).
        booted, absent = self._service_status(dc, ws, env, m)

        # the test — the ONLY command run, and it never came from the agent.
        test_exit: Optional[int] = None
        test_output, timed_out = "", False
        if test_cmd:
            svc = self._test_service(handle.app, handle.target_repo, m)
            tr = self._run(dc + ["exec", "-T", svc, "sh", "-lc", test_cmd], ws, env,
                           timeout=self.cfg.finalize_timeout_s)
            if tr is None:  # timed out (issue #6): record, don't raise.
                timed_out, test_output = True, "test exceeded finalize timeout"
            else:
                test_exit = tr.returncode
                test_output = ((tr.stdout or "") + (tr.stderr or ""))[-8000:]

        # Guardrail audits (Phase 4): Blast Radius & AST Test Integrity
        guardrail_passed = True
        guardrail_reason = None
        if handle.golden_head and diff and diff.strip():
            git_root = self._git_root(handle.app, ws, handle.target_repo)
            cfr = self._run(["git", "-C", str(git_root), "diff", "--name-only", f"{handle.golden_head}..HEAD"],
                            ws, env, timeout=15)
            changed_files = [f.strip() for f in (cfr.stdout if cfr else "").splitlines() if f.strip()]

            # 1. Blast radius audit
            br_ok, br_reason = audit_blast_radius(diff, changed_files)
            if not br_ok:
                guardrail_passed = False
                guardrail_reason = br_reason
            else:
                # 2. AST test integrity verification
                def _run_git(cmd):
                    r = self._run(cmd, ws, env, timeout=15)
                    return r.stdout if r and r.returncode == 0 else None

                ast_ok, ast_reason = verify_ast_test_integrity(git_root, handle.golden_head, changed_files, _run_git)
                if not ast_ok:
                    guardrail_passed = False
                    guardrail_reason = ast_reason

        return Evidence(
            readiness=readiness, readiness_ok=readiness_ok, seed_rows=seed_rows,
            test_cmd=test_cmd, test_exit=test_exit, test_output=test_output,
            test_timed_out=timed_out, diff=diff, golden_head=handle.golden_head,
            schema_rev=schema_rev, services_booted=booted, services_absent=absent,
            branch_note=branch_note,
            guardrail_passed=guardrail_passed, guardrail_reason=guardrail_reason,
        )

    def _recover_stray_branch(self, git_root: Path, golden_head: str, lease_id: str,
                              ws: Path, env: dict) -> tuple[str, Optional[str]]:
        """Safety net for a real 2026-08-30 incident, not a hypothetical: an agent
        committed to its own, differently-named branch instead of `agent/<lease_id>`
        (already checked out for it at claim time — see strike.sh/pool.py), leaving
        `git diff golden_head..HEAD` silently empty despite real, committed work
        sitting one `git checkout` away. `_BEHAVIOR_PREAMBLE` now tells the agent not
        to do this, but a prompt instruction is not a guarantee — this is the actual
        backstop, and it does more than just report the truth: `open_pr()` always
        pushes/PRs from `agent/<lease_id>` by name, so merely reporting a different
        branch's diff here would still produce an EMPTY PR on GitHub. Instead, if
        exactly one other local branch has commits beyond golden_head that
        `agent/<lease_id>` doesn't already include, force that ref to point at the
        stray branch's tip (a plain branch-pointer move, no working-tree/HEAD
        change) so `open_pr()`'s later push picks up the real content unmodified.
        More than one candidate is genuinely ambiguous — reported, not guessed at.
        """
        expected = f"agent/{lease_id}"
        br = self._run(["git", "-C", str(git_root), "for-each-ref",
                        "--format=%(refname:short)", "refs/heads/"], ws, env, timeout=15)
        if br is None or br.returncode != 0:
            return "", None
        candidates = []
        for branch in br.stdout.split():
            if branch == expected:
                continue
            cr = self._run(["git", "-C", str(git_root), "rev-list",
                            f"{expected}..{branch}", "--count"], ws, env, timeout=15)
            if cr and cr.returncode == 0 and cr.stdout.strip().isdigit() \
                    and int(cr.stdout.strip()) > 0:
                candidates.append(branch)

        if not candidates:
            return "", None

        if len(candidates) > 1:
            return "", (
                f"{expected} shows no diff, and MULTIPLE branches have commits "
                f"beyond it not reflected there ({', '.join(candidates)}) — too "
                f"ambiguous to pick one automatically. Check these branches "
                f"manually; finalize did NOT open a PR."
            )

        stray = candidates[0]
        fixup = self._run(["git", "-C", str(git_root), "branch", "-f", expected, stray],
                          ws, env, timeout=15)
        if fixup is None or fixup.returncode != 0:
            return "", (
                f"{expected} shows no diff; found likely stray branch '{stray}' "
                f"with the real commits, but could not repoint {expected} to it "
                f"({(fixup.stderr if fixup else 'timeout')[:200]!r}). Check "
                f"'{stray}' manually; finalize did NOT open a PR."
            )
        dr = self._run(["git", "-C", str(git_root), "diff", f"{golden_head}..{expected}"],
                       ws, env, timeout=30)
        return (dr.stdout if dr else ""), (
            f"{expected} originally showed no diff — the agent had committed to "
            f"'{stray}' instead of the branch already checked out for it. "
            f"Repointed {expected} to '{stray}''s tip before diffing/opening the PR."
        )

    def open_pr(self, handle: WorkspaceHandle, *, base: str, draft: bool,
                title: str, body: str, label: Optional[str] = None) -> str:
        """E2: push agent/<id> and open a draft PR via `gh`. Uses the operator's
        real process env (PATH/HOME/gh creds), not the curated finalize env.
        Idempotent: returns an existing PR for the branch instead of erroring."""
        if not handle.ws_dir:
            raise ProviderError("no ws_dir on the lease handle; cannot open a PR")
        if shutil.which("gh") is None:
            # fail fast (before pushing a branch) with an actionable message —
            # finalize catches this and stamps the evidence without a PR.
            raise ProviderError(
                "`gh` CLI not found on the control-plane host; install gh (+ auth) "
                "or set HOLODECK_PR=0 to let the agent open its own PR")
        git_root = self._git_root(handle.app, Path(handle.ws_dir), handle.target_repo)
        branch = f"agent/{handle.lease_id}"
        env = {**os.environ}  # gh + git push need the operator's real env/creds
        # idempotent: return the existing PR for this branch rather than duplicating.
        view = self._run(["gh", "pr", "view", branch, "--json", "url", "-q", ".url"],
                         git_root, env, timeout=30)
        if view and view.returncode == 0 and view.stdout.strip().startswith("http"):
            url = view.stdout.strip()
            self._attach_label(git_root, env, url, label)  # re-finalize keeps the label current
            return url
        push = self._run(["git", "-C", str(git_root), "push", "-u", "origin", branch],
                         git_root, env, timeout=120)
        if push is None or push.returncode != 0:
            raise ProviderError(
                f"git push failed for {branch}: {(push.stderr if push else 'timeout')[:300]}")
        pr_body = body
        if handle.base_overrides:
            dep_lines = "\n".join(f"- `{repo}`: `{ref}`" for repo, ref in handle.base_overrides.items())
            pr_body = f"{body}\n\n### 🔗 Upstream Dependencies\nThis pull request was developed and verified against unmerged branch(es):\n{dep_lines}\n"
        args = ["gh", "pr", "create", "--base", base, "--head", branch,
                "--title", title, "--body", pr_body]
        if draft:
            args.append("--draft")
        cr = self._run(args, git_root, env, timeout=60)
        if cr is None or cr.returncode != 0:
            raise ProviderError(f"gh pr create failed: {(cr.stderr if cr else 'timeout')[:300]}")
        out = (cr.stdout or "").strip()
        url = out.splitlines()[-1] if out else ""
        self._attach_label(git_root, env, url, label)
        return url

    def _attach_label(self, git_root, env, url: str, label: Optional[str]) -> None:
        """Best-effort: ensure the label exists, then add it to the PR. A label
        failure (missing scope, name clash, gh hiccup) must NEVER fail the PR —
        the PR is the artifact; the label is a convenience for the reviewer."""
        if not label or not url:
            return
        # `gh label create` returns non-zero if the label already exists — fine.
        self._run(["gh", "label", "create", label, "--color", "1a7a5b",
                   "--description", "Holodeck preview environment"], git_root, env, timeout=30)
        r = self._run(["gh", "pr", "edit", url, "--add-label", label], git_root, env, timeout=30)
        if r is None or r.returncode != 0:
            log.warning("open_pr: could not add label %r to %s (PR still created): %s",
                        label, url, (r.stderr[:200] if r else "timeout"))

    def release(self, handle: WorkspaceHandle) -> None:
        # Tear down by WORKSPACE id, not by ticket. destroy.sh derives the dir and
        # compose project from holo_id($1), and for a normal strike
        # basename(ws_dir) == holo_id(ticket), so this is identical to passing the
        # ticket. It diverges only when the lease was served from a warm pool slot:
        # the workspace is then `pool-02`, not `comp-6542`, and passing the ticket
        # would destroy nothing while leaking the real slot. holo_id() is idempotent
        # on an already-normalised id, so basename is safe to hand it either way.
        target = Path(handle.ws_dir).name if handle.ws_dir else handle.ticket
        proc = subprocess.run(
            ["./destroy.sh", target], cwd=self.scripts,
            env=_base_env(handle.app), shell=False, capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            log.warning("destroy FAILED lease=%s rc=%s: %s", handle.lease_id, proc.returncode,
                       ((proc.stdout or "") + (proc.stderr or ""))[-2000:])
            raise ProviderError(
                f"destroy.sh failed (rc={proc.returncode}): "
                f"{(proc.stdout or '') + (proc.stderr or '')}"[-2000:]
            )
        log.info("destroy succeeded lease=%s", handle.lease_id)

    def cancel_acquire(self, lease_id: str) -> None:
        """Kill the strike.sh subprocess for lease_id, if one is still running.

        Doesn't run destroy.sh itself — killing strike.sh makes the blocked
        acquire() call in the strike thread raise, and that thread's own
        except-branch (LeaseService._strike) does the actual workspace rollback,
        the same path any other acquire() failure already goes through."""
        with self._inflight_lock:
            proc = self._inflight.get(lease_id)
        if proc is None or proc.poll() is not None:
            log.info("cancel_acquire lease=%s: no in-flight strike (already finished)", lease_id)
            return
        log.warning("cancel_acquire lease=%s: killing in-flight strike (pid=%s)", lease_id, proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

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
        """Scoped exec (B3): `docker compose -p ws-<id> exec -T <svc> <argv>`.

        Same compose-file set + COMPOSE_PROJECT_NAME as strike.sh/finalize, so it
        targets exactly this lease's stack. `-T` disables TTY allocation (there's
        no client PTY over HTTP — the WS/PTY path stays reserved). argv is a list,
        shell=False (issue #1): a caller string never becomes a shell command.

        `detach=True` uses `docker compose exec -d`: docker starts the process in
        the background and returns at once, so we never attach/capture its stdio.
        This is what launches long-running daemons (the sandbox provider's
        `start_host` → `omnigent host`). Without it, `-T` + `capture_output`
        blocks until the daemon exits (never) — which stalls `start_host` and, on
        the single-worker control plane, wedges every other request."""
        if not argv:
            raise ProviderError("exec requires a non-empty command")
        m = self._manifest(handle.app)
        svc = service or m["HOLO_APP_SERVICE"]
        proj = handle.compose_project or f"ws-{handle.lease_id}"
        ws = Path(handle.ws_dir) if handle.ws_dir else self.cfg.holo_dir
        env = {**_base_env(handle.app), "COMPOSE_PROJECT_NAME": proj}

        # `-d` (detached) and `-T` are mutually exclusive; -d never allocates a TTY.
        cmd = self._dc(handle) + ["exec", "-d" if detach else "-T"]
        if workdir:
            cmd += ["-w", workdir]
        cmd += [svc, *argv]

        # A detached start returns immediately; cap it low so a hung `exec -d`
        # can't itself block (it shouldn't, but the single worker must stay live).
        timeout = timeout_s or (15 if detach else self.cfg.exec_timeout_s)
        try:
            p = subprocess.run(cmd, cwd=ws, env=env, shell=False,
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("exec TIMED OUT lease=%s svc=%s detach=%s argv=%r (>%ss)",
                        handle.lease_id, svc, detach, argv, timeout)
            # 124 = conventional timeout exit code; surface it rather than raising.
            return ExecResult(argv=argv, exit_code=124, stdout="",
                             stderr=f"exec exceeded {timeout}s and was killed")
        # One line per exec, always — this is the only server-side record of what
        # ran and how it went; the uvicorn access log only has method/path/status.
        # Not just failures: a "successful" 200 whose command actually exited
        # non-zero (e.g. the mkdir-into-the-wrong-container case) is exactly the
        # kind of thing that's invisible without this.
        level = log.info if p.returncode == 0 else log.warning
        level("exec lease=%s svc=%s detach=%s rc=%s argv=%r stdout=%r stderr=%r",
              handle.lease_id, svc, detach, p.returncode, argv,
              (p.stdout or "")[:_LOG_OUTPUT_CHARS], (p.stderr or "")[:_LOG_OUTPUT_CHARS])
        if detach:
            # No streamed output in detached mode — the process runs on in the bg.
            return ExecResult(argv=argv, exit_code=p.returncode,
                             stdout="detached", stderr=p.stderr)
        return ExecResult(argv=argv, exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr)

    async def stream_exec(
        self,
        handle: WorkspaceHandle,
        argv: list[str],
        *,
        service: Optional[str] = None,
        workdir: Optional[str] = None,
    ) -> AsyncIterator[tuple[str, str]]:
        """Streaming exec for the WS gateway: same scoped `docker compose exec -T`
        as the one-shot path, but stdout/stderr are relayed line-by-line as they
        arrive, then a final ('exit', code). Uses an async subprocess so one slow
        command never blocks the event loop."""
        if not argv:
            raise ProviderError("exec requires a non-empty command")
        m = self._manifest(handle.app)
        svc = service or m["HOLO_APP_SERVICE"]
        proj = handle.compose_project or f"ws-{handle.lease_id}"
        ws = str(Path(handle.ws_dir) if handle.ws_dir else self.cfg.holo_dir)
        env = {**_base_env(handle.app), "COMPOSE_PROJECT_NAME": proj}

        cmd = self._dc(handle) + ["exec", "-T"]
        if workdir:
            cmd += ["-w", workdir]
        cmd += [svc, *argv]

        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=ws, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        # interleave both streams onto one queue so ordering reflects arrival.
        queue: asyncio.Queue = asyncio.Queue()

        async def drain(stream, channel: str) -> None:
            while True:
                line = await stream.readline()
                if not line:
                    break
                await queue.put((channel, line.decode(errors="replace")))
            await queue.put(None)

        drains = [
            asyncio.create_task(drain(proc.stdout, "stdout")),
            asyncio.create_task(drain(proc.stderr, "stderr")),
        ]
        finished = 0
        while finished < len(drains):
            item = await queue.get()
            if item is None:
                finished += 1
                continue
            yield item
        await proc.wait()
        yield ("exit", str(proc.returncode))

    def put(
        self,
        handle: WorkspaceHandle,
        remote_path: str,
        content: bytes,
        *,
        service: Optional[str] = None,
    ) -> None:
        """`docker compose -p ws-<id> cp <hosttmp> <svc>:<remote_path>`.

        We hold bytes, not a host path, so stage them in a temp file and let
        `compose cp` (argv, shell=False) place them — no `cat >`-into-a-shell, so
        `remote_path` can't inject. Targets the running container, independent of
        whether the checkout is bind-mounted or baked into the image."""
        if not remote_path:
            raise ProviderError("put requires a non-empty remote path")
        m = self._manifest(handle.app)
        svc = service or m["HOLO_APP_SERVICE"]
        proj = handle.compose_project or f"ws-{handle.lease_id}"
        ws = Path(handle.ws_dir) if handle.ws_dir else self.cfg.holo_dir
        env = {**_base_env(handle.app), "COMPOSE_PROJECT_NAME": proj}

        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            tmp.write(content)
            tmp.close()
            cmd = self._dc(handle) + ["cp", tmp.name, f"{svc}:{remote_path}"]
            p = subprocess.run(cmd, cwd=ws, env=env, shell=False,
                               capture_output=True, text=True, timeout=60)
            if p.returncode != 0:
                raise ProviderError(
                    f"docker compose cp failed (rc={p.returncode}): "
                    f"{((p.stdout or '') + (p.stderr or ''))[-1000:]}"
                )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def prepare(self, app: Optional[str] = None) -> list[str]:
        """Substrate preflight (no lease needed): scripts present, docker
        reachable, and the relevant app's manifest resolves + its golden exists.

        Substrate checks (scripts, docker) ALWAYS run — they gate every app.
        The per-app checks are scoped by `app`:

          app="compliance"  -> only compliance's manifest + golden
          app=None          -> every app in the manifests dir (operator view)

        Scoping matters because this fails CLOSED. `cfg.apps` is every
        manifests/*.sh on disk, so an unbuilt golden for an app nobody asked
        for (e.g. a half-finished compliance-ui) used to make /readyz 503 and
        `HolodeckSandboxLauncher.prepare()` raise — blocking Omnigent sessions
        for apps whose goldens were fine. An unknown app name is itself a
        problem rather than a silent full scan.
        """
        problems: list[str] = []
        for script in ("strike.sh", "destroy.sh", "holo-env.sh"):
            if not (self.scripts / script).exists():
                problems.append(f"missing scripts/{script}")
        dv = self._run(["docker", "version"], self.scripts, dict(os.environ), timeout=15)
        if dv is None or dv.returncode != 0:
            problems.append("docker not reachable (`docker version` failed)")

        if app is None:
            apps = sorted(self.cfg.apps)
        elif app in self.cfg.apps:
            apps = [app]
        else:
            # Don't fall back to scanning everything: a typo'd app would then be
            # reported as "ready" on the strength of OTHER apps' goldens.
            return problems + [
                f"unknown app '{app}' (known: {', '.join(sorted(self.cfg.apps)) or 'none'})"
            ]

        for a in apps:
            try:
                m = self._manifest(a)
            except ProviderError as e:
                problems.append(f"manifest resolve failed for '{a}': {e}")
                continue
            golden = m.get("HOLO_GOLDEN")
            if golden and not Path(golden).exists():
                problems.append(f"golden image for '{a}' not found at {golden}")
        return problems

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            file_copy=True, one_shot_exec=True, streaming_exec=True,
            programmatic_terminate=True, preview_port=True, resume_stopped=False,
        )

    def list_orphans(self) -> list[WorkspaceHandle]:
        """Workspace dirs under $HOLO_WORKSPACES (issue #4). The reconciler in
        main.py decides whether to reap or just refuse to reuse their ports.

        Warm-pool slots are deliberately EXCLUDED. They are unclaimed workspaces
        with no lease by design, so they look exactly like orphans — and under
        HOLODECK_RECONCILE=reap the reconciler would destroy the entire pool on
        every control-plane restart. The pool manager owns their lifecycle and
        re-adopts them itself at startup (including re-reserving their ports)."""
        root = self._ws_root()
        if root is None or not root.is_dir():
            return []
        ports = self._published_ports_by_project()
        out = []
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            if (p / POOL_MARKER).exists():
                continue  # a warm-pool slot, not an orphan — see docstring
            proj = f"ws-{p.name}"
            # A directory does not record its port, so ask Docker. Without this the
            # reconciler could list an orphan but not reserve what it holds — which is
            # how a restart handed out 18000 while api-1 was still bound to it.
            held = sorted(ports.get(proj, ()))
            out.append(WorkspaceHandle(
                lease_id=p.name, app=self._infer_app(p), ticket=p.name,
                preview_port=held[0] if held else None,
                compose_project=proj, ws_dir=str(p),
            ))
        return out

    def _published_ports_by_project(self) -> dict[str, set[int]]:
        """Host ports currently published, grouped by compose project.

        Only PUBLISHED ports appear as `127.0.0.1:18000->8080/tcp`; container-internal
        ones show as bare `5432/tcp` and consume no host port, so the regex naturally
        ignores them.
        """
        out: dict[str, set[int]] = {}
        r = self._run(["docker", "ps", "--format",
                       '{{.Label "com.docker.compose.project"}}\t{{.Ports}}'],
                      self.scripts, dict(os.environ), timeout=20)
        if not r or r.returncode != 0:
            return out
        for line in r.stdout.splitlines():
            proj, _, portstr = line.partition("\t")
            if not proj:
                continue
            for m in re.finditer(r":(\d+)->", portstr):
                out.setdefault(proj, set()).add(int(m.group(1)))
        return out

    def _infer_app(self, ws_dir: Path) -> str:
        """Which app an orphan belongs to, from the golden stamp the clone carried.

        golden-build.compliance.sh writes `app=<name>` into .holodeck-golden; main's
        golden omits it. Getting this right matters for reap mode: destroy.sh needs the
        correct HOLO_APP or it loads the wrong compose file and fails.
        """
        stamp = ws_dir / ".holodeck-golden"
        try:
            for line in stamp.read_text().splitlines():
                for tok in line.split():
                    if tok.startswith("app="):
                        return tok[4:]
        except OSError:
            pass
        return "main"

    # ---- helpers -----------------------------------------------------------
    def _git_root(self, app: str, ws_dir: Path, target_repo: Optional[str] = None) -> Path:
        """The directory golden_head/diff/PR actually operate on for `app`.

        Single-repo apps (main, compliance): ws_dir itself. Composites
        (compliance-ui, control-tower): ws_dir is a plain directory holding several
        separate clones, not a git repo — `git rev-parse HEAD` there fails and the
        diff is silently empty.

        target_repo (a per-LEASE value, already validated against
        valid_target_repos(app) before it ever reaches here) wins when set — this is
        what makes a ticket's own repo choice actually take effect end to end. Falls
        back to the manifest's static HOLO_GIT_SUBDIR otherwise (single-repo apps, or
        a composite lease acquired without picking one — see manifests/
        compliance-ui.sh / control-tower.sh).
        """
        subdir = target_repo or self._manifest(app).get("HOLO_GIT_SUBDIR", "")
        return (ws_dir / subdir) if subdir else ws_dir

    def valid_target_repos(self, app: str) -> set[str]:
        """The app's HOLO_COMPOSITE_REPOS, from the manifest. Empty for a
        single-repo app — there is nothing to validate a target_repo against, so
        the API rejects any caller-supplied value for such an app rather than
        silently accepting and ignoring it."""
        raw = self._manifest(app).get("HOLO_COMPOSITE_REPOS", "")
        return set(raw.split())

    def _git_head(self, ws_dir: Path) -> Optional[str]:
        try:
            r = subprocess.run(
                ["git", "-C", str(ws_dir), "rev-parse", "HEAD"],
                shell=False, capture_output=True, text=True, timeout=15,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    def _run(self, argv: list[str], cwd: Path, env: dict[str, str], timeout: int):
        """Run a re-derivation command; return the CompletedProcess, or None on
        timeout (finalize records timed_out rather than raising — issue #6).
        Never shell=True."""
        try:
            return subprocess.run(argv, cwd=cwd, env=env, shell=False,
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        except (FileNotFoundError, OSError):
            # the binary (e.g. `gh`) isn't installed on the host — treat like a
            # timeout (return None) so callers degrade instead of raising.
            return None

    def _service_status(self, dc: list[str], cwd: Path, env: dict[str, str],
                        m: dict[str, str]):
        """Which WS_SERVICES are running vs not (D11). WS_SERVICES comes from the
        manifest — guessing "" made this stamp silently empty for every lease."""
        want = m.get("WS_SERVICES", "").split()
        r = self._run(dc + ["ps", "--services", "--status", "running"], cwd, env, timeout=20)
        running = set(r.stdout.split()) if r and r.returncode == 0 else set()
        booted = [s for s in want if s in running]
        absent = [s for s in want if s not in running]
        return booted, absent

    def _dc(self, handle: WorkspaceHandle) -> list[str]:
        """docker compose base argv matching strike.sh's file set."""
        m = self._manifest(handle.app)
        # compliance uses compose.yaml, main uses docker-compose.yaml — guessing either
        # one breaks the other app entirely.
        cf = m["HOLO_COMPOSE_FILE"]
        files = ["-f", cf, "-f", "compose.ws.yaml"]
        ov = m.get("HOLO_PGDATA_OVERRIDE", "")
        if ov:
            files += ["-f", str(self.cfg.holo_dir / ov)]
        return ["docker", "compose", *files]
