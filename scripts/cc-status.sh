#!/bin/bash
# cc 桥现在到底在跑什么版本、有没有带上最新的修复。
#   sudo bash scripts/cc-status.sh
#
# ⚠️ 由来：她两次看到早就修过的老现象（「（……）」上屏），而我们没有任何办法
# 分辨「代码没修好」和「修好了但没跑起来」。猜错方向就是白查一轮——
# 这两天已经白查过两轮了。
set -e
# REPO 可被环境变量覆盖——这样测试能真的把整个脚本跑一遍（见 tests/test_cc_status.py）
REPO=${REPO:-/home/ombre/Ombre-Brain}
cd "$REPO"

# ⚠️⚠️ 这个脚本唯一的规矩，来自 Cheiineeey/relay-cache-where-it-breaks 第 2 节：
#
#   「加任何判定之前，先问一句：**不知道的时候，它会说什么？**
#     如果答案是「跟坏消息一样」，那这个判定就是错的。」
#
# 今天这个脚本自己就犯了：git fetch 失败（不知道）它印的是「跟远端一致 ✅」，
# 她照着信了两轮，我也跟着往错方向查了两轮。
# 所以下面每一处判定都是**三态**：是 ✅ / 否 ❌ / **看不见 ❓**。
# 「看不见」永远单独一档，绝不许折叠进「否」。

# 读日志：区分「确实没有」和「我读不到」。前者是结论，后者什么结论都不是。
logs() {   # 用法：logs <journalctl 参数...>；读得到→打印并返回 0，读不到→返回 2
    local out rc
    out=$(journalctl "$@" --no-pager 2>&1); rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "❓ 读不到日志，这一段测不出任何结论：$(printf '%s' "$out" | head -1)"
        return 2
    fi
    printf '%s' "$out"
    return 0
}

# 服务状态：活着 / 死了 / 根本没装——三件事，三种处置
svc_state() {
    systemctl list-unit-files "$1.service" >/dev/null 2>&1 || { echo missing; return; }
    systemctl cat "$1.service" >/dev/null 2>&1 || { echo missing; return; }
    systemctl is-active --quiet "$1" && echo active || echo dead
}

echo "── 代码 ──"
runuser -u ombre -- git -C "$REPO" log --oneline -1
# ⚠️ 原来这里是 `fetch ... || true`：fetch 失败时它拿**过期的** origin 记录去比，
# 于是理直气壮地报「跟远端一致 ✅」——而机器其实落后 7 个提交。
# 她照着这句话信了两轮。fetch 失败就必须说失败，绝不许拿旧数据充数。
FETCH_ERR=$(runuser -u ombre -- git -C "$REPO" fetch origin --quiet 2>&1) && FETCH_OK=1 || FETCH_OK=0
B=$(runuser -u ombre -- git -C "$REPO" rev-parse --abbrev-ref HEAD)
L=$(runuser -u ombre -- git -C "$REPO" rev-parse HEAD)
if [ "$FETCH_OK" = 0 ]; then
    echo "❌ 连不上远端，下面这句「一致/落后」不作数：$FETCH_ERR"
    case "$FETCH_ERR" in
        *"insufficient permission"*|*"Permission denied"*|*"failed to write object"*)
            echo "   → 仓库里有不属于 ombre 的文件，自动更新一直在悄悄失败。"
            echo "   → 修：sudo chown -R ombre:ombre $REPO"
            ;;
    esac
else
    R=$(runuser -u ombre -- git -C "$REPO" rev-parse "origin/$B" 2>/dev/null || echo "$L")
    [ "$L" = "$R" ] && echo "跟远端一致 ✅" \
        || echo "⚠️ 落后远端 $(runuser -u ombre -- git -C "$REPO" rev-list --count "$L..$R" 2>/dev/null) 个提交，自动更新没拉下来"
fi
[ -f "$REPO/.autoupdate-blocked" ] && \
    echo "⚠️ 有坏提交被拉黑：$(cat "$REPO/.autoupdate-blocked" | cut -c1-8)"

echo ""
echo "── 自动更新定时器 ──"
# ⚠️ 她的机器卡在一个几小时前的提交上，而自动更新「应该」每 5 分钟拉一次。
# 定时器没开、或者每次都在报错，从聊天记录里完全看不出来——只会表现成
# 「怎么改了还是老样子」。所以直接把它的状态摆出来。
case "$(svc_state ombre-autoupdate.timer)" in
    active)  echo "定时器在跑 ✅（下次：$(systemctl show ombre-autoupdate.timer -p NextElapseUSecRealtime --value)）" ;;
    dead)    echo "❌ ombre-autoupdate.timer 没在跑——代码永远不会自己更新" ;;
    missing) echo "❓ 没装 ombre-autoupdate.timer（不是「没在跑」，是没装过）" ;;
