# Holodeck — Phase 1 scripts

Prove the substrate on one box: a **golden image** of `main`, CoW-cloned into
**isolated, seeded workspaces** in seconds, torn down on demand.

These are written to spec against your `main` checkout but **not yet run** —
one real run confirms the two assumptions the whole design rests on:

1. **`COMPOSE_PROJECT_NAME` fully isolates** N stacks on one host (no port/name/network collisions).
2. **Postgres boots warm on a *cloned* pgdata** — no `db.reset`, no migrate — so a workspace is usable in seconds.

## Files

| File | Role |
|------|------|
| `lib.sh` | Config + logging + the portable CoW clone (APFS `cp -c` / Linux `--reflink`). |
| `golden-build.sh` | Snapshot a seeded, stack-down `main` into the golden image. |
| `strike.sh` | CoW-clone the golden → isolated warm workspace (branch, ports stripped, stack up). |
| `destroy.sh` | Stop the workspace stack and delete the clone. |
| `holo-demo.sh` | One-command demo: strike N → prove warm+seeded+isolated → bundle evidence → destroy. |

## Quick start

```bash
chmod +x *.sh

# 1. Build the golden image from your (already-seeded) checkout.
#    Stops your dev stack for a consistent pgdata snapshot.
./golden-build.sh

# 2. Strike three isolated workspaces from it — seconds each, no reseed.
./strike.sh JSQ-118
./strike.sh JSQ-119
./strike.sh JSQ-120 --preview 18020        # app on http://127.0.0.1:18020

# 3. Run tests inside one (the isolation + warm-DB proof):
docker compose -p ws-jsq-118 exec webserver jr test -- jsq/tests/<pick_something_fast>

# 4. Tear down.
./destroy.sh JSQ-118 && ./destroy.sh JSQ-119 && ./destroy.sh JSQ-120
```

## Config (env overrides, see `lib.sh`)

- `HOLO_MAIN` — source checkout (default `~/code/main`).
- `HOLO_ROOT` — where golden + workspaces live (default `~/holodeck-data`).
- `WS_SERVICES` — services booted per workspace. Default is the real minimum
  (`database redis localstack webserver`) — **`localstack` is required**: the
  webserver hard-blocks on its secret at boot. If `jr test` needs more, add
  them; set `WS_SERVICES=""` to boot the whole compose file.
- `DEMO_HOST` — seeded arena host for `/health_check` (default `rockstonecap.dev.junipersquare.com`).

## Notes / caveats

- **macOS today, Linux in prod.** The clone uses APFS clonefile locally and
  btrfs/xfs reflink on the app-test EC2 — same script, `uname` picks the path.
  Golden + workspaces must sit on the **same volume** as the source for CoW.
- **Requirements at boot:** `~/secrets/aws.env` present, Docker running,
  Compose ≥ 2.24 (for the `!reset`/`!override` port override — you have v5.2.0).
- **Consistency:** `golden-build.sh` stops the stack before cloning so the
  pgdata is flushed. Never strike from a *running* source.
- **Port list drift:** `HOLO_PORT_SERVICES` in `lib.sh` lists the services that
  publish host ports (verified 2026-07). If compose adds one, add it there, or
  regenerate:
  ```bash
  cd ~/code/main && python3 - <<'PY'
  import re; svc=None; seen=set()
  for l in open('docker-compose.yaml'):
      m=re.match(r'^  ([a-z0-9_-]+):\s*$',l)
      if m: svc=m.group(1)
      if re.search(r'^\s+- "?\d+',l) and svc: seen.add(svc)
  print(' '.join(sorted(seen)))
  PY
  ```

## What this is NOT (yet)

Phase 1 only. No control plane, no lease API, no STS creds, no reaper, no
shared ALB — those are Phase 2 (see the architecture doc). This is the
irreducible core: golden → clone → isolated warm boot → `jr test` → destroy.
