"""Warm workspace pool — pre-booted, seeded stacks so a strike is a claim.

A cold strike is ~2 minutes: CoW-clone the golden, `compose up`, wait for
Postgres recovery, wait for the app to boot. None of that depends on WHICH
ticket asked for it, so it can all be paid in advance. A pool slot is an
ordinary workspace booted ahead of time under a synthetic id (`pool-01`); a
"strike" against a warm pool then reduces to cutting the agent branch and
verifying the stack is alive — sub-second.

The indirection this buys is the whole trick: a running compose project cannot
be renamed and its directory cannot be moved (the bind mounts resolve against
it), so the workspace keeps its pool identity for life while the LEASE keeps
the ticket identity. `lease_id=comp-6542` living in `ws_dir=.../pool-01` is
normal and expected. Everything above the WorkspaceProvider seam — the lease
service, the API, the console, Omnigent — is unaware a pool exists.

Substrate-agnostic on purpose: it drives three callbacks supplied by whichever
provider owns it, so an EksProvider could reuse it unchanged.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("holodeck.pool")

# Marker file written into a slot's workspace dir once it has booted cleanly.
# Two readers: the pool manager (to re-adopt slots after a control-plane
# restart) and ComposeProvider.list_orphans (to NOT report them as orphans —
# under HOLODECK_RECONCILE=reap that would destroy the pool on every restart).
POOL_MARKER = ".holodeck-pool"

_SLOT_RE = re.compile(r"^pool-(\d+)$")


@dataclass
class PoolSlot:
    slot_id: str          # "pool-01" — also the workspace dir name + compose project suffix
    app: str
    port: int             # published when the containers were CREATED; cannot be rebound
    ws_dir: str
    compose_project: str


class PoolManager:
    """Keeps `target` warm slots per app.

    Callbacks (provider-supplied):
      boot(slot_id, app, port) -> PoolSlot | None   provision a warm slot; None on failure
                                                    (MUST clean up its own partial workspace)
      destroy(slot)            -> None              tear a slot down
      verify(slot)             -> bool              is this slot still alive and serving?
      ws_root(app)             -> Path | None       where workspace dirs live
    """

    def __init__(self, cfg, boot: Callable, destroy: Callable,
                 verify: Callable, ws_root: Callable) -> None:
        self.cfg = cfg
        self.target = cfg.pool_size
        self.apps = cfg.pool_apps
        self._boot = boot
        self._destroy = destroy
        self._verify = verify
        self._ws_root = ws_root

        self._ready: dict[str, list[PoolSlot]] = {}
        self._booting: set[str] = set()      # slot_ids mid-boot; reserved so ids can't collide
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def enabled(self) -> bool:
        return self.target > 0

    # ---- claim -------------------------------------------------------------
    def claim(self, app: str) -> Optional[PoolSlot]:
        """Hand out a verified warm slot, or None to fall back to a cold strike.

        Verification is deliberately OUTSIDE the lock (it does real I/O) and a
        slot that fails it is destroyed rather than returned: an idle stack can
        die (OOM, docker restart), and handing an agent a dead workspace is far
        worse than making it wait for a cold strike. We loop so one dead slot
        doesn't mask a healthy one behind it.
        """
        if not self.enabled:
            return None
        while True:
            with self._lock:
                slots = self._ready.get(app) or []
                if not slots:
                    return None
                slot = slots.pop(0)
            if self._verify(slot):
                log.info("pool: claimed %s (port %s) for app=%s", slot.slot_id, slot.port, app)
                return slot
            log.warning("pool: slot %s failed verification — destroying, trying next",
                        slot.slot_id)
            threading.Thread(target=self._destroy_quiet, args=(slot,),
                             name=f"pool-destroy-{slot.slot_id}", daemon=True).start()

    def _destroy_quiet(self, slot: PoolSlot) -> None:
        try:
            self._destroy(slot)
        except Exception:
            log.exception("pool: destroy of %s failed", slot.slot_id)

    # ---- reconcile loop ----------------------------------------------------
    def state(self) -> dict:
        """Operator snapshot — surfaced at /ops/state so a misconfigured pool is
        visible instead of just being slow."""
        with self._lock:
            return {
                "enabled": self.enabled,
                "target": self.target,
                "apps": list(self.apps),
                "booting": sorted(self._booting),
                "ready": {app: [{"slot": s.slot_id, "port": s.port} for s in slots]
                          for app, slots in self._ready.items()},
            }

    def start(self) -> None:
        if not self.enabled:
            log.info("pool: disabled (HOLODECK_POOL_SIZE=0)")
            return
        if not self.apps:
            # Loud, because the symptom is otherwise invisible: the pool starts,
            # reports a target, warms NOTHING, and every strike silently takes the
            # cold path. Empty means every requested app was filtered out by the
            # HOLODECK_APPS allowlist — usually HOLODECK_POOL_APPS was left unset
            # so it fell back to default_app, which the allowlist excludes.
            log.error("pool: HOLODECK_POOL_SIZE=%d but NO apps to warm — the pool is a "
                      "no-op and every strike will take the ~2min cold path. Set "
                      "HOLODECK_POOL_APPS to an app allowed by HOLODECK_APPS.",
                      self.target)
            return
        self.adopt()
        self._thread = threading.Thread(target=self._loop, name="holo-pool", daemon=True)
        self._thread.start()
        log.info("pool: started, target=%d per app %s", self.target, self.apps)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.cfg.pool_interval_s):
            try:
                self.reconcile()
            except Exception:
                log.exception("pool: reconcile tick failed")

    def reconcile(self) -> None:
        """Bring each app's pool back to target. ONE boot per tick, on purpose.

        A boot runs the full strike, so an unthrottled refill of an empty pool
        would fire `target` concurrent `compose up`s, saturate
        max_concurrent_strikes, and make a REAL ticket queue behind pool
        refills — strictly worse than having no pool. One per tick fills a
        3-slot pool in ~3 boots instead of a thundering herd; a claim needs no
        strike slot at all, so it is never blocked by this.

        This single idempotent path also covers every event that could change
        the count — a claim, a release, a slot dying idle, a failed boot, or a
        cold start — so there are no per-event hooks to drift out of sync.
        """
        for app in self.apps:
            with self._lock:
                ready = list(self._ready.get(app) or [])
                booting = len(self._booting)
            # Prune slots that died while idle so `deficit` reflects reality.
            for slot in ready:
                if not self._verify(slot):
                    log.warning("pool: slot %s died idle — replacing", slot.slot_id)
                    with self._lock:
                        if slot in self._ready.get(app, []):
                            self._ready[app].remove(slot)
                    self._destroy_quiet(slot)

            with self._lock:
                have = len(self._ready.get(app) or []) + booting
            if have >= self.target:
                continue
            slot_id = self._next_slot_id(app)
            if slot_id is None:
                continue
            threading.Thread(target=self._boot_one, args=(slot_id, app),
                             name=f"pool-boot-{slot_id}", daemon=True).start()

    def _boot_one(self, slot_id: str, app: str) -> None:
        """Register a booted slot. Deliberately does NO substrate work — boot()
        owns provisioning AND marking, so a slot that reaches us is complete.
        Doing the marking here instead meant a trivial failure (a bad path, a
        permission) discarded a workspace that had already cost a full 2-minute
        boot and left it running, untracked."""
        try:
            slot = self._boot(slot_id, app)
            if slot is None:
                log.warning("pool: boot of %s failed — will retry next tick", slot_id)
                return
            with self._lock:
                self._ready.setdefault(app, []).append(slot)
            log.info("pool: slot %s ready on port %s (app=%s)", slot_id, slot.port, app)
        except Exception:
            log.exception("pool: boot of %s raised", slot_id)
        finally:
            with self._lock:
                self._booting.discard(slot_id)

    def _next_slot_id(self, app: str) -> Optional[str]:
        """Lowest free pool-NN, reserved under the lock so two ticks can't collide.

        Existing dirs count as taken even when they are not ours to reuse (a
        half-built slot from a crashed boot) — reusing that id would make
        strike.sh refuse with 'workspace already exists'.
        """
        root = self._ws_root(app)
        on_disk = set()
        if root is not None and root.is_dir():
            on_disk = {p.name for p in root.iterdir() if p.is_dir() and _SLOT_RE.match(p.name)}
        with self._lock:
            taken = on_disk | self._booting | {
                s.slot_id for slots in self._ready.values() for s in slots
            }
            for i in range(1, self.target * 4 + 2):   # headroom for slots mid-teardown
                candidate = f"pool-{i:02d}"
                if candidate not in taken:
                    self._booting.add(candidate)
                    return candidate
        log.warning("pool: no free slot id (target=%d) — skipping this tick", self.target)
        return None

    # ---- startup adoption --------------------------------------------------
    def adopt(self) -> None:
        """Re-attach slots that outlived a control-plane restart — and reclaim any
        that didn't survive it (dead, no live containers), or that exceed the
        current target, so every startup converges on exactly `target` slots with
        zero drift.

        Without this a restart abandons every warm slot (they hold ports and
        containers but nothing tracks them) AND boots a fresh set beside them.
        Adoption makes a restart cheap: a slot that's still alive and current is
        REUSED as-is, not torn down and rebuilt — only what's actually dead,
        stale, or over-budget gets destroyed. Ports are re-reserved by the
        provider's boot/adopt path so the lease allocator never hands out one a
        slot is bound to.

        A marked directory with no live containers used to just get skipped
        (`continue`) — relying on the separate orphan-reap path to clean it up,
        which deliberately excludes anything pool-marked (to protect a LIVE pool
        from being reaped), so a dead-but-marked slot fell into a gap neither
        path covered and permanently burned its slot id (_next_slot_id treats
        any pool-NN directory as taken, live or not). Destroying it here closes
        that gap.
        """
        for app in self.apps:
            root = self._ws_root(app)
            if root is None or not root.is_dir():
                continue
            alive: list[PoolSlot] = []
            for p in sorted(root.iterdir()):
                if not p.is_dir() or not (p / POOL_MARKER).exists():
                    continue
                slot = self._adopt_one(p, app)
                if slot is None:
                    log.warning("pool: marked slot %s has no live containers — reclaiming", p.name)
                    self._destroy_quiet(PoolSlot(slot_id=p.name, app=app, port=-1,
                                                 ws_dir=str(p), compose_project=f"ws-{p.name}"))
                    continue
                if self._verify(slot):
                    alive.append(slot)
                else:
                    log.warning("pool: adopted slot %s is dead — destroying", slot.slot_id)
                    self._destroy_quiet(slot)

            # Enforce the target even across a restart: keep at most `target` of
            # the genuinely alive slots (e.g. target was lowered since these were
            # booted) and retire the rest, rather than quietly serving over-budget
            # capacity forever.
            keep, extra = alive[: self.target], alive[self.target:]
            with self._lock:
                self._ready.setdefault(app, []).extend(keep)
            for slot in keep:
                log.info("pool: adopted existing slot %s on port %s", slot.slot_id, slot.port)
            for slot in extra:
                log.warning("pool: adopted slot %s exceeds target=%d — retiring",
                            slot.slot_id, self.target)
                self._destroy_quiet(slot)

    # Set by the provider (it alone knows how to read a workspace's port back).
    adopt_one: Optional[Callable] = None

    def _adopt_one(self, ws_dir: Path, app: str) -> Optional[PoolSlot]:
        if self.adopt_one is None:
            return None
        try:
            return self.adopt_one(ws_dir, app)
        except Exception:
            log.exception("pool: could not adopt %s", ws_dir)
            return None
