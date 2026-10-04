#!/usr/bin/env bash
# golden-build.sh — build/refresh the golden image.
#
# The golden image is a CoW copy of a `main` checkout whose DB is already
# seeded + migrated, taken while the stack is DOWN (so pgdata is consistent).
# Your existing checkout already has an 857 MB seeded docker/database/data,
# so by default we just snapshot it as-is; pass --seed to reseed first.
#
# Usage:
#   ./golden-build.sh            # snapshot the current (seeded) checkout
#   ./golden-build.sh --seed     # run `jr db.reset && jr db.migrate` first (REWRITES dev DB)
#   ./golden-build.sh --yes      # don't prompt

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

DO_SEED=0; FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --seed) DO_SEED=1 ;;
    --yes|-y) FORCE=1 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac; shift
done

holo_preflight
[[ -d "$HOLO_MAIN/.git" ]] || die "HOLO_MAIN=$HOLO_MAIN is not a git checkout"
cd "$HOLO_MAIN"

confirm() { [[ $FORCE -eq 1 ]] && return 0; read -rp "$1 [y/N] " a; [[ "$a" == y* ]]; }

if [[ $DO_SEED -eq 1 ]]; then
  warn "--seed runs 'jr db.reset' — this REWRITES the dev database in $HOLO_MAIN"
  confirm "continue?" || die "aborted"
  ( set -x; jr db.reset && jr db.migrate ) || die "seed failed"
fi

warn "about to 'docker compose down' in $HOLO_MAIN for a consistent snapshot"
warn "(this stops your running dev stack, if any)"
confirm "stop the stack and build golden?" || die "aborted"
docker compose down || warn "compose down returned nonzero (already down?)"

if [[ -e "$HOLO_GOLDEN" ]]; then
  confirm "overwrite existing golden at $HOLO_GOLDEN?" || die "aborted"
  # the golden contains uid-999/mode-700 pgdata on Linux -> escalate only if needed
  [[ "$HOLO_GOLDEN" == "$HOLO_ROOT"/?* ]] \
    || die "refusing to remove a golden outside \$HOLO_ROOT ($HOLO_ROOT): $HOLO_GOLDEN"
  holo_rm_tree "$HOLO_GOLDEN"
fi
mkdir -p "$(dirname "$HOLO_GOLDEN")"

# A9: scrub/assert BEFORE the clone — after this point the tree becomes the golden.
holo_scrub_secrets "$HOLO_MAIN"

log "CoW-cloning checkout -> golden (instant on APFS/btrfs, shares blocks)..."
holo_cow_clone "$HOLO_MAIN" "$HOLO_GOLDEN"

{ git -C "$HOLO_GOLDEN" rev-parse HEAD 2>/dev/null || echo unknown; date; } > "$HOLO_GOLDEN/.holodeck-golden"
ok "golden ready at $HOLO_GOLDEN  (HEAD $(git -C "$HOLO_GOLDEN" rev-parse --short HEAD 2>/dev/null || echo '?'))"
log "now strike a workspace:  $SCRIPT_DIR/strike.sh FSA-101"
