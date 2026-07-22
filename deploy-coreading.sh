#!/bin/bash
set -Eeuo pipefail

REPO=/home/ombre/Ombre-Brain
STAMP=$(date -u +%Y%m%d-%H%M%S)
SAFE=/root/ombre-coreading-deploy-$STAMP
OLD_SHA=""
SWITCHED=0
ANNO_WAS_ACTIVE=0

if [ "$(id -u)" -ne 0 ] || [ "$PWD" != "$REPO" ]; then
    echo "Run as root from $REPO"
    exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
    echo "Refusing to deploy over uncommitted VPS changes"
    exit 1
fi

OLD_SHA=$(git rev-parse HEAD)
systemctl is-active --quiet anno-mcp.service && ANNO_WAS_ACTIVE=1 || true
mkdir -m 700 "$SAFE"
git status --short > "$SAFE/git-status.txt"
printf '%s\n' "$OLD_SHA" > "$SAFE/old-main.sha"
cp -a /etc/systemd/system/ombre-brain.service "$SAFE/"
cp -a /etc/systemd/system/ombre-apibot.service "$SAFE/"
for file in "$REPO/.env" "$REPO/.env.apibot" "$REPO/buckets/telegram_state.json"; do
    [ ! -e "$file" ] || cp -a "$file" "$SAFE/"
done

rollback() {
    echo "Deployment failed; restoring commit $OLD_SHA"
    cd "$REPO"
    git reset --hard "$OLD_SHA"
    cp -a "$SAFE/ombre-brain.service" /etc/systemd/system/ombre-brain.service
    cp -a "$SAFE/ombre-apibot.service" /etc/systemd/system/ombre-apibot.service
    systemctl daemon-reload
    systemctl restart ombre-brain.service ombre-apibot.service || true
    if [ "$ANNO_WAS_ACTIVE" -eq 0 ]; then
        systemctl disable --now anno-mcp.service >/dev/null 2>&1 || true
    fi
}
trap rollback ERR

# Stopping both writers makes every SQLite/JSON file in buckets a consistent snapshot.
systemctl stop ombre-apibot.service ombre-brain.service
tar -C "$REPO" -czf "$SAFE/buckets.tar.gz" buckets
chmod 600 "$SAFE/buckets.tar.gz"
tar -tzf "$SAFE/buckets.tar.gz" >/dev/null
sha256sum "$SAFE/buckets.tar.gz" > "$SAFE/SHA256SUMS"
systemctl start ombre-brain.service ombre-apibot.service

git fetch origin main
git merge --ff-only origin/main

.venv/bin/pip install 'beautifulsoup4>=4.12.0' 'playwright>=1.50.0'
.venv/bin/python -m playwright install --with-deps chromium

.venv/bin/python -m pytest -q \
    tests/test_chat_store.py tests/test_adhd_manager.py tests/test_coreading.py tests/test_prompt_output.py
.venv/bin/python -m compileall -q \
    server.py telegram_bot.py chat_store.py adhd_manager.py coreading.py anno_client.py
bash -n install-anno.sh deploy-coreading.sh
bash install-anno.sh

SWITCHED=1
systemctl restart ombre-brain.service ombre-apibot.service
.venv/bin/python - <<'PY'
import json
import time
import urllib.request

for _ in range(40):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/version", timeout=2) as response:
            data = json.load(response)
        if data.get("version") == "v5.4.1":
            print("Brain version:", data["version"])
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit("brain health/version check failed")
PY

test "$(systemctl is-active ombre-brain.service)" = active
test "$(systemctl is-active ombre-apibot.service)" = active
test "$(systemctl is-active cc-bridge.service)" = active
test "$(systemctl is-active anno-mcp.service)" = active
curl -fsS http://127.0.0.1:3300/health >/dev/null
if ss -ltn | grep -F ':3300 ' | grep -Fq '0.0.0.0'; then
    echo "Anno is exposed publicly"
    exit 1
fi

trap - ERR
echo "DEPLOY-COREADING-PASS"
echo "Backup: $SAFE"
echo "Backup SHA-256: $(cut -d' ' -f1 "$SAFE/SHA256SUMS")"
echo "Version: v5.4.1"
echo "Services: ombre-brain=active ombre-apibot=active cc-bridge=active anno-mcp=active"
