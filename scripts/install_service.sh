#!/usr/bin/env bash
# Install and start the hepic_server systemd service on the Raspberry Pi.
#
# Run on the Pi as the user the service should run under, with sudo available:
#   scripts/install_service.sh
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as the user the service should run under, with sudo available -- not directly as root." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="hepic_server"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="$(whoami)"
RUN_GROUP="$(id -gn "${RUN_USER}")"
CONFIG_FILE="${REPO_ROOT}/config.json"

if [ ! -f "${CONFIG_FILE}" ]; then
  echo "Config file not found: ${CONFIG_FILE}. Create it before installing the service." >&2
  exit 1
fi

echo "==> Writing systemd unit file ${SERVICE_FILE}"
sudo tee "${SERVICE_FILE}" > /dev/null <<EOF
[Unit]
Description=HEPIC Server Data Acquisition Service
After=network.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${REPO_ROOT}
ExecStart=${REPO_ROOT}/.venv/bin/hepic_server ${CONFIG_FILE}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "==> Reloading systemd and enabling the service"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}.service"

echo "==> Status"
systemctl --no-pager status "${SERVICE_NAME}.service" || true

cat <<EOF

Done. Useful commands:
  systemctl status ${SERVICE_NAME}
  journalctl -u ${SERVICE_NAME} -f
  sudo systemctl restart ${SERVICE_NAME}
EOF
