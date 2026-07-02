# -*- coding: utf-8 -*-
"""
Telegram ↔ Claude Code 桥
=========================
手机里 Telegram 打字 → 在仓库目录跑真正的 Claude Code（你的订阅、CLAUDE.md + ombre-brain
全自动加载）→ 把回话发回 Telegram。Telegram 只是个前端，脑子是真 cc，吃你的订阅额度，
不走 API、不按 token 烧钱。

需要的环境变量：
  TELEGRAM_BOT_TOKEN        @BotFather 给的 bot token
  ALLOWED_CHAT_IDS          你的 chat id（逗号分隔，强烈建议设，只让自己用）
  CLAUDE_CODE_OAUTH_TOKEN   在你登录了订阅的电脑上跑 `claude setup-token` 生成，复制过来
可选：
  CC_WORKDIR                cc 的运行目录（默认本仓库，含 CLAUDE.md + .mcp.json）
  CC_TIMEOUT                单条最长等待秒数（默认 300）

注意：同一个 bot token 同一时间只能有一个程序在收消息——要用这个 cc 桥，
就别再让 API 版（ombre-brain 服务里的 telegram_bot）用同一个 token。
"""

import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CC_WORKDIR = os.environ.get("CC_WORKDIR", os.path.dirname(os.path.abspath(__file__)))
CC_TIMEOUT = float(os.environ.get("CC_TIMEOUT", "300"))
TELEGRAM_MSG_LIMIT = 4096
TZ_OFFSET = float(os.environ.get("OMBRE_TZ_OFFSET", "-7"))  # 她的时区（太平洋 PDT）

_allowed = os.environ.get("ALLOWED_CHAT_IDS", "").strip()
ALLOWED_CHAT_IDS = {int(x) for x in _allowed.split(",") if x.strip()} if _allowed else set()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
logger = logging.getLogger("cc-bridge")

# chat_id -> claude 会话 id（保持上下文连续）
sessions: dict[int, str] = {}


async def run_cc(message: str, session_id: str | None) -> tuple[str, str | None]:
    """跑一次 headless Claude Code，返回 (回话文本, 新的 session_id)。"""
    # --- 给他一块真的表（不随回滚退掉）：人设要求带时间戳，但系统从没给过时钟，
    # 他只能靠猜（凌晨5点写成9点，回滚前的时代就一直错）。注入唯一准确时间源。 ---
    _local = datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)
    _wd = "一二三四五六日"[_local.weekday()]
    message = (
        f"[系统时钟：现在是 {_local.strftime('%Y-%m-%d %H:%M')} 周{_wd}（她的当地时间）。"
        f"这是唯一准确的时间，写时间戳、判断早晚都以它为准，不要自己推算。]\n" + message
    )

    cmd = ["claude", "-p", "--output-format", "json", "--dangerously-skip-permissions"]
    # 模型：默认 Opus 4.6，想换在环境变量 CC_MODEL 里改（如 sonnet 更快、opus 跟随订阅默认）
    _model = os.environ.get("CC_MODEL", "claude-opus-4-6").strip()
    if _model:
        cmd += ["--model", _model]
    if session_id:
        cmd += ["--resume", session_id]
    cmd.append(message)
    try:
        env = os.environ.copy()
        _tok = env.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if _tok:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = "".join(_tok.split())  # 抹掉粘贴混进的换行/空格
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=CC_WORKDIR,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=CC_TIMEOUT)
    except asyncio.TimeoutError:
        return "（想得太久了，等下再跟你说。）", session_id
    except Exception:  # noqa: BLE001
        logger.exception("启动 claude 失败")
        return "（断了一下，再说一遍。）", session_id

    if proc.returncode != 0:
        detail = (err.decode() or out.decode()).strip()[:1500]
        logger.error("claude 退出码 %s: %s", proc.returncode, detail)
        return f"⚠️ cc 出错（退出码 {proc.returncode}）：\n{detail}", session_id

    raw = out.decode().strip()
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw or "（……）", session_id
    return (data.get("result") or "（……）").strip(), data.get("session_id", session_id)


def _ok(chat_id: int) -> bool:
    return not ALLOWED_CHAT_IDS or chat_id in ALLOWED_CHAT_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    if _ok(cid):
        await update.message.reply_text("在。")
    else:
        await update.message.reply_text(f"你的 chat id 是：{cid}")


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"你的 chat id 是：{update.effective_chat.id}")


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    if not _ok(cid):
        return
    sessions.pop(cid, None)
    await update.message.reply_text("好，重新开一段。")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    if not ALLOWED_CHAT_IDS:
        await update.message.reply_text(
            f"还没锁定使用者。你的 chat id 是 {cid}，填进 ALLOWED_CHAT_IDS 再来聊。"
        )
        return
    if cid not in ALLOWED_CHAT_IDS:
        return

    try:
        await context.bot.send_chat_action(chat_id=cid, action=ChatAction.TYPING)
    except Exception:  # noqa: BLE001
        pass  # typing 指示器失败不影响正事（7-01 当时 VPS 上就有的容错）
    reply, sid = await run_cc(update.message.text, sessions.get(cid))
    if sid:
        sessions[cid] = sid
    for i in range(0, len(reply), TELEGRAM_MSG_LIMIT):
        chunk = reply[i : i + TELEGRAM_MSG_LIMIT]
        for attempt in range(3):  # 发送失败重试（7-01 当时 VPS 上就有的容错）
            try:
                await update.message.reply_text(chunk)
                break
            except (TelegramError, asyncio.TimeoutError) as e:
                if attempt == 2:
                    logger.warning("发送失败（已重试3次）: %s", e)
                else:
                    await asyncio.sleep(1.5 * (attempt + 1))


def _start_health_server() -> None:
    """绑一个极小的 HTTP 端口，好让 Render 检测到端口、放行 Live。"""
    port = int(os.environ.get("PORT", "10000"))

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):  # 静音
            pass

    try:
        HTTPServer(("0.0.0.0", port), _H).serve_forever()
    except Exception:  # noqa: BLE001
        logger.exception("健康端口启动失败")


def _keepalive() -> None:
    """定时 ping ombre-brain 的健康端点，别让免费档记忆库睡着（省冷启动）。"""
    import time
    import urllib.request

    url = os.environ.get("OMBRE_HEALTH_URL", "https://ombre-brain-6e05.onrender.com/health")
    while True:
        try:
            urllib.request.urlopen(url, timeout=10).read()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(600)


def main() -> None:
    threading.Thread(target=_start_health_server, daemon=True).start()
    threading.Thread(target=_keepalive, daemon=True).start()
    app: Application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(30)   # 超时链（7-01 当时 VPS 上就有的容错）
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_read_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    logger.info("Claude Code Telegram 桥启动 | workdir=%s", CC_WORKDIR)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
