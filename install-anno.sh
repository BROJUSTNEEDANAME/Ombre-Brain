#!/bin/bash
set -Eeuo pipefail

ANNO_COMMIT=ae28401737f487aba4d3caba2f9205e4c9f54da3
SOURCE=/opt/anno-mcp-source
APP=/opt/marginalia
DATA=/var/lib/anno
REPO=/home/ombre/Ombre-Brain

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root"
    exit 1
fi

id anno >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin anno
install -d -m 750 -o anno -g anno "$DATA/data" "$DATA/uploads"
install -d -m 755 -o root -g root /opt /etc/ombre

if [ ! -d "$SOURCE/.git" ]; then
    git clone https://github.com/Shitsuten/anno-mcp.git "$SOURCE"
fi
git -C "$SOURCE" fetch origin "$ANNO_COMMIT"
git -C "$SOURCE" checkout --detach "$ANNO_COMMIT"

install -d -m 755 -o root -g root "$APP"
install -m 644 "$SOURCE/server/server.mjs" "$SOURCE/server/package.json" "$APP/"
install -m 755 "$SOURCE/server/extract_pdf.py" "$SOURCE/server/extract_epub.py" "$APP/"
if [ ! -L "$APP/data" ] && [ -e "$APP/data" ]; then
    echo "$APP/data exists and is not a symlink; refusing to overwrite it"
    exit 1
fi
if [ ! -L "$APP/uploads" ] && [ -e "$APP/uploads" ]; then
    echo "$APP/uploads exists and is not a symlink; refusing to overwrite it"
    exit 1
fi
ln -sfn "$DATA/data" "$APP/data"
ln -sfn "$DATA/uploads" "$APP/uploads"

cd "$APP"
npm install --omit=dev --ignore-scripts
chown -R root:root "$APP"

if [ ! -f /etc/ombre/anno.env ]; then
    umask 077
    printf 'MCP_AUTH_TOKEN=%s\n' "$(openssl rand -hex 32)" > /etc/ombre/anno.env
fi
chmod 600 /etc/ombre/anno.env
install -m 644 "$REPO/deploy/anno-mcp.service" /etc/systemd/system/anno-mcp.service
systemctl daemon-reload
systemctl enable --now anno-mcp.service

ANNO_READY=0
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:3300/health >/dev/null 2>&1; then
        ANNO_READY=1
        break
    fi
    sleep 1
done
if [ "$ANNO_READY" -ne 1 ]; then
    systemctl status anno-mcp.service --no-pager -l || true
    echo "Anno did not become healthy within 30 seconds"
    exit 1
fi
if ss -ltnp | grep -F ':3300 ' | grep -Fq '0.0.0.0'; then
    echo "Anno unexpectedly exposed on 0.0.0.0:3300"
    exit 1
fi
echo "ANNO-INSTALL-PASS commit=$ANNO_COMMIT listen=127.0.0.1:3300 data=$DATA"
