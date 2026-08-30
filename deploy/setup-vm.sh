#!/usr/bin/env bash
# One-time bootstrap for a fresh Ubuntu VM (e.g. an Oracle Cloud Always Free
# Ampere A1 instance): installs Docker, opens only the dashboard port on the
# OS firewall, and clones the repo. Run this ON THE VM, over SSH, as a user
# with sudo access:
#
#   curl -fsSL https://raw.githubusercontent.com/yobage/OpenSkyOpenMeteo/master/deploy/setup-vm.sh | bash
#
# or copy it up and run `bash setup-vm.sh` after cloning yourself.
#
# What this does NOT do (must be done separately, see README):
#   - Create the Oracle Cloud account / VM itself
#   - Open port 8501 in the cloud provider's own Security List / NSG
#     (the OS firewall alone is not enough on Oracle Cloud)
#   - Copy your .env file onto the VM (it holds secrets and is gitignored,
#     so it never goes through git) — scp it up separately
set -euo pipefail

REPO_URL="https://github.com/yobage/OpenSkyOpenMeteo.git"
REPO_DIR="$HOME/OpenSkyOpenMeteo"

echo "==> Installing Docker Engine + Compose plugin"
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"

echo "==> Configuring the OS firewall (ufw): allow SSH + the dashboard port only"
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow OpenSSH
    sudo ufw allow 8501/tcp comment 'flighthub dashboard'
    sudo ufw --force enable
else
    echo "ufw not found; skipping OS firewall setup (configure manually if needed)"
fi

if [ -d "$REPO_DIR" ]; then
    echo "==> $REPO_DIR already exists, pulling latest instead of cloning"
    git -C "$REPO_DIR" pull
else
    echo "==> Cloning $REPO_URL"
    git clone "$REPO_URL" "$REPO_DIR"
fi

cat <<EOF

==> Bootstrap done. Remaining manual steps:

1. Log out and back in (or run 'newgrp docker') so your user picks up
   docker-group membership without needing sudo.

2. In the Oracle Cloud console: open your instance's VCN -> Security Lists
   (or the attached Network Security Group) and add an Ingress Rule:
     source CIDR: 0.0.0.0/0, destination port: 8501, protocol: TCP
   The ufw rule above is necessary but not sufficient on Oracle Cloud --
   traffic is also filtered at the cloud network layer.

3. From your own machine, copy your local .env up (it has real API keys,
   never commit it):
     scp .env <user>@<vm-public-ip>:$REPO_DIR/.env

4. On the VM:
     cd $REPO_DIR
     docker compose up -d --build

5. Visit http://<vm-public-ip>:8501 and sign in with your DASHBOARD_PASSWORD.

EOF
