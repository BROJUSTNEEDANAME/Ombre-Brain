#!/bin/bash
# 每分钟把「服务器 → Telegram」这条路分段测一次，记一行。下次他又「不回、/status 也不回」，
# 翻记录就知道断在哪一段。
#
# 由来 2026-11-21：11/16、11/20 两次断了十几到几十分钟，日志里只有 TimedOut。桥走的是
# Cloudflare 中转（TELEGRAM_API_BASE），可分不清是 服务器→Cloudflare、Cloudflare→Telegram
# 还是服务器自己的网整体抖了——断的时候没人在测，事后测永远是好的。
#
# ⚠️ 那天我还犯了个错：拿 `grep '^TELEGRAM_API_BASE=https'` 查有没有走中转，设置里那行带引号，
# 没匹配上，我就跟她说「没走绕行」。这里读设置一律去掉引号/空格/尖括号再看。
#
# 用法：
#   sudo bash scripts/tg-probe.sh --install   装每分钟一次的定时（跑的是仓库里这份，不拷贝）
#   sudo bash scripts/tg-probe.sh --report    看最近出过问题的那几行
#   bash scripts/tg-probe.sh                  手动测一次
#
# 每行：时间(UTC) | 中转站 | 中转→Telegram | 直连Telegram | 对照(Google)
# 每格是「状态码/秒数」；连不上写「❓连不上」——不知道就是不知道，不折叠成别的。
# 机器人密码只拿来拼地址，绝不打印。
set -u
export LC_ALL=C
REPO=${REPO:-/home/ombre/Ombre-Brain}
ENV_FILE=${ENV_FILE:-$REPO/.env.ccbridge}
LOG=${LOG:-/var/log/ombre-tg-probe.log}

val() {   # 读设置：容忍 export 前缀、引号、空格、尖括号、Windows 换行
    grep -E "^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=" "$ENV_FILE" 2>/dev/null \
        | tail -1 | cut -d= -f2- | tr -d " \"'<>\r"
}

if [ "${1:-}" = "--install" ]; then
    SELF="$REPO/scripts/tg-probe.sh"
    echo "* * * * * root bash $SELF >> $LOG 2>&1" > /etc/cron.d/ombre-tg-probe
    chmod 644 /etc/cron.d/ombre-tg-probe
    printf '%s {\n  weekly\n  rotate 4\n  compress\n  missingok\n  notifempty\n}\n' "$LOG" \
        > /etc/logrotate.d/ombre-tg-probe
    echo "已装：每分钟测一次，记在 $LOG"
    echo "先手动测一次给你看："
    bash "$SELF"
    exit 0
fi

if [ "${1:-}" = "--report" ]; then
    [ -r "$LOG" ] || { echo "❓ 还没有记录（$LOG 不存在或读不了）"; exit 0; }
    total=$(wc -l < "$LOG")
    bad=$(grep -cE '❓|/([5-9]|[1-9][0-9])\.[0-9]s' "$LOG")
    echo "共 $total 行，其中出问题（连不上或超过 5 秒）的 $bad 行。最近的 30 行问题："
    grep -E '❓|/([5-9]|[1-9][0-9])\.[0-9]s' "$LOG" | tail -30
    exit 0
fi

probe() {   # 打印「状态码/秒数」或「❓连不上」
    local out code t
    out=$(curl -s -o /dev/null -m 20 -w '%{http_code} %{time_total}' "$1" 2>/dev/null)
    code=${out%% *}; t=${out##* }
    if [ -z "$out" ] || [ "$code" = "000" ]; then
        printf '❓连不上'
    else
        printf '%s/%.1fs' "$code" "$t"
    fi
}

BASE=$(val TELEGRAM_API_BASE); BASE=${BASE%/}
TOKEN=$(val TELEGRAM_BOT_TOKEN)

line="$(date -u '+%F %T')"
if [ -n "$BASE" ]; then
    line="$line | 中转站 $(probe "$BASE/")"
    if [ -n "$TOKEN" ]; then
        line="$line | 中转→TG $(probe "$BASE/bot$TOKEN/getMe")"
    else
        line="$line | 中转→TG 没token"
    fi
else
    line="$line | 中转站 没设 | 中转→TG 没设"
fi
line="$line | 直连TG $(probe "https://api.telegram.org/")"
line="$line | 对照 $(probe "https://www.google.com/generate_204")"
echo "$line"
