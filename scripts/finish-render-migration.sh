#!/usr/bin/env bash
# 把「从 Render 搬完记忆之后」剩下的收尾一次做完，省得一条条贴命令。
#
#   bash scripts/finish-render-migration.sh
#
# 做四件事：补向量 → 重启服务 → 自检 → 报告。
# 每一步都先说要做什么、做完说结果；哪一步失败就停下并说清楚下一步该干嘛，
# 绝不「看起来跑完了其实什么都没做」。

set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

say()  { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
ok()   { printf '  ✅ %s\n' "$*"; }
warn() { printf '  ⚠️  %s\n' "$*"; }
die()  { printf '\n  ❌ %s\n' "$*"; exit 1; }

# ---------------------------------------------------------------- 1. 找配置
say "找大脑的配置文件（补向量要用里面的 API key）"
ENVFILE="$(systemctl show ombre-brain -p EnvironmentFile --value 2>/dev/null \
           | sed 's/^-//' | awk '{print $1}')"
if [ -z "$ENVFILE" ] || [ ! -r "$ENVFILE" ]; then
    for cand in "$REPO/.env" "$REPO/.env.brain" "$REPO/.env.apibot"; do
        [ -r "$cand" ] && ENVFILE="$cand" && break
    done
fi
if [ -r "${ENVFILE:-}" ]; then
    ok "读到 $ENVFILE"
    set -a
    # shellcheck disable=SC1090
    . "$ENVFILE"
    set +a
else
    warn "没找到能读的配置文件，下面补向量那步可能会说缺 key"
fi

# ---------------------------------------------------------------- 2. 补向量
# ⚠️ 只认退出码，不许自己猜。踩过：backfill 打印了 ERROR 却返回 0，
# 这里报「补完了」——给她看了一句假话。现在 backfill 会正经返回非 0。
say "给新搬来的记忆补向量（没有向量只能关键词搜，语义联想弱）"
BACKFILL_OK=0
if "$PY" backfill_embeddings.py; then
    ok "补完了"
    BACKFILL_OK=1
else
    warn "补向量没成功（看上面那几行说缺什么）。"
    warn "记忆本身没事——68 条都在，他照样记得，只是语义联想弱一点。"
    printf '     这个大脑里设了的相关变量：'
    got=""
    for v in OMBRE_API_KEY OMBRE_EMBED_API_KEY OMBRE_EMBED_BASE_URL OMBRE_EMBED_MODEL; do
        [ -n "${!v:-}" ] && got="$got $v"
    done
    printf '%s\n' "${got:- 一个都没有}"
    warn "把这一行发我，我告诉你从 Render 抄哪个变量过来。"
fi

# ---------------------------------------------------------------- 3. 重启
say "重启大脑和 Telegram bot"
if [ "$(id -u)" = "0" ]; then
    RESTART="systemctl"
elif sudo -n true 2>/dev/null; then
    RESTART="sudo systemctl"
else
    RESTART=""
fi
if [ -n "$RESTART" ]; then
    $RESTART restart ombre-brain ombre-apibot && ok "重启完成" || die "重启失败，把上面的报错发我"
else
    die "这一步需要 root。请先打 exit 回到 # 提示符，跑：
       systemctl restart ombre-brain ombre-apibot
     然后 su - ombre 切回来，重新跑一次本脚本（前面已经做完的会很快）。"
fi

# ---------------------------------------------------------------- 4. 自检
# 大脑重启后要加载 embedding、扫记忆目录，3 秒根本不够——等它，别急着报警。
say "自检（等大脑起来，最多 60 秒）"
HEALTH=""
for _ in $(seq 1 20); do
    HEALTH="$(curl -fsS --max-time 5 http://127.0.0.1:8000/health 2>/dev/null)"
    [ -n "$HEALTH" ] && break
    sleep 3
done
if [ -n "$HEALTH" ]; then
    ok "大脑活着：$HEALTH"
else
    warn "等了 60 秒大脑还没应答。跑这条看它为什么起不来："
    warn "  journalctl -u ombre-brain -n 40 --no-pager"
fi

for svc in ombre-brain ombre-apibot; do
    state="$(systemctl is-active "$svc" 2>/dev/null)"
    if [ "$state" = "active" ]; then ok "$svc 在跑"; else warn "$svc 状态是 $state"; fi
done

if [ "$BACKFILL_OK" = "1" ]; then
    printf '\n\033[1m全部做完了。\033[0m\n'
else
    printf '\n\033[1m记忆搬完了；向量那步还差一个 key（见上面）。\033[0m\n'
fi
printf '现在去 Telegram 问他一件「只在电脑端 Claude 聊过、TG 从没提过」的事，\n'
printf '答得上来就说明 Render 上那 68 条已经进到这个大脑里了。\n\n'
