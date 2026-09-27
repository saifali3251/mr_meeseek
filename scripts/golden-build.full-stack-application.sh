#!/usr/bin/env bash
# golden-build.full-stack-application.sh — build the full-stack-application
# ("Fieldwork") golden.
#
# COMPOSITE as of the 2026-09-22 repo split: two repos (test_backend,
# test_frontend), cloned as siblings under HOLO_SRC — same shape as
# control-tower's golden-build, just two repos instead of six and no
# credential minting (every base image here is public).
#
# Steps:
#   1. checks out/refreshes both repos under the composite root (HOLO_SRC)
#   2. stages the (fully self-contained) composite compose file
#   3. builds all three images
#   4. relocates db's pgdata (fresh), boots the stack
#   5. migrates + seeds
#   6. proves: seed rows present, frontend dev server live
#   7. clean shutdown, scrub secrets, single CoW snapshot -> golden
#
# Usage:
#   ./golden-build.full-stack-application.sh              # prompts before destructive steps
#   ./golden-build.full-stack-application.sh --yes         # non-interactive
#   ./golden-build.full-stack-application.sh --no-build    # reuse cached images
#   ./golden-build.full-stack-application.sh --no-checkout # skip repo clone/refresh (use as-is)

set -euo pipefail
export HOLO_APP="${HOLO_APP:-full-stack-application}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"        # sources manifests/full-stack-application.sh via HOLO_APP

FORCE=0; DO_BUILD=1; DO_CHECKOUT=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) FORCE=1 ;;
    --no-build) DO_BUILD=0 ;;
    --no-checkout) DO_CHECKOUT=0 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac; shift
done
confirm() { [[ $FORCE -eq 1 ]] && return 0; read -rp "$1 [y/N] " a; [[ "$a" == y* ]]; }

holo_preflight
[[ -n "$HOLO_SRC" ]] || die "HOLO_SRC (composite root) is unset — set it in holodeck.local.env"

COMPOSITE_REPOS=(test_backend test_frontend)

# ---- 1. lay down / refresh both repos under the composite root ------------
mkdir -p "$HOLO_SRC"
if [[ $DO_CHECKOUT -eq 1 ]]; then
  for repo in "${COMPOSITE_REPOS[@]}"; do
    dest="$HOLO_SRC/$repo"
    if [[ -d "$dest/.git" ]]; then
      log "refreshing $repo ..."
      git -C "$dest" fetch --quiet --depth 1 origin || warn "fetch failed for $repo (offline?)"
      git -C "$dest" reset --hard FETCH_HEAD >/dev/null 2>&1 || warn "reset to FETCH_HEAD failed for $repo"
    else
      log "cloning $repo ..."
      git clone --quiet --depth 1 "$HOLO_GIT_BASE/$repo.git" "$dest" \
        || die "clone failed for $repo — check network/auth ($HOLO_GIT_BASE/$repo) and that HOLO_GIT_BASE is set correctly (still a placeholder until the real repos exist)"
    fi
  done
else
  log "--no-checkout: using existing repos under $HOLO_SRC as-is"
  for repo in "${COMPOSITE_REPOS[@]}"; do
    [[ -d "$HOLO_SRC/$repo" ]] || die "missing repo checkout: $HOLO_SRC/$repo (drop --no-checkout)"
  done
fi

# ---- 2. stage the (self-contained) compose override -----------------------
log "staging composite compose file into $HOLO_SRC ..."
cp "$HOLO_DIR/overrides/full-stack-application.compose.yaml" "$HOLO_SRC/$HOLO_COMPOSE_FILE"

# keep the relocated pgdata out of the build context — owned by the postgres
# uid after first boot, `docker build` tars the context as the invoking
# non-root user (same reasoning every other manifest's golden-build documents).
di="$HOLO_SRC/.dockerignore"
touch "$di"
grep -qxF "docker/pgdata/" "$di" \
  || printf '\n# holodeck: seeded pgdata owned by postgres uid — never in the build context\n%s\n' "docker/pgdata/" >> "$di"

cd "$HOLO_SRC"
override="$HOLO_DIR/$HOLO_PGDATA_OVERRIDE"
# Explicit project name — without this, Compose derives one from $HOLO_SRC's
# directory name (full-stack-application), which never changed across the
# repo split even though its contents did. A crashed prior run (dc down
# never reached) leaves its network/containers behind under that same
# implicit name; the NEXT run silently reuses that stale network instead of
# erroring, and a new "db" container sharing a network alias with an old one
# means backend's DNS-resolved connection can land on the wrong container —
# confirmed for real 2026-09-23 (alembic no-op + seed "already exists" vs.
# the proof query's exec-targeted container correctly seeing 0 rows, at the
# same time). Explicit name removes the ambiguity outright.
export COMPOSE_PROJECT_NAME="holo-golden-full-stack-application"
dc() { docker compose --project-directory "$HOLO_SRC" \
         -f "$HOLO_SRC/$HOLO_COMPOSE_FILE" -f "$override" "$@"; }

