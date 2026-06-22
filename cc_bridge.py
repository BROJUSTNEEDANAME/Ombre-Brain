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

from telegram import Update
from telegram.constants import ChatAction
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
    cmd = ["claude", "-p", "--output-format", "json", "--dangerously-skip-permissions"]
    if session_id:
        cmd += ["--resume", session_id]
    cmd.append(message)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=CC_WORKDIR,
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
        logger.error("claude 退出码 %s: %s", proc.returncode, err.decode()[:800])
        return "（断了一下，再说一遍。）", session_id

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

    await context.bot.send_chat_action(chat_id=cid, action=ChatAction.TYPING)
    reply, sid = await run_cc(update.message.text, sessions.get(cid))
    if sid:
        sessions[cid] = sid
    for i in range(0, len(reply), TELEGRAM_MSG_LIMIT):
        await update.message.reply_text(reply[i : i + TELEGRAM_MSG_LIMIT])


def main() -> None:
    app: Application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    logger.info("Claude Code Telegram 桥启动 | workdir=%s", CC_WORKDIR)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
