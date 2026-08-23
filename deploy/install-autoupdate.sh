#!/usr/bin/env bash
# 一次性安装：装自动部署器 + 立刻让当前代码生效。跑完以后再也不用开终端。
set -euo pipefail
REPO=/home/ombre/Ombre-Brain
install -m 755 "$REPO/deploy/auto-update.sh" /usr/local/bin/ombre-auto-update
cp "$REPO/deploy/ombre-autoupdate.service" "$REPO/deploy/ombre-autoupdate.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ombre-autoupdate.timer
systemctl restart ombre-brain
systemctl restart ombre-apibot
echo "✅ 已生效。以后代码推送后约 5 分钟内自动部署，无需再开终端。"
