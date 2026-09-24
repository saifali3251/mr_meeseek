#!/usr/bin/env bash
# setup-box-full-stack-application.sh — bootstrap a fresh box to build the
# full-stack-application golden. Modeled on
# reference-materials/root-docs/setup-omnigent-poc-box.sh, stripped down for
# an app that needs none of that script's harder dependencies:
#   - no Node on the HOST (frontend build/dev-server run entirely inside Docker)
#   - no AWS CLI / ECR login (every base image is public)
#   - no CodeArtifact / AWS session credential minting
# So this is Docker + the CoW volume + the holodeck checkout + one golden-build
# call — nothing else.
#
# Idempotent: safe to re-run after a partial failure, same convention as the
# script this is modeled on.
#
# Run as a regular login user, NOT root.
#
# Required env vars:
#   HOLODECK_GITHUB_PAT   PAT with read access to the holodeck repo
#
# Required when the CoW volume isn't mounted yet (first run only):
#   HOLO_DEVICE           block device for the CoW volume, e.g. /dev/nvme1n1
#                         — never auto-detected, same reasoning as the
#                         original script: guessing wrong formats the wrong disk.
#
# App source is git-cloned now, NOT copied onto the box — as of the
# 2026-09-22 repo split, full-stack-application is two repos (test_backend,
# test_frontend) and golden-build.full-stack-application.sh clones both
# itself (same pattern as control-tower's composite). This script only
# creates the empty composite-root directory; nothing to rsync here anymore.
#
# If those repos are private, HOLO_GIT_BASE (set in
# manifests/full-stack-application.sh, still a placeholder) needs
# credentials embedded, e.g. HOLO_GIT_BASE="https://<PAT>@github.com/<org>"
# — separate from HOLODECK_GITHUB_PAT below, which is only for cloning
# holodeck itself in this script.
#
# Optional:
#   HOLO_BRANCH    holodeck branch to check out (default: main)
#   HOLO_APP_SRC   composite root for full-stack-application's two repos
#                  (default: $HOLO_MOUNT/full-stack-application)

set -euo pipefail

HOLODECK_GITHUB_PAT="${HOLODECK_GITHUB_PAT:-}"
HOLO_BRANCH="${HOLO_BRANCH:-main}"
HOLO_MOUNT=/opt/holo/holodeck-data
HOLO_APP_SRC="${HOLO_APP_SRC:-$HOLO_MOUNT/full-stack-application}"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run this as a regular user, not root." >&2
  exit 1
fi

log() { printf '\033[34m[setup]\033[0m %s\n' "$*"; }

# ---- 1. base packages + docker --------------------------------------------
log "installing base packages"
sudo apt-get update -y
# python3-venv / python3-pip: Debian splits these out of the base python3
# package. Without them, `python3 -m venv` creates the directory then fails
# at "ensurepip is not available" — needed for the control-plane's own venv
# (control-plane/README's venv setup), not anything in this script itself.
sudo apt-get install -y ca-certificates curl gnupg git unzip jq make python3-venv python3-pip

if ! command -v docker >/dev/null 2>&1; then
  log "installing docker"
  distro_id="$(. /etc/os-release && echo "$ID")"
  distro_codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$distro_id/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$distro_id $distro_codename stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
  log "docker group membership just added — re-executing under it"
  exec sg docker "$0" "$@"
fi
log "docker already installed"
docker buildx version >/dev/null || { echo "docker buildx not working — check the install" >&2; exit 1; }

# ---- 2. CoW-capable volume for HOLO_ROOT -----------------------------------
if mount | grep -q " $HOLO_MOUNT "; then
  log "CoW volume already mounted at $HOLO_MOUNT, skipping format/mount"
else
  if [[ -z "${HOLO_DEVICE:-}" ]]; then
    echo "HOLO_DEVICE is not set. This step formats a disk — never guessed" >&2
    echo "automatically. Run 'lsblk' yourself, identify the new *unpartitioned*" >&2
    echo "volume (NOT the root disk), then re-run with HOLO_DEVICE=/dev/nvmeXnY" >&2
    lsblk
    exit 1
  fi
  log "formatting $HOLO_DEVICE as XFS (reflink=1) and mounting at $HOLO_MOUNT"
  sudo mkfs.xfs -f -m reflink=1 "$HOLO_DEVICE"
  sudo mkdir -p "$HOLO_MOUNT"
  sudo mount "$HOLO_DEVICE" "$HOLO_MOUNT"
  sudo chown -R "$USER:$USER" "$HOLO_MOUNT"
  uuid=$(sudo blkid -s UUID -o value "$HOLO_DEVICE")
  grep -q "$HOLO_MOUNT" /etc/fstab || echo "$uuid $HOLO_MOUNT xfs defaults,noatime 0 2" | sudo tee -a /etc/fstab
fi
df -T "$HOLO_MOUNT"

# ---- 3. clone/stage meeseek (for manifest/override/golden-build scripts) ---
sudo mkdir -p /opt/holo
sudo chown "$USER:$USER" /opt/holo
if [[ ! -d /opt/holo/holodeck/.git ]]; then
  if [[ -d "$(pwd)/.git" && -f "$(pwd)/manifests/full-stack-application.sh" ]]; then
    log "copying current meeseek directory to /opt/holo/holodeck"
    cp -r "$(pwd)" /opt/holo/holodeck
  else
    log "cloning meeseek (branch $HOLO_BRANCH)"
    auth_prefix=""
    [[ -n "$HOLODECK_GITHUB_PAT" ]] && auth_prefix="${HOLODECK_GITHUB_PAT}@"
    git clone --quiet "https://${auth_prefix}github.com/saifali3251/mr_meeseek.git" /opt/holo/holodeck
    (cd /opt/holo/holodeck && git checkout --quiet "$HOLO_BRANCH")
  fi
else
  log "meeseek already installed at /opt/holo/holodeck, skipping"
fi

# ---- 4. composite-root directory for the two app repos --------------------
# Just the empty directory — golden-build.full-stack-application.sh clones
# test_backend/test_frontend into it itself.
mkdir -p "$HOLO_APP_SRC"
log "composite root ready at $HOLO_APP_SRC (repos cloned by golden-build)"

# ---- 5. per-box config -------------------------------------------------------
cat > /opt/holo/holodeck/holodeck.local.env <<EOF
HOLO_ROOT=$HOLO_MOUNT
HOLO_SRC=$HOLO_APP_SRC
EOF

# ---- 6. golden build ---------------------------------------------------------
log "running golden-build.full-stack-application.sh"
(cd /opt/holo/holodeck && ./scripts/golden-build.full-stack-application.sh --yes)

log "done. Validate with:"
log "  cd /opt/holo/holodeck && HOLO_APP=full-stack-application ./scripts/strike.sh FSA-1 --preview 13000"
