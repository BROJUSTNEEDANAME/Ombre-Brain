#!/usr/bin/env bash
# Ombre Brain 自动部署器：跟踪当前检出分支的远端，有新提交就拉取、重启，
# 起不来就自动回滚。由 ombre-autoupdate.timer 每 5 分钟触发；没更新时零动作。
#
# 回滚的由来：她的 Telegram 是每天在用的东西。我推过起不来的代码，
# 她那边就是「发消息完全没反应」。宁可停在旧版本，也不能让她面对一个死掉的他。
# 注意：只能挡住「进程起不来」这类问题；进程活着但每条消息报错的那种
# （比如函数里引用了未定义的名字）挡不住，那要靠交付前的 scripts/check.sh。
set -euo pipefail
REPO=/home/ombre/Ombre-Brain
# ⚠️ cc 桥是「装了才有」的，所以按 unit 是否存在动态决定，不写死——
# 写死了没装的机器每轮都会 restart 一个不存在的服务、日志里刷红。
# 只管「启用了的」服务。由来：她早就不用 API bot 了，它在崩溃循环里；可这里写死
# 了 ombre-apibot，部署后一查它不活就整个回滚——她关不掉它，一关自动更新就废。
# 用 is-enabled 判：disabled/masked 的一律不管、不重启、不拿它判成败。
SERVICES=(ombre-brain)
if [ "$(systemctl is-enabled ombre-apibot.service 2>/dev/null)" = enabled ]; then
    SERVICES+=(ombre-apibot)
fi
# ⚠️ 别用 `systemctl cat` 做存在性判断：它会调分页器，在定时器这种无终端的
# 环境里会失败，于是 ccbridge 被**静默**跳出名单——真事：她的 ccbridge 就这么
# 停在三天前的旧代码上，而日志每轮都乐呵呵地印「✅ 已部署，两个服务都活着」。
# `list-unit-files` 也不行：模式匹配不到时它照样退出 0。
# LoadState 是唯一可靠的：不存在时是 not-found，存在才是 loaded，且不分页。
if [ "$(systemctl show -p LoadState --value ombre-ccbridge.service 2>/dev/null)" = loaded ] \
   && [ "$(systemctl is-enabled ombre-ccbridge.service 2>/dev/null)" = enabled ]; then
    SERVICES+=(ombre-ccbridge)
fi
# ⚠️⚠️ 部署坏了必须**送到她眼前**，不是记进日志。
# 上次（9/12）我给部署器加了「没换进程就报 ❌」——报进 journal 了，没人看。
# 结果 9/17→9/22 ccbridge 又停了五天，这五天我做的一切一行都没跑，
# 她一次次替我踩，最后一句「上次自动更新就没起作用，你到底干什么吃的」。
# 这正是我自己写在 CLAUDE.md 里那条「✅ 只许打在她知道了上」——我却把它
# 打在「我记录了」上。所以：出事就直接发 Telegram 给她。
tg() {
    local msg="$1"
    local envf="$REPO/.env.ccbridge"
    [ -f "$envf" ] || return 0
    local tok cid base
    tok=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$envf" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "[:space:]\"'")
    cid=$(grep -E '^ALLOWED_CHAT_IDS=' "$envf" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "[:space:]\"'" | cut -d, -f1)
    base=$(grep -E '^TELEGRAM_API_BASE=' "$envf" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "[:space:]\"'<>")
    [ -n "$tok" ] && [ -n "$cid" ] || return 0
    base=${base:-https://api.telegram.org}
    curl -s -m 15 -o /dev/null -X POST "${base%/}/bot${tok}/sendMessage" \
        --data-urlencode "chat_id=${cid}" \
        --data-urlencode "text=${msg}" || true
}
log() { logger -t ombre-autoupdate "$*"; echo "$*"; }
g() { runuser -u ombre -- git -C "$REPO" "$@"; }

cd "$REPO"
BRANCH=$(g rev-parse --abbrev-ref HEAD)
# ⚠️ fetch 失败必须喊出来，而且要喊出**怎么修**。
# 真事：仓库里混进了 root 拥有的 .git/objects，ombre 从此写不进去，
# 每一轮 fetch 都是 "insufficient permission ... failed to write object"。
# 因为 set -e，脚本在这里就死了——日志里有，但没人会去看，
# 表现出来就是「代码永远停在几小时前那个提交」。她连问四次「怎么还是这样」。
if ! FETCH_ERR=$(g fetch origin "$BRANCH" --quiet 2>&1); then
    log "❌ git fetch 失败，自动更新停摆：$FETCH_ERR"
    tg "⚠️ 自动更新停摆了：拉不到代码。他会一直跑旧版本，我这边改的东西都不生效。
