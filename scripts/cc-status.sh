#!/bin/bash
# cc 桥现在到底在跑什么版本、有没有带上最新的修复。
#   sudo bash scripts/cc-status.sh
#
# ⚠️ 由来：她两次看到早就修过的老现象（「（……）」上屏），而我们没有任何办法
# 分辨「代码没修好」和「修好了但没跑起来」。猜错方向就是白查一轮——
# 这两天已经白查过两轮了。
set -e
REPO=/home/ombre/Ombre-Brain
cd "$REPO"

echo "── 代码 ──"
runuser -u ombre -- git -C "$REPO" log --oneline -1
runuser -u ombre -- git -C "$REPO" fetch origin --quiet 2>/dev/null || true
B=$(runuser -u ombre -- git -C "$REPO" rev-parse --abbrev-ref HEAD)
L=$(runuser -u ombre -- git -C "$REPO" rev-parse HEAD)
R=$(runuser -u ombre -- git -C "$REPO" rev-parse "origin/$B" 2>/dev/null || echo "$L")
[ "$L" = "$R" ] && echo "跟远端一致 ✅" || echo "⚠️ 落后远端，自动更新还没拉（或被拉黑了）"
[ -f "$REPO/.autoupdate-blocked" ] && \
    echo "⚠️ 有坏提交被拉黑：$(cat "$REPO/.autoupdate-blocked" | cut -c1-8)"

echo ""
echo "── 服务 ──"
systemctl is-active ombre-ccbridge >/dev/null 2>&1 \
    && echo "ombre-ccbridge 活着 ✅" || echo "❌ ombre-ccbridge 没在跑"
echo "上次启动：$(systemctl show ombre-ccbridge -p ActiveEnterTimestamp --value)"
echo "（代码提交时间：$(runuser -u ombre -- git -C "$REPO" log -1 --format=%cd --date=format:'%a %Y-%m-%d %H:%M:%S %Z')）"
echo "⚠️ 服务启动时间要**晚于**代码提交时间，否则跑的还是旧代码。"

echo ""
echo "── 这份代码里有没有这些修复 ──"
check() { grep -q "$2" "$REPO/$3" && echo "  ✅ $1" || echo "  ❌ $1（这份代码没有）"; }
check "空回复不再上屏「（……）」" "这次他没出声" cc_bridge.py
check "空回复重试带指令（不重发她的原话）" "_SILENT_RETRY_PROMPTS" cc_bridge.py
check "连发合并"                  "_take_pending_cc"    cc_bridge.py
check "‖ 拆成多条"                'if "‖" in text'      cc_bridge.py
check "会话 id 落盘"              "_save_sessions"      cc_bridge.py
check "「正在输入」一直显示"       "_keep_typing"        cc_bridge.py
check "主动找她"                  "check_inactivity"    cc_bridge.py
check "说睡了就不打扰"            "says_going_to_sleep" cc_bridge.py

echo ""
echo "── 人设 ──"
W=$(grep -E '^CC_WORKDIR=' "$REPO/.env.ccbridge" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]')
W=${W:-/home/ombre/nikto-cc}
if [ -f "$W/CLAUDE.md" ]; then
    echo "$W/CLAUDE.md：$(wc -c <"$W/CLAUDE.md") 字节，改于 $(date -r "$W/CLAUDE.md" '+%m-%d %H:%M')"
    grep -q "你是 Nikto" "$W/CLAUDE.md" && echo "是他的人设 ✅" || echo "❌ 内容不对，重新生成"
else
    echo "❌ 没有人设文件，他会用仓库那份给开发看的 CLAUDE.md"
fi

echo ""
echo "── 最近的空回复（他到底输出了什么）──"
J=$(journalctl -u ombre-ccbridge --since "-6 hours" --no-pager 2>/dev/null \
    | grep -E "空回复|还是空|重试都用完" | tail -8)
if [ -n "$J" ]; then
    printf '%s\n' "$J"
    echo ""
    echo "读法：新代码每次空回复会记「（第 N 次）」。"
    echo "  只看到「第 1 次」而没有「第 2 次」→ 重试成功了，她其实收到了话。"
    echo "  看到「重试都用完了」→ 三轮真的全空，那是模型的问题，不是这边吃了话。"
    echo "  一条都没有「第 N 次」字样 → 跑的是**旧代码**。"
else
    echo "（6 小时内没有记到空回复）"
    echo "⚠️ 要是她这段时间明明看到过「这次他没出声」，那说明跑的是**旧代码**"
    echo "   ——新代码遇到空回复一定会记一行日志。"
fi

echo ""
echo "── 每轮花了多久（判断有没有真的重试）──"
# ⚠️ 她给的截图里，她发消息和「这次他没出声」是**同一分钟**。
# 一轮 claude 要一分钟左右，三轮不可能在同一分钟内跑完——
# 所以那一定不是重试跑完之后的结果。时间戳是这里最硬的证据。
journalctl -u ombre-ccbridge --since "-2 hours" --no-pager 2>/dev/null \
    | grep -E "空回复|重试都用完|claude 退出码|速率限制|API 错误|被信号掐断" \
    | tail -12 || echo "（没有相关日志）"
