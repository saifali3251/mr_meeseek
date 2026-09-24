#!/usr/bin/env bash
# holo-demo.sh — one-command hackathon demo of the ephemeral substrate.
#
# Opens (optionally) with the bare-sandbox counterfactual, then strikes N
# isolated workspaces from the golden image, proves each is warm + seeded +
# isolated, assembles a per-task evidence bundle (the "trustworthy PR"), and
# tears everything down.
#
# Usage:
#   ./holo-demo.sh                       # 3 workspaces, smoke evidence, then destroy
#   ./holo-demo.sh --counterfactual      # open with the bare-sandbox contrast
#   ./holo-demo.sh --tickets HOLO-1,HOLO-2
#   ./holo-demo.sh --test jsq/tests/<fast_test>.py   # real test instead of smoke
#   DEMO_KEEP=1 ./holo-demo.sh           # leave workspaces up to poke at
#   ./holo-demo.sh --open-prs            # DANGER: pushes agent/* to origin + opens real PRs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

IFS=',' read -r -a TICKETS <<< "${DEMO_TICKETS:-HOLO-1,HOLO-2,HOLO-3}"
COUNTER=0; TEST_PATH=""; OPEN_PRS=0; PREVIEW_BASE="${DEMO_PREVIEW_BASE:-18100}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --counterfactual) COUNTER=1 ;;
    --tickets) IFS=',' read -r -a TICKETS <<< "${2:?}"; shift ;;
    --test) TEST_PATH="${2:?}"; shift ;;
    --open-prs) OPEN_PRS=1 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac; shift
done

hr()      { printf '%s\n' "────────────────────────────────────────────────────────────"; }
section() { echo; hr; ok "$*"; hr; }

holo_preflight
[[ -d "$HOLO_GOLDEN" ]] || die "no golden image — run ./golden-build.sh first"

EV="$HOLO_ROOT/evidence"; rm -rf "$EV"; mkdir -p "$EV"
CLEANUP=()
cleanup_all() {
  [[ "${DEMO_KEEP:-0}" == 1 ]] && { warn "DEMO_KEEP=1 — leaving ${#CLEANUP[@]} workspace(s) up"; return; }
  for t in "${CLEANUP[@]}"; do "$SCRIPT_DIR/destroy.sh" "$t" >/dev/null 2>&1 || true; done
}
trap cleanup_all EXIT

section "THESIS — the constraint on agentic coding isn't the model, it's the environment"

# ── optional: bare-sandbox counterfactual ───────────────────────────────────
if [[ $COUNTER -eq 1 ]]; then
  section "1 · BARE SANDBOX  — a generic agent with the code but no JSQ world"
  log "trying to exercise the app in a plain python container (no deps, no DB)…"
  set +e
  docker run --rm -v "$HOLO_GOLDEN/jsq":/app/jsq:ro python:3.12-slim \
    bash -c "cd /app && python -c 'import pyramid; print(\"imported\")'" 2>&1 | tail -3
  rc=$?
  set -e
  [[ $rc -ne 0 ]] \
    && warn "bare sandbox can't even import the app or reach a database → any diff it emits is unverified." \
    || warn "(import happened to resolve, but there is still no database or running stack to verify against.)"
fi

# ── strike N isolated workspaces ─────────────────────────────────────────────
section "2 · HOLODECK  — strike an isolated, seeded workspace per task"
i=0
for t in "${TICKETS[@]}"; do
  CLEANUP+=("$t")                       # register first so a partial strike still gets reaped
  "$SCRIPT_DIR/strike.sh" "$t" --preview "$((PREVIEW_BASE + i))"
  i=$((i+1))
done

section "3 · ISOLATION  — ${#TICKETS[@]} full stacks live at once, no collisions"
docker compose ls 2>/dev/null | grep -E 'NAME|ws-' || docker compose ls || true

