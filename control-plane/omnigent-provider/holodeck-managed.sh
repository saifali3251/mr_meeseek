#!/usr/bin/env bash
# holodeck-managed.sh — install + wire + smoke-test the holodeck MANAGED sandbox
# provider on an Omnigent server box (covers DEPLOY-MANAGED.md Steps 2-6).
#
# Usage:  edit the CONFIG block below (or pass as env vars), then run subcommands
# IN ORDER:
#     ./holodeck-managed.sh install        # Step 2  wheel + discovery check
#     ./holodeck-managed.sh config         # Step 3  add sandbox: block
#     ./holodeck-managed.sh wire-systemd   # Step 4  drop-in + restart  (or: wire-bare)
#     ./holodeck-managed.sh smoke          # Step 5  /v1/info + managed session
#     ./holodeck-managed.sh rollback       # Step 6  undo
#
# `wire-systemd` RESTARTS the server. Everything else is non-destructive.
set -euo pipefail

# ── CONFIG — edit or pass as env ───────────────────────────────────────────
OMNIPY="${OMNIPY:-$(head -1 "$(command -v omnigent)" 2>/dev/null | sed 's/^#!//')}" # omnigent interpreter
# version-agnostic: newest matching wheel wins, so a version bump needs no edit here
WHEEL="${WHEEL:-$(ls -t /opt/holodeck/omnigent_community_sandbox_holodeck-*.whl 2>/dev/null | head -1)}"
WRAP="${WRAP:-/opt/holodeck/holodeck_server.py}"          # the launch wrapper
CONFIG="${CONFIG:-/etc/omnigent/config.yaml}"             # omnigent server config
SERVICE="${SERVICE:-omnigent}"                            # systemd unit name
SERVER_PORT="${SERVER_PORT:-8080}"                        # omnigent server port

# provider runtime (read by the launcher inside the server)
HOLODECK_URL="${HOLODECK_URL:-http://127.0.0.1:8099}"     # control-plane lease API (reachable FROM here)
HOLODECK_APP="${HOLODECK_APP:-full-stack-application}"
HOLODECK_TOKEN="${HOLODECK_TOKEN:-}"                      # only if control plane is network-exposed
PYTHONPATH_EXTRA="${PYTHONPATH_EXTRA:-}"                  # set to /opt/holodeck ONLY if wheel installed via --target

# managed sandbox config
SERVER_URL="${SERVER_URL:-}"                              # REQUIRED: public URL the workspace dials back to

# smoke test
API_TOKEN="${API_TOKEN:-}"                                # bearer for /v1/*  (blank if auth off)
AGENT_ID="${AGENT_ID:-}"                                  # durable agent id (blank = skip session, info only)
TICKET="${TICKET:-FSA-SMOKE}"
# ───────────────────────────────────────────────────────────────────────────

