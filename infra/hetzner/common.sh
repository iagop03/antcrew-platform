#!/usr/bin/env bash
# common.sh — shared functions for all infra/hetzner scripts
# Source this file; do not execute directly.

# ── Colour helpers ────────────────────────────────────────────────────────────
_C_RESET='\033[0m'
_C_GREEN='\033[0;32m'
_C_YELLOW='\033[1;33m'
_C_RED='\033[0;31m'
_C_CYAN='\033[0;36m'

log()   { printf "[%s] ${_C_GREEN}INFO${_C_RESET}  %s\n"  "$(date '+%H:%M:%S')" "$*"; }
warn()  { printf "[%s] ${_C_YELLOW}WARN${_C_RESET}  %s\n"  "$(date '+%H:%M:%S')" "$*"; }
error() { printf "[%s] ${_C_RED}ERROR${_C_RESET} %s\n"  "$(date '+%H:%M:%S')" "$*" >&2; }
step()  { printf "\n[%s] ${_C_CYAN}===>${_C_RESET} %s\n" "$(date '+%H:%M:%S')" "$*"; }

# ── Prerequisite checks ───────────────────────────────────────────────────────
require_hcloud() {
    if ! command -v hcloud &>/dev/null; then
        error "hcloud CLI not found. Run infra/hetzner/setup.sh to install it."
        exit 1
    fi
}

require_token() {
    if [[ -z "${HCLOUD_TOKEN:-}" ]]; then
        error "HCLOUD_TOKEN is not set."
        error "Generate a Read+Write API token at:"
        error "  Hetzner Cloud Console → Project → Security → API Tokens"
        error "Then export it: export HCLOUD_TOKEN=<your-token>"
        exit 1
    fi
}

# ── Config loader ─────────────────────────────────────────────────────────────
# Usage: load_env <env-name>  (e.g. load_env int)
load_env() {
    local env_name="${1:-}"
    if [[ -z "$env_name" ]]; then
        error "Usage: $0 <env>  (e.g. $0 int  or  $0 uat)"
        exit 1
    fi

    local env_file
    env_file="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config/${env_name}.env"

    if [[ ! -f "$env_file" ]]; then
        error "Config file not found: $env_file"
        error "Available envs: $(ls "$(dirname "$env_file")"/*.env 2>/dev/null | xargs -n1 basename | sed 's/\.env//' | tr '\n' ' ')"
        exit 1
    fi

    # shellcheck source=/dev/null
    source "$env_file"

    # Validate required variables
    local required_vars=(SERVER_NAME VOLUME_NAME SERVER_TYPE LOCATION SSH_KEY_NAME MOUNT_POINT APP_DIR)
    for var in "${required_vars[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            error "Required variable $var is not set in $env_file"
            exit 1
        fi
    done

    log "Loaded config: $env_file (server=$SERVER_NAME, volume=$VOLUME_NAME, location=$LOCATION)"
}

# ── SSH helpers ───────────────────────────────────────────────────────────────
# Path to the SSH key used for Hetzner servers (override via SSH_KEY_PATH env)
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/hetzner_int_uat}"

ssh_cmd() {
    local ip="$1"
    shift
    ssh -i "$SSH_KEY_PATH" \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=5 \
        -o BatchMode=yes \
        root@"$ip" "$@"
}

# Wait for SSH to be available on <ip>. Times out after SSH_WAIT_TIMEOUT seconds.
wait_for_ssh() {
    local ip="$1"
    local timeout="${SSH_WAIT_TIMEOUT:-120}"
    local elapsed=0
    local interval=5

    step "Waiting for SSH on $ip (timeout=${timeout}s)"
    while (( elapsed < timeout )); do
        if ssh_cmd "$ip" true &>/dev/null; then
            log "SSH is up on $ip (after ${elapsed}s)"
            return 0
        fi
        printf "."
        sleep "$interval"
        elapsed=$(( elapsed + interval ))
    done
    printf "\n"
    error "SSH did not become available on $ip within ${timeout}s"
    return 1
}

# ── Hetzner helpers ───────────────────────────────────────────────────────────
server_exists() {
    local name="$1"
    hcloud server describe "$name" &>/dev/null
}

server_ip() {
    local name="$1"
    hcloud server describe "$name" -o json | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['public_net']['ipv4']['ip'])
"
}

volume_id() {
    local name="$1"
    hcloud volume describe "$name" -o json | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])"
}

volume_exists() {
    local name="$1"
    hcloud volume describe "$name" &>/dev/null
}

# Return the /dev/disk/by-id path for a Hetzner volume (uses volume numeric ID)
volume_dev_path() {
    local vol_id="$1"
    echo "/dev/disk/by-id/scsi-0HC_Volume_${vol_id}"
}

# ── Cost estimate helper ──────────────────────────────────────────────────────
print_cost_estimate() {
    local server_type="$1"
    local hours_running="$2"   # how long the server has been running, in hours (float ok)

    # Approximate hourly rates (EUR) — verify at hetzner.com/cloud/pricing
    local rate
    case "$server_type" in
        cx22) rate="0.00616" ;;
        cx32) rate="0.01233" ;;
        cx42) rate="0.02466" ;;
        *)    rate="0.01000" ;;   # unknown type, rough estimate
    esac

    local cost
    cost=$(python3 -c "print(f'{float(${hours_running}) * float(${rate}):.4f}')" 2>/dev/null || echo "?")
    log "Estimated cost avoided: ~€${cost} (${hours_running}h × €${rate}/h for ${server_type})"
    log "(Note: Hetzner bills per-second; this is an approximation)"
}
