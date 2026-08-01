#!/usr/bin/env bash
# snapshot-refresh.sh — Regenerate the base snapshot from the currently running server.
# Use this after installing new software or updating config on the base server.
# Usage: ./snapshot-refresh.sh <env>   (e.g. ./snapshot-refresh.sh int)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# ── Validate ──────────────────────────────────────────────────────────────────
require_hcloud
require_token
load_env "${1:-}"

ENV_NAME="${1}"
ENV_FILE="$SCRIPT_DIR/config/${ENV_NAME}.env"
TIMESTAMP="$(date '+%Y%m%d-%H%M')"
DESCRIPTION="${ENV_NAME}-base-${TIMESTAMP}"

# ── Check server is running ───────────────────────────────────────────────────
if ! server_exists "$SERVER_NAME"; then
    error "Server '$SERVER_NAME' is not running."
    error "Bring it up first: ./up.sh $ENV_NAME"
    exit 1
fi

IP="$(server_ip "$SERVER_NAME")"
step "Creating snapshot of '$SERVER_NAME' (IP: $IP)"
log  "  Description: $DESCRIPTION"

# ── Stop app cleanly before snapshot (avoids in-progress write inconsistencies)
step "Stopping application before snapshotting..."
ssh_cmd "$IP" bash <<REMOTE
set -uo pipefail
cd "${APP_DIR}" 2>/dev/null || true
if command -v docker &>/dev/null && [ -f docker-compose.yml -o -f compose.yml ]; then
    echo "Stopping Docker Compose for clean snapshot..."
    docker compose stop
fi
REMOTE
log "Application stopped."

# ── Create snapshot ───────────────────────────────────────────────────────────
step "Running hcloud server create-image..."
hcloud server create-image \
    --type        snapshot \
    --description "$DESCRIPTION" \
    --server      "$SERVER_NAME"

# Extract the new snapshot ID (most recent snapshot with our description prefix)
NEW_SNAPSHOT_ID="$(hcloud image list --type snapshot -o json | python3 -c "
import sys, json
images = json.load(sys.stdin)
matching = [i for i in images if '${ENV_NAME}-base-' in (i.get('description') or '')]
matching.sort(key=lambda i: i['created'], reverse=True)
if not matching:
    print('', end='')
else:
    print(matching[0]['id'])
")"

if [[ -z "$NEW_SNAPSHOT_ID" ]]; then
    error "Could not find new snapshot. Check 'hcloud image list --type snapshot'."
    exit 1
fi

log "New snapshot ID: $NEW_SNAPSHOT_ID (description: $DESCRIPTION)"

# ── Restart application ───────────────────────────────────────────────────────
step "Restarting application after snapshot..."
ssh_cmd "$IP" bash <<REMOTE
set -uo pipefail
cd "${APP_DIR}" 2>/dev/null || true
if command -v docker &>/dev/null && [ -f docker-compose.yml -o -f compose.yml ]; then
    docker compose up -d --remove-orphans
fi
REMOTE
log "Application restarted."

# ── Update .env file ──────────────────────────────────────────────────────────
OLD_SNAPSHOT_ID="${SNAPSHOT_ID:-}"
step "Updating SNAPSHOT_ID in $ENV_FILE"
log  "  Old: ${OLD_SNAPSHOT_ID:-<unset>}"
log  "  New: $NEW_SNAPSHOT_ID"

if grep -q "^SNAPSHOT_ID=" "$ENV_FILE"; then
    # Replace existing line
    sed -i "s|^SNAPSHOT_ID=.*|SNAPSHOT_ID=${NEW_SNAPSHOT_ID}|" "$ENV_FILE"
else
    # Append if missing
    echo "SNAPSHOT_ID=${NEW_SNAPSHOT_ID}" >> "$ENV_FILE"
fi
log "Updated $ENV_FILE"

# ── Offer to delete old snapshot ──────────────────────────────────────────────
if [[ -n "$OLD_SNAPSHOT_ID" && "$OLD_SNAPSHOT_ID" != "$NEW_SNAPSHOT_ID" ]]; then
    step "Old snapshot still exists: ID=$OLD_SNAPSHOT_ID"
    warn "Keeping old snapshots preserves rollback points but costs ~€0.01/GB/month."

    printf "\nDelete old snapshot %s? [y/N] " "$OLD_SNAPSHOT_ID"
    read -r REPLY </dev/tty
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        hcloud image delete "$OLD_SNAPSHOT_ID"
        log "Old snapshot $OLD_SNAPSHOT_ID deleted."
    else
        log "Old snapshot $OLD_SNAPSHOT_ID kept. Delete manually when no longer needed:"
        log "  hcloud image delete $OLD_SNAPSHOT_ID"
    fi
fi

step "Done"
log "Snapshot '$DESCRIPTION' (ID: $NEW_SNAPSHOT_ID) is now active for env '$ENV_NAME'."
log "Next up.sh $ENV_NAME will use this snapshot."
