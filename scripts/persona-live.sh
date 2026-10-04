#!/usr/bin/env bash
# 「新人设到底生效没有」的唯一可信答案。
#
# 由来：改完人设之后，我反复把「代码是最新的」当成「跑的是最新代码」报给闪闪，
# 让她七个修复全在磁盘上、一个都没在跑的情况下连问四次「怎么还是这样」。
#
# 2026-10-03 第二次被戳破：这个脚本以前靠一句**手写的哨兵**（某次很早的改动里的一句话）
# 判「新人设在磁盘上」。没人去换那句话，它就永远 ✅——她拿着「✅成了：去 telegram 用就行」
# 去用，而我那天的改动根本不在机器上（推错了分支）。一个假的 ✅ 比没有更坏。
# 所以现在不认哨兵，只认三件能查实的事：
#   ① 磁盘上的提交 == 这台机器所跟踪分支的远端最新（git 说的，不是我说的）
#   ② 每个服务的启动时间晚于 personality.py 的改动时间（早于＝还在跑旧的）
#   ③ cc 桥那份 nikto-cc/CLAUDE.md 与「按磁盘上的 personality.py 现在生成」逐字一致
# ✅ 只在三条全真时打，并把在跑的提交号和标题印出来——让她看到「有的是哪一个」。
# 三态：✅ 成了 / ❌ 没成（且说清卡在哪一步、怎么修）/ ❓ 测不出来。
# ❓ 绝不当成 ✅ —— 「不知道」是独立的第三态，不是好消息。
#
# 用法：bash scripts/persona-live.sh
#   REPO=/别的/路径            仓库位置（默认 /home/ombre/Ombre-Brain）
#   CC_WORKDIR=/路径           cc 桥目录（默认读 .env.ccbridge，再默认 /home/ombre/nikto-cc）
#   PERSONA_LIVE_NO_FETCH=1    不联网 fetch，只跟本地已知的 origin/<分支> 比（会标 ❓）
#   SENTINEL='一句独一无二的话' 可选的附加检查：这句必须出现在 personality.py 里

REPO=${REPO:-/home/ombre/Ombre-Brain}
SERVICES=(ombre-brain ombre-apibot ombre-ccbridge)
P="$REPO/personality.py"

echo "=== Nikto 新人设生效检查 ==="
if [ ! -f "$P" ]; then
    echo "❌ 找不到 $P"
    echo "   → 仓库路径不对？用 REPO=/实际/路径 bash scripts/persona-live.sh"
    exit 1
fi

# git 要以仓库属主的身份跑：root 直接 git 会撞 "dubious ownership"，auto-update 也是这么做的
OWNER=$(stat -c %U "$REPO" 2>/dev/null || echo "")
if [ -n "$OWNER" ] && [ "$(id -un)" != "$OWNER" ] && command -v runuser >/dev/null 2>&1; then
    AS_OWNER=1
    g() { runuser -u "$OWNER" -- git -C "$REPO" "$@"; }
else
    AS_OWNER=0
    g() { git -C "$REPO" "$@"; }
fi
# fetch 单独写：timeout 包不了 bash 函数。第一版用 bash -c "$(declare -f g)" 绕，
# 子 shell 里没有 $OWNER，于是 runuser -u "" → 「user  does not exist」，① 永远 ❓。
# 她第一次跑就撞上了。命令展开写，不玩花活。
fetch_branch() {
    if [ "$AS_OWNER" = 1 ]; then
        timeout 25 runuser -u "$OWNER" -- git -C "$REPO" fetch origin "$1" --quiet
    else
        timeout 25 git -C "$REPO" fetch origin "$1" --quiet
    fi
}

# ---------- ① 代码：磁盘上的提交是不是远端最新 ----------
CODE_OK=2   # 0 没到 / 1 到了 / 2 测不出来
if ! BRANCH=$(g rev-parse --abbrev-ref HEAD 2>/dev/null) || [ -z "$BRANCH" ]; then
    echo "① 代码：❓ $REPO 不是 git 仓库（或 git 读不了），测不出来磁盘上是哪一版"
