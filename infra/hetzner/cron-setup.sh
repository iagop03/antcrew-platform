#!/usr/bin/env bash
# cron-setup.sh — Install WSL crontab entries to auto up/down INT and UAT
#                 Mon-Fri 09:00 up, 19:00 down (UTC — adjust for your timezone).
# Usage: ./cron-setup.sh [--remove]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

REMOVE="${1:-}"
LOG_DIR="${HOME}/.local/share/antcrew-infra/logs"
mkdir -p "$LOG_DIR"

# Cron schedule (UTC — adjust if your team is not in UTC)
# Mon-Fri = 1-5
UP_CRON="0 9 * * 1-5"    # 09:00 UTC Mon-Fri
DOWN_CRON="0 19 * * 1-5"  # 19:00 UTC Mon-Fri

INT_UP_CMD="${SCRIPT_DIR}/up.sh int   >> ${LOG_DIR}/int-up.log 2>&1"
INT_DOWN_CMD="${SCRIPT_DIR}/down.sh int >> ${LOG_DIR}/int-down.log 2>&1"
UAT_UP_CMD="${SCRIPT_DIR}/up.sh uat   >> ${LOG_DIR}/uat-up.log 2>&1"
UAT_DOWN_CMD="${SCRIPT_DIR}/down.sh uat >> ${LOG_DIR}/uat-down.log 2>&1"

# The cron job needs HCLOUD_TOKEN; we read it from a credentials file
# rather than embedding it in crontab (where it would be visible in ps output).
CRED_FILE="${HOME}/.config/antcrew-infra/credentials"

if [[ "$REMOVE" == "--remove" ]]; then
    step "Removing antcrew-infra cron entries"
    crontab -l 2>/dev/null \
        | grep -v "antcrew-infra" \
        | grep -v "up.sh\|down.sh" \
        | crontab -
    log "Cron entries removed."
    exit 0
fi

# ── Verify cron service is running (WSL-specific) ─────────────────────────────
step "Checking cron service"
if ! service cron status &>/dev/null; then
    warn "cron service is not running."
    log  "Starting cron in WSL..."
    sudo service cron start
    log  "To auto-start cron when WSL launches, add to /etc/wsl.conf:"
    log  "  [boot]"
    log  "  command = service cron start"
fi

# ── Write credentials file ────────────────────────────────────────────────────
step "Saving HCLOUD_TOKEN to $CRED_FILE"
if [[ -z "${HCLOUD_TOKEN:-}" ]]; then
    error "HCLOUD_TOKEN not set. Set it and re-run."
    exit 1
fi
mkdir -p "$(dirname "$CRED_FILE")"
printf "export HCLOUD_TOKEN='%s'\n" "$HCLOUD_TOKEN" > "$CRED_FILE"
chmod 600 "$CRED_FILE"
log "Credentials saved (mode 600). Keep this file out of git."

# ── Build crontab ─────────────────────────────────────────────────────────────
step "Installing cron entries"

CRON_HEADER="# antcrew-infra — auto-managed, do not edit manually — use cron-setup.sh"
CRON_SOURCE_LINE="source ${CRED_FILE}"

NEW_ENTRIES="
${CRON_HEADER}
SHELL=/bin/bash
${UP_CRON}   . ${CRED_FILE} && ${INT_UP_CMD}
${UP_CRON}   . ${CRED_FILE} && ${UAT_UP_CMD}
${DOWN_CRON} . ${CRED_FILE} && ${INT_DOWN_CMD}
${DOWN_CRON} . ${CRED_FILE} && ${UAT_DOWN_CMD}
# antcrew-infra-end
"

# Remove existing antcrew-infra block, then append new one
(crontab -l 2>/dev/null | grep -v "antcrew-infra" | grep -v "up.sh\|down.sh"; echo "$NEW_ENTRIES") | crontab -

log "Cron entries installed. Current crontab:"
crontab -l | grep -A20 "antcrew-infra" || true

# ── Print log locations ───────────────────────────────────────────────────────
step "Cron schedule (UTC):"
printf "\n"
printf "  INT up    : %s\n" "$UP_CRON"
printf "  INT down  : %s\n" "$DOWN_CRON"
printf "  UAT up    : %s\n" "$UP_CRON"
printf "  UAT down  : %s\n" "$DOWN_CRON"
printf "\n"
printf "  Logs: %s/{int,uat}-{up,down}.log\n" "$LOG_DIR"
printf "\n"
warn "TIMEZONE: schedule above is UTC. Adjust if your team works in a different timezone."
warn "Example for CET (UTC+1): change '0 9' to '0 8' and '0 19' to '0 18'."
printf "\n"
log  "To remove: ./cron-setup.sh --remove"
log  "Credentials: $CRED_FILE (mode 600, not committed to git)"
