#!/bin/bash

# ==============================================================================
# HEPIC Server 安装脚本 (使用 Python Venv) - 多文件版
#
# 这个脚本会：
# 1. 检查是否以 root (sudo) 权限运行
# 2. 安装 systemd 依赖 (python3-venv)
# 3. 创建应用目录 (/opt/hepic_server) 和配置目录 (/etc/hepic_server)
# 4. 复制当前目录下所有 .py 脚本到应用目录
# 5. 创建默认 config.json
# 6. 创建 venv 并使用 TUNA 镜像安装依赖
# 7. 修正文件权限
# 8. 创建、启用并启动 systemd 服务
# ==============================================================================

# --- 1. 定义变量 ---
APP_NAME="hepic_server"

# 注意：这里定义的是【主程序】的文件名，Systemd 将运行这个文件
# 即使复制了多个文件，我们也需要知道哪一个是入口
SCRIPT_ENTRY="hepic_server.py" 

INSTALL_DIR="/opt/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
VENV_DIR="${INSTALL_DIR}/venv"

# 主程序的完整路径 (用于 Systemd)
SCRIPT_DEST="${INSTALL_DIR}/${SCRIPT_ENTRY}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
SRC="hepic_server"
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

# --- 3. 安装系统依赖 ---
echo "📦 正在安装系统依赖 (python3-venv)..."
apt-get update
apt-get install -y python3-venv

# --- 4. 创建目录 ---
echo "📁 正在创建目录..."
mkdir -p "${INSTALL_DIR}"
mkdir -p "${CONFIG_DIR}"
echo "   - ${INSTALL_DIR}"
echo "   - ${CONFIG_DIR}"

# --- 5. 复制应用程序文件 (修改处) ---
# echo "🐍 正在复制所有 .py 脚本到 ${INSTALL_DIR}..."

# 检查是否存在 .py 文件
if find "${SRC}" -type f -name "*.py" | grep -q .; then
    find "${SRC}" -type f -name "*.py" -exec cp --parents {} "${INSTALL_DIR}/" \;
    find "${INSTALL_DIR}/${SRC}" -type f -name "*.py" -exec chmod +x {} \;
    echo "   -> 已复制所有 Python 文件。"
else
    echo "❌ 错误：当前目录下没有找到 .py 文件！"
    exit 1
fi

# --- 6. 创建默认配置文件 ---
echo "📝 正在创建默认配置文件 ${CONFIG_FILE}..."

if [ ! -f "${CONFIG_FILE}" ]; then
  cp "config.json" "${CONFIG_FILE}" 
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
# 指向我们在变量中定义的 SCRIPT_ENTRY
ExecStart=${VENV_DIR}/bin/hepic_server ${CONFIG_FILE}
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
echo "📦 正在虚拟环境中安装依赖 ... (使用 TUNA 镜像)"
"${VENV_DIR}/bin/pip" install -i https://pypi.tuna.tsinghua.edu.cn/simple -e .
# 如果你有其他依赖（比如 pyserial, requests），请在下面添加:
# "${VENV_DIR}/bin/pip" install -i https://pypi.tuna.tsinghua.edu.cn/simple pyserial requests

# --- 10. 修正文件权限 ---
echo "🔐 正在设置 ${RUN_USER} 对 ${INSTALL_DIR} 的所有权..."
chown -R "${RUN_USER}:${RUN_GROUP}" "${INSTALL_DIR}"

# --- 11. 启用并启动服务 ---
echo "🔄 正在重新加载 systemd 并启动服务..."
systemctl daemon-reload
systemctl enable "${APP_NAME}.service"
systemctl restart "${APP_NAME}.service"

# --- 12. 完成 ---
echo ""
echo "✅ 安装完成!"
echo "-------------------------------------------------------"
echo "  服务已启动。"
echo "  主入口脚本: ${SCRIPT_DEST}"
echo ""
echo "  查看日志:"
echo "  journalctl -u ${APP_NAME}.service -f"
echo "-------------------------------------------------------"
