#!/usr/bin/env bash
# manifests/full-stack-application.sh — Holodeck app manifest for
# full-stack-application ("Fieldwork").
#
# COMPOSITE, two independently-owned repos (test_backend, test_frontend) —
# split deliberately (2026-09-22) so Holodeck can target either repo
# independently via a ticket's target_repo, producing a repo-specific PR
# (backend-only or frontend-only), not one PR touching both.
#
# test_backend/test_frontend are PLACEHOLDER names (repos not pushed yet as
# of this revision) — update HOLO_GIT_BASE and the repo names below together
# once the real repos exist.
HOLO_APP=full-stack-application

: "${HOLO_GIT_BASE:=https://${HOLODECK_GITHUB_PAT:+${HOLODECK_GITHUB_PAT}@}github.com/saifali3251}"
: "${HOLO_SRC:=$HOME/code/full-stack-application}"   # composite root; overridden by holodeck.local.env
: "${HOLO_GOLDEN:=$HOLO_ROOT/golden-full-stack-application}"

# golden-build stages overrides/full-stack-application.compose.yaml into HOLO_SRC
# under this name — fully self-contained (declares db/backend/frontend itself).
HOLO_COMPOSE_FILE=compose.full-stack-application.yaml

HOLO_COMPOSITE_REPOS="test_backend test_frontend"

WS_SERVICES="db backend frontend"

# db has no host port (compose-network only).
HOLO_PORT_SERVICES=(backend frontend)

# Browser entrypoint — the frontend's live Vite DEV SERVER (port 5173), not
# the production nginx build. See overrides/full-stack-application.compose.yaml
# for why (hot-reload for a struck workspace's UI preview).
HOLO_APP_SERVICE=frontend
HOLO_APP_PORT=5173
HOLO_READINESS_SCHEME=http
HOLO_READINESS_PATH=/

HOLO_PG_SERVICE=db
HOLO_PG_USER=project_tracker
HOLO_PG_DB=project_tracker
HOLO_SEED_PROOF_SQL="SELECT count(*) FROM projects;"

# db's data lives in a named volume in the composite override -> relocate
# into the checkout for CoW, same reason every other manifest does this.
HOLO_PGDATA_MODE=named-volume
HOLO_PGDATA_OVERRIDE="overrides/full-stack-application.pgdata.yaml"

# Fallback subdir when a lease has no target_repo at all (e.g. a human
# striking from /ops without picking one).
HOLO_GIT_SUBDIR=test_backend

: "${HOLO_MIGRATE_CMD:=alembic upgrade head}"
: "${HOLO_SEED_CMD:=python -m app.seed}"

# Repo-specific test commands executed by the Host Notary
HOLO_TEST_CMD_test_backend="PYTHONPATH=. pytest tests/"
HOLO_TEST_CMD_test_frontend="npm run lint && npx tsc -b"
: "${HOLO_TEST_CMD:=PYTHONPATH=. pytest tests/}"

# Services corresponding to target repositories for test execution
HOLO_TEST_SERVICE_test_backend="backend"
HOLO_TEST_SERVICE_test_frontend="frontend"

# No credential minting needed — every base image here is public, and both
# repos (once real) are assumed public or reachable with the same
# HOLODECK_GITHUB_PAT the box already uses for holodeck itself.
: "${HOLO_BUILD_CMD:=docker compose -f compose.full-stack-application.yaml build}"
: "${HOLO_TOKEN_CMD:=}"

# NOT part of this manifest (Omnigent-side setting, not a control-plane
# one): HOLODECK_EXEC_SERVICE=backend in omnigent-deploy/omnigent/.env —
# backend is where the agent's shell runs and where the omnigent/claude
# CLIs get installed (see overrides/full-stack-application.compose.yaml).
