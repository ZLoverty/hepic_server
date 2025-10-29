#!/bin/bash

# ==============================================================================
# HEPIC Server 安装脚本 (使用 Python Venv)
#
# 这个脚本会：
# 1. 检查是否以 root (sudo) 权限运行
# 2. 安装 systemd 依赖 (python3-venv)
# 3. 创建应用目录 (/opt/hepic_server) 和配置目录 (/etc/hepic_server)
# 4. 复制 Python 脚本
# 5. 创建默认 config.json
# 6. 创建 venv 并使用 TUNA 镜像安装依赖
# 7. 修正文件权限
# 8. 创建、启用并启动 systemd 服务
# ==============================================================================

# --- 1. 定义变量 ---
APP_NAME="hepic_server"
SCRIPT_SOURCE="hepic_server.py"

INSTALL_DIR="/opt/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
VENV_DIR="${INSTALL_DIR}/venv"

SCRIPT_DEST="${INSTALL_DIR}/${SCRIPT_SOURCE}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

RUN_USER="${SUDO_USER:-pi}"
RUN_GROUP=$(id -gn "${RUN_USER}")

PYTHON_PATH=$(which python3)

# --- 2. 权限检查 ---
set -e  # 如果任何命令失败，立即退出
if [ "$EUID" -ne 0 ]; then
  echo "❌ 错误：请使用 sudo 运行此脚本 (e.g., 'sudo ./install.sh')"
  exit 1
fi

echo "🚀 HEPIC Server 安装程序正在运行..."
echo "    将以用户: ${RUN_USER} (组: ${RUN_GROUP}) 身份运行服务"

# --- 3. 安装系统依赖 (修复 Bug 2) ---
echo "📦 正在安装系统依赖 (python3-venv)..."
apt-get update
apt-get install -y python3-venv

# --- 4. 创建目录 ---
echo "📁 正在创建目录..."
mkdir -p "${INSTALL_DIR}"
mkdir -p "${CONFIG_DIR}"
echo "   - ${INSTALL_DIR}"
echo "   - ${CONFIG_DIR}"

# --- 5. 复制应用程序文件 ---
echo "🐍 正在复制 Python 脚本到 ${SCRIPT_DEST}..."
cp "${SCRIPT_SOURCE}" "${SCRIPT_DEST}"
chmod +x "${SCRIPT_DEST}"

# --- 6. 创建默认配置文件 ---
echo "📝 正在创建默认配置文件 ${CONFIG_FILE}..."

if [ ! -f "${CONFIG_FILE}" ]; then
  cat > "${CONFIG_FILE}" << EOL
{
    "host": "0.0.0.0",
    "port": 10001,
    "send_delay": 0.01,
    "log_level": "INFO",
    "mettler_ip": "192.168.0.8" 
}
EOL
  echo "   -> 默认配置已创建。请稍后编辑此文件！"
else
  echo "   -> 配置文件 ${CONFIG_FILE} 已存在，跳过创建。"
fi

# --- 7. 创建 systemd 服务文件 ---
echo "⚙️  正在创建 systemd 服务文件 ${SERVICE_FILE}..."
cat > "${SERVICE_FILE}" << EOL
[Unit]
Description=HEPIC Server Data Acquisition Service
After=network.target

[Service]
ExecStart=${VENV_DIR}/bin/python ${SCRIPT_DEST} ${CONFIG_FILE}
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${INSTALL_DIR}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOL

# --- 8. 创建 Python 虚拟环境 ---
echo "🐍 正在 ${VENV_DIR} 创建 Python 虚拟环境..."
${PYTHON_PATH} -m venv "${VENV_DIR}"

# --- 9. 在 Venv 中安装依赖 (使用 TUNA 镜像) ---
echo "📦 正在虚拟环境中安装依赖 (numpy)... (使用 TUNA 镜像)"
# 使用 Venv 内部的 pip, 并指定 TUNA 镜像
"${VENV_DIR}/bin/pip" install -i https://pypi.tuna.tsinghua.edu.cn/simple numpy
# 如果有其他依赖，在这里添加，例如: 
# "${VENV_DIR}/bin/pip" install -i https://pypi.tuna.tsinghua.edu.cn/simple other-package

# --- 10. 修正文件权限 (修复 Bug 1) ---
echo "🔐 正在设置 ${RUN_USER} 对 ${INSTALL_DIR} 的所有权..."
# 这是必须的，以便 ${RUN_USER} 可以执行 venv 并读取脚本
chown -R "${RUN_USER}:${RUN_GROUP}" "${INSTALL_DIR}"

# --- 11. 启用并启动服务 ---
echo "🔄 正在重新加载 systemd 并启动服务..."
systemctl daemon-reload       # 告诉 systemd 扫描新文件
systemctl enable "${APP_NAME}.service" # 设置为开机自启
systemctl start "${APP_NAME}.service"  # 立即启动服务

# --- 12. 完成 ---
echo ""
echo "✅ 安装完成!"
echo "-------------------------------------------------------"
echo "  服务已启动并设置为开机自启。"
echo ""
echo "  重要: 请用你的实际 IP 地址编辑配置文件:"
echo "  sudo nano ${CONFIG_FILE}"
echo ""
echo "  编辑后，使用此命令重启服务:"
echo "  sudo systemctl restart ${APP_NAME}.service"
echo ""
echo "  查看服务状态:"
echo "  systemctl status ${APP_NAME}.service"
echo ""
echo "  实时查看日志 (推荐):"
echo "  journalctl -u ${APP_NAME}.service -f"
echo "-------------------------------------------------------"