#!/usr/bin/env bash
# One-time setup of the API on a fresh Debian 12 VM. Run it ON the VM, after the project files
# (without .venv, node_modules or .cache) are in ~/app and the catalogue is in ~/app/.cache/catalogue.
#
#   bash ~/app/scripts/deploy_vm.sh api.example.org      # or 34-82-0-1.sslip.io
#
# It installs Python, a systemd service for the server (restarts on crash and at boot) and Caddy,
# which gets a free HTTPS certificate for the name you give it. Re-running is safe.
set -euo pipefail

DOMAIN="${1:?usage: deploy_vm.sh <domain, e.g. 34-82-0-1.sslip.io>}"
APP="$HOME/app"
[ -d "$APP/backend" ] || { echo "no $APP/backend: copy the project to ~/app first"; exit 1; }
[ -d "$APP/.cache/catalogue" ] || { echo "no $APP/.cache/catalogue: copy the catalogue first"; exit 1; }

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip caddy

cd "$APP"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

sudo tee /etc/systemd/system/tombaugh-api.service >/dev/null <<EOF
[Unit]
Description=Tombaugh API
After=network-online.target

[Service]
User=$USER
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python -m backend.server --source catalogue --catalogue $APP/.cache/catalogue --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# flush_interval -1: the frame stream must reach the browser as it is produced
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$DOMAIN {
	reverse_proxy 127.0.0.1:8000 {
		flush_interval -1
	}
}
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now tombaugh-api
sudo systemctl restart caddy
sleep 5
curl -s http://127.0.0.1:8000/health && echo
echo "Public check (certificate can take a minute): curl https://$DOMAIN/health"