else
    HEAD_LINE=$(g log -1 --format='%h %s' 2>/dev/null)
    echo "   分支：$BRANCH"
    echo "   磁盘上的提交：$HEAD_LINE"
    FETCHED=1
    if [ -z "${PERSONA_LIVE_NO_FETCH:-}" ]; then
        if ! FERR=$(fetch_branch "$BRANCH" 2>&1); then
            FETCHED=0
            echo "   ❓ fetch 失败（${FERR:0:120}）——下面只能跟本地上次拉到的远端比"
        fi
    else
        FETCHED=0
        echo "   ❓ 没 fetch（PERSONA_LIVE_NO_FETCH=1）——下面只跟本地上次拉到的远端比"
    fi
    if ! REMOTE=$(g rev-parse "origin/$BRANCH" 2>/dev/null); then
        echo "① 代码：❓ 本地没有 origin/$BRANCH 这个远端引用，比不了"
    else
        LOCAL=$(g rev-parse HEAD)
        if [ "$LOCAL" = "$REMOTE" ]; then
            if [ "$FETCHED" = 1 ]; then
                echo "① 代码：✅ 磁盘上的提交就是 origin/$BRANCH 的最新"
                CODE_OK=1
            else
                echo "① 代码：❓ 跟本地已知的远端一致，但没 fetch，远端可能已经更新了"
            fi
        else
            COUNTS=$(g rev-list --left-right --count "origin/$BRANCH...HEAD" 2>/dev/null || echo "? ?")
            BEHIND=${COUNTS%%[[:space:]]*}; AHEAD=${COUNTS##*[[:space:]]}
            echo "① 代码：❌ 磁盘上不是远端最新（落后 $BEHIND 个提交，本地多 $AHEAD 个）"
            echo "   远端最新：$(g log -1 --format='%h %s' "origin/$BRANCH" 2>/dev/null)"
            echo "   → auto-update 没拉下来。看：journalctl -t ombre-autoupdate -n 20 --no-pager"
            echo "   → 定时器活着没：systemctl list-timers --all --no-pager | grep ombre"
            echo "   → 也确认你推的是这个分支（$BRANCH），不是别的分支"
            CODE_OK=0
        fi
    fi
fi
if [ -n "${SENTINEL:-}" ]; then
    if grep -qF "$SENTINEL" "$P"; then
        echo "   附加检查：✅ 「$SENTINEL」在 personality.py 里"
    else
        echo "   附加检查：❌ 「$SENTINEL」不在 personality.py 里 —— 这版代码没带这句"
        CODE_OK=0
    fi
fi

PT=$(stat -c %Y "$P")
echo "   personality.py 改动时间：$(date -d "@$PT" '+%m-%d %H:%M')"

# ---------- ② 服务：是不是在跑磁盘上这一版 ----------
RUNNING_OK=1
ANY=0
CC_LOADED=0
for s in "${SERVICES[@]}"; do
    # LoadState 而不是 list-unit-files：后者匹配不到也退出 0，会把不存在的
    # 服务也报进来。（auto-update 用 `systemctl cat` 判断，在无终端环境里
    # 因分页器失败，静默漏掉了 ccbridge——同一类坑。）
    [ "$(systemctl show -p LoadState --value "$s.service" 2>/dev/null)" = loaded ] || continue
    ANY=1
    [ "$s" = ombre-ccbridge ] && CC_LOADED=1
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
    # 顺带把主进程本身的启动时间和 PID 印出来：ActiveEnterTimestamp 是 unit 的，
    # ExecMainStartTimestamp 是进程的。两者对不上就是「restart 发出去了、进程没换」。
    MAIN="pid $(systemctl show "$s" -p MainPID --value 2>/dev/null) 起于 $(systemctl show "$s" -p ExecMainStartTimestamp --value 2>/dev/null | cut -c1-24)"
    if [ "$STE" -ge "$PT" ]; then
        echo "② $s：✅ 启动于 $(date -d "@$STE" '+%m-%d %H:%M')，晚于人设改动 → 跑的是磁盘上这一版（$MAIN）"
    else
        echo "② $s：❌ 启动于 $(date -d "@$STE" '+%m-%d %H:%M')，早于人设改动 → 还在跑旧的（$MAIN）"
        RUNNING_OK=0
    fi
done
if [ "$ANY" = 0 ]; then
    echo "② ❓ 一个 ombre-* 服务都没找到（这台机器没装？）"
    RUNNING_OK=2
fi

# ---------- ③ cc 桥人设：是不是按磁盘上这一版 personality.py 生成的 ----------
# 以前这里 grep 一句哨兵。现在真生成一份出来逐字比——生成是确定性的（同一份
# personality.py 两次生成逐字相同），所以不一致就是没重新生成、或生成失败。
CC_OK=1
if [ -z "${CC_WORKDIR:-}" ]; then
    # 先看跑着的 unit 的 Environment=（setup-ccbridge.sh 写在这儿），再看 .env，再默认
    CC_WORKDIR=$(systemctl show ombre-ccbridge.service -p Environment --value 2>/dev/null \
                 | tr ' ' '\n' | grep -E '^CC_WORKDIR=' | tail -1 | cut -d= -f2-)
    [ -n "$CC_WORKDIR" ] || CC_WORKDIR=$(grep -E '^CC_WORKDIR=' "$REPO/.env.ccbridge" 2>/dev/null \
                 | tail -1 | cut -d= -f2- | tr -d '[:space:]')
    CC_WORKDIR=${CC_WORKDIR:-/home/ombre/nikto-cc}
    CHECK_CC=$CC_LOADED
else
    CHECK_CC=1   # 显式指定了目录就一定查
fi
CC_MD="$CC_WORKDIR/CLAUDE.md"
GEN="$REPO/scripts/make-cc-persona.py"
if [ "$CHECK_CC" != 1 ]; then
    echo "③ cc 桥人设：－ 这台机器没装 cc 桥，跳过"
elif [ ! -f "$CC_MD" ]; then
    echo "③ cc 桥人设：❓ 找不到 $CC_MD（CC_WORKDIR 对不对？）"
    CC_OK=2
elif [ ! -f "$GEN" ]; then
    echo "③ cc 桥人设：❓ 找不到生成脚本 $GEN，比不了"
    CC_OK=2
else
    PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY=$(command -v python3 || echo python3)
    LEAN=""; grep -qF "长度要参差" "$CC_MD" || LEAN="--lean"
    TMPD=$(mktemp -d)
    if OMBRE_PERSONA_NO_PROBE=1 "$PY" "$GEN" $LEAN "$TMPD" >/dev/null 2>&1 && [ -f "$TMPD/CLAUDE.md" ]; then
        if cmp -s "$TMPD/CLAUDE.md" "$CC_MD"; then
            echo "③ cc 桥人设（$CC_MD）：✅ 与磁盘上 personality.py 现在生成的逐字一致${LEAN:+（精简版）}"
        else
            echo "③ cc 桥人设（$CC_MD）：❌ 跟磁盘上 personality.py 生成的不一致 → 还是旧的"
            echo "   → 修：sudo runuser -u ombre -- $PY $GEN $LEAN $CC_WORKDIR"
            CC_OK=0
        fi
    else
        echo "③ cc 桥人设：❓ 用 $PY 跑生成脚本失败，比不了（手动跑一下看报什么）"
        CC_OK=2
    fi
    rm -rf "$TMPD"
fi

echo "--- auto-update 最近日志 ---"
journalctl -t ombre-autoupdate -n 5 --no-pager 2>/dev/null || echo "❓ 读不到日志"

echo "=========================="
if [ "$CODE_OK" = 1 ] && [ "$RUNNING_OK" = 1 ] && [ "$CC_OK" = 1 ]; then
    echo "✅ 成了：${HEAD_LINE:-这一版} 已经在跑，他那份人设与它逐字一致——去 telegram 用就行"
elif [ "$CODE_OK" = 0 ]; then
    echo "❌ 没成：代码还没到 VPS（或到的不是你推的那个分支）。先修上面 ① 的提示再说"
elif [ "$RUNNING_OK" = 0 ]; then
    echo "❌ 没成：代码到了，但跑的还是旧的。"
    echo "   → 修：sudo systemctl restart ${SERVICES[*]}"
elif [ "$CC_OK" = 0 ]; then
    echo "❌ 没成：代码到了、服务也重启了，但 cc 桥那份人设没重新生成。按 ③ 的命令修"
else
    echo "❓ 测不出来（这不等于「没问题」）—— 把上面整段发给我"
fi
