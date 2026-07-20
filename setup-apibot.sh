#!/bin/bash
# 一键部署 API Telegram Bot 的 systemd 服务
# 在 VPS 的 Ombre-Brain 仓库目录里跑：sudo bash setup-apibot.sh

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$REPO_DIR/.env.apibot"
SERVICE_FILE="/etc/systemd/system/ombre-apibot.service"
PYTHON="$REPO_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "!! 找不到主仓库虚拟环境：$PYTHON"
    exit 1
fi

echo "=== Ombre API Bot 部署 ==="
echo "仓库路径: $REPO_DIR"
echo "Python:   $PYTHON"

# 创建环境变量文件（如果不存在）
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'ENVEOF'
# API Bot 的 Telegram token（和 cc_bridge 的不同的那个 bot）
TELEGRAM_API_BOT_TOKEN=在这里填你的API bot token

# GLM API key
LLM_API_KEY=在这里填你的key

# 你的 Telegram chat ID
ALLOWED_CHAT_IDS=在这里填你的chat id

# 大脑地址：指向本机的 brain server
OMBRE_MCP_URL=http://127.0.0.1:8000/mcp

# 保留现有 GLM 模型
OMBRE_BOT_MODEL=glm-5.1

# 时区
OMBRE_BOT_TZ=America/Los_Angeles
ENVEOF
    echo ""
    echo "!! 已创建 $ENV_FILE"
    echo "!! 请先编辑它，填入你的 token 和 key，然后重新运行本脚本"
    echo "!! 命令：nano $ENV_FILE"
    echo ""
    exit 1
fi

# 检查环境变量文件有没有填
if grep -q "在这里填" "$ENV_FILE"; then
    echo ""
    echo "!! .env.apibot 里还有没填的变量！"
    echo "!! 请先编辑：nano $ENV_FILE"
    echo ""
    exit 1
fi
if ! grep -Eq '^ALLOWED_CHAT_IDS=[0-9]+(,[0-9]+)*$' "$ENV_FILE"; then
    echo "!! ALLOWED_CHAT_IDS 必须保留现有 Telegram 用户白名单，拒绝开放启动"
    exit 1
fi
chown ombre:ombre "$ENV_FILE"
chmod 600 "$ENV_FILE"

# 安装依赖
echo "安装 Python 依赖..."
"$PYTHON" -m pip install -q -r "$REPO_DIR/requirements-telegram.txt"

# 写 systemd service
cat > "$SERVICE_FILE" << SVCEOF
[Unit]
Description=Ombre Brain API Telegram Bot
After=network-online.target ombre-brain.service
Wants=network-online.target
Requires=ombre-brain.service

[Service]
Type=simple
User=ombre
Group=ombre
WorkingDirectory=$REPO_DIR
EnvironmentFile=$ENV_FILE
Environment=OMBRE_BUCKETS_DIR=$REPO_DIR/buckets
ExecStart=$PYTHON $REPO_DIR/telegram_bot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

# 启动
systemctl daemon-reload
systemctl enable ombre-apibot
systemctl restart ombre-apibot

echo ""
echo "=== 完成！==="
echo "查看状态：sudo systemctl status ombre-apibot"
echo "查看日志：sudo journalctl -u ombre-apibot -f"
echo ""

sleep 2
systemctl status ombre-apibot --no-pager || true
