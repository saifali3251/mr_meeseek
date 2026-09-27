"""Golden image synchronization & rebuild manager.

Implements a Debounced State Machine and Single-Flight Coalescer to automatically
keep the golden images fresh when changes are pushed/merged to the main branch
of any underlying repository (including composite multi-repo applications),
without build-queue explosion or race conditions.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from holodeck.config import Config

log = logging.getLogger("holodeck.golden_sync")


class GoldenSyncManager:
    """Manages background golden image builds, debouncing rapid-fire merges,
    and draining stale warm pool slots upon build success.
    """

    def __init__(self, cfg: Config, pool=None) -> None:
        self.cfg = cfg
        self.pool = pool
        self._lock = threading.Lock()
        # Per-app state: "IDLE" | "DEBOUNCING" | "BUILDING"
        self._state: dict[str, str] = {}
        self._dirty: dict[str, bool] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._last_build: dict[str, dict] = {}
        self._stop = threading.Event()
        self._cron_thread: Optional[threading.Thread] = None

    def state(self, app: Optional[str] = None) -> dict:
        """Return current golden sync status for an app or all apps."""
        with self._lock:
            if app:
                return {
                    "app": app,
                    "state": self._state.get(app, "IDLE"),
                    "dirty": self._dirty.get(app, False),
                    "last_build": self._last_build.get(app),
                }
            return {
                a: {
                    "state": self._state.get(a, "IDLE"),
                    "dirty": self._dirty.get(a, False),
                    "last_build": self._last_build.get(a),
                }
                for a in set(list(self._state.keys()) + list(self.cfg.apps))
            }

    def notify_push(self, app: str, repo: str, branch: str = "main",
                    commit_sha: str = "") -> dict:
        """Handle a push/merge notification for an app repo (e.g. from GitHub webhook)."""
        if branch != "main" and not branch.endswith("/main"):
            log.debug("golden_sync: ignoring push to non-main branch '%s' for repo %s", branch, repo)
            return {"status": "ignored", "reason": f"branch {branch} is not main"}

        log.info("golden_sync: push received for app=%s (repo=%s commit=%s)", app, repo, commit_sha[:8] if commit_sha else "?")

        with self._lock:
            cur_state = self._state.get(app, "IDLE")
            debounce_s = getattr(self.cfg, "golden_debounce_s", 45)

            if cur_state == "IDLE":
                self._state[app] = "DEBOUNCING"
                timer = threading.Timer(debounce_s, self._trigger_build, args=[app])
                self._timers[app] = timer
                timer.daemon = True
                timer.start()
                log.info("golden_sync: [%s] entered DEBOUNCING (quiet window %ds)", app, debounce_s)
                return {"status": "debouncing", "app": app, "debounce_s": debounce_s}

            elif cur_state == "DEBOUNCING":
                # Rapid-fire merge: reset quiet-window timer
                old_timer = self._timers.get(app)
                if old_timer:
                    old_timer.cancel()
                timer = threading.Timer(debounce_s, self._trigger_build, args=[app])
                self._timers[app] = timer
                timer.daemon = True
                timer.start()
                log.info("golden_sync: [%s] reset debounce timer (%ds remaining)", app, debounce_s)
                return {"status": "debouncing_reset", "app": app, "debounce_s": debounce_s}

            elif cur_state == "BUILDING":
                # Build already running: mark dirty so we do a single catch-up rebuild once it finishes
                self._dirty[app] = True
                log.info("golden_sync: [%s] build in progress; marked dirty for single-flight catchup", app)
                return {"status": "queued_dirty", "app": app}

        return {"status": "acknowledged", "app": app}

    def trigger_sync(self, app: str, force: bool = False) -> dict:
        """Explicit/manual trigger to rebuild golden (e.g. from /ops or CLI)."""
        with self._lock:
            cur_state = self._state.get(app, "IDLE")
            if cur_state == "BUILDING" and not force:
                self._dirty[app] = True
                return {"status": "already_building", "dirty": True}

            # Cancel any debounce timer and run immediately
            timer = self._timers.get(app)
            if timer:
                timer.cancel()

            self._state[app] = "BUILDING"
            self._dirty[app] = False

        threading.Thread(target=self._run_build_worker, args=[app],
                         name=f"golden-sync-{app}", daemon=True).start()
        return {"status": "started", "app": app}

    def _trigger_build(self, app: str) -> None:
        """Called when the debounce timer fires."""
        with self._lock:
            self._state[app] = "BUILDING"
            self._dirty[app] = False

        threading.Thread(target=self._run_build_worker, args=[app],
                         name=f"golden-sync-{app}", daemon=True).start()

    def _find_build_script(self, app: str) -> Optional[Path]:
        """Locate scripts/golden-build.<app>.sh or fallback scripts/golden-build.sh."""
        scripts_dir = self.cfg.scripts_dir
        app_script = scripts_dir / f"golden-build.{app}.sh"
        if app_script.is_file():
            return app_script
        generic_script = scripts_dir / "golden-build.sh"
        if generic_script.is_file():
            return generic_script
        return None

    def _run_build_worker(self, app: str) -> None:
        """Execute the golden build script in a background worker thread."""
        script = self._find_build_script(app)
        start_time = time.time()
        log.info("golden_sync: [%s] starting golden build via %s...", app, script)

        if not script:
            err = f"no golden-build script found for app '{app}'"
            log.error("golden_sync: [%s] %s", app, err)
            with self._lock:
                self._state[app] = "IDLE"
                self._last_build[app] = {
                    "status": "failed",
                    "error": err,
                    "duration_s": 0,
                    "at": time.time(),
                }
            return

        cmd = ["bash", str(script), "--yes"]
        env = {**subprocess.os.environ, "HOLO_APP": app}

        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.cfg.holo_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=getattr(self.cfg, "golden_build_timeout_s", 900),  # 15m cap
            )
            duration_s = round(time.time() - start_time, 2)

            if res.returncode == 0:
                log.info("golden_sync: [%s] build SUCCESS in %ss", app, duration_s)
                # Build succeeded! Drain stale warm pool slots so pool refreshes with new golden
                drained_count = 0
                if self.pool is not None:
                    try:
                        drained = self.pool.drain(app)
                        drained_count = len(drained) if drained else 0
                        log.info("golden_sync: [%s] drained %d stale warm pool slots", app, drained_count)
                    except Exception:
                        log.exception("golden_sync: [%s] pool drain failed", app)

                with self._lock:
                    self._last_build[app] = {
                        "status": "success",
                        "duration_s": duration_s,
                        "drained_slots": drained_count,
                        "at": time.time(),
                        "error": None,
                    }
            else:
                log.error("golden_sync: [%s] build FAILED (exit %d) in %ss:\n%s",
                          app, res.returncode, duration_s, res.stderr[-1000:] if res.stderr else "")
                with self._lock:
                    self._last_build[app] = {
                        "status": "failed",
                        "exit_code": res.returncode,
                        "duration_s": duration_s,
                        "error": res.stderr[-1000:] if res.stderr else "nonzero exit",
                        "at": time.time(),
                    }

        except subprocess.TimeoutExpired:
            log.error("golden_sync: [%s] build timed out", app)
            with self._lock:
                self._last_build[app] = {
                    "status": "timeout",
                    "duration_s": round(time.time() - start_time, 2),
                    "error": "timed out",
                    "at": time.time(),
                }
        except Exception as e:
            log.exception("golden_sync: [%s] unexpected build exception", app)
            with self._lock:
                self._last_build[app] = {
                    "status": "error",
                    "duration_s": round(time.time() - start_time, 2),
                    "error": str(e),
                    "at": time.time(),
                }

        # Check single-flight dirty coalescing
        with self._lock:
            if self._dirty.get(app, False):
                log.info("golden_sync: [%s] dirty flag was set during build — triggering single catchup build", app)
                self._dirty[app] = False
                threading.Thread(target=self._run_build_worker, args=[app],
                                 name=f"golden-sync-{app}", daemon=True).start()
            else:
                self._state[app] = "IDLE"

    def start(self) -> None:
        """Start optional background cron checking if enabled."""
        cron_s = getattr(self.cfg, "golden_cron_s", 0)
        if cron_s > 0:
            log.info("golden_sync: starting cron checker (interval=%ds)", cron_s)
            self._cron_thread = threading.Thread(target=self._cron_loop, daemon=True, name="golden-sync-cron")
            self._cron_thread.start()

    def stop(self) -> None:
        """Stop any pending timers or background threads."""
        self._stop.set()
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

    def _cron_loop(self) -> None:
        cron_s = getattr(self.cfg, "golden_cron_s", 3600)
        while not self._stop.wait(cron_s):
            try:
                for app in self.cfg.apps:
                    log.debug("golden_sync: cron checking app %s", app)
                    # Trigger sync if idle
                    with self._lock:
                        if self._state.get(app, "IDLE") == "IDLE":
                            self._trigger_build(app)
            except Exception:
                log.exception("golden_sync: error in cron loop")

