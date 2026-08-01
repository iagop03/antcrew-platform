#!/usr/bin/env bash
# setup.sh — One-time prerequisite setup for the INT/UAT ephemeral server infrastructure.
# Installs hcloud CLI, validates HCLOUD_TOKEN, and creates + registers the SSH key.
# Safe to re-run (idempotent).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

SSH_KEY_FILE="${SSH_KEY_PATH:-$HOME/.ssh/hetzner_int_uat}"
SSH_KEY_NAME="int-uat-key"

step "AntCrew ephemeral infra — prerequisite setup"

# ── 1. hcloud CLI ─────────────────────────────────────────────────────────────
step "1/4 — Checking hcloud CLI"
if command -v hcloud &>/dev/null; then
    log "hcloud already installed: $(hcloud version)"
else
    log "Installing hcloud CLI..."
    OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64)  ARCH="amd64" ;;
        aarch64) ARCH="arm64" ;;
        armv7l)  ARCH="arm" ;;
        *)
            error "Unsupported architecture: $ARCH"
            error "Download manually from: https://github.com/hetznercloud/cli/releases"
            exit 1
            ;;
    esac

    LATEST="$(curl -sfL "https://api.github.com/repos/hetznercloud/cli/releases/latest" \
              | grep '"tag_name"' | cut -d'"' -f4)"
    URL="https://github.com/hetznercloud/cli/releases/download/${LATEST}/hcloud-${OS}-${ARCH}.tar.gz"
    log "Downloading $URL"
    curl -sfL "$URL" -o /tmp/hcloud.tar.gz
    tar -xzf /tmp/hcloud.tar.gz -C /tmp hcloud
    # Prefer user-local install (no sudo needed); fall back to system-wide
    INSTALL_DIR="$HOME/.local/bin"
    mkdir -p "$INSTALL_DIR"
    mv /tmp/hcloud "$INSTALL_DIR/hcloud"
    chmod +x "$INSTALL_DIR/hcloud"
    rm -f /tmp/hcloud.tar.gz

    # Ensure PATH contains the install dir (idempotent)
    for RC in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [[ -f "$RC" ]] && ! grep -q 'HOME/.local/bin' "$RC"; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$RC"
            log "Added ~/.local/bin to PATH in $RC"
        fi
    done
    export PATH="$INSTALL_DIR:$PATH"
    log "Installed: $(hcloud version)"
fi

# ── 2. API token ─────────────────────────────────────────────────────────────
step "2/4 — Checking HCLOUD_TOKEN"
if [[ -z "${HCLOUD_TOKEN:-}" ]]; then
    error "HCLOUD_TOKEN is not set."
    printf "\n"
    printf "  1. Open: https://console.hetzner.cloud\n"
    printf "  2. Select your project → Security → API Tokens\n"
    printf "  3. Generate a new token with Read & Write permissions\n"
    printf "  4. Export it: export HCLOUD_TOKEN=<your-token>\n"
    printf "  5. Re-run this script\n"
    printf "\n"
    printf "  IMPORTANT: Never commit the token to git.\n"
    printf "  Add it to your shell profile (e.g. ~/.bashrc) or use a secrets manager.\n"
    exit 1
fi
log "HCLOUD_TOKEN is set."

# Quick connectivity check
if ! hcloud server list &>/dev/null; then
    error "hcloud API call failed. Is HCLOUD_TOKEN valid and pointing to the right project?"
    exit 1
fi
log "Hetzner API connectivity: OK"

# ── 3. SSH key ────────────────────────────────────────────────────────────────
step "3/4 — Setting up SSH key ($SSH_KEY_FILE)"
if [[ ! -f "$SSH_KEY_FILE" ]]; then
    log "Generating new ed25519 key at $SSH_KEY_FILE..."
    ssh-keygen -t ed25519 -f "$SSH_KEY_FILE" -N "" -C "antcrew-hetzner-int-uat"
    log "Key generated."
else
    log "SSH key already exists: $SSH_KEY_FILE"
fi

# Register key in Hetzner project (idempotent)
if hcloud ssh-key describe "$SSH_KEY_NAME" &>/dev/null; then
    log "SSH key '$SSH_KEY_NAME' already registered in Hetzner project."
else
    log "Registering SSH key '$SSH_KEY_NAME' in Hetzner project..."
    hcloud ssh-key create \
        --name                "$SSH_KEY_NAME" \
        --public-key-from-file "${SSH_KEY_FILE}.pub"
    log "SSH key registered."
fi

# ── 4. Create volumes if missing ──────────────────────────────────────────────
step "4/4 — Checking Hetzner volumes"

for ENV_NAME in int uat; do
    ENV_FILE="$SCRIPT_DIR/config/${ENV_NAME}.env"
    if [[ ! -f "$ENV_FILE" ]]; then
        warn "Config not found: $ENV_FILE — skipping volume check for $ENV_NAME"
        continue
    fi

    (
        # shellcheck source=/dev/null
        source "$ENV_FILE"
        if hcloud volume describe "$VOLUME_NAME" &>/dev/null; then
            log "[$ENV_NAME] Volume '$VOLUME_NAME' already exists."
        else
            log "[$ENV_NAME] Creating volume '$VOLUME_NAME' (${VOLUME_SIZE}GB, $LOCATION)..."
            hcloud volume create \
                --name     "$VOLUME_NAME" \
                --size     "${VOLUME_SIZE:-10}" \
                --location "$LOCATION" \
                --format   ext4
            log "[$ENV_NAME] Volume '$VOLUME_NAME' created and formatted."
            warn "[$ENV_NAME] NEXT STEP: mount the volume, move persistent data to it,"
            warn "          then unmount and create the base snapshot."
            warn "          See infra/hetzner/README.md → 'Initial setup' section."
        fi
    )
done

# ── Summary ───────────────────────────────────────────────────────────────────
step "Setup complete"
printf "\n"
printf "  hcloud      : %s\n" "$(hcloud version)"
printf "  SSH key     : %s (name: %s)\n" "$SSH_KEY_FILE" "$SSH_KEY_NAME"
printf "  Project     : %s\n" "$(hcloud context active 2>/dev/null || echo '<default>')"
printf "\n"
printf "  Next steps:\n"
printf "   1. Fill in SNAPSHOT_ID in config/int.env and config/uat.env\n"
printf "      (see README.md → 'Initial setup' for the base server + snapshot procedure)\n"
printf "   2. Run: ./up.sh int\n"
printf "   3. Run: ./down.sh int\n"
printf "\n"