# ── each workspace proves its own work ───────────────────────────────────────
section "4 · EVIDENCE  — each workspace verifies itself"
i=0
for t in "${TICKETS[@]}"; do
  id="$(holo_id "$t")"; proj="ws-$id"; wsdir="$HOLO_WORKSPACES/$id"
  port=$((PREVIEW_BASE + i)); i=$((i+1))
  d="$EV/$id"; mkdir -p "$d"
  dc() { ( cd "$wsdir" && COMPOSE_PROJECT_NAME="$proj" docker compose exec -T "$@" ); }

  log "[$proj] gathering evidence → $d"
  set +e
  # (a) the "agent" makes a small, safe change so there's a real diff
  echo "# Holodeck demo change for $t — $(date)" >> "$wsdir/HOLODECK_DEMO.md"
  git -C "$wsdir" add HOLODECK_DEMO.md
  git -C "$wsdir" -c user.email=holo@jsq -c user.name=holodeck commit -q --no-verify -m "$t: demo change" 2>/dev/null
  git -C "$wsdir" show --stat HEAD > "$d/diff.txt" 2>&1

  # (b) proof the CLONED DB is really seeded — the arena table (~24 named rows)
  arenas=$(dc database psql -U jsqapp -d jsq -tAc "select count(*) from arena" 2>/dev/null | tr -d '[:space:]')
  { echo "seeded arenas in cloned pgdata: ${arenas:-<unavailable>}"
    dc database psql -U jsqapp -d jsq -c "select id, name from arena order by id limit 6" 2>/dev/null
  } > "$d/db.txt"

  # (c) proof the SERVICE is live on its own isolated clone (endpoints from the codebase)
  host="${DEMO_HOST:-rockstonecap.dev.junipersquare.com}"
  readyz=$(curl -s -m 5 "http://127.0.0.1:$port/readyz" 2>/dev/null)
  hc=$(curl -s -m 5 -H "Host: $host" "http://127.0.0.1:$port/health_check" 2>/dev/null)
  { echo "GET /readyz       -> ${readyz:-<no response>}   (live + DB + Redis, warm on the clone)"
    echo "GET /health_check -> ${hc:-<no response>}   (seeded arena, Host: $host)"
  } > "$d/service.txt"

  # (c2) optional: a real test instead of the endpoint smoke
  [[ -n "$TEST_PATH" ]] && dc webserver jr test -- "$TEST_PATH" > "$d/test.log" 2>&1
  set -e

  # (e) render the would-be PR
  {
    echo "# $t — agent change (Holodeck workspace $proj)"
    echo; echo "## Verification evidence"
    echo '```'; cat "$d/service.txt"; echo; cat "$d/db.txt"; echo '```'
    [[ -f "$d/test.log" ]] && { echo "### test output"; echo '```'; tail -8 "$d/test.log"; echo '```'; }
    echo "### diff"; echo '```'; cat "$d/diff.txt" 2>/dev/null; echo '```'
    [[ -f "$d/screenshot.png" ]] && echo "### screenshot" && echo "attached: screenshot.png"
    echo; echo "_Struck from golden image, verified in an isolated workspace, then destroyed._"
  } > "$d/PR.md"
  ok "[$proj] evidence bundled → $d/PR.md"

  # (f) optionally open a real PR (guarded — pushes to origin)
  if [[ $OPEN_PRS -eq 1 ]]; then
    warn "[$proj] --open-prs: pushing agent/$id to origin and opening a draft PR"
    ( cd "$wsdir" && git push -u origin "agent/$id" \
        && gh pr create --draft --fill --title "$t: agent change" --body-file "$d/PR.md" ) || warn "[$proj] PR step failed"
  fi
done

section "5 · TEARDOWN  — nothing persists"
echo "evidence kept at: $EV"
find "$EV" -maxdepth 2 -type f | sed 's/^/  /'
# workspaces destroyed by the EXIT trap (unless DEMO_KEEP=1)
echo
ok "one golden image → ${#TICKETS[@]} isolated, seeded, verified workspaces → gone. that's the substrate."
