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
ETC_DIR="/etc/hepic_server"
CONFIG_FILE="${ETC_DIR}/config.json"

# Live config lives in /etc/hepic_server so it survives `git pull` / reinstall.
# Keep it if it already exists; otherwise seed it from the repo's defaults.
echo "==> Ensuring config exists at ${ETC_DIR}"
sudo mkdir -p "${ETC_DIR}"
for name in config.json sensors_config.yaml; do
  if [ -f "${ETC_DIR}/${name}" ]; then
    echo "  ${ETC_DIR}/${name} already exists -- keeping it."
  elif [ -f "${REPO_ROOT}/${name}" ]; then
    echo "  ${ETC_DIR}/${name} not found -- initializing from ${REPO_ROOT}/${name}."
    sudo cp "${REPO_ROOT}/${name}" "${ETC_DIR}/${name}"
  else
    echo "Neither ${ETC_DIR}/${name} nor ${REPO_ROOT}/${name} exist -- create one before continuing." >&2
    exit 1
  fi
done
sudo chown -R "${RUN_USER}:${RUN_GROUP}" "${ETC_DIR}"

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
