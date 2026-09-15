#!/usr/bin/env bash
# 一次性安装：装自动部署器 + 立刻让当前代码生效。跑完以后再也不用开终端。
#
# ⚠️ 这个脚本是「拷贝」式安装：auto-update.sh 会被复制到 /usr/local/bin。
# 以前这意味着仓库里改了部署器也不会生效（真事：改了五次没一次跑上，
# ccbridge 因此跑了三天旧进程）。现在 auto-update.sh 每轮会自己比对、自己换，
# 所以这里只负责第一次装上；但手动重装它永远是安全的。
set -euo pipefail
REPO=/home/ombre/Ombre-Brain
install -m 755 "$REPO/deploy/auto-update.sh" /usr/local/bin/ombre-auto-update
cp "$REPO/deploy/ombre-autoupdate.service" "$REPO/deploy/ombre-autoupdate.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ombre-autoupdate.timer

# ⚠️ 只重启「装了而且启用了的」服务。写死过 ombre-apibot，她把它关掉之后
# 这里就报错退出（set -e），整个安装半途而废。
for s in ombre-brain ombre-apibot ombre-ccbridge; do
    [ "$(systemctl show -p LoadState --value "$s.service" 2>/dev/null)" = loaded ] || continue
    [ "$(systemctl is-enabled "$s.service" 2>/dev/null)" = enabled ] || continue
    systemctl restart "$s" && echo "· 已重启 $s"
done
echo "✅ 已生效。以后代码推送后约 5 分钟内自动部署，无需再开终端。"