错误：$FETCH_ERR"
    case "$FETCH_ERR" in
        *"insufficient permission"*|*"Permission denied"*|*"failed to write object"*)
            log "   → 仓库里有不属于 ombre 的文件。修：sudo chown -R ombre:ombre $REPO"
            ;;
    esac
    exit 1
fi
LOCAL=$(g rev-parse HEAD)
REMOTE=$(g rev-parse "origin/$BRANCH")

# ⚠️ 「代码是新的」不等于「跑的是新代码」。
# 她这几天手动 git pull 过好几次；等定时器醒来时本地已经是最新的，
# 这里直接 exit 0，**根本走不到重启那一步**——服务就一直跑着几小时前的旧代码，
# 而日志里一切正常。她连问三次「怎么还是这样」，我改了三轮其实早就修好了。
# 所以再加一道：任何一个服务的启动时间早于当前 HEAD 的提交时间，就是在跑旧代码。
HEAD_TS=$(g log -1 --format=%ct)
STALE=""
for s in "${SERVICES[@]}"; do
    ST=$(systemctl show "$s" -p ActiveEnterTimestamp --value 2>/dev/null)
    [ -z "$ST" ] && continue
    STE=$(date -d "$ST" +%s 2>/dev/null) || continue
    [ "$STE" -lt "$HEAD_TS" ] && STALE="$STALE $s"
done

if [ "$LOCAL" = "$REMOTE" ]; then
    [ -z "$STALE" ] && exit 0
    log "代码已是最新，但这些服务还跑着旧代码，重启：$STALE"
fi

# 坏提交拉黑：回滚之后本地必然落后于远端，不记住的话下一轮又拉一遍，
# 变成每 5 分钟重启一次她的服务的无限循环（模拟测试里踩到了）。
BLOCK="$REPO/.autoupdate-blocked"
if [ -f "$BLOCK" ] && [ "$(cat "$BLOCK")" = "$REMOTE" ]; then
    exit 0   # 这个提交已经证明起不来，等下一个新提交再说
fi

# 只接受快进合并：本地被手改过就报警不硬来，绝不覆盖手工修改
if ! g merge --ff-only "origin/$BRANCH" --quiet; then
    log "❌ 无法快进合并（本地有改动？），本次不部署，保持旧版本"
    exit 1
fi
NEW=$(g rev-parse --short HEAD)

# ⚠️⚠️ 部署器更新得了所有人，唯独更新不了自己——这一条害得最惨。
# systemd 跑的是 /usr/local/bin/ombre-auto-update，那是 install-autoupdate.sh
# **一次性拷过去**的副本。仓库里这个文件我改了五次（包括「别用 systemctl cat」
# 那条关键修复），一行都没生效：跑的始终是安装那天的旧副本。
# 后果：ccbridge 一直不在重启名单里，从 9 月 12 号起跑了三天旧进程，
# 而日志每轮都印「✅ 已部署」。她那边表现成「新命令没有」「改了也没变化」。
# 所以拉到新代码后第一件事：把自己换成新的，然后用新版接着跑这一轮。
SELF=/usr/local/bin/ombre-auto-update
if [ -z "${OMBRE_SELF_UPDATED:-}" ] && [ -e "$SELF" ] \
   && ! cmp -s "$REPO/deploy/auto-update.sh" "$SELF"; then
    log "部署器自身有更新，装上并用新版重跑这一轮"
    install -m 755 "$REPO/deploy/auto-update.sh" "$SELF"
    OMBRE_SELF_UPDATED=1 exec "$SELF"     # 防无限自我重启：只让新版接手一次
fi
# unit 文件同理，它们也是拷过去的
UNIT_CHANGED=""
for u in ombre-autoupdate.service ombre-autoupdate.timer; do
    if [ -f "$REPO/deploy/$u" ] && ! cmp -s "$REPO/deploy/$u" "/etc/systemd/system/$u"; then
        cp "$REPO/deploy/$u" "/etc/systemd/system/$u" && UNIT_CHANGED=1
    fi