log(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
ok(){  printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
die(){ printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
[ -n "$OMNIPY" ] || die "could not resolve OMNIPY (omnigent interpreter) — set OMNIPY=/path/to/venv/bin/python"

verify_discovery(){
  log "verify provider discovery (in $OMNIPY)"
  "$OMNIPY" - <<'PY'
import sys
from omnigent.onboarding.sandboxes import registry as r
provs = list(r.available_providers()); errs = r.plugin_state().load_errors
print("  providers:", provs); print("  load_errors:", errs)
if "holodeck" not in provs: sys.exit("holodeck NOT discovered — check wheel install / PYTHONPATH")
if errs: sys.exit(f"load_errors present: {errs}")
print("  OK: holodeck discovered, clean load")
PY
  ok "discovery good"
}

cmd_install(){        # Step 2
  log "Step 2 — install wheel into the omnigent env"
  [ -f "$WHEEL" ] || die "wheel not found: $WHEEL"
  "$OMNIPY" -m pip install --no-deps "$WHEEL"
  # sitecustomize.py's ticket-passthrough patch (_install_holodeck_ticket_passthrough)
  # opens its own SqlAlchemyConversationStore(DATABASE_URL) to resolve the real
  # ticket from session labels — that needs a Postgres driver, which the wheel
  # itself deliberately does NOT declare (it "uses the server's own omnigent").
  # Confirmed missing on a real deployment: ModuleNotFoundError: psycopg2, which
  # silently degrades every managed launch to Omnigent's generated host name
  # instead of the real ticket (ticket<->lease correlation breaks completely,
  # with zero visible error) — see the exception logging added alongside this.
  "$OMNIPY" -m pip install psycopg2-binary
  verify_discovery
}

cmd_verify(){ verify_discovery; }

cmd_config(){         # Step 3
  log "Step 3 — ensure sandbox: block in $CONFIG"
  [ -n "$SERVER_URL" ] || die "SERVER_URL required (public URL the workspace dials back to)"
  [ -f "$CONFIG" ]     || die "config not found: $CONFIG"
  if grep -qE '^[[:space:]]*sandbox:' "$CONFIG"; then
    printf '  A sandbox: block already exists. Ensure it reads:\n'
    printf '    sandbox:\n      provider: holodeck\n      server_url: %s\n' "$SERVER_URL"
    printf '  (not modifying it automatically to avoid clobbering)\n'
  else
    cp "$CONFIG" "$CONFIG.bak.$(date +%s)"
    cat >> "$CONFIG" <<EOF

sandbox:
  provider: holodeck
  server_url: $SERVER_URL
EOF
    ok "appended sandbox: block (backup: $CONFIG.bak.*)"
  fi
}

cmd_wire_systemd(){   # Step 4 (systemd) — preserves the ORIGINAL server flags
  log "Step 4 — systemd drop-in to launch via the wrapper (RESTARTS $SERVICE)"
  [ -f "$WRAP" ] || die "wrapper not found: $WRAP"
  local orig tail_args
  orig="$(systemctl cat "$SERVICE" 2>/dev/null | grep -m1 'ExecStart=' | sed 's/.*ExecStart=//')" \
    || die "no ExecStart found for unit '$SERVICE'"
  echo "  original ExecStart: $orig"
  # keep every arg that appeared AFTER the 'server' subcommand (host/port/etc.)
  tail_args="$(printf '%s' "$orig" | sed -E 's/^.*[[:space:]]server([[:space:]]|$)/ /')"
  [ "$tail_args" = "$orig" ] && tail_args=""     # 'server' not found -> no tail
  printf '%s' "$tail_args" | grep -q -- '--config' || tail_args="$tail_args --config $CONFIG"
  local newexec="$OMNIPY $WRAP server$tail_args"
  echo "  new ExecStart:      $newexec"

  local d="/etc/systemd/system/${SERVICE}.service.d"
  sudo mkdir -p "$d"
  {
    echo "[Service]"
    echo "Environment=HOLODECK_URL=$HOLODECK_URL"
    echo "Environment=HOLODECK_APP=$HOLODECK_APP"
    [ -n "$HOLODECK_TOKEN" ]    && echo "Environment=HOLODECK_TOKEN=$HOLODECK_TOKEN"
    [ -n "$PYTHONPATH_EXTRA" ]  && echo "Environment=PYTHONPATH=$PYTHONPATH_EXTRA"
    echo "ExecStart="
    echo "ExecStart=$newexec"
  } | sudo tee "$d/holodeck.conf" >/dev/null
  ok "wrote $d/holodeck.conf"
  sudo systemctl daemon-reload
  sudo systemctl restart "$SERVICE"
  sleep 3
  systemctl is-active "$SERVICE" >/dev/null && ok "$SERVICE active" || die "$SERVICE not active — see: journalctl -u $SERVICE -n 60"
  journalctl -u "$SERVICE" -n 20 --no-pager || true
}

cmd_wire_bare(){      # Step 4 (foreground) — Ctrl-C to stop
  log "Step 4 — run in FOREGROUND via the wrapper (Ctrl-C to stop)"
  [ -f "$WRAP" ] || die "wrapper not found: $WRAP"
  export HOLODECK_URL HOLODECK_APP
  [ -n "$HOLODECK_TOKEN" ]   && export HOLODECK_TOKEN
  [ -n "$PYTHONPATH_EXTRA" ] && export PYTHONPATH="$PYTHONPATH_EXTRA${PYTHONPATH:+:$PYTHONPATH}"
  exec "$OMNIPY" "$WRAP" server --config "$CONFIG"
}

cmd_smoke(){          # Step 5
  log "Step 5a — /v1/info: is managed launch wired for holodeck?"
  curl -fsS "http://localhost:$SERVER_PORT/v1/info" | "$OMNIPY" -c \
    'import sys,json;d=json.load(sys.stdin);print("  managed_sandboxes_enabled:",d.get("managed_sandboxes_enabled"),"| sandbox_provider:",d.get("sandbox_provider"))' \
    || die "could not read /v1/info on port $SERVER_PORT"
  if [ -z "$AGENT_ID" ]; then
    echo "  (set AGENT_ID to also fire a managed session)"; return
  fi
  log "Step 5b — fire a managed session (watch the control plane for POST /leases)"
  curl -fsS -X POST "http://localhost:$SERVER_PORT/v1/sessions" \
    ${API_TOKEN:+-H "Authorization: Bearer $API_TOKEN"} \
    -H 'content-type: application/json' \
    -d "{\"agent_id\":\"$AGENT_ID\",\"host_type\":\"managed\",\"labels\":{\"holodeck_ticket\":\"$TICKET\"},\"initial_items\":[{\"type\":\"message\",\"data\":{\"role\":\"user\",\"content\":[{\"type\":\"input_text\",\"text\":\"holodeck managed smoke test\"}]}}]}"
  echo
  ok "session create returned above — expect a POST /leases on the control plane (lease id = $(printf '%s' "$TICKET" | tr 'A-Z' 'a-z'))"
}

cmd_rollback(){       # Step 6
  log "Step 6 — rollback"
  sudo rm -f "/etc/systemd/system/${SERVICE}.service.d/holodeck.conf" 2>/dev/null && \
    { sudo systemctl daemon-reload; sudo systemctl restart "$SERVICE" || true; ok "removed systemd drop-in + restarted"; } || \
    echo "  (no systemd drop-in to remove)"
  "$OMNIPY" -m pip uninstall -y omnigent-community-sandbox-holodeck || true
  echo "  NOTE: the sandbox: block in $CONFIG was left intact — remove it by hand if desired (backups: $CONFIG.bak.*)"
}

case "${1:-}" in
  install)      cmd_install ;;
  verify)       cmd_verify ;;
  config)       cmd_config ;;
  wire-systemd) cmd_wire_systemd ;;
  wire-bare)    cmd_wire_bare ;;
  smoke)        cmd_smoke ;;
  rollback)     cmd_rollback ;;
  *) cat >&2 <<USAGE
holodeck-managed.sh — wire the holodeck MANAGED sandbox on an Omnigent box

  install        Step 2  pip install the wheel + verify discovery
  verify         re-run the discovery check only
  config         Step 3  add the sandbox: block (needs SERVER_URL)
  wire-systemd   Step 4  systemd drop-in via the wrapper + restart (preserves original flags)
  wire-bare      Step 4  run in the foreground instead
  smoke          Step 5  /v1/info check (+ managed session if AGENT_ID set)
  rollback       Step 6  undo drop-in + uninstall wheel

Set config via the block at the top of this file or env vars, e.g.:
  OMNIPY=/opt/omnigent/venv/bin/python WHEEL=/opt/holodeck/*.whl WRAP=/opt/holodeck/holodeck_server.py \\
  CONFIG=/etc/omnigent/config.yaml SERVICE=omnigent SERVER_PORT=8080 \\
  HOLODECK_URL=http://10.0.0.5:8099 SERVER_URL=https://omni.example.com \\
  ./holodeck-managed.sh install
USAGE
     exit 2 ;;
esac
