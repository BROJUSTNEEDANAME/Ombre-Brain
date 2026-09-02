#!/usr/bin/env bash
# 把 Telegram 的 /model claude 档位打开：装依赖 → 存 key → 真调一次 API 验证 → 重启。
#
#   bash scripts/enable-claude.sh
#
# key 是交互输入的，不会出现在命令行历史里，也不会打印到屏幕上。
# ⚠️ 顺序是刻意的：**先验证再重启**。key 配错了却先重启，
# 换来的是她在 Telegram 上发现他哑了——绝不能这样。

set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

ENVFILE="$REPO/.env.apibot"
PY="$REPO/.venv/bin/python"
PIP="$REPO/.venv/bin/pip"

say()  { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
ok()   { printf '  ✅ %s\n' "$*"; }
warn() { printf '  ⚠️  %s\n' "$*"; }
die()  { printf '\n  ❌ %s\n\n' "$*"; exit 1; }

[ -x "$PY" ] || die "找不到虚拟环境 $PY —— 你是不是不在 /home/ombre/Ombre-Brain 里？"
[ -f "$ENVFILE" ] || die "找不到 $ENVFILE —— Telegram bot 的配置文件应该在这儿。"
[ -w "$ENVFILE" ] || die "$ENVFILE 你没有写权限。先 exit 回到 # 跑：
       chown ombre:ombre $ENVFILE
     再 su - ombre 回来重跑。"

# ---------------------------------------------------------------- 1. 依赖
say "检查 anthropic 官方 SDK"
if "$PY" -c "import anthropic" 2>/dev/null; then
    ok "已经装了（$("$PY" -c 'import anthropic;print(anthropic.__version__)' 2>/dev/null)）"
else
    echo "  没装，正在装…"
    "$PIP" install -q "anthropic>=0.49.0" || die "装不上。把上面的报错发我。"
    "$PY" -c "import anthropic" 2>/dev/null || die "装完还是 import 不了，把上面的报错发我。"
    ok "装好了"
fi

# ---------------------------------------------------------------- 2+3. key & 验证
# 存 key 和验证 key 必须是一个循环：验证不过就得能当场重输。
# 否则第二次跑会撞上「已经配过了，这次不动它」，她被自己那个错 key 锁死。
ask_key() {
    echo "  去 https://console.anthropic.com/settings/keys 拿一个，或者从 Render 的"
    echo "  ombre-brain → Environment → ANTHROPIC_API_KEY 里复制（服务暂停了也能看）。"
    printf '  粘贴 key 然后回车（输入不会显示在屏幕上）：'
    KEY=""
    if { : < /dev/tty; } 2>/dev/null; then read -rs KEY < /dev/tty; else read -rs KEY; fi
    echo
    KEY="$(printf '%s' "$KEY" | tr -d '[:space:]')"
    [ -n "$KEY" ] || return 1
    case "$KEY" in
        sk-ant-*) : ;;
        *) warn "这串不是 sk-ant- 开头的，可能不是 Anthropic 的 key。存下来后会真的去验证。" ;;
    esac
    # 先删旧行再追加：同名变量堆两份很容易看糊涂
    sed -i '/^OMBRE_ANTHROPIC_KEY=/d' "$ENVFILE"
    printf '\nOMBRE_ANTHROPIC_KEY=%s\n' "$KEY" >> "$ENVFILE"
    chmod 600 "$ENVFILE"
    ok "写进 $ENVFILE 了"
}

verify_key() {
    set -a
    # shellcheck disable=SC1090
    . "$ENVFILE"
    set +a
    "$PY" "$REPO/scripts/_verify_claude_key.py" 2>&1
}

say "Anthropic API key"
if grep -q '^OMBRE_ANTHROPIC_KEY=.\+' "$ENVFILE"; then
    ok "已经配过了，先拿它去验证"
else
    ask_key || die "什么都没输入，没改任何东西。想好了再跑一次。"
fi

# 顺序是刻意的：**先验证再重启**。key 错了却先重启，
# 换来的是她在 Telegram 上发现他哑了——绝不能这样。
say "真的调一次 Claude，确认这个 key 能用（几秒，几乎不花钱）"
PASSED=0
for _try in 1 2 3; do
    VERIFY="$(verify_key)"
    case "$VERIFY" in
        OK\|*) ok "Claude 回话了：${VERIFY#OK|}"; PASSED=1; break ;;
    esac
    printf '  ❌ %s\n' "${VERIFY#FAIL|}"
    echo "     （常见原因：key 复制少了字符 / 余额不足 / key 被停用）"
    printf '  要重新输一个 key 再试吗？[y/N] '
    AGAIN=""
    if { : < /dev/tty; } 2>/dev/null; then read -r AGAIN < /dev/tty; else read -r AGAIN; fi
    case "$AGAIN" in
        y|Y|yes|YES) ask_key || { warn "没输入，放弃。"; break; } ;;
        *) break ;;
    esac
done
if [ "$PASSED" != "1" ]; then
    echo
    echo "  key 没通过验证，所以我不重启 bot —— 他现在还在用 GLM，照常能聊。"
    echo "  想手改：nano $ENVFILE 改 OMBRE_ANTHROPIC_KEY 那行，再跑一次本脚本。"
    exit 1
fi

# ---------------------------------------------------------------- 4. 重启
say "重启 Telegram bot"
if [ "$(id -u)" = "0" ]; then R="systemctl"; elif sudo -n true 2>/dev/null; then R="sudo systemctl"; else R=""; fi
if [ -n "$R" ]; then
    $R restart ombre-apibot && ok "重启完成" || die "重启失败，把上面的报错发我"
else
    die "这一步要 root。先 exit 回到 # 跑：
       systemctl restart ombre-apibot
     然后就能用了，不用再跑本脚本。"
fi

printf '\n\033[1m好了。去 Telegram 打 /model claude 就切过去了。\033[0m\n'
printf '  /model claude   —— 关思考，快\n'
printf '  /model claudet  —— 开思考，复杂的更稳\n'
printf '  /model 5.3      —— 随时切回 GLM\n'
printf '切模型不清上下文，这一屏聊的他都还记得，记忆库也是同一个。\n\n'
