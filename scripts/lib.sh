#!/usr/bin/env bash
# holodeck/lib.sh — shared config + helpers. Sourced by the other scripts.
#
# Portable copy-on-write: APFS clonefile on macOS (local demo), reflink on
# Linux btrfs/xfs (the app-test EC2). One golden image, many disposable clones.

set -euo pipefail

HOLO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---- per-machine config (gitignored) ----------------------------------------
# MUST be sourced BEFORE any derived default below. HOLO_WORKSPACES is computed from
# HOLO_ROOT, so sourcing this later left the golden under the configured HOLO_ROOT while
# workspaces stayed under the DEFAULT one — a split-brain that silently breaks CoW when
# HOLO_ROOT points at a dedicated volume. Caught on the POC EC2 box 2026-08-03.
#
#   # holodeck.local.env
#   HOLO_ROOT=/opt/holo/holodeck-data
#   HOLO_SRC=/opt/holo/golden-src/compliance-backend
#   HOLO_BUILD_CMD="docker compose build webserver"
#
# Precedence: environment > holodeck.local.env > manifest default.
# The file uses plain `VAR=value` (what people expect of an env file), which would
# otherwise clobber anything passed on the command line. So snapshot the HOLO_* vars that
# are already set, source the file, then restore the snapshot — giving the environment the
# last word without asking the file's author to remember `:=`.
if [[ -f "$HOLO_DIR/holodeck.local.env" ]]; then
  _holo_preset="$(compgen -v 2>/dev/null | grep '^HOLO_' || true)"
  _holo_snapshot=""
  # shellcheck disable=SC2086
  [[ -n "$_holo_preset" ]] && _holo_snapshot="$(declare -p $_holo_preset 2>/dev/null || true)"
  # shellcheck disable=SC1091
  source "$HOLO_DIR/holodeck.local.env"
  [[ -n "$_holo_snapshot" ]] && eval "$_holo_snapshot"
  unset _holo_preset _holo_snapshot
fi

# ---- config (override any via env) ----
: "${HOLO_ROOT:=$HOME/holodeck-data}"                 # where golden + workspaces live
: "${HOLO_WORKSPACES:=$HOLO_ROOT/ws}"                 # per-task workspace clones
: "${HOLO_MAIN:=$HOME/code/main}"                     # source checkout the golden is built from
# NOTE: HOLO_GOLDEN is deliberately NOT defaulted here — it is PER-APP and each manifest
# sets it with `: "${HOLO_GOLDEN:=...}"`. Defaulting it here would pre-set the variable, so
# the manifest's `:=` became a no-op and `HOLO_APP=compliance` silently resolved to *main's*
# golden. Caught in testing 2026-08-03. Add per-app vars to manifests, never here.
# Services booted per workspace. Lean by default so N stacks fit on a laptop.
# webserver hard-depends on database + redis + localstack (it loads secrets
# from localstack at boot via a healthcheck gate), so those are the real
# minimum; celery/nginx/workers are not needed for the API demo.
# Set WS_SERVICES="" to boot the whole compose file (heavy).
: "${WS_SERVICES:=database redis localstack webserver}"

# Services that publish host ports in main/docker-compose.yaml (verified 2026-07).
# These get their ports stripped per-workspace so N stacks don't collide.
# Regenerate: see README (parses the compose file).
HOLO_PORT_SERVICES=(nginx redis database jaeger-all-in-one otel-collector \
  webserver mcp-server internal-gql-server celery flower localstack)

# ---- per-app defaults (main). Overridden by the app manifest below. ----
: "${HOLO_COMPOSE_FILE:=docker-compose.yaml}"          # compose filename in the checkout
: "${HOLO_APP_SERVICE:=webserver}"                     # app container (preview remaps its port)
: "${HOLO_APP_PORT:=6543}"                             # app internal port
: "${HOLO_PG_SERVICE:=database}"                        # postgres service
: "${HOLO_PG_USER:=jsqapp}"                            # postgres user (warm-DB probe)
: "${HOLO_PG_DB:=jsq}"                                 # postgres db
: "${HOLO_READINESS_PATH:=/readyz}"                    # readiness endpoint (no Host header)
: "${HOLO_READINESS_SCHEME:=http}"                     # http|https — https for TLS entrypoints (nginx/qong on 443/8989)
: "${HOLO_SEED_PROOF_SQL:=SELECT count(*) FROM arena;}" # >0 only when the DB is seeded
: "${HOLO_PGDATA_OVERRIDE:=}"                          # compose override relocating pgdata (named-volume apps)

