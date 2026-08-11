#!/usr/bin/env bash
# Fetch a hepic_server release from Tencent COS (instead of `git clone`),
# verify its checksum, extract it, and run install.sh.
#
# Use this on devices that can't reach GitHub -- release.yml mirrors every
# tagged release to COS under hepic-server/releases/<tag>/, plus a
# hepic-server/latest.json pointer at the bucket root.
#
# Usage (run as the user the service should run under, e.g. pi -- NOT root):
#   COS_BASE_URL="https://<bucket>.cos.<region>.myqcloud.com" ./install_from_cos.sh
#   ./install_from_cos.sh --base-url https://cdn.example.com --version v0.3.0
#
# COS_BASE_URL can also be assembled from COS_BUCKET + COS_REGION (the same
# vars release.yml's coscmd config step uses), or passed with --base-url /
# --bucket + --region. If you front the bucket with a CDN, pass that domain
# as --base-url instead.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as the user the service should run under (e.g. pi), with sudo available -- not directly as root." >&2
  exit 1
fi

BASE_URL="https://hepic-server-1456772252.cos.ap-guangzhou.myqcloud.com"
BUCKET="${COS_BUCKET:-}"
REGION="${COS_REGION:-}"
VERSION=""
INSTALL_DIR="${HOME}/hepic_server"
RUN_INSTALL=1

usage() {
  cat <<EOF
Usage: $0 [--base-url URL] [--bucket BUCKET --region REGION] [--version TAG] [--dir DIR] [--no-run]

  --base-url URL   COS bucket or CDN domain, e.g. https://mybucket-1250000000.cos.ap-shanghai.myqcloud.com
  --bucket NAME    COS bucket name (used with --region if --base-url is not given)
  --region NAME    COS region, e.g. ap-shanghai
  --version TAG    Release tag to install, e.g. v0.3.0 (default: whatever latest.json points to)
  --dir DIR        Where to install (default: \$HOME/hepic_server)
  --no-run         Fetch and extract only; don't run install.sh afterwards
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --no-run) RUN_INSTALL=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [ -z "${BASE_URL}" ]; then
  if [ -n "${BUCKET}" ] && [ -n "${REGION}" ]; then
    BASE_URL="https://${BUCKET}.cos.${REGION}.myqcloud.com"
  else
    echo "Missing COS location: set --base-url (or COS_BASE_URL), or --bucket/--region (or COS_BUCKET/COS_REGION)." >&2
    exit 1
  fi
fi
BASE_URL="${BASE_URL%/}"

for cmd in curl tar sha256sum python3; do
  command -v "${cmd}" >/dev/null 2>&1 || { echo "Missing required command: ${cmd}" >&2; exit 1; }
done

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

if [ -z "${VERSION}" ]; then
  echo "==> [1/4] Resolving latest version from ${BASE_URL}/hepic-server/latest.json"
  curl -fsSL -o "${WORK_DIR}/latest.json" "${BASE_URL}/hepic-server/latest.json"
  VERSION="$(python3 -c 'import json; print(json.load(open("'"${WORK_DIR}"'/latest.json"))["tag"])')"
fi
echo "    Installing hepic_server ${VERSION}"

PREFIX="hepic-server/releases/${VERSION}"
SRC_ASSET="hepic-server-src-${VERSION}.tar.gz"
SUMS_ASSET="SHA256SUMS.txt"

echo "==> [2/4] Downloading release artifact and checksums"
curl -fsSL -o "${WORK_DIR}/${SRC_ASSET}" "${BASE_URL}/${PREFIX}/${SRC_ASSET}"
curl -fsSL -o "${WORK_DIR}/${SUMS_ASSET}" "${BASE_URL}/${PREFIX}/${SUMS_ASSET}"

echo "==> [3/4] Verifying checksum"
(cd "${WORK_DIR}" && sha256sum -c "${SUMS_ASSET}")

echo "==> [4/4] Extracting to ${INSTALL_DIR}"
tar -xzf "${WORK_DIR}/${SRC_ASSET}" -C "${WORK_DIR}"
EXTRACTED_DIR="${WORK_DIR}/hepic-server-${VERSION}"
if [ ! -d "${EXTRACTED_DIR}" ]; then
  echo "Expected extracted directory ${EXTRACTED_DIR} not found -- archive layout may have changed." >&2
  exit 1
fi

if [ -e "${INSTALL_DIR}" ]; then
  BACKUP_DIR="${INSTALL_DIR}.bak.$(date +%Y%m%d%H%M%S)"
  echo "    ${INSTALL_DIR} already exists -- moving it aside to ${BACKUP_DIR}"
  mv "${INSTALL_DIR}" "${BACKUP_DIR}"
fi
mkdir -p "$(dirname "${INSTALL_DIR}")"
mv "${EXTRACTED_DIR}" "${INSTALL_DIR}"
chmod +x "${INSTALL_DIR}/install.sh" "${INSTALL_DIR}/scripts/install_service.sh"

echo
echo "hepic_server ${VERSION} extracted to ${INSTALL_DIR}."

if [ "${RUN_INSTALL}" -eq 1 ]; then
  echo "==> Running install.sh"
  "${INSTALL_DIR}/install.sh"
else
  echo "Skipped install.sh (--no-run). Run it manually with: ${INSTALL_DIR}/install.sh"
fi
