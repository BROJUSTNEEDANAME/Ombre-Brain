#!/bin/bash
# claude.ai 的连接器连不上你的 MCP 服务器时，先跑这个。
#   bash scripts/mcp-connector-check.sh '<连接器里那个完整 URL>'
#
# ⚠️ 必须在**能访问那个地址的机器上**跑（你的 Mac，或者那台 VPS 自己）。
# 我在 Anthropic 的容器里跑，出不去你的 tailnet——我那边测到的 403 是我自己
# 代理拦的，不是你服务器说的。差点就报错给你了。
#
# 为什么会有这个脚本：claude.ai 报「Couldn't register with ombre brain's
# sign-in service」，意思是它在跟你的服务器做 OAuth 动态注册。而你的服务器
# 用的是 FastMCP 的 streamable_http_app()，**一个 auth 端点都没有**。
# 那它为什么要去注册？多半是探测「有没有登录系统」时收到了不干净的回答。
#
# 规矩同 cc-status：不知道的时候必须说不知道。连不上就是连不上，不猜。
set -u
URL="${1:-}"
[ -z "$URL" ] && { echo "用法：bash $0 '<连接器里那个完整 URL>'"; exit 2; }

ORIGIN=$(printf '%s' "$URL" | sed -E 's#^(https?://[^/]+).*#\1#')
PATH_PART=$(printf '%s' "$URL" | sed -E 's#^https?://[^/]+##')

# 三态：拿到状态码 / 连不上 / 没装 curl
# ⚠️ 第一版让 probe 既打印人话又用 stdout 返回状态码，两个搅在一起——
# 自测时「连不上」被报成了「意外的状态码」，正是这个脚本要防的那件事。
# 现在人话直接打印，状态码走全局变量 CODE，各走各的。
CODE=""
probe() {   # $1=说明 $2=url  [$3...=额外 curl 参数]
    local desc="$1" u="$2"; shift 2
    CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$@" "$u" 2>/dev/null)
    if [ -z "$CODE" ] || [ "$CODE" = "000" ]; then
        CODE=""
        echo "  ❓ $desc → 连不上（DNS？没在 tailnet 里？服务没起？）"
    else
        echo "  $CODE $desc"
    fi
}

command -v curl >/dev/null || { echo "❌ 没有 curl，装一个再来"; exit 2; }

echo "── 目标 ──"
echo "  主机：$ORIGIN"
echo "  路径：$PATH_PART"

echo ""
echo "── 一、MCP 端点本身通不通 ──"
BODY='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"0"}}}'
probe "POST 未鉴权 initialize" "$URL" \
    -X POST -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -d "$BODY"
echo ""
case "$CODE" in
    200|202) echo "  ✅ 服务器直接接受了未鉴权请求——它是 authless 的，本来就不该走 OAuth。" ;;
    401|403) echo "  ⚠️ 服务器要鉴权。claude.ai 看到这个就会去找登录系统，然后注册失败。" ;;
    404)     echo "  ⚠️ 404：路径不对，或者 Tailscale 没把这个前缀映射到服务上。" ;;
    "")      echo "  ❓ 连不上，所以这一整个脚本测不出任何结论——"
             echo "     先确认这台机器能不能访问 $ORIGIN（在 tailnet 里吗？服务起着吗？）" ;;
    *)       echo "  ⚠️ 意外的状态码，把它连同下面几行一起发给我。" ;;
esac

echo ""
echo "── 二、OAuth 发现探到了什么（问题多半在这儿）──"
echo "  claude.ai 会去主机**根路径**找这两个。干净的 404 才是对的；"
echo "  返回 200 或者跳转，它就以为「这台服务器有登录系统」。"
probe "根 /.well-known/oauth-authorization-server"\
      "$ORIGIN/.well-known/oauth-authorization-server"
probe "根 /.well-known/openid-configuration"\
      "$ORIGIN/.well-known/openid-configuration"
probe "资源元数据（带路径）"\
      "$ORIGIN/.well-known/oauth-protected-resource$PATH_PART"
echo ""
echo "  读法：这三行是 404 或连不上 = 正常；出现 200/301/302 = 找到病根了。"

echo ""
echo "── 三、根路径是谁在应答 ──"
probe "GET $ORIGIN/" "$ORIGIN/"
echo "  （如果根路径有东西在应答，而你只想暴露那个秘密路径，"
echo "    那 Tailscale 的映射范围就比你以为的大。）"
