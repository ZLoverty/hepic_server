#!/usr/bin/env bash
# One-stop installer for the Raspberry Pi. Run once after `git clone`, as the
# user the service should run under (e.g. pi), NOT as root:
#
#   ./install.sh
#
# Steps:
#   1. install the python3-venv system package (needs sudo)
#   2. create the venv in-repo and pip install hepic_server into it
#   3. install the systemd unit and start the service (needs sudo)
#
# Each privileged step calls `sudo` on its own -- the script itself never
# re-execs as root, so it always runs with your normal user permissions.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as the user the service should run under (e.g. pi), with sudo available -- not directly as root." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"

echo "==> [1/3] Installing system dependency: python3-venv"
sudo apt-get update
sudo apt-get install -y --no-install-recommends python3-venv

echo "==> [2/3] Creating Python venv and installing hepic_server"
if [ ! -d "${REPO_ROOT}/.venv" ]; then
  python3 -m venv "${REPO_ROOT}/.venv"
fi
"${REPO_ROOT}/.venv/bin/pip" install --index-url "${PIP_INDEX_URL}" --upgrade pip
"${REPO_ROOT}/.venv/bin/pip" install --index-url "${PIP_INDEX_URL}" -e "${REPO_ROOT}"

echo "==> [3/3] Installing and starting the systemd service"
"${REPO_ROOT}/scripts/install_service.sh"

echo
echo "Install complete."
