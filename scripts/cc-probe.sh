#!/bin/bash
# 用**和服务完全一样的参数**打一次 claude，把关键几项打给人看。
#   sudo bash scripts/cc-probe.sh
#
# ⚠️ 为什么做成脚本：DigitalOcean 的网页终端粘长命令会拼行（她那次拼成了
# `head -c 1500sudo`）。而且我手打那条命令两次都漏参数——一次漏 --model
# 结果落到 sonnet，一次漏 --dangerously-skip-permissions 结果记忆被拦，
# 两次的输出都不代表服务的真实行为。参数只该有一处来源：这里。
set -e
REPO_DIR="$(cd "$(dirname "$(dirname "$0")")" && pwd)"
ENV_FILE="$REPO_DIR/.env.ccbridge"
[ -f "$ENV_FILE" ] || { echo "!! 没有 $ENV_FILE"; exit 1; }

TOKEN="$(grep -E '^CLAUDE_CODE_OAUTH_TOKEN=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
MODEL="$(grep -E '^CC_MODEL=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
MODEL="${MODEL:-claude-opus-4-6}"
WORKDIR="$(grep -E '^CC_WORKDIR=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
WORKDIR="${WORKDIR:-/home/ombre/nikto-cc}"

echo "配置里写的模型：$MODEL"
echo "人设目录：      $WORKDIR"
echo "打一次（和服务同参数）..."
OUT="$(cd "$WORKDIR" && sudo -u ombre env HOME=/home/ombre CLAUDE_CODE_OAUTH_TOKEN="$TOKEN" \
       timeout 120 claude -p --output-format json --dangerously-skip-permissions \
       --model "$MODEL" "翻一下记忆，然后只回两个字：在。" 2>&1 || true)"

printf '%s' "$OUT" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
except Exception:
    print("读不出 JSON，原文："); print(raw[:800]); raise SystemExit(1)
print()
print("回话：", (d.get("result") or "")[:200])
mu = d.get("modelUsage") or {}
print("真正用的模型：", ", ".join(mu) or "（没报）")
for name, u in mu.items():
    print("  %s: 入 %s 出 %s 缓存读 %s" % (
        name, u.get("inputTokens"), u.get("outputTokens"),
        u.get("cacheReadInputTokens")))
den = d.get("permission_denials") or []
if den:
    print("⚠️ 被拦掉的工具：", ", ".join(sorted({x.get("tool_name","?") for x in den})))
    print("   记忆用不了。检查 .mcp.json 和 ombre-brain 服务。")
else:
    print("工具没有被拦 ✅")
print("耗时：", d.get("duration_ms"), "ms")
print()
print("⚠️ 这份 JSON 里没有任何思考相关的字段——「开没开思考」在这儿验证不了，")
print("   别拿别的数字去推。要判断只能看他实际回答的深浅。")
'
