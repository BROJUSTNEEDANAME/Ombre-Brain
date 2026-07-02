#!/usr/bin/env bash
# ============================================================
# Ombre Brain 自动部署
# 定时把 VPS 对齐到 GitHub 分支：有新提交就拉下来、语法自检通过后重启服务。
# 由 systemd timer 每几分钟跑一次（以 root 运行，好 systemctl 重启）。
# 效果：claude.ai 那边一 push，这边几分钟内自动上线 —— 零终端、零登录、零复制。
# 坏提交（语法错）会自动回滚、不上线。
# ============================================================
set -uo pipefail

REPO="${OMBRE_REPO_DIR:-/home/ombre/Ombre-Brain}"
BRANCH="${OMBRE_DEPLOY_BRANCH:-claude/ombre-brain-archive-7ha6xf}"
export HOME=/root
# 每条 git 都内联 safe.directory，不依赖 systemd 环境里的 HOME/gitconfig，
# 彻底避免 root 操作 ombre 仓库时的 "dubious ownership" 静默失败（之前就卡在这）。
GIT="git -c safe.directory=$REPO -C $REPO"

# 拉远端（匿名读公开仓库即可，不需要登录）
$GIT fetch origin "$BRANCH" -q 2>/dev/null || exit 0

LOCAL=$($GIT rev-parse HEAD 2>/dev/null || echo "")
REMOTE=$($GIT rev-parse "origin/$BRANCH" 2>/dev/null || echo "")
[ -z "$REMOTE" ] && exit 0
[ "$LOCAL" = "$REMOTE" ] && exit 0   # 没有新提交

logger -t ombre-autodeploy "发现更新 ${LOCAL:0:7} -> ${REMOTE:0:7}，开始部署"
$GIT reset --hard "origin/$BRANCH" -q || { $GIT reset --hard "$LOCAL" -q 2>/dev/null; exit 1; }

# 语法自检：新代码 Python 编译不过就回滚，绝不上线坏代码
if ! python3 -m compileall -q "$REPO"/*.py 2>/dev/null; then
    logger -t ombre-autodeploy "新代码语法检查失败，回滚到 ${LOCAL:0:7}"
    $GIT reset --hard "$LOCAL" -q 2>/dev/null
    exit 1
fi

# 重启两个服务（大脑 + bot）。都设了 Restart=always，稳。
systemctl restart ombre-brain 2>/dev/null || true
systemctl restart cc-bridge 2>/dev/null || true
logger -t ombre-autodeploy "已部署并重启 -> ${REMOTE:0:7}"
