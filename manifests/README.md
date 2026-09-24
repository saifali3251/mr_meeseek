# Holodeck app manifests

A **manifest** is the only thing that varies per app. It's a sourced shell file
holding an app's service set, ports, DB coords, readiness endpoint, seeded-data
proof, and (for named-volume apps) how to relocate + seed its database.

- [`main.sh`](main.sh) — the JSQ monolith (bind-mount DB, pre-seeded). Baseline.
- [`main-ui.sh`](main-ui.sh) — `main` with its browser UI reachable (nginx:443, TLS).
- [`compliance.sh`](compliance.sh) — the hackathon **hero** (named-volume DB, manual migrate + offline seed).
- [`compliance-ui.sh`](compliance-ui.sh) — the **composite**: the full Canopy UI with the
  Compliance tab, stitching five repos (compliance-backend + main + graphql-federation-gateway +
  fund-admin-frontend + jsq-dev-proxy/qong) into one CoW-clonable stack. Browser entrypoint is
  qong on HTTPS 8989; fund-admin-frontend is a Module Federation remote loaded by main's
  Canopy host shell under `/canopy/`. Built by
  [`scripts/golden-build.compliance-ui.sh`](../scripts/golden-build.compliance-ui.sh).
- [`control-tower.sh`](control-tower.sh) — **Stage C**, a full composite giving a live
  browsable preview: control-tower's own stack (devbox + postgres + temporal) + the
  live-linked `jsq-control-tower-components` (`jsqbuild tsup --watch`) +
  `fund-admin-frontend` (Yarn `portal:` link instead of the pinned CodeArtifact version —
  verified live 2026-08-26 twice, locally and on real EC2/xfs) + main + graphql-
  federation-gateway + qong (browser entrypoint). An **independent golden** from
  `compliance-ui`'s (`golden-control-tower` and `golden-compliance-ui` are two separate
  goldens, by explicit decision, not a merged composite). control-tower is federated as a
  gateway subgraph via `CONTROL_TOWER_SUBGRAPH_URL` — a field `compliance-ui.compose.yaml`
  already declares (there pointed at a placeholder), so most of the federation wiring was
  already done. Named-volume DB (same relocation problem as compliance) for
  control-tower; main's is a bind mount, seeded via a baseline-dump restore since main
  starts empty on a fresh clone. Seed is `publish_workflow_definitions` +
  `set_workflow_source_system` only — both offline, no live Jira. All stages (A: backend
  only, B: + FE link mechanism, C: this) landed under the same `HOLO_APP=control-tower`,
  not separate app names. Built by
  [`scripts/golden-build.control-tower.sh`](../scripts/golden-build.control-tower.sh);
  the live-link Dockerfiles live under
  [`../docker/jsq-control-tower-components/`](../docker/jsq-control-tower-components/) and
  [`../docker/control-tower-ui/`](../docker/control-tower-ui/) (a deliberate fork of
  `docker/fund-admin-frontend/Dockerfile`, not an edit to it); qong's routing is
  [`../docker/qong/kong.control-tower.yml`](../docker/qong/kong.control-tower.yml),
  selected via a build arg on the shared `docker/qong/Dockerfile` (parameterized, not
  forked — safe since compliance-ui never passes the arg and keeps its exact prior
  behavior by default).

## The contract (variables a manifest sets)

| Var | Meaning |
|-----|---------|
| `HOLO_APP` | short name (also picks `HOLO_GOLDEN=$HOLO_ROOT/golden-<app>`) |
| `HOLO_SRC` | the app checkout the golden is built from |
| `HOLO_COMPOSE_FILE` | compose filename (`docker-compose.yaml` for main, `compose.yaml` for compliance) |
| `WS_SERVICES` | minimal services to boot per workspace |
| `HOLO_PORT_SERVICES[]` | services publishing host ports → stripped per workspace |
| `HOLO_APP_SERVICE` / `HOLO_APP_PORT` | app container + internal port (preview remaps to this) |
| `HOLO_PG_SERVICE` / `HOLO_PG_USER` / `HOLO_PG_DB` | Postgres coords for the warm-DB probe + proof |
| `HOLO_READINESS_PATH` | readiness endpoint (no Host header) |
| `HOLO_READINESS_SCHEME` | `http` (default) or `https` — `https` for TLS entrypoints (main nginx:443, compliance-ui qong:8989); polled with `-k` |
| `HOLO_SEED_PROOF_SQL` | query returning >0 only when the DB is seeded |
| `HOLO_PGDATA_MODE` | `bind-mount` (clones for free) or `named-volume` (must relocate) |
| `HOLO_PGDATA_OVERRIDE` | compose override that relocates pgdata into the checkout |
| `HOLO_MIGRATE_CMD` / `HOLO_SEED_CMD` | one-time seed recipe, run during golden-build |
| `HOLO_GIT_SUBDIR` | (composites only) subdirectory of the workspace that's a real git repo — `golden_head`/finalize's diff resolve here instead of the workspace root |
| `HOLO_BUILD_CMD` / `HOLO_TOKEN_CMD` | image build + CodeArtifact token (golden-build only) |

## How to consume a manifest

Source `lib.sh` first (defaults), then the manifest (app overrides):

```bash
source "$SCRIPT_DIR/lib.sh"
source "$HOLO_DIR/manifests/${HOLO_APP:-main}.sh"
```

## Shared core is manifest-aware (done)

`lib.sh` sources `manifests/$HOLO_APP.sh` (default `main`), and `strike.sh` /
`destroy.sh` / `golden-build.compliance.sh` read the manifest vars — compose file,
app service/port, PG coords, readiness path, seeded-data proof, and the pgdata
override. `main` behavior is unchanged (its manifest equals the old defaults).

Select the app with the `HOLO_APP` env var:

```bash
# compliance (hero) — build the golden once, then strike/verify/destroy
HOLO_APP=compliance ./scripts/golden-build.compliance.sh      # needs jr build + SSO (once)
HOLO_APP=compliance ./scripts/strike.sh CPL-1 --preview 18081 # self-verifies /readiness + seeded rows
HOLO_APP=compliance ./scripts/destroy.sh CPL-1

# or the whole loop in one command:
./scripts/holo-mvp.compliance.sh --build      # golden -> strike -> verify -> destroy
```

`strike.sh --preview` prints the warm-DB result, the seeded-data count, and the
live `/readiness` response — the end-to-end proof.

## Adding another app

Copy `compliance.sh`, fill in from recon of the target repo's compose file, write its
pgdata override the same way (if its DB lives in a named volume). No code changes —
just a new manifest. `control-tower.sh` is a worked example of this staged, incremental
approach — a composite that will eventually need a UI (Stage C) started as a single-repo,
backend-only manifest first (Stage A), then grew into a composite one stage at a time
under the same app name, matching the recommendation in
[`../holodeck_materials/08_decisions_needed.md`](../../holodeck_materials/08_decisions_needed.md)
(D12) to scope "onboarded" as backend-verifiable before promising a preview URL.