# ---- app manifest: per-app overrides. HOLO_APP=main is the default. ----
# (HOLO_DIR and holodeck.local.env are handled at the top of this file — they must come
#  before the derived defaults above.)
HOLO_APP="${HOLO_APP:-main}"
if [[ -f "$HOLO_DIR/manifests/$HOLO_APP.sh" ]]; then
  # shellcheck disable=SC1090
  source "$HOLO_DIR/manifests/$HOLO_APP.sh"
elif [[ "$HOLO_APP" != "main" ]]; then
  printf '[holo] no manifest for app "%s" at %s/manifests/%s.sh\n' "$HOLO_APP" "$HOLO_DIR" "$HOLO_APP" >&2
  exit 1
fi

# ---- logging ----
_cg=$'\033[32m'; _cy=$'\033[33m'; _cr=$'\033[31m'; _cb=$'\033[34m'; _co=$'\033[0m'
if [[ ! -t 1 ]]; then _cg=; _cy=; _cr=; _cb=; _co=; fi
log()  { printf '%s[holo]%s %s\n' "$_cb" "$_co" "$*"; }
ok()   { printf '%s[holo]%s %s\n' "$_cg" "$_co" "$*"; }
warn() { printf '%s[holo]%s %s\n' "$_cy" "$_co" "$*" >&2; }
die()  { printf '%s[holo]%s %s\n' "$_cr" "$_co" "$*" >&2; exit 1; }

# ---- ticket -> safe id/project ("JSQ-118" -> "jsq-118") ----
holo_id() { echo "$1" | tr '[:upper:] /' '[:lower:]--' | tr -cd 'a-z0-9-'; }

# ---- portable stat helpers (GNU coreutils on Linux, BSD stat on macOS) ----
holo_inode()  { stat -c %i "$1" 2>/dev/null || stat -f %i "$1"; }
holo_device() { stat -c %d "$1" 2>/dev/null || stat -f %d "$1"; }

# ---- run as root: no-op if we already are (SSM send-command runs as root) ----
holo_sudo() { if [[ ${EUID:-$(id -u)} -eq 0 ]]; then "$@"; else sudo "$@"; fi; }

# ---- remove a tree that may contain uid-999 / mode-700 files (pgdata) --------
#
# On Linux, Postgres's data dir inside a workspace/golden/checkout is owned by uid 999
# mode 700, so a plain `rm -rf` fails with EACCES. On macOS, Docker Desktop translates
# bind-mount ownership and no escalation is needed. So: try unprivileged, escalate only
# if that fails — never demand sudo unconditionally.
#
# ⚠️ CALLERS MUST VALIDATE THE PATH FIRST. This deletes whatever it is given.
holo_rm_tree() {
  local target=${1:-}
  [[ -n "$target" && "$target" != "/" && "$target" != "$HOME" \
     && "$target" != "." && "$target" != ".." ]] \
    || die "holo_rm_tree: refusing to remove '$target'"
  # Refuse a symlink: chmod -R / rm -rf below would follow it into the target tree,
  # recursively chmod-ing files that live OUTSIDE the intended path.
  [[ -L "$target" ]] && die "holo_rm_tree: refusing to remove symlink '$target'"
  [[ -e "$target" ]] || return 0

  # Re-check the CANONICAL path: a relative path, a trailing `..`, or an interior symlink
  # could still resolve to / or $HOME. A string re-join is NOT enough — e.g. "$HOME/foo/.."
  # would pass a literal `!= $HOME` check yet resolve to $HOME. Canonicalize via the PARENT
  # (cd … && pwd -P, which collapses `..` and follows symlinks) and re-join the basename.
  # We deliberately never `cd` INTO $target: a seeded pgdata dir is owned by the postgres
  # uid (999, mode 0700), so `cd $target` fails for the invoking user and the old
  # dir-branch died with "cannot resolve" before ever reaching the sudo removal below.
  # The parent is searchable, and the `-L $target` refusal above already blocks a final
  # symlink component, so parent+basename canonicalizes dirs and files alike.
  local parent base resolved
  base="$(basename "$target")"
  [[ "$base" == "." || "$base" == ".." ]] \
    && die "holo_rm_tree: refusing to remove '$target' (unsafe basename '$base')"
  parent="$(cd "$(dirname "$target")" 2>/dev/null && pwd -P)" \
    || die "holo_rm_tree: cannot resolve parent of '$target'"
  resolved="${parent%/}/$base"
  [[ -n "$resolved" && "$resolved" != "/" && "$resolved" != "$HOME" ]] \
    || die "holo_rm_tree: refusing to remove '$target' (resolves to '$resolved')"

  chmod -R u+w "$target" 2>/dev/null || true
  rm -rf "$target" 2>/dev/null && return 0

  warn "unprivileged removal failed (uid-999 pgdata?) — retrying with sudo: $target"
  holo_sudo chmod -R u+w "$target" 2>/dev/null || true
  holo_sudo rm -rf "$target"
  [[ -e "$target" ]] && die "failed to remove $target"
  return 0
}

