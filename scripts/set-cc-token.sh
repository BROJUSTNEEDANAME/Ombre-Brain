#!/bin/bash
# 换 cc 桥的 CLAUDE_CODE_OAUTH_TOKEN，并当场验证它真的能用。
#   sudo bash scripts/set-cc-token.sh
#
# ⚠️ 为什么要有这个脚本：token 是从终端里复制出来的，中间极容易混进换行或空格。
# 混进去之后 systemd 只读到半截，服务起来了却一直 401——而她只会看到「他不理我」，
# 得一路查到 journalctl 才知道原因。她的原话：「我怎么知道他这次给我的有没有换行啊」。
# 所以这里不让她手抄进文件：粘进来，脚本负责洗干净、写进去、并且真打一次 API。

set -e
REPO_DIR="$(cd "$(dirname "$(dirname "$0")")" && pwd)"
ENV_FILE="$REPO_DIR/.env.ccbridge"
[ -f "$ENV_FILE" ] || { echo "!! 没有 $ENV_FILE，先跑 setup-ccbridge.sh"; exit 1; }

echo "把 claude setup-token 给你的那串粘在这里，回车："
read -r RAW
# 空格、制表、换行、以及粘贴时常混进来的不换行空格，全部抹掉
TOKEN="$(printf '%s' "$RAW" | tr -d '[:space:]' | sed 's/\xc2\xa0//g')"

if [ ${#TOKEN} -lt 20 ]; then
    echo "!! 只收到 ${#TOKEN} 个字符，太短了，八成没粘全。重来一次。"; exit 1
fi
echo "洗干净后长度：${#TOKEN}（原始 ${#RAW}）"
if [ ${#TOKEN} -ne ${#RAW} ]; then
    echo "   （粘进来的里面有 $(( ${#RAW} - ${#TOKEN} )) 个空白字符，已经去掉——"
    echo "     这正是上次那个「莫名其妙换行报错」）"
fi

# 先验证再写：写坏一个能用的配置比不写更糟
echo ""
echo "拿这个 token 真打一次 claude（不是猜，是真的调）..."
OUT="$(sudo -u ombre env HOME=/home/ombre CLAUDE_CODE_OAUTH_TOKEN="$TOKEN" \
        timeout 90 claude -p --output-format json "只回两个字：在。" 2>&1 || true)"
if printf '%s' "$OUT" | grep -qiE '401|expired|authenticate|invalid'; then
    echo "❌ 这个 token 不能用。原文："
    printf '%s\n' "$OUT" | head -5
    echo "（没有改动 $ENV_FILE，旧配置原样保留）"
    exit 1
fi
echo "✅ 能用。"

TMP="$(mktemp)"
grep -v '^CLAUDE_CODE_OAUTH_TOKEN=' "$ENV_FILE" > "$TMP"
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$TOKEN" >> "$TMP"
cat "$TMP" > "$ENV_FILE"; rm -f "$TMP"
chown ombre:ombre "$ENV_FILE"; chmod 600 "$ENV_FILE"
echo "✅ 已写进 $ENV_FILE"

systemctl restart ombre-ccbridge
sleep 3
if systemctl is-active --quiet ombre-ccbridge; then
    echo "✅ 服务起来了。去跟他说句话。"
else
    echo "❌ 服务没起来："
    journalctl -u ombre-ccbridge -n 20 --no-pager || true
    exit 1
fi
