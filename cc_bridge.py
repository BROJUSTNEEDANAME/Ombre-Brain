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


def _split_for_telegram(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """把长回复切成 <=limit 的多段。尽量在段落/换行/句末标点处断开，
    避免长剧情被拦腰截断，读起来更顺。实在找不到断点才硬切。（找回自 2c6b494）"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    seps = ("\n\n", "\n", "。", "！", "？", "…", "”", ". ", "! ", "? ")
    while len(rest) > limit:
        window = rest[:limit]
        cut = -1
        for sep in seps:
            idx = window.rfind(sep)
            if idx > limit * 0.5:  # 断点别太靠前，否则切得太碎
                cut = idx + len(sep)
                break
        if cut <= 0:
            cut = limit  # 没有合适的自然断点，硬切
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


async def _reply_with_retry(message, text: str, retries: int = 3) -> None:
    """发一条消息，遇到网络/超时失败就重试几次，别让长回复中途丢。"""
    for attempt in range(retries):
        try:
            await message.reply_text(text)
            return
        except (TelegramError, asyncio.TimeoutError) as e:
            if attempt == retries - 1:
                logger.warning("发送失败（已重试 %s 次）: %s", retries, e)
                return
            await asyncio.sleep(1.5 * (attempt + 1))


async def _respond(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   cid: int, message: str) -> None:
    """跑一次 cc 并把回复（可能很长）分段发回。文字和图片消息共用。"""
    try:
        await context.bot.send_chat_action(chat_id=cid, action=ChatAction.TYPING)
    except Exception:  # noqa: BLE001
        pass  # typing 指示器失败不影响正事
    reply, sid = await run_cc(message, sessions.get(cid))
    if sid:
        sessions[cid] = sid
    for chunk in _split_for_telegram(reply):
        await _reply_with_retry(update.message, chunk)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    if not ALLOWED_CHAT_IDS:
        await update.message.reply_text(
            f"还没锁定使用者。你的 chat id 是 {cid}，填进 ALLOWED_CHAT_IDS 再来聊。"
        )
        return
    if cid not in ALLOWED_CHAT_IDS:
        return
    await _respond(update, context, cid, update.message.text)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """收到图片：下载下来，让 cc 用 Read 工具看图后回应。带配文一起传。（找回自 2c6b494）"""
    cid = update.effective_chat.id
    if not ALLOWED_CHAT_IDS:
        await update.message.reply_text(
            f"还没锁定使用者。你的 chat id 是 {cid}，填进 ALLOWED_CHAT_IDS 再来聊。"
        )
        return
    if cid not in ALLOWED_CHAT_IDS:
        return

    photo = update.message.photo[-1]  # 最大尺寸那张
    img_dir = os.path.join(CC_WORKDIR, ".tg_images")
    os.makedirs(img_dir, exist_ok=True)
    path = os.path.join(img_dir, f"{photo.file_unique_id}.jpg")
    try:
        tg_file = await context.bot.get_file(photo.file_id)
        await tg_file.download_to_drive(path)
    except Exception:  # noqa: BLE001
        logger.exception("下载图片失败")
        await update.message.reply_text("（图片没收着，再发一次。）")
        return

    caption = (update.message.caption or "").strip()
    msg = (
        f"[闪闪发来一张图片，已保存在：{path}。"
        f"请用 Read 工具打开看这张图，然后自然地回应她，别念文件路径。]"
    )
    if caption:
        msg += f"\n她的配文：{caption}"
    await _respond(update, context, cid, msg)


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
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    logger.info("Claude Code Telegram 桥启动 | workdir=%s", CC_WORKDIR)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
