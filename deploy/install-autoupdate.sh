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

# ⚠️ cc 的人设是**生成**出来的（nikto-cc/CLAUDE.md ← personality.py）。
# auto-update.sh 拉到新提交时会重生成，但手动重装这条路原来漏了这一步——
# 于是「服务重启了、人设还是旧的」，最难查的那种静默失败。真事：刚破完
# 部署器自更新的僵局，人设仍然是旧的，差点又白高兴一场。
CC_WORKDIR=$(grep -E '^CC_WORKDIR=' "$REPO/.env.ccbridge" 2>/dev/null | tail -1 \
             | cut -d= -f2- | tr -d '[:space:]')
CC_WORKDIR=${CC_WORKDIR:-/home/ombre/nikto-cc}
if [ -x "$REPO/.venv/bin/python" ] && [ -f "$REPO/scripts/make-cc-persona.py" ]; then
    if runuser -u ombre -- "$REPO/.venv/bin/python" \
            "$REPO/scripts/make-cc-persona.py" "$CC_WORKDIR" >/dev/null 2>&1; then
        echo "· cc 人设已重新生成（$CC_WORKDIR）"
    else
        echo "⚠️ cc 人设重新生成失败，那边可能还在用旧人设"
    fi
fi

# ⚠️ 只重启「装了而且启用了的」服务。写死过 ombre-apibot，她把它关掉之后
# 这里就报错退出（set -e），整个安装半途而废。
for s in ombre-brain ombre-apibot ombre-ccbridge; do
    [ "$(systemctl show -p LoadState --value "$s.service" 2>/dev/null)" = loaded ] || continue
    [ "$(systemctl is-enabled "$s.service" 2>/dev/null)" = enabled ] || continue
    systemctl restart "$s" && echo "· 已重启 $s"
done
echo "✅ 已生效。以后代码推送后约 5 分钟内自动部署，无需再开终端。"