# ---- copy-on-write clone: src -> dst (dst must not exist) ----
#
# Every historical failure of this function reported SUCCESS. So it now (a) resolves
# symlinks, (b) never silently degrades to a full copy, and (c) asserts the
# postcondition. See holodeck_materials/05_risk_register.md V2 / V14.
holo_cow_clone() {
  local src=$1 dst=$2

  # (a) V14: `cp -R <symlink> <dst>` copies the SYMLINK, not the tree — producing a
  #     "golden"/"workspace" that points back at the live checkout. Resolve instead.
  [[ -e "$src" ]] || die "clone source does not exist: $src"
  src="$(cd "$src" && pwd -P)"
  [[ -e "$dst" ]] && die "clone destination already exists: $dst"

  # CoW only works within one filesystem — otherwise it's a silent full copy.
  local dstparent; dstparent="$(dirname "$dst")"; mkdir -p "$dstparent"
  [[ "$(holo_device "$src")" == "$(holo_device "$dstparent")" ]] \
    || die "src and dst are on different filesystems — CoW impossible ($src -> $dst)"

  case "$(uname -s)" in
    Darwin)
      # Docker Desktop translates bind-mount ownership, so no privilege needed here.
      cp -cR "$src" "$dst" || die "APFS clonefile failed — same APFS volume required"
      ;;
    Linux)
      # -a  : preserves ownership, so Postgres still boots on the clone. Plain -R would
      #       NOT, and Postgres refuses to start on a wrongly-owned data dir.
      #       -a keeps the *mix* intact: repo files stay yours, pgdata stays 999:0.
      # =always (not =auto): 'auto' silently full-copies on ext4. Fail loudly instead.
      #
      # Escalate only if the unprivileged attempt fails: pgdata is mode 700 owned by
      # uid 999 (postgres in-container), unreadable to a normal user — verified on the
      # POC EC2 box 2026-08-03. But don't demand sudo when it isn't needed.
      if ! cp --reflink=always -a "$src" "$dst" 2>/dev/null; then
        rm -rf "$dst" 2>/dev/null || holo_sudo rm -rf "$dst" 2>/dev/null || true
        holo_sudo cp --reflink=always -a "$src" "$dst" \
          || die "reflink clone failed — need xfs(reflink=1) or btrfs (NOT ext4), plus privileges for uid-999 pgdata"
      fi
      ;;
    *)
      die "unsupported OS for CoW clone: $(uname -s)"
      ;;
  esac

  # (c) assert the postcondition — this is the layer that was missing.
  [[ -d "$dst" && ! -L "$dst" ]] || die "clone produced a symlink, not a directory: $dst"
  [[ "$(holo_inode "$src")" != "$(holo_inode "$dst")" ]] \
    || die "clone shares an inode with the source — not a clone: $dst"
  [[ -n "$(ls -A "$dst" 2>/dev/null)" ]] || die "clone is empty: $dst"
}

