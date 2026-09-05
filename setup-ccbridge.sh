#!/bin/bash
# 一键部署 cc 桥（走 Claude 订阅，不烧 API）
# 在 VPS 的 Ombre-Brain 仓库目录里跑：sudo bash setup-ccbridge.sh
#
# ⚠️ 它和 API bot 必须用**两个不同的** Telegram bot——同一个 token 同时被两个
#    程序收消息，Telegram 会互相抢，表现是「有时回有时不回」，极难查。
#    这里会主动比对两个 env 文件里的 token，一样就拒绝启动。

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$REPO_DIR/.env.ccbridge"
API_ENV="$REPO_DIR/.env.apibot"
SERVICE_FILE="/etc/systemd/system/ombre-ccbridge.service"
PERSONA_DIR="/home/ombre/nikto-cc"
PYTHON="$REPO_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "!! 找不到虚拟环境：$PYTHON"; exit 1
fi
# ⚠️ 必须用**服务真正的运行用户**去找 claude，不能用当前这个 root。
# 第一版只查了 root 有没有 claude——root 有、ombre 没有，脚本照样说装好了，
# 然后她那边就是「发了没回复」，日志里一句 FileNotFoundError 谁也不会去看。
# systemd 的 PATH 也极窄（不含 ~/.local/bin、nvm、npm 全局目录），
# 所以这里把找到的真实路径写死进 unit 的 PATH。
CLAUDE_BIN="$(sudo -u ombre bash -lc 'command -v claude' 2>/dev/null || true)"
if [ -z "$CLAUDE_BIN" ]; then
    echo "!! ombre 用户找不到 claude 命令（root 有不算数——服务是用 ombre 跑的）。"
    echo "!! 自己确认一下：  sudo -u ombre bash -lc 'command -v claude'"
    echo "!! 装到全局再来，例如：  npm i -g @anthropic-ai/claude-code"
    exit 1
fi
CLAUDE_DIR="$(dirname "$CLAUDE_BIN")"
echo "claude:   $CLAUDE_BIN"

if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'ENVEOF'
# cc 桥用的 Telegram bot token（⚠️ 必须和 .env.apibot 里那个是不同的 bot）
TELEGRAM_BOT_TOKEN=在这里填第二个bot的token

# 你的 Telegram chat ID（和 API bot 那边一样）
ALLOWED_CHAT_IDS=在这里填你的chat id

# 在你**登录了订阅的电脑**上跑 `claude setup-token`，把输出贴进来
CLAUDE_CODE_OAUTH_TOKEN=在这里填setup-token的输出

# 模型。Opus 4.6 走订阅额度；想省额度可以填 claude-sonnet-4-6
CC_MODEL=claude-opus-4-6

# 单条最长等多久
CC_TIMEOUT=300

# 时区
OMBRE_BOT_TZ=America/Los_Angeles
ENVEOF
    chown ombre:ombre "$ENV_FILE"; chmod 600 "$ENV_FILE"
    echo ""
    echo "!! 已创建 $ENV_FILE，先填好再重新跑本脚本"
    echo "!! 命令：nano $ENV_FILE"
    exit 1
fi

if grep -q "在这里填" "$ENV_FILE"; then
    echo "!! $ENV_FILE 里还有没填的，先编辑：nano $ENV_FILE"; exit 1
fi
# ⚠️ 这道检查只该拦住「空的／没填的」白名单，不该拦住合法写法。
# 第一版要求整行严格等于纯数字，于是这三种全被误杀：等号后带空格、
# 值加了引号、群组的负数 chat id（-100... 开头）。她填对了却被拒，
# 而报错只说「必须填成纯数字」，看不出是哪儿不对。
_IDS="$(grep -E '^[[:space:]]*ALLOWED_CHAT_IDS=' "$ENV_FILE" | head -1 | cut -d= -f2- \
        | tr -d '"'"'"' \r' | xargs 2>/dev/null || true)"
if ! printf '%s' "$_IDS" | grep -Eq '^-?[0-9]+(,-?[0-9]+)*$'; then
    echo "!! ALLOWED_CHAT_IDS 读出来是：「$_IDS」"
    echo "!! 它必须是你的 Telegram 数字 chat id，多个用逗号隔开（群组是负数，也行）。"
    echo "!! 空着或写别的会让这个 bot 对所有人开放，所以拒绝启动。"
    echo "!! 编辑：nano $ENV_FILE"
    exit 1
fi

# ⚠️ 两个 bot 共用一个 token 是最难查的那类故障：两个程序轮流抢同一条消息，
# 她那边看到的是「有时候回有时候不回」，日志两边都正常。这里当场拦掉。
if [ -f "$API_ENV" ]; then
    A=$(grep -E '^TELEGRAM_API_BOT_TOKEN=' "$API_ENV" | cut -d= -f2- | tr -d ' \r')
    B=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d ' \r')
    if [ -n "$A" ] && [ "$A" = "$B" ]; then
        echo "!! 这两个服务填的是同一个 bot token。"
        echo "!! 同一个 token 不能同时被两个程序收消息——必须用两个不同的 bot。"
        exit 1
    fi
fi

chown ombre:ombre "$ENV_FILE"; chmod 600 "$ENV_FILE"

# 人设目录：cc 会读那儿的 CLAUDE.md。不做这一步，他加载的是仓库里给开发看的
# CLAUDE.md，她面对的就是个编程助理，不是他。
echo "生成人设目录 $PERSONA_DIR ..."
sudo -u ombre "$PYTHON" "$REPO_DIR/scripts/make-cc-persona.py" "$PERSONA_DIR"

"$PYTHON" -m pip install -q -r "$REPO_DIR/requirements-telegram.txt"

cat > "$SERVICE_FILE" << SVCEOF
[Unit]
Description=Ombre Nikto cc bridge (Claude subscription)
After=network-online.target ombre-brain.service
Wants=network-online.target

[Service]
Type=simple
User=ombre
Group=ombre
WorkingDirectory=$REPO_DIR
EnvironmentFile=$ENV_FILE
Environment=CC_WORKDIR=$PERSONA_DIR
Environment=PATH=$CLAUDE_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=HOME=/home/ombre
Environment=OMBRE_BUCKETS_DIR=$REPO_DIR/buckets
ExecStart=$PYTHON $REPO_DIR/cc_bridge.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable ombre-ccbridge
systemctl restart ombre-ccbridge
sleep 3

# ⚠️ 绝不在服务其实崩着的时候说「装好了」。上一版无脑打这句，她看到满屏
# status=1/FAILURE 底下跟着一个绿勾——那种自相矛盾比不报还糟，
# 因为她会以为是自己看错了。这类假捷报我已经给过她一次（backfill 那回）。
if systemctl is-active --quiet ombre-ccbridge; then
    echo ""
    echo "✅ 起来了。看日志：journalctl -u ombre-ccbridge -f"
    echo "   改人设后要重新生成：python3 scripts/make-cc-persona.py $PERSONA_DIR"
else
    echo ""
    echo "❌ 服务装上了，但没起来。原因在这儿："
    echo "---------------------------------------------"
    journalctl -u ombre-ccbridge -n 25 --no-pager || true
    echo "---------------------------------------------"
    echo "把上面这段发出来就能定位。"
    exit 1
fi
