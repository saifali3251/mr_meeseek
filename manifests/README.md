# Holodeck App Manifests

A **manifest** defines the environment configuration for an application. It is a sourced shell file
specifying an app's services, ports, database coordinates, readiness endpoints, seed verification queries,
and (for named-volume apps) how to relocate and seed its database.

Currently configured manifests:
- [`full-stack-application.sh`](full-stack-application.sh) — full stack application stack (backend, frontend, postgres database).

## The Contract (Variables a Manifest Sets)

| Variable | Meaning |
|---|---|
| `HOLO_APP` | Short name (identifies `HOLO_GOLDEN=$HOLO_ROOT/golden-<app>`) |
| `HOLO_SRC` | Source checkout path the golden image is built from |
| `HOLO_COMPOSE_FILE` | Compose filename (e.g., `compose.yaml`) |
| `WS_SERVICES` | Minimal services to boot per workspace |
| `HOLO_PORT_SERVICES[]` | Services publishing host ports (stripped per workspace) |
| `HOLO_APP_SERVICE` / `HOLO_APP_PORT` | Main application container and internal port |
| `HOLO_PG_SERVICE` / `HOLO_PG_USER` / `HOLO_PG_DB` | Postgres service and database coordinates |
| `HOLO_READINESS_PATH` | Health/readiness endpoint |
| `HOLO_READINESS_SCHEME` | `http` or `https` for readiness polling |
| `HOLO_SEED_PROOF_SQL` | SQL query to verify database is seeded |
| `HOLO_PGDATA_MODE` | `bind-mount` or `named-volume` |
| `HOLO_PGDATA_OVERRIDE` | Compose override relocating pgdata into checkout |
| `HOLO_MIGRATE_CMD` / `HOLO_SEED_CMD` | Database migration and seeding commands |
| `HOLO_GIT_SUBDIR` | Subdirectory containing git repository for multi-repo setups |
| `HOLO_COMPOSITE_REPOS` | Array of repo directories in a multi-repo composite |

## How to Consume a Manifest

Source `lib.sh` first (defaults), then the manifest (app overrides):

```bash
source "$SCRIPT_DIR/lib.sh"
source "$HOLO_DIR/manifests/${HOLO_APP:-full-stack-application}.sh"
```

## Adding Another App

Create a new file under `manifests/<app-name>.sh` (use `full-stack-application.sh` as a template). Define the required variables matching your application's `compose.yaml` and database setup.