# ---- does this filesystem actually support CoW? (probe, don't trust flags) ----
# Probes with the CURRENT user's privileges — HOLO_ROOT must be user-writable anyway,
# so this needs no escalation (and must not prompt for a password during preflight).
holo_check_cow() {
  local dir=$1 t1 t2 rc=0
  if ! mkdir -p "$dir" 2>/dev/null; then
    holo_sudo mkdir -p "$dir" && holo_sudo chown "$(id -u):$(id -g)" "$dir"
  fi
  t1="$dir/.holo-cow-probe.$$"; t2="$t1.clone"
  # Fail LOUDLY if the probe file can't be written (permissions / full disk): returning
  # success here would report "CoW supported" and let strike.sh silently full-copy a
  # multi-GB tree. A failed probe means preflight can't prove CoW, so it must fail.
  dd if=/dev/zero of="$t1" bs=1M count=4 status=none 2>/dev/null \
    || { rm -f "$t1" "$t2"; return 1; }
  case "$(uname -s)" in
    Darwin) cp -c "$t1" "$t2" 2>/dev/null || rc=1 ;;
    Linux)  cp --reflink=always "$t1" "$t2" 2>/dev/null || rc=1 ;;
  esac
  rm -f "$t1" "$t2"
  return $rc
}

# ---- scrub + assert: no secrets in the tree about to become the golden ------
#
# The golden is a clone of the checkout, so ANYTHING secret in the checkout is handed to
# every workspace and every agent that runs in one. Task A9 says "no long-lived secret
# baked into the golden"; this makes that automatic instead of relying on the operator.
#
# Real incidents this prevents (both hit on 2026-08-03):
#   - `make refresh-token` writes ./.secrets/CODEARTIFACT_TOKEN into the checkout
#   - a hand-added OVERRIDE_TOKEN (a real compliance-permissioned JWT) in .env
: "${HOLO_SECRET_SCRUB_PATHS:=.secrets .aws}"          # deleted before snapshot
: "${HOLO_SECRET_FORBIDDEN_KEYS:=OVERRIDE_TOKEN}"      # must not appear in .env

holo_scrub_secrets() {
  local src=$1 p k hit
  for p in ${HOLO_SECRET_SCRUB_PATHS}; do
    if [[ -e "$src/$p" ]]; then
      warn "scrubbing $p from the golden source (would otherwise be baked in)"
      holo_rm_tree "$src/$p"
    fi
  done

  # Assert rather than scrub for .env — it's usually needed for the app to boot, so
  # silently editing it could break the build. Fail loudly and let a human decide.
  if [[ -f "$src/.env" ]]; then
    for k in ${HOLO_SECRET_FORBIDDEN_KEYS}; do
      if grep -qE "^[[:space:]]*${k}=" "$src/.env" 2>/dev/null; then
        die "refusing to build a golden: $src/.env contains ${k}. Every workspace would carry it. Remove it: sed -i '/^${k}=/d' $src/.env"
      fi
    done
  fi

  # Broad backstop for anything obviously credential-shaped near the top of the tree.
  hit="$(find "$src" -maxdepth 2 \
          \( -name '*.pem' -o -name '*id_rsa*' -o -name 'aws.env' -o -name '*.p12' \) \
          -not -path '*/node_modules/*' -print -quit 2>/dev/null || true)"
  [[ -n "$hit" ]] && die "refusing to build a golden: credential-shaped file in the tree: $hit"
  ok "secret scan clean — safe to snapshot"
}

holo_preflight() {
  command -v docker >/dev/null || die "docker not found"

  # Compose >= 2.24 is a HARD requirement: port stripping (compose.ws.yaml) and pgdata
  # relocation both use !reset / !override, introduced in 2.24. Nothing validated this
  # before, so an older Compose failed with a confusing YAML error instead.
  local cv
  cv="$(docker compose version --short 2>/dev/null)" || die "'docker compose' (v2+) required"
  if ! printf '%s\n2.24.0\n' "$cv" | sort -V | head -1 | grep -qx '2.24.0'; then
    die "Compose >= 2.24 required for !reset/!override (found $cv)"
  fi

  # Fail loudly now rather than silently full-copying multi-GB trees later (V2).
  holo_check_cow "$HOLO_ROOT" \
    || die "$HOLO_ROOT does not support copy-on-write — need APFS, xfs(reflink=1) or btrfs (NOT ext4/tmpfs)"

  [[ -f "$HOME/secrets/aws.env" ]] || \
    warn "~/secrets/aws.env not found — services reference it; 'compose up' may fail"
}