done
[ -n "$UNIT_CHANGED" ] && { systemctl daemon-reload; log "部署器的 unit 文件已更新"; }

log "拉到新提交 $BRANCH @ $NEW，重启服务：${SERVICES[*]}"

# ⚠️ cc 桥的人设是**生成**出来的（nikto-cc/CLAUDE.md 来自 personality.py）。
# 光重启不重新生成，改完人设那边会一直用旧的，而且一点提示都没有——
# 这种「看起来更新了、其实没更新」的静默失败最难查。
CC_WORKDIR=$(grep -E '^CC_WORKDIR=' "$REPO/.env.ccbridge" 2>/dev/null | tail -1 \
             | cut -d= -f2- | tr -d '[:space:]')
CC_WORKDIR=${CC_WORKDIR:-/home/ombre/nikto-cc}
if printf '%s\n' "${SERVICES[@]}" | grep -qx ombre-ccbridge; then
    if runuser -u ombre -- "$REPO/.venv/bin/python" \
            "$REPO/scripts/make-cc-persona.py" "$CC_WORKDIR" >/dev/null 2>&1; then
        log "cc 人设已按新代码重新生成（$CC_WORKDIR）"
    else
        log "⚠️ cc 人设重新生成失败，那边可能还在用旧人设"
    fi
fi

# ⚠️ 「restart 命令发出去了」不等于「它换成新进程了」。
# 真事：日志连着三天每轮都印「✅ 已部署，两个服务都活着」，可 ccbridge 的
# ActiveEnterTimestamp 一直停在 9 月 12 号——它确实活着（所以 is-active 过了），
# 活的却是三天前那个进程。她那边表现成「新加的命令没有」「改了人设没变化」。
# 所以重启前后各记一次 ActiveEnterTimestampMonotonic（整数，不用解析日期）：
# 没往前走 = 根本没换进程，这是 ❌，绝不许混进 ✅ 那一行。
declare -A WAS_AT=()
for s in "${SERVICES[@]}"; do
    WAS_AT[$s]=$(systemctl show "$s" -p ActiveEnterTimestampMonotonic --value 2>/dev/null || echo 0)
    [ -n "${WAS_AT[$s]}" ] || WAS_AT[$s]=0
done

for s in "${SERVICES[@]}"; do
    systemctl restart "$s" || log "⚠️ systemctl restart $s 返回非零——这一步就没成"
done
sleep 8   # 给它们一点启动时间再判活

FAILED=""
STUCK=""
for s in "${SERVICES[@]}"; do
    if ! systemctl is-active --quiet "$s"; then
        FAILED="$FAILED $s"
        continue
    fi
    NOW_AT=$(systemctl show "$s" -p ActiveEnterTimestampMonotonic --value 2>/dev/null || echo 0)
    [ -n "$NOW_AT" ] || NOW_AT=0
    if [ "${WAS_AT[$s]}" != 0 ] && [ "$NOW_AT" = "${WAS_AT[$s]}" ]; then
        STUCK="$STUCK $s"
    fi
done

if [ -n "$FAILED" ]; then
    log "❌ 新版本起不来（$FAILED），回滚到 $LOCAL"
    tg "⚠️ 新版本起不来（$FAILED），已经自动回滚到上一版。他还活着，但用的是旧代码。"
    g reset --hard "$LOCAL" --quiet
    echo "$REMOTE" > "$BLOCK"     # 拉黑这个提交，别再反复重启她的服务
    for s in "${SERVICES[@]}"; do systemctl restart "$s" || true; done
    exit 1
fi
rm -f "$BLOCK"

# 有服务没真的换进程 → 代码是新的，跑的还是旧的。这不该回滚（旧进程还好好地
# 陪着她），但必须喊出来，而且不许再打那句 ✅。
if [ -n "$STUCK" ]; then
    log "❌ 代码已更新到 $NEW，但这些服务没换进程、还在跑旧代码：$STUCK"
    tg "⚠️ 代码更新到了 $NEW，但$STUCK 没换进程，他跑的还是旧代码——改的东西一个都没生效。
在 VPS 上跑：sudo systemctl restart$STUCK"
    log "   → 查原因：systemctl status$STUCK；必要时 systemctl stop$STUCK 再 start"
    exit 1
fi
log "✅ 已部署 $BRANCH @ $NEW，${#SERVICES[@]} 个服务都在跑新代码：${SERVICES[*]}"
