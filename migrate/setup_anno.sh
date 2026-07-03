#!/usr/bin/env bash
# ============================================================
# 一次性安装「一起看书」anno-mcp（root 在 DO 网页控制台直接跑）
# 代码来源：闪闪自己的 fork（brojustneedaname/anno-mcp）。已由 claude.ai 全量审计
#   （2026-07-02）：零外联、无 eval、文件访问受限于自身目录、依赖全主流。
#
#   1) 克隆她的 fork
#   2) 按应用写死的路径铺到 /opt/marginalia（server.mjs 硬编码此路径）
#   3) systemd 服务 anno（只绑本机 3300，不上公网）
#   4) Caddy 本机 3301：托管阅读器静态页 + 转发 API/MCP
#   5) tailscale serve 8443：只暴露到她自己的私网（非 Funnel、谷歌搜不到）
#
# 前提：先在 GitHub 网页把 Shitsuten/anno-mcp Fork 到自己账号。
# 跑法： bash /home/ombre/Ombre-Brain/migrate/setup_anno.sh
# ============================================================
set -uo pipefail
SRC=/home/ombre/anno-mcp          # 克隆处（她的 fork）
APP=/opt/marginalia               # 应用硬编码的运行目录
FORK=https://github.com/brojustneedaname/anno-mcp

echo "[1/6] 克隆你的 fork ..."
if [ ! -d "$SRC/.git" ]; then
    sudo -u ombre git clone --depth 1 "$FORK" "$SRC" || {
        echo "!! 克隆失败——确认你已在 GitHub 网页上 Fork 了 Shitsuten/anno-mcp"; exit 1; }
else
    sudo -u ombre git -c safe.directory="$SRC" -C "$SRC" pull -q || true
fi

echo "[2/6] 铺到 /opt/marginalia（应用写死的路径）..."
mkdir -p "$APP" "$APP/data" "$APP/uploads"
cp "$SRC"/server/server.mjs "$SRC"/server/extract_pdf.py "$SRC"/server/extract_epub.py \
   "$SRC"/server/package.json "$APP"/
cp -r "$SRC"/client "$APP"/client
chown -R ombre:ombre "$APP"

echo "[3/6] 装依赖（npm + python venv，几分钟）..."
cd "$APP" || exit 1
sudo -u ombre npm install --no-fund --no-audit 2>&1 | tail -2
sudo -u ombre python3 -m venv "$APP/.venv"
sudo -u ombre "$APP/.venv/bin/pip" install -q pymupdf ebooklib

echo "[4/6] 装 systemd 服务 anno ..."
NODE_BIN=$(sudo -u ombre bash -lc 'command -v node' | tail -1)
[ -z "$NODE_BIN" ] && { echo "!! 找不到 node"; exit 1; }
cat > /etc/systemd/system/anno.service <<UNIT
[Unit]
Description=Anno shared-reading server (Marginalia)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ombre
WorkingDirectory=$APP
Environment=PORT=3300
Environment=PATH=$APP/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$NODE_BIN $APP/server.mjs
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now anno

echo "[5/6] Caddy 托管阅读器（本机 3301）..."
if ! grep -q "anno reader" /etc/caddy/Caddyfile 2>/dev/null; then
    cat >> /etc/caddy/Caddyfile <<CADDY

# === anno reader（一起看书，私网用）===
:3301 {
	bind 127.0.0.1
	handle /marginalia/* {
		reverse_proxy 127.0.0.1:3300
	}
	handle /api/* {
		reverse_proxy 127.0.0.1:3300
	}
	handle /mcp/* {
		reverse_proxy 127.0.0.1:3300
	}
	handle {
		root * $APP/client
		file_server
	}
}
CADDY
    systemctl restart caddy
fi

echo "[6/6] 私网暴露（tailnet 8443，只有你自己的设备能开）..."
tailscale serve --bg --https=8443 3301 2>/dev/null \
  || tailscale serve --bg --https=8443 http://127.0.0.1:3301 2>/dev/null \
  || tailscale serve https:8443 / http://127.0.0.1:3301 2>/dev/null || true

sleep 2
echo
echo "==== 状态 ===="
systemctl is-active anno caddy
curl -s -o /dev/null -w "本机自测 reader: HTTP %{http_code}\n" http://127.0.0.1:3301/ || true
HOSTDNS=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || echo "你的机器名.ts.net")
echo "阅读器地址（手机/电脑开着 Tailscale 才能访问）："
echo "    https://$HOSTDNS:8443/"
echo "上传书 → 网页里传 PDF/EPUB/TXT；然后在 Telegram 跟他说\"陪我看书\"。"
