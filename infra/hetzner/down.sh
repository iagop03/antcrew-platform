#!/usr/bin/env bash
# down.sh — Stop Docker services, detach volume, delete server.
# The Volume is NEVER deleted — only the Server.
# Usage: ./down.sh <env>   (e.g. ./down.sh int  or  ./down.sh uat)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# ── Validate ──────────────────────────────────────────────────────────────────
require_hcloud
require_token
load_env "${1:-}"

# ── Idempotency: already down? ────────────────────────────────────────────────
if ! server_exists "$SERVER_NAME"; then
    warn "Server '$SERVER_NAME' does not exist — nothing to do."
    exit 0
fi

# ── Safety summary ────────────────────────────────────────────────────────────
IP="$(server_ip "$SERVER_NAME")"
step "About to shut down and DELETE the following (this is logged for audit):"
log  "  Server  : $SERVER_NAME  (IP: $IP)"
log  "  Volume  : $VOLUME_NAME  ← will be DETACHED, NOT deleted"
log  "  Type    : $SERVER_TYPE"
log  "  Location: $LOCATION"

# Record server creation time for cost estimate
SERVER_CREATED_AT="$(hcloud server describe "$SERVER_NAME" -o json | python3 -c "
import sys, json, datetime
d = json.load(sys.stdin)
print(d['created'])
" 2>/dev/null || echo "")"

# ── Stop application gracefully before detaching volume ──────────────────────
step "Stopping application services on $IP"
if ssh_cmd "$IP" true &>/dev/null; then
    ssh_cmd "$IP" bash <<REMOTE || warn "Application stop returned non-zero — continuing anyway"
set -uo pipefail
cd "${APP_DIR}" 2>/dev/null || true
if command -v docker &>/dev/null && [ -f docker-compose.yml -o -f compose.yml ]; then
    echo "Stopping Docker Compose services..."
    docker compose stop
fi

# Unmount data volume cleanly before detach
if mountpoint -q "${MOUNT_POINT}" 2>/dev/null; then
    echo "Unmounting ${MOUNT_POINT}..."
    sync
    umount "${MOUNT_POINT}"
fi
REMOTE
else
    warn "Server not reachable via SSH — skipping graceful stop (will force-detach volume)"
fi

# ── Detach volume ─────────────────────────────────────────────────────────────
step "Detaching volume '$VOLUME_NAME' from '$SERVER_NAME'"
hcloud volume detach "$VOLUME_NAME"
log "Volume detached."

# ── Delete server ─────────────────────────────────────────────────────────────
step "Deleting server '$SERVER_NAME'"
log "  (The volume '$VOLUME_NAME' will NOT be deleted)"
hcloud server delete "$SERVER_NAME"
log "Server deleted."

# ── Verify volume still exists ───────────────────────────────────────────────
step "Verifying volume '$VOLUME_NAME' still exists..."
if volume_exists "$VOLUME_NAME"; then
    log "Volume '$VOLUME_NAME' is safe and unattached."
    hcloud volume describe "$VOLUME_NAME" -o json | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('  Volume ID  :', d['id'])
print('  Size       :', d['size'], 'GB')
print('  Location   :', d['location']['name'])
print('  Server     :', d.get('server', 'none (detached)'))
" 2>/dev/null || true
else
    error "UNEXPECTED: Volume '$VOLUME_NAME' not found after server deletion!"
    error "Check Hetzner console immediately: https://console.hetzner.cloud"
    exit 1
fi

# ── Cost estimate ─────────────────────────────────────────────────────────────
if [[ -n "$SERVER_CREATED_AT" ]]; then
    HOURS_RUNNING=$(python3 -c "
import datetime, sys
created = datetime.datetime.fromisoformat('${SERVER_CREATED_AT}'.replace('Z','+00:00'))
now = datetime.datetime.now(datetime.timezone.utc)
hours = (now - created).total_seconds() / 3600
print(f'{hours:.2f}')
" 2>/dev/null || echo "?")
    print_cost_estimate "$SERVER_TYPE" "$HOURS_RUNNING"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
step "Done"
log "Server '$SERVER_NAME' deleted. Volume '$VOLUME_NAME' preserved."
log "To bring it back up: $SCRIPT_DIR/up.sh $1"
