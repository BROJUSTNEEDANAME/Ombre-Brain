#!/usr/bin/env bash
# Ombre Brain 自动部署器：跟踪当前检出分支的远端，有新提交就拉取并重启服务。
# 由 ombre-autoupdate.timer 每 5 分钟触发一次；没更新时零动作、零打扰。
set -euo pipefail
REPO=/home/ombre/Ombre-Brain
cd "$REPO"
BRANCH=$(runuser -u ombre -- git -C "$REPO" rev-parse --abbrev-ref HEAD)
runuser -u ombre -- git -C "$REPO" fetch origin "$BRANCH" --quiet
LOCAL=$(runuser -u ombre -- git -C "$REPO" rev-parse HEAD)
REMOTE=$(runuser -u ombre -- git -C "$REPO" rev-parse "origin/$BRANCH")
[ "$LOCAL" = "$REMOTE" ] && exit 0
# 只接受快进合并：本地被手改过就报警不硬来，绝不覆盖手工修改
runuser -u ombre -- git -C "$REPO" merge --ff-only "origin/$BRANCH" --quiet
systemctl restart ombre-brain
systemctl restart ombre-apibot
logger -t ombre-autoupdate "deployed $BRANCH @ $(runuser -u ombre -- git -C "$REPO" rev-parse --short HEAD), services restarted"
