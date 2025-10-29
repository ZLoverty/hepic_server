#!/bin/bash

# ==============================================================================
# PiServer 安装脚本
# 
# 这个脚本会：
# 1. 检查是否以 root (sudo) 权限运行
# 2. 安装 Python 依赖 (numpy)
# 3. 创建应用目录 (/opt/piserver) 和配置目录 (/etc/piserver)
# 4. 复制 Python 脚本到 /opt/piserver/
# 5. 创建一个默认的 config.json 到 /etc/piserver/
# 6. 创建一个 systemd 服务文件
# 7. 重新加载 systemd、启用并启动服务
# ==============================================================================

# --- 1. 定义变量 ---
# 你的应用程序的名称
APP_NAME="hepic_server"

# 源文件 (在 git 仓库中)
SCRIPT_SOURCE="hepic_server.py"

# 目标路径
INSTALL_DIR="/opt/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
VENV_DIR="${INSTALL_DIR}/venv" # Venv 目录

# 目标文件名
SCRIPT_DEST="${INSTALL_DIR}/${SCRIPT_SOURCE}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

# 获取运行 sudo 的用户名 (例如 'pi')，如果失败则默认为 'pi'
RUN_USER="${SUDO_USER:-pi}"
RUN_GROUP=$(id -gn "${RUN_USER}")

# 自动查找 Python 3 的路径
PYTHON_PATH=$(which python3)

# --- 2. 权限检查 ---
set -e  # 如果任何命令失败，立即退出
if [ "$EUID" -ne 0 ]; then
  echo "❌ 错误：请使用 sudo 运行此脚本 (e.g., 'sudo ./install.sh')"
  exit 1
fi

echo "🚀 PiServer 安装程序正在运行..."
echo "    将以用户: ${RUN_USER} (组: ${RUN_GROUP}) 身份运行服务"

# --- 3. 创建目录 ---
echo "📁 正在创建目录..."
mkdir -p "${INSTALL_DIR}"
mkdir -p "${CONFIG_DIR}"
echo "   - ${INSTALL_DIR}"
echo "   - ${CONFIG_DIR}"

# --- 4. 复制应用程序文件 ---
echo "🐍 正在复制 Python 脚本到 ${SCRIPT_DEST}..."
cp "${SCRIPT_SOURCE}" "${SCRIPT_DEST}"
# 确保 Python 脚本可以被执行 (虽然我们是通过 python3 调用的，但这是个好习惯)
chmod +x "${SCRIPT_DEST}"

# --- 5. 创建默认配置文件 ---
echo "📝 正在创建默认配置文件 ${CONFIG_FILE}..."

# 使用 "cat << EOL" 来写入多行文本
# 注意：我们检查文件是否已存在，如果存在则不覆盖，以防用户升级
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

# --- 6. 创建 systemd 服务文件 ---
echo "⚙️  正在创建 systemd 服务文件 ${SERVICE_FILE}..."
cat > "${SERVICE_FILE}" << EOL
[Unit]
Description=PiServer Data Acquisition Service
After=network.target

[Service]
# 你的 Python 脚本的启动命令
# 它将自动传递配置文件路径作为参数
ExecStart=${VENV_DIR}/bin/python ${SCRIPT_DEST} ${CONFIG_FILE}

# 以 pi 用户身份运行 (安全性更高)
User=${RUN_USER}
Group=${RUN_GROUP}

# 在哪里运行
WorkingDirectory=${INSTALL_DIR}

# 失败时自动重启
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOL

# --- 7. 创建 Python 虚拟环境 ---
echo "🐍 正在 ${VENV_DIR} 创建 Python 虚拟环境..."
${PYTHON_PATH} -m venv "${VENV_DIR}"

# --- 8. 在 Venv 中安装依赖 ---
echo "📦 正在虚拟环境中安装依赖 (numpy)..."
# 使用 Venv 内部的 pip
"${VENV_DIR}/bin/pip" install numpy
# 如果有其他依赖，在这里添加，例如: "${VENV_DIR}/bin/pip" install other-package

# --- 9. 启用并启动服务 ---
echo "🔄 正在重新加载 systemd 并启动服务..."
systemctl daemon-reload       # 告诉 systemd 扫描新文件
systemctl enable "${APP_NAME}.service" # 设置为开机自启
systemctl start "${APP_NAME}.service"  # 立即启动服务

# --- 9. 完成 ---
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