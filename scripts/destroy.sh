#!/usr/bin/env bash
# destroy.sh — tear down an ephemeral workspace: stop its stack, delete the clone.
#
# Usage:  ./destroy.sh JSQ-118

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

TICKET="${1:?usage: destroy.sh <ticket>}"
id="$(holo_id "$TICKET")"; proj="ws-$id"; wsdir="$HOLO_WORKSPACES/$id"

log "[$proj] app=$HOLO_APP  stopping containers + volumes..."
if [[ -d "$wsdir" ]]; then
  compose_files=(-f "$HOLO_COMPOSE_FILE" -f compose.ws.yaml)
  [[ -n "$HOLO_PGDATA_OVERRIDE" ]] && compose_files+=(-f "$HOLO_DIR/$HOLO_PGDATA_OVERRIDE")
  ( cd "$wsdir" && COMPOSE_PROJECT_NAME="$proj" \
      docker compose "${compose_files[@]}" down -v --remove-orphans ) \
    || warn "[$proj] compose down returned nonzero"

  # On Linux the clone contains uid-999/mode-700 pgdata (see lib.sh holo_cow_clone),
  # so removal needs privileges. That makes this `rm -rf` the most dangerous command
  # in Holodeck — guard the path before running it, never after.
  case "$wsdir" in
    "$HOLO_WORKSPACES"/?*) : ;;
    *) die "refusing to rm -rf outside \$HOLO_WORKSPACES ($HOLO_WORKSPACES): $wsdir" ;;
  esac
  [[ -L "$wsdir" ]] && die "refusing to rm -rf a symlink: $wsdir"
  [[ -d "$wsdir" ]] || die "refusing to rm -rf a non-directory: $wsdir"
  [[ "$(cd "$wsdir" && pwd -P)" == "$(cd "$HOLO_WORKSPACES" && pwd -P)/"* ]] \
    || die "refusing: $wsdir resolves outside $HOLO_WORKSPACES"

  # Path is validated above; holo_rm_tree handles the uid-999 escalation (lib.sh).
  holo_rm_tree "$wsdir"
  ok "[$proj] destroyed — nothing left behind."
else
  docker compose -p "$proj" down -v --remove-orphans 2>/dev/null || true
  warn "no workspace dir at $wsdir (already removed?) — attempted container cleanup by project name"
fi
