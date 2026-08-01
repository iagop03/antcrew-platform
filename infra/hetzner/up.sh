#!/usr/bin/env bash
# up.sh — Create ephemeral server from snapshot, attach volume, wait for SSH.
# Usage: ./up.sh <env>   (e.g. ./up.sh int  or  ./up.sh uat)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# ── Validate ─────────────────────────────────────────────────────────────────
require_hcloud
require_token
load_env "${1:-}"

# ── Idempotency: already up? ──────────────────────────────────────────────────
if server_exists "$SERVER_NAME"; then
    IP="$(server_ip "$SERVER_NAME")"
    warn "Server '$SERVER_NAME' already exists — nothing to do."
    log  "  IP : $IP"
    log  "  SSH: ssh -i $SSH_KEY_PATH root@$IP"
    exit 0
fi

# ── Validate snapshot ────────────────────────────────────────────────────────
if [[ -z "${SNAPSHOT_ID:-}" ]]; then
    error "SNAPSHOT_ID is not set in config/${1}.env"
    error "Run setup.sh first to create the base snapshot and record its ID."
    exit 1
fi

step "Creating server '$SERVER_NAME' from snapshot $SNAPSHOT_ID"
log  "  Type    : $SERVER_TYPE"
log  "  Location: $LOCATION"
log  "  SSH key : $SSH_KEY_NAME"

hcloud server create \
    --name         "$SERVER_NAME" \
    --type         "$SERVER_TYPE" \
    --image        "$SNAPSHOT_ID" \
    --location     "$LOCATION" \
    --ssh-key      "$SSH_KEY_NAME"

log "Server created."

# ── Attach volume ─────────────────────────────────────────────────────────────
step "Attaching volume '$VOLUME_NAME' to '$SERVER_NAME'"

if ! volume_exists "$VOLUME_NAME"; then
    error "Volume '$VOLUME_NAME' does not exist. Create it with:"
    error "  hcloud volume create --name $VOLUME_NAME --size ${VOLUME_SIZE:-10} --location $LOCATION"
    error "Then format it (ext4) and populate it from your base server."
    # Clean up the server we just created so state stays consistent
    warn "Deleting server '$SERVER_NAME' to stay consistent..."
    hcloud server delete "$SERVER_NAME"
    exit 1
fi

hcloud volume attach --server "$SERVER_NAME" "$VOLUME_NAME"
log "Volume attached."

# ── Get IP ────────────────────────────────────────────────────────────────────
IP="$(server_ip "$SERVER_NAME")"
log "Public IP: $IP"

# ── Wait for SSH ──────────────────────────────────────────────────────────────
wait_for_ssh "$IP"

# ── Mount volume + start application ─────────────────────────────────────────
step "Mounting volume and starting application on $IP"

VOL_ID="$(volume_id "$VOLUME_NAME")"
DEV="$(volume_dev_path "$VOL_ID")"

ssh_cmd "$IP" bash <<REMOTE
set -euo pipefail

# Mount the data volume (idempotent)
mkdir -p "${MOUNT_POINT}"
if ! mountpoint -q "${MOUNT_POINT}"; then
    echo "Mounting ${DEV} -> ${MOUNT_POINT}"
    mount -o discard,defaults "${DEV}" "${MOUNT_POINT}"
else
    echo "Volume already mounted at ${MOUNT_POINT}"
fi

# Persist the mount across reboots if not already in fstab
if ! grep -q "${MOUNT_POINT}" /etc/fstab; then
    echo "${DEV} ${MOUNT_POINT} ext4 discard,defaults,nofail 0 0" >> /etc/fstab
fi

# Start the application
cd "${APP_DIR}"
if command -v docker &>/dev/null && [ -f docker-compose.yml -o -f compose.yml ]; then
    echo "Starting Docker Compose services..."
    docker compose up -d --remove-orphans
else
    echo "WARNING: No Docker Compose file found at ${APP_DIR}. Manual start required."
fi
REMOTE

# ── Summary ───────────────────────────────────────────────────────────────────
step "Server is ready"
printf "\n"
printf "  Environment : %s\n"  "$1"
printf "  Server      : %s\n"  "$SERVER_NAME"
printf "  IP          : %s\n"  "$IP"
printf "  Volume      : %s → %s\n" "$VOLUME_NAME" "$MOUNT_POINT"
printf "\n"
printf "  SSH:  ssh -i %s root@%s\n" "$SSH_KEY_PATH" "$IP"
printf "  Down: %s/down.sh %s\n" "$SCRIPT_DIR" "$1"
printf "\n"
