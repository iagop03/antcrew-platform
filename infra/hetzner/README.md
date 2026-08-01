# AntCrew — Ephemeral Hetzner servers for INT + UAT

Servers are created on demand from a snapshot and destroyed when not needed.
Persistent data lives on a **detached Volume** (never deleted automatically).
Database lives on **Neon PostgreSQL** (unaffected by server lifecycle).

## Cost estimate (Mon-Fri, 9:00–19:00 UTC)

| Item | Unit price | Usage | INT/month | UAT/month | Combined |
|------|-----------|-------|-----------|-----------|----------|
| CX22 server | €0.00616/hr | 220 hr/mo (10h × 22 days) | €1.36 | €1.36 | **€2.72** |
| Volume 10 GB | €0.0584/GB/mo | always-on | €0.58 | €0.58 | **€1.17** |
| Snapshot ~5 GB | €0.01/GB/mo | 1 each | €0.05 | €0.05 | **€0.10** |
| **Total** | | | **€1.99** | **€1.99** | **€3.98** |

**Previous cost (24/7 servers): ~€8.49/mo × 2 = €16.98/mo**
**Savings: ~€13/month (~76% reduction)**

> Prices based on Hetzner public pricing (nbg1, CX22). Verify at https://www.hetzner.com/cloud/pricing.
> Hetzner bills per-second — actual cost scales linearly with real usage hours.

## File structure

```
infra/hetzner/
  setup.sh             # One-time prerequisite setup (run first)
  up.sh                # Create server + attach volume + start app
  down.sh              # Stop app + detach volume + delete server
  snapshot-refresh.sh  # Regenerate base snapshot after software updates
  cron-setup.sh        # Install cron entries for auto 9–19h schedule (WSL)
  common.sh            # Shared functions — sourced by other scripts, not run directly
  config/
    int.env            # INT environment variables
    uat.env            # UAT environment variables
```

## Prerequisites

- **WSL2 (Ubuntu 24.04)** on Windows — all scripts run inside WSL
- **HCLOUD_TOKEN** — Hetzner Cloud API token with Read + Write (never commit this)
- **hcloud CLI** — installed by `setup.sh`
- **SSH key** — generated and registered by `setup.sh`

## Quick reference

```bash
# All commands run from inside WSL:

# First-time setup (installs hcloud, creates SSH key, creates volumes)
export HCLOUD_TOKEN=<your-token>
./infra/hetzner/setup.sh

# Daily usage
./infra/hetzner/up.sh int      # bring INT up
./infra/hetzner/up.sh uat      # bring UAT up
./infra/hetzner/down.sh int    # shut INT down
./infra/hetzner/down.sh uat    # shut UAT down

# Auto-schedule (Mon-Fri 09:00–19:00 UTC)
./infra/hetzner/cron-setup.sh
./infra/hetzner/cron-setup.sh --remove  # uninstall

# After software updates on the base server
./infra/hetzner/snapshot-refresh.sh int
```

## Initial setup (one-time, ~20 minutes per environment)

Do this once for INT, then repeat for UAT:

### 1. Run prerequisites
```bash
export HCLOUD_TOKEN=<your-token>
./infra/hetzner/setup.sh
```
This installs hcloud, generates `~/.ssh/hetzner_int_uat`, registers it in Hetzner,
and creates the `int-data` and `uat-data` volumes.

### 2. Create the base server (NOT from snapshot — base install)
```bash
hcloud server create \
  --name antcrew-int-base \
  --type cx22 \
  --image ubuntu-24.04 \
  --location nbg1 \
  --ssh-key int-uat-key
```
Note the IP: `hcloud server describe antcrew-int-base -o json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['public_net']['ipv4']['ip'])"`

### 3. Install and configure the application
```bash
ssh -i ~/.ssh/hetzner_int_uat root@<IP>

# Inside the server:
apt-get update && apt-get install -y docker.io docker-compose-plugin

# Mount the data volume
mkdir -p /mnt/antcrew-data
VOLUME_ID=$(hcloud volume describe int-data -o json | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
# Attach volume first: hcloud volume attach --server antcrew-int-base int-data
mount -o discard,defaults /dev/disk/by-id/scsi-0HC_Volume_${VOLUME_ID} /mnt/antcrew-data
echo "/dev/disk/by-id/scsi-0HC_Volume_${VOLUME_ID} /mnt/antcrew-data ext4 discard,defaults,nofail 0 0" >> /etc/fstab

# Deploy application
mkdir -p /opt/antcrew
cd /opt/antcrew
# ... copy docker-compose.yml, set env vars (DATABASE_URL from Neon, etc.) ...
docker compose up -d
```

### 4. Verify the application works

SSH in and confirm Docker services are healthy:
```bash
ssh -i ~/.ssh/hetzner_int_uat root@<IP>
docker compose ps
curl http://localhost:8000/health
```

### 5. Create the snapshot
```bash
# From your local WSL shell:
hcloud volume detach int-data           # detach before snapshot for consistency
hcloud server create-image \
  --type snapshot \
  --description int-base-v1 \
  --server antcrew-int-base

hcloud image list --type snapshot       # note the ID of the new snapshot
```

### 6. Update config and delete base server
```bash
# Set the snapshot ID in config/int.env:
# SNAPSHOT_ID=<id from previous step>

hcloud server delete antcrew-int-base   # snapshot is self-contained; server no longer needed
```

### 7. Test the full cycle
```bash
./infra/hetzner/up.sh int
# Verify: ssh in, check docker compose ps, check /health endpoint
./infra/hetzner/down.sh int
# Verify: hcloud server list (no antcrew-int), hcloud volume list (int-data present)
```

Repeat steps 2–7 for UAT (`antcrew-uat-base`, `uat-data`, `uat.env`).

## Updating the base snapshot

When you install new packages, update Docker images, or change the base config:

```bash
./infra/hetzner/up.sh int               # bring the server up
# ... make your changes via SSH ...
./infra/hetzner/snapshot-refresh.sh int # creates new snapshot, updates config/int.env
./infra/hetzner/down.sh int             # destroy the server; next up.sh uses new snapshot
```

## Windows shell aliases (optional convenience)

Add to your WSL `~/.bashrc` or `~/.zshrc`:
```bash
INFRA="$HOME/path/to/antcrew-platform/infra/hetzner"
alias int-up="$INFRA/up.sh int"
alias int-down="$INFRA/down.sh int"
alias uat-up="$INFRA/up.sh uat"
alias uat-down="$INFRA/down.sh uat"
```

## Security notes

- `HCLOUD_TOKEN` is **never** stored in this repo; read from environment only.
- The token is stored in `~/.config/antcrew-infra/credentials` (mode 600) for cron use.
- SSH private key: `~/.ssh/hetzner_int_uat` (mode 600, never committed).
- `config/*.env` files contain no secrets — safe to commit.
- The Volume is **never** deleted by any script. Only `hcloud volume delete` (manual) removes it.
- `down.sh` prints a full audit log of what it deletes before doing so.

## Troubleshooting

| Problem | Resolution |
|---------|-----------|
| SSH times out after server creation | Increase `SSH_WAIT_TIMEOUT` in the env file |
| `mount: can't find /dev/disk/by-id/scsi-0HC_Volume_*` | Volume not yet visible — add `sleep 5` before mount in `up.sh` |
| Server created but volume not attached | Run `hcloud volume attach --server <name> <vol-name>` manually |
| Cron not running | `sudo service cron start` in WSL; add `[boot] command = service cron start` to `/etc/wsl.conf` |
| App not starting | SSH in and run `docker compose logs` from `/opt/antcrew` |
