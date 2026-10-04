#!/usr/bin/env bash
# holo-env.sh — print the manifest-resolved config for an app, one KEY=VALUE per line.
#
# WHY THIS EXISTS
# ---------------
# lib.sh is the single source of truth for per-app config: it sources
# holodeck.local.env, applies defaults, then sources manifests/$HOLO_APP.sh. Those
# values only exist inside a shell that sourced lib.sh — they are NOT present in the
# parent process's environment.
#
# strike.sh / destroy.sh are fine because they source lib.sh themselves. But anything
# that talks to a workspace WITHOUT going through a script — e.g. the control plane's
# finalize, which runs `docker compose exec` directly — cannot see those values, and
# guessing them produces silent wrongness:
#
#   HOLO_COMPOSE_FILE  guessed "docker-compose.yaml"; compliance uses "compose.yaml"
#   HOLO_WORKSPACES    has no safe default at all -> a RELATIVE path -> FileNotFoundError
#   HOLO_PG_*          guessed compliance values; wrong for main (database/jsqapp/jsq)
#   WS_SERVICES        guessed "" -> the services-booted evidence stamp was always empty
#
# So: consumers ask lib.sh instead of guessing. Adding a manifest variable makes it
# available to every consumer by listing it once below.
#
# Usage:
#   HOLO_APP=compliance ./holo-env.sh
#   HOLO_APP=main       ./holo-env.sh
#
# CONTRACT
#   - one KEY=VALUE per line, no quoting, no trailing whitespace
#   - split on the FIRST '=' only (values may contain '=')
#   - values must not contain newlines (none of the exported keys do)
#   - unset variables are emitted with an empty value, not omitted
#   - exits non-zero (via lib.sh) if HOLO_APP has no manifest

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

# Everything a non-script consumer needs to talk to a workspace correctly.
# Keep in sync with manifests/README.md's contract table.
# Dynamically include any HOLO_* variables defined by the manifest (including
# repo-specific overrides like HOLO_TEST_CMD_<repo>, HOLO_TEST_SERVICE_<repo>).
_VARS=(
  HOLO_APP
  HOLO_ROOT HOLO_WORKSPACES HOLO_GOLDEN HOLO_SRC
  HOLO_COMPOSE_FILE HOLO_PGDATA_OVERRIDE HOLO_GIT_SUBDIR
  HOLO_APP_SERVICE HOLO_APP_PORT
  HOLO_PG_SERVICE HOLO_PG_USER HOLO_PG_DB
  HOLO_READINESS_PATH HOLO_READINESS_SCHEME HOLO_SEED_PROOF_SQL
  HOLO_TEST_CMD
  HOLO_COMPOSITE_REPOS
  WS_SERVICES
)

for _k in $(compgen -v HOLO_ 2>/dev/null); do
  _VARS+=("$_k")
done

_SEEN=""
for _v in "${_VARS[@]}"; do
  case " $_SEEN " in
    *" $_v "*) ;;
    *)
      _SEEN="$_SEEN $_v"
      printf '%s=%s\n' "$_v" "${!_v-}"
      ;;
  esac
done
