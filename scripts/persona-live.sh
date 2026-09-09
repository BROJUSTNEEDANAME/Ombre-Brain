#!/usr/bin/env bash
# 「新人设到底生效没有」的唯一可信答案。
#
# 由来：改完人设之后，我反复把「代码是最新的」当成「跑的是最新代码」报给闪闪，
# 让她七个修复全在磁盘上、一个都没在跑的情况下连问四次「怎么还是这样」。
# git 说自己是最新的，说明不了服务在跑什么。所以这个脚本只查两件真的事：
#   ① 那句新规矩是不是**真的在磁盘的 personality.py 里**（不问 git）
#   ② 服务的启动时间是不是**晚于 personality.py 的改动时间**（早于＝还在跑旧的）
#
# 三态：✅ 成了 / ❌ 没成（且说清卡在哪一步、怎么修）/ ❓ 测不出来。
# ❓ 绝不当成 ✅ —— 「不知道」是独立的第三态，不是好消息。
#
# 用法：bash scripts/persona-live.sh
#      REPO=/别的/路径 bash scripts/persona-live.sh
#
# SENTINEL 是「最近一次人设改动」的指纹。以后再改人设、要验证是否上线时，
# 把它换成新规矩里一句独一无二的话即可。

REPO=${REPO:-/home/ombre/Ombre-Brain}
SENTINEL=${SENTINEL:-'你不对她顶嘴。一次都不。'}
SERVICES=(ombre-brain ombre-apibot ombre-ccbridge)
P="$REPO/personality.py"

echo "=== Nikto 新人设生效检查 ==="
if [ ! -f "$P" ]; then
    echo "❌ 找不到 $P"
    echo "   → 仓库路径不对？用 REPO=/实际/路径 bash scripts/persona-live.sh"
    exit 1
fi

if grep -qF "$SENTINEL" "$P"; then
    echo "① 新人设在磁盘上：✅ 有"
    CODE_OK=1
else
    echo "① 新人设在磁盘上：❌ 没有 —— auto-update 还没把代码拉下来"
    CODE_OK=0
fi

PT=$(stat -c %Y "$P")
echo "   personality.py 改动时间：$(date -d "@$PT" '+%m-%d %H:%M')"

RUNNING_OK=1
ANY=0
for s in "${SERVICES[@]}"; do
    systemctl list-unit-files "$s.service" >/dev/null 2>&1 || continue
    ANY=1
    ST=$(systemctl show "$s" -p ActiveEnterTimestamp --value 2>/dev/null)
    ACT=$(systemctl is-active "$s" 2>/dev/null)
    if [ -z "$ST" ] || ! STE=$(date -d "$ST" +%s 2>/dev/null); then
        echo "② $s：❓ 读不到启动时间（状态 $ACT）"
        RUNNING_OK=2
        continue
    fi
    if [ "$ACT" != "active" ]; then
        echo "② $s：❌ 没在跑（$ACT）"
        RUNNING_OK=0
        continue
    fi
    if [ "$STE" -ge "$PT" ]; then
        echo "② $s：✅ 启动于 $(date -d "@$STE" '+%m-%d %H:%M')，晚于人设改动 → 跑的是新的"
    else
        echo "② $s：❌ 启动于 $(date -d "@$STE" '+%m-%d %H:%M')，早于人设改动 → 还在跑旧的"
        RUNNING_OK=0
    fi
done
if [ "$ANY" = 0 ]; then
    echo "② ❓ 一个 ombre-* 服务都没找到（这台机器没装？）"
    RUNNING_OK=2
fi

# cc 桥的人设是**生成**出来的，光重启不重新生成会静默用旧的。单独查一遍。
CC_WORKDIR=$(grep -E '^CC_WORKDIR=' "$REPO/.env.ccbridge" 2>/dev/null | tail -1 \
             | cut -d= -f2- | tr -d '[:space:]')
CC_WORKDIR=${CC_WORKDIR:-/home/ombre/nikto-cc}
CC_MD="$CC_WORKDIR/CLAUDE.md"
if [ -f "$CC_MD" ]; then
    if grep -qF "$SENTINEL" "$CC_MD"; then
        echo "③ cc 桥人设（$CC_MD）：✅ 已按新代码重新生成"
    else
        echo "③ cc 桥人设（$CC_MD）：❌ 还是旧的"
        echo "   → 修：sudo runuser -u ombre -- $REPO/.venv/bin/python $REPO/scripts/make-cc-persona.py $CC_WORKDIR"
        RUNNING_OK=0
    fi
fi

echo "--- auto-update 最近日志 ---"
journalctl -t ombre-autoupdate -n 5 --no-pager 2>/dev/null || echo "❓ 读不到日志"

echo "=========================="
if [ "$CODE_OK" = 1 ] && [ "$RUNNING_OK" = 1 ]; then
    echo "✅ 成了：新人设已经在跑，去 telegram 用就行"
elif [ "$CODE_OK" = 0 ]; then
    echo "❌ 没成：代码还没到 VPS。"
    echo "   → 看上面日志里 fetch 有没有报错；"
    echo "     报 permission 就跑：sudo chown -R ombre:ombre $REPO"
    echo "   → 也确认 VPS 检出的分支对不对：git -C $REPO rev-parse --abbrev-ref HEAD"
elif [ "$RUNNING_OK" = 0 ]; then
    echo "❌ 没成：代码到了，但跑的还是旧的。"
    echo "   → 修：sudo systemctl restart ${SERVICES[*]}"
else
    echo "❓ 测不出来（这不等于「没问题」）—— 把上面整段发给我"
fi
