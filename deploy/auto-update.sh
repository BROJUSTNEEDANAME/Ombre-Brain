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
SERVICES=(ombre-brain ombre-apibot)
log() { logger -t ombre-autoupdate "$*"; echo "$*"; }
g() { runuser -u ombre -- git -C "$REPO" "$@"; }

cd "$REPO"
BRANCH=$(g rev-parse --abbrev-ref HEAD)
g fetch origin "$BRANCH" --quiet
LOCAL=$(g rev-parse HEAD)
REMOTE=$(g rev-parse "origin/$BRANCH")
[ "$LOCAL" = "$REMOTE" ] && exit 0

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
log "拉到新提交 $BRANCH @ $NEW，重启服务"

for s in "${SERVICES[@]}"; do systemctl restart "$s" || true; done
sleep 8   # 给它们一点启动时间再判活

FAILED=""
for s in "${SERVICES[@]}"; do
    systemctl is-active --quiet "$s" || FAILED="$FAILED $s"
done

if [ -n "$FAILED" ]; then
    log "❌ 新版本起不来（$FAILED），回滚到 $LOCAL"
    g reset --hard "$LOCAL" --quiet
    echo "$REMOTE" > "$BLOCK"     # 拉黑这个提交，别再反复重启她的服务
    for s in "${SERVICES[@]}"; do systemctl restart "$s" || true; done
    exit 1
fi
rm -f "$BLOCK"
log "✅ 已部署 $BRANCH @ $NEW，两个服务都活着"
