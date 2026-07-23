#!/bin/bash
set -Eeuo pipefail

REPO=/home/ombre/Ombre-Brain
STAMP=$(date -u +%Y%m%d-%H%M%S)
SAFE=/root/ombre-coreading-deploy-$STAMP
OLD_SHA=""
SWITCHED=0
ANNO_WAS_ACTIVE=0
SITE_DROPIN=/etc/systemd/system/ombre-brain.service.d/20-site-url.conf
SITE_ENV=/etc/ombre/site-url.env
HAD_SITE_DROPIN=0
HAD_SITE_ENV=0

if [ "$(id -u)" -ne 0 ] || [ "$PWD" != "$REPO" ]; then
    echo "Run as root from $REPO"
    exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Refusing to deploy over tracked VPS code changes"
    exit 1
fi

OLD_SHA=$(git rev-parse HEAD)
systemctl is-active --quiet anno-mcp.service && ANNO_WAS_ACTIVE=1 || true
mkdir -m 700 "$SAFE"
git status --short > "$SAFE/git-status.txt"
printf '%s\n' "$OLD_SHA" > "$SAFE/old-main.sha"
cp -a /etc/systemd/system/ombre-brain.service "$SAFE/"
cp -a /etc/systemd/system/ombre-apibot.service "$SAFE/"
if [ -f "$SITE_DROPIN" ]; then
    cp -a "$SITE_DROPIN" "$SAFE/20-site-url.conf"
    HAD_SITE_DROPIN=1
fi
if [ -f "$SITE_ENV" ]; then
    cp -a "$SITE_ENV" "$SAFE/site-url.env"
    HAD_SITE_ENV=1
fi
for file in "$REPO/.env" "$REPO/.env.apibot" "$REPO/buckets/telegram_state.json"; do
    [ ! -e "$file" ] || cp -a "$file" "$SAFE/"
done

rollback() {
    echo "Deployment failed; restoring commit $OLD_SHA"
    cd "$REPO"
    git reset --hard "$OLD_SHA"
    cp -a "$SAFE/ombre-brain.service" /etc/systemd/system/ombre-brain.service
    cp -a "$SAFE/ombre-apibot.service" /etc/systemd/system/ombre-apibot.service
    if [ "$HAD_SITE_DROPIN" -eq 1 ]; then
        mkdir -p "$(dirname "$SITE_DROPIN")"
        cp -a "$SAFE/20-site-url.conf" "$SITE_DROPIN"
    else
        rm -f "$SITE_DROPIN"
    fi
    if [ "$HAD_SITE_ENV" -eq 1 ]; then
        mkdir -p "$(dirname "$SITE_ENV")"
        cp -a "$SAFE/site-url.env" "$SITE_ENV"
    else
        rm -f "$SITE_ENV"
    fi
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

# The expected version always comes from the code being deployed, never a
# hard-coded literal: a stale literal here silently rolls back good deploys.
EXPECTED_VERSION=$(sed -n 's/^OMBRE_WEB_VERSION = "\(v[^"]*\)".*/\1/p' server.py)
test -n "$EXPECTED_VERSION"

.venv/bin/pip install 'beautifulsoup4>=4.12.0' 'playwright>=1.50.0'
.venv/bin/python -m playwright install --with-deps chromium

.venv/bin/python -m pytest -q \
    tests/test_chat_store.py tests/test_adhd_manager.py tests/test_coreading.py \
    tests/test_prompt_output.py tests/test_personality.py tests/test_writing_style.py \
    tests/test_prompt_cache.py tests/test_dedup_helpers.py \
    tests/test_home_recovery_contract.py tests/test_public_site.py
.venv/bin/python -m compileall -q \
    server.py telegram_bot.py chat_store.py adhd_manager.py coreading.py anno_client.py \
    personality.py writing_style.py prompt_cache.py reply_sanitizer.py public_site.py \
    configure-site-url.py
bash -n install-anno.sh deploy-coreading.sh
bash install-anno.sh
.venv/bin/python configure-site-url.py /etc/caddy/Caddyfile "$SITE_DROPIN" "$SITE_ENV"
systemctl daemon-reload

SWITCHED=1
systemctl restart ombre-brain.service ombre-apibot.service
EXPECTED_VERSION="$EXPECTED_VERSION" .venv/bin/python - <<'PY'
import json
import os
import time
import urllib.request

expected = os.environ["EXPECTED_VERSION"]
for _ in range(40):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/version", timeout=2) as response:
            data = json.load(response)
        if data.get("version") == expected:
            print("Brain version:", data["version"])
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit("brain health/version check failed")
PY

.venv/bin/python - <<'PY'
import json
import secrets
import urllib.parse
import urllib.request
from pathlib import Path

from public_site import resolve_public_site_url

base = resolve_public_site_url()
if not base:
    raise SystemExit("public site URL is not configured")
marker = "OMBRE_PUBLIC_PAGE_OK_" + secrets.token_hex(4)
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/tools/make_page",
    data=json.dumps({"html": f"<p>{marker}</p>", "title": "deploy check"}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=15) as response:
    result = json.load(response).get("result", "")
if not result.startswith(base + "/p/"):
    raise SystemExit("make_page returned the wrong public URL")
page_id = Path(urllib.parse.urlparse(result).path).name
page = Path("/home/ombre/Ombre-Brain/buckets/pages") / f"{page_id}.html"
try:
    with urllib.request.urlopen(result, timeout=15) as response:
        body = response.read().decode("utf-8")
    if marker not in body:
        raise SystemExit("public page returned the wrong content")
finally:
    page.unlink(missing_ok=True)
print("PUBLIC-PAGE-PASS")
PY

test "$(systemctl is-active ombre-brain.service)" = active
test "$(systemctl is-active ombre-apibot.service)" = active
test "$(systemctl is-active cc-bridge.service)" = active
test "$(systemctl is-active anno-mcp.service)" = active
curl -fsS http://127.0.0.1:3300/health >/dev/null
LISTEN_ADDR="$(ss -ltnH | awk '$4 ~ /:3300$/ { print $4; exit }' || true)"
if [ "$LISTEN_ADDR" != "127.0.0.1:3300" ] && [ "$LISTEN_ADDR" != "[::1]:3300" ]; then
    echo "Anno listener is not loopback-only: ${LISTEN_ADDR:-missing}"
    false
fi

trap - ERR
echo "DEPLOY-COREADING-PASS"
echo "Backup: $SAFE"
echo "Backup SHA-256: $(cut -d' ' -f1 "$SAFE/SHA256SUMS")"
echo "Version: $EXPECTED_VERSION"
echo "Services: ombre-brain=active ombre-apibot=active cc-bridge=active anno-mcp=active"
