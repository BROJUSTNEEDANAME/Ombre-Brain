#!/bin/bash
set -Eeuo pipefail

REPO=/home/ombre/Ombre-Brain
OLD=/root/Ombre-Brain
UNIT=/etc/systemd/system/ombre-apibot.service
STAMP=$(date -u +%Y%m%d-%H%M%S)
SAFE=/root/ombre-telegram-switch-$STAMP
SWITCHED=0

if [ "$(id -u)" -ne 0 ] || [ "$PWD" != "$REPO" ]; then
    echo "Run as root from $REPO"
    exit 1
fi

mkdir -m 700 "$SAFE"
cp -a "$UNIT" "$SAFE/ombre-apibot.service"
cp -a "$OLD/.env.apibot" "$SAFE/root.env.apibot"
cp -a "$OLD/telegram_state.json" "$SAFE/root.telegram_state.json"
if [ -f "$REPO/.env.apibot" ]; then
    cp -a "$REPO/.env.apibot" "$SAFE/main.env.apibot"
fi
if [ -f "$REPO/buckets/telegram_state.json" ]; then
    cp -a "$REPO/buckets/telegram_state.json" "$SAFE/main.telegram_state.json"
fi
if [ -f "$REPO/buckets/adhd_manage_state.json" ]; then
    cp -a "$REPO/buckets/adhd_manage_state.json" "$SAFE/adhd_manage_state.json"
fi
git rev-parse HEAD > "$SAFE/main.sha"
git -C "$OLD" rev-parse HEAD > "$SAFE/old.sha"
chmod -R go-rwx "$SAFE"

rollback_bot() {
    if [ "$SWITCHED" -eq 1 ]; then
        echo "Deployment failed; restoring the old Telegram unit."
        cp -a "$SAFE/ombre-apibot.service" "$UNIT"
        systemctl daemon-reload
    fi
    systemctl start ombre-brain.service || true
    systemctl restart ombre-apibot.service || true
}
trap rollback_bot ERR

git fetch origin main --quiet
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
.venv/bin/python -m pytest -q tests/test_chat_store.py tests/test_adhd_manager.py
.venv/bin/python -m compileall -q server.py telegram_bot.py chat_store.py adhd_manager.py
bash -n setup-apibot.sh
git diff --exit-code
git diff --cached --exit-code

systemctl stop ombre-apibot.service

OLD_STATE="$OLD/telegram_state.json" NEW_STATE="$REPO/buckets/telegram_state.json" \
    .venv/bin/python - <<'PY'
import json
import os
import pwd
import shutil
import tempfile

old_path = os.environ["OLD_STATE"]
new_path = os.environ["NEW_STATE"]

with open(old_path, encoding="utf-8") as handle:
    old = json.load(handle)
try:
    with open(new_path, encoding="utf-8") as handle:
        new = json.load(handle)
except FileNotFoundError:
    new = {}


def unique(left, right):
    result = []
    seen = set()
    for item in list(left or []) + list(right or []):
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def merge_list_maps(left, right):
    return {
        key: unique((left or {}).get(key, []), (right or {}).get(key, []))
        for key in set(left or {}) | set(right or {})
    }


def numeric_max(left, right):
    result = dict(left or {})
    for key, value in (right or {}).items():
        result[key] = max(result.get(key, value), value)
    return result


merged = dict(new)
merged["histories"] = merge_list_maps(new.get("histories"), old.get("histories"))
merged["todos"] = merge_list_maps(new.get("todos"), old.get("todos"))
merged["last_user_ts"] = numeric_max(new.get("last_user_ts"), old.get("last_user_ts"))
merged["nudge_count"] = numeric_max(new.get("nudge_count"), old.get("nudge_count"))
merged["voice_mode"] = {**new.get("voice_mode", {}), **old.get("voice_mode", {})}
for key, value in old.items():
    merged.setdefault(key, value)

for field in ("histories", "todos"):
    for source in (new, old):
        for key, values in source.get(field, {}).items():
            assert all(item in merged[field].get(key, []) for item in values)
for field in ("last_user_ts", "nudge_count"):
    for source in (new, old):
        for key, value in source.get(field, {}).items():
            assert merged[field].get(key, value) >= value

folder = os.path.dirname(new_path)
fd, temporary = tempfile.mkstemp(prefix=".telegram-state-", dir=folder)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(merged, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, new_path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)

account = pwd.getpwnam("ombre")
os.chown(new_path, account.pw_uid, account.pw_gid)
os.chmod(new_path, 0o600)
PY

cp -a "$OLD/.env.apibot" "$REPO/.env.apibot"
chown ombre:ombre "$REPO/.env.apibot"
chmod 600 "$REPO/.env.apibot"
cmp -s "$OLD/.env.apibot" "$REPO/.env.apibot"

systemctl restart ombre-brain.service
.venv/bin/python - <<'PY'
import json
import time
import urllib.request

for attempt in range(30):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/version", timeout=2) as response:
            data = json.load(response)
        if data.get("version") == "v5.3":
            print("Brain version:", data["version"])
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit("brain health/version check failed")
PY

SWITCHED=1
bash setup-apibot.sh >/tmp/ombre-apibot-setup.log

test "$(systemctl is-active ombre-brain.service)" = active
test "$(systemctl is-active ombre-apibot.service)" = active
test "$(systemctl is-active cc-bridge.service)" = active
systemctl show -p ExecStart --value ombre-apibot.service | grep -Fq "$REPO/.venv/bin/python"
systemctl show -p ExecStart --value ombre-apibot.service | grep -Fq "$REPO/telegram_bot.py"
systemctl show -p EnvironmentFiles --value ombre-apibot.service | grep -Fq "$REPO/.env.apibot"
grep -Eq '^ALLOWED_CHAT_IDS=[0-9]+(,[0-9]+)*$' "$REPO/.env.apibot"

trap - ERR
echo "DEPLOY-PASS"
echo "Backup: $SAFE"
echo "Version: v5.3"
echo "Services: ombre-brain=active ombre-apibot=active cc-bridge=active"