esac
echo "最近几次自动更新说了什么："
if A=$(logs -t ombre-autoupdate --since "-3 hours"); then
    [ -n "$A" ] && printf '%s\n' "$A" | tail -5 || echo "（3 小时内它一次都没说过话——多半没被触发过）"
else
    printf '%s\n' "$A"
fi

echo ""
echo "── 服务 ──"
case "$(svc_state ombre-ccbridge)" in
    active)  echo "ombre-ccbridge 活着 ✅" ;;
    dead)    echo "❌ ombre-ccbridge 装了但没在跑——sudo systemctl start ombre-ccbridge" ;;
    missing) echo "❓ 根本没装 ombre-ccbridge 这个服务（不是「没在跑」，是没装过）" ;;
esac
echo "上次启动：$(systemctl show ombre-ccbridge -p ActiveEnterTimestamp --value)"
echo "（代码提交时间：$(runuser -u ombre -- git -C "$REPO" log -1 --format=%cd --date=format:'%a %Y-%m-%d %H:%M:%S %Z')）"
echo "⚠️ 服务启动时间要**晚于**代码提交时间，否则跑的还是旧代码。"

echo ""
echo "── 这份代码里有没有这些修复 ──"
check() {
    # 文件读不到就是「看不见」，不是「这份代码没有」——后者是个会让人换错方向的结论。
    if [ ! -r "$REPO/$3" ]; then echo "  ❓ $1（看不见 $3，不等于没有）"; return; fi
    grep -q -- "$2" "$REPO/$3" && echo "  ✅ $1" || echo "  ❌ $1（这份代码没有）"
}
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
    if [ ! -r "$W/CLAUDE.md" ]; then
        echo "❓ 文件在，但读不出来（权限？）——测不出内容对不对"
    elif grep -q "你是 Nikto" "$W/CLAUDE.md"; then echo "是他的人设 ✅"
    else echo "❌ 内容不对，重新生成"; fi
else
    echo "❌ 没有人设文件，他会用仓库那份给开发看的 CLAUDE.md"
fi

echo ""
echo "── 最近的空回复（他到底输出了什么）──"
# ⚠️ 这一处原来最恶劣：读不到日志时它照样印「6 小时内没有记到空回复」，
# 后面还跟一句「那说明跑的是**旧代码**」——一条测不出来的证据推出了一个确定的结论。
if ! RAW=$(logs -u ombre-ccbridge --since "-6 hours"); then
    printf '%s\n' "$RAW"
    J=""
    BLIND=1
else
    J=$(printf '%s' "$RAW" | grep -E "空回复|还是空|重试都用完" | tail -8)
    BLIND=0
fi
if [ -n "$J" ]; then
    printf '%s\n' "$J"
    echo ""
    echo "读法：新代码每次空回复会记「（第 N 次）」。"
    echo "  只看到「第 1 次」而没有「第 2 次」→ 重试成功了，她其实收到了话。"
    echo "  看到「重试都用完了」→ 三轮真的全空，那是模型的问题，不是这边吃了话。"
    echo "  一条都没有「第 N 次」字样 → 跑的是**旧代码**。"
elif [ "$BLIND" = 1 ]; then
    echo "⚠️ 日志读不到，所以「有没有空回复」这件事这次**测不出来**。"
    echo "   别拿这一段去下任何结论。"
else
    echo "（6 小时内确实没有记到空回复）"
    echo "⚠️ 要是她这段时间明明看到过「这次他没出声」，那说明跑的是**旧代码**"
    echo "   ——新代码遇到空回复一定会记一行日志。"
fi

echo ""
echo "── 每轮花了多久（判断有没有真的重试）──"
# ⚠️ 她给的截图里，她发消息和「这次他没出声」是**同一分钟**。
# 一轮 claude 要一分钟左右，三轮不可能在同一分钟内跑完——
# 所以那一定不是重试跑完之后的结果。时间戳是这里最硬的证据。
if B=$(logs -u ombre-ccbridge --since "-2 hours"); then
    printf '%s' "$B" | grep -E "空回复|重试都用完|claude 退出码|速率限制|API 错误|被信号掐断|返回空 result" \
        | tail -12 || echo "（2 小时内确实没有相关日志）"
else
    printf '%s\n' "$B"
fi

echo ""
echo "── 空 result 的原因（claude 自己在 JSON 里写了）──"
# 「原始输出＝''」只说明是空的，没说为什么。subtype=error_max_turns 就是
# 「这一轮全花在工具调用上、轮数用完了」，那是完全不同的病。
if C=$(logs -u ombre-ccbridge --since "-6 hours"); then
    printf '%s' "$C" | grep "返回空 result" | tail -5 \
        || echo "（没有这行——要么这段时间没空过，要么跑的还是没带这条日志的旧代码）"
else
    printf '%s\n' "$C"
fi
