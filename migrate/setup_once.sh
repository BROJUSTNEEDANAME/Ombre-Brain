#!/usr/bin/env bash
# ============================================================
# 一次性安装（以 root 在 DigitalOcean 网页控制台直接跑，不需要 Claude Code / 登录）：
#   1) 拉最新代码（时间检索 / 主动找她 / 看图 / 检索升级 全部生效）
#   2) 装自动部署 timer —— 以后 push 即自动上线，再也不用进终端
#   3) 主动找她间隔设为 1 小时
#   4) 重启大脑 + bot
# 跑法： bash /home/ombre/Ombre-Brain/migrate/setup_once.sh
# ============================================================
set -uo pipefail
REPO=/home/ombre/Ombre-Brain
BRANCH=claude/ombre-brain-archive-7ha6xf

echo "[1/4] 拉最新代码 ..."
sudo -u ombre git -C "$REPO" fetch origin -q
sudo -u ombre git -C "$REPO" reset --hard "origin/$BRANCH" -q
echo "      已对齐到 $(git -C "$REPO" rev-parse --short HEAD)"

echo "[2/4] 安装自动部署 ..."
chmod +x "$REPO/migrate/autodeploy.sh"
cp "$REPO/migrate/ombre-autodeploy.service" /etc/systemd/system/
cp "$REPO/migrate/ombre-autodeploy.timer" /etc/systemd/system/
git config --global --add safe.directory "$REPO"   # 让 root 能操作 ombre 的仓库
systemctl daemon-reload
systemctl enable --now ombre-autodeploy.timer
echo "      自动部署已启用（每 3 分钟自动检查上线）"

echo "[3/4] 主动找她间隔设为 1 小时 ..."
mkdir -p /etc/systemd/system/cc-bridge.service.d
cat > /etc/systemd/system/cc-bridge.service.d/override.conf <<'CONF'
[Service]
Environment=OMBRE_IDLE_HOURS=1
CONF
systemctl daemon-reload

echo "[4/4] 重启服务 ..."
systemctl restart ombre-brain 2>/dev/null || true
systemctl restart cc-bridge 2>/dev/null || true

echo
echo "==== 完成，状态如下 ===="
systemctl is-active ombre-brain cc-bridge ombre-autodeploy.timer
echo "HEAD: $(git -C "$REPO" rev-parse --short HEAD)"
echo "以后 claude.ai 一 push，这台机 3 分钟内自动上线，你不用再进终端。"