# ---- 3. build all three images --------------------------------------------
if [[ $DO_BUILD -eq 1 ]]; then
  log "building images: $HOLO_BUILD_CMD"
  ( set -x; bash -lc "$HOLO_BUILD_CMD" ) \
    || die "image build failed — check $HOLO_BUILD_CMD"
else
  log "--no-build: reusing cached images"
fi

# ---- 4. relocate db's pgdata, boot the stack -----------------------------------
log "relocating db pgdata -> ./docker/pgdata (fresh)..."
if [[ -d "$HOLO_SRC/docker/pgdata" ]]; then
  confirm "pgdata exists — wipe and re-seed?" || die "aborted"
  holo_rm_tree "$HOLO_SRC/docker/pgdata"
fi
mkdir -p "$HOLO_SRC/docker/pgdata"

log "booting: ${WS_SERVICES} ..."
dc up -d ${WS_SERVICES} || die "compose up failed — see: docker compose logs"

log "waiting for db reachable from backend (as $HOLO_PG_USER)..."
ready=0
for _ in $(seq 1 45); do
  if dc exec -T backend python -c "
import psycopg
psycopg.connect(host='$HOLO_PG_SERVICE',port=5432,user='$HOLO_PG_USER',password='$HOLO_PG_USER',dbname='$HOLO_PG_DB',connect_timeout=3).close()
" >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 2
done
[[ $ready -eq 1 ]] || die "db not reachable from backend after 90s — see: docker compose logs db backend"

# ---- 5. migrate + seed -----------------------------------------------------------
log "applying migrations: $HOLO_MIGRATE_CMD"
dc exec -T backend /bin/sh -c "$HOLO_MIGRATE_CMD" || die "migrate failed"

log "seeding: $HOLO_SEED_CMD"
dc exec -T backend /bin/sh -c "$HOLO_SEED_CMD" || die "seed failed"

# ---- 6. prove: seed rows present, frontend dev server live -----------------------
cnt="$(dc exec -T "$HOLO_PG_SERVICE" psql -U "$HOLO_PG_USER" -d "$HOLO_PG_DB" -tAc "$HOLO_SEED_PROOF_SQL" | tr -d '[:space:]' || echo 0)"
[[ "${cnt:-0}" =~ ^[0-9]+$ && "${cnt:-0}" -gt 0 ]] || die "seed proof failed ($HOLO_SEED_PROOF_SQL -> '${cnt}')"
ok "seeded: $HOLO_SEED_PROOF_SQL -> $cnt rows"

log "waiting for frontend dev server (proves Vite booted + proxy config valid)..."
fe_ok=0
for _ in $(seq 1 40); do
  if dc exec -T backend curl -sf -o /dev/null --max-time 3 "http://frontend:5173/" >/dev/null 2>&1; then
    fe_ok=1; break
  fi
  sleep 3
done
if [[ $fe_ok -eq 1 ]]; then
  ok "frontend dev server live — hot-reload path proven reachable"
else
  warn "frontend not answering after 2min — dumping its logs so this isn't a silent timeout:"
  dc logs --tail=80 frontend || warn "couldn't even fetch frontend logs"
  confirm "snapshot anyway (frontend unproven)?" || die "aborted — fix the frontend dev server before snapshotting"
fi

log "clean shutdown (flush WAL for a consistent snapshot)..."
dc down || warn "compose down returned nonzero"

# ---- 7. scrub secrets, CoW-clone -> golden ---------------------------------------
if [[ -e "$HOLO_GOLDEN" ]]; then
  confirm "overwrite existing golden at $HOLO_GOLDEN?" || die "aborted"
  [[ "$HOLO_GOLDEN" == "$HOLO_ROOT"/?* ]] \
    || die "refusing to remove a golden outside \$HOLO_ROOT ($HOLO_ROOT): $HOLO_GOLDEN"
  holo_rm_tree "$HOLO_GOLDEN"
fi
mkdir -p "$(dirname "$HOLO_GOLDEN")"
for repo in "${COMPOSITE_REPOS[@]}"; do
  [[ -d "$HOLO_SRC/$repo" ]] && holo_scrub_secrets "$HOLO_SRC/$repo"
done

log "CoW-cloning $HOLO_SRC -> golden (carries warm, seeded db)..."
holo_cow_clone "$HOLO_SRC" "$HOLO_GOLDEN"
{ echo "app=$HOLO_APP seeded=$cnt"; date;
  for repo in "${COMPOSITE_REPOS[@]}"; do
    printf '%s=%s\n' "$repo" "$(git -C "$HOLO_SRC/$repo" rev-parse HEAD 2>/dev/null || echo unknown)"
  done; } > "$HOLO_GOLDEN/.holodeck-golden"
ok "golden ready at $HOLO_GOLDEN (app=$HOLO_APP, seeded=$cnt)"

# ---- validate ---------------------------------------------------------------
echo
log "validate the golden with a throwaway strike:"
log "  HOLO_APP=full-stack-application $SCRIPT_DIR/strike.sh FSA-1 --preview 13000"
log "  # expect: db warm, projects > 0, http://127.0.0.1:13000 live (Vite dev server)"
log "  HOLO_APP=full-stack-application $SCRIPT_DIR/destroy.sh FSA-1"
