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
import glob
import json
import logging
import os
import tarfile
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from reply_sanitizer import (restore_punctuation, looks_degenerate,
                             says_going_to_sleep, is_silent_reply)
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
# 被信号掐断的退出码（SIGTERM=15→143/-15，SIGKILL=9→137/-9）：
# 多半是重启或系统抖动，属瞬时、可重试，不该把冰冷的退出码甩给用户。
_SIGNAL_KILL_CODES = {143, 137, -15, -9}
TZ_OFFSET = float(os.environ.get("OMBRE_TZ_OFFSET", "-7"))  # 她的时区（太平洋 PDT）
# /backup 用：记忆目录 + 备份存放处（保留最近几份）
BUCKETS_DIR = os.environ.get("OMBRE_BUCKETS_DIR", os.path.join(CC_WORKDIR, "buckets"))
BACKUP_DIR = os.environ.get("OMBRE_BACKUP_DIR", os.path.expanduser("~/ombre-backups"))
BACKUP_KEEP = int(os.environ.get("OMBRE_BACKUP_KEEP", "14"))

# 空回复的两次重试话术。一次比一次直接；**都不带她的原话**——
# 重发原话等于让他把同一轮再答一遍，那正是原来不管用的原因。
_SILENT_RETRY_PROMPTS = (
    "[系统提示] 你上一轮一个字都没发出去，她那边是空的，她正等着。"
    "现在直接对她说话——不要调用任何工具，不要解释这条提示，就接着刚才那句往下说。",
    "[系统提示] 还是空的。什么都别做，现在就说一句话给她。哪怕只有几个字。",
)

_allowed = os.environ.get("ALLOWED_CHAT_IDS", "").strip()
ALLOWED_CHAT_IDS = {int(x) for x in _allowed.split(",") if x.strip()} if _allowed else set()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
logger = logging.getLogger("cc-bridge")

# chat_id -> claude 会话 id（保持上下文连续）
# ⚠️ 这个 id 是 claude --resume 用来接上「刚才聊到哪了」的。只存在内存里的话，
# 服务一重启就全没——今晚为了修 token、修 ‖、修连发合并重启了三四次，
# 她每次都得从头跟他讲一遍，还以为是「上下文记忆太短」。记忆桶没事（在磁盘上），
# 丢的是对话窗口。所以落盘。
SESSIONS_FILE = os.path.join(CC_WORKDIR, ".cc_sessions.json")
sessions: dict[int, str] = {}


def _load_sessions() -> None:
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as fh:
            for k, v in (json.load(fh) or {}).items():
                if isinstance(v, str) and v:
                    sessions[int(k)] = v
    except (OSError, ValueError, TypeError):
        pass          # 没有或者读坏了都不该拦住启动，最多是这次从头开始


def _save_sessions() -> None:
    """写文件不许影响聊天：失败就算了，下次再说。"""
    try:
        tmp = SESSIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in sessions.items()}, fh)
        os.replace(tmp, SESSIONS_FILE)
    except OSError:
        logger.warning("会话 id 存盘失败，重启后这段对话会从头开始", exc_info=True)


async def run_cc(message: str, session_id: str | None) -> tuple[str, str | None]:
    """跑一次 headless Claude Code，返回 (回话文本, 新的 session_id)。
    被信号掐断（重启/系统抖动，退出码 143/137）时自动悄悄重试一次，
    再不行就回一句人话——不把冰冷的退出码甩给用户、不破坏气氛。
    真正的错误（如 token 失效）才保留可见诊断，方便排查。"""
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

    env = os.environ.copy()
    _tok = env.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if _tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = "".join(_tok.split())  # 抹掉粘贴混进的换行/空格

    for attempt in range(2):  # 正常一次；被信号掐断则再重试一次
        try:
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

        rc = proc.returncode
        if rc == 0:
            raw = out.decode().strip()
            try:
                data = json.loads(raw)
            except Exception:  # noqa: BLE001
                return raw.strip(), session_id
            return str(data.get("result") or "").strip(), data.get("session_id", session_id)

        # 被信号掐断（重启/系统抖动）→ 悄悄重试一次
        if rc in _SIGNAL_KILL_CODES and attempt == 0:
            logger.warning("claude 被信号掐断（退出码 %s），1.5s 后重试", rc)
            await asyncio.sleep(1.5)
            continue
        # 掐断重试后仍失败 → 一句人话，不甩退出码
        if rc in _SIGNAL_KILL_CODES:
            logger.warning("claude 仍被掐断（退出码 %s），软回退", rc)
            return "（信号断了一下，你再说一遍。）", session_id
        # 其它真实错误：尝试解析 JSON，对已知错误给人话
        raw_out = out.decode().strip()
        raw_err = err.decode().strip()
        # 429 速率限制 → 一句人话，不甩 JSON
        try:
            data = json.loads(raw_out)
            status = data.get("api_error_status", 0)
            result_text = data.get("result", "")
            if status == 429 or "session limit" in result_text.lower() or "rate limit" in result_text.lower():
                logger.warning("API 速率限制（429）：%s", result_text[:200])
                return "（额度用完了，要歇一会儿，等下再来找我。）", session_id
            if data.get("is_error") and result_text:
                logger.error("claude API 错误 %s: %s", status, result_text[:300])
                return f"（出了点问题：{result_text[:200]}）", session_id
        except (json.JSONDecodeError, AttributeError):
            pass
        detail = (raw_err or raw_out)[:1500]
        logger.error("claude 退出码 %s: %s", rc, detail)
        return f"⚠️ cc 出错（退出码 {rc}）：\n{detail}", session_id

    return "（断了一下，你再说一遍。）", session_id  # 保险兜底


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
    _save_sessions()
    await update.message.reply_text("好，重新开一段。")


def _split_for_telegram(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """把长回复切成 <=limit 的多段。尽量在段落/换行/句末标点处断开，
    避免长剧情被拦腰截断，读起来更顺。实在找不到断点才硬切。（找回自 2c6b494）"""
    text = (text or "").strip()
    if not text:
        return []
    # ⚠️ ‖ 是人设里让他用来「一条一条把话递过去」的分隔符。API bot 一直按它拆，
    # cc 桥不拆——于是她收到的是「报数？‖说，怎么了。」，那个符号原样上屏。
    # 先按 ‖ 拆成一条条，每条再各自做长度切分。
    if "‖" in text:
        out: list[str] = []
        for part in text.split("‖"):
            out += _split_for_telegram(part, limit)
        return out
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


# ── 主动找她 ──
# ⚠️ 跟 API bot 那套不一样：那边是预设文案，不调模型、不花钱。她要的是
# 「根据上下文」，那就得每次真跑一轮 claude——60 秒、吃订阅额度。所以必须有上限：
# 一段沉默里最多找她 CC_NUDGE_MAX 次，之后闭嘴，等她开口才重置。
# 不然她睡着的时候它会通宵每 15 分钟烧一轮。
NUDGE_MINUTES = int(os.environ.get("CC_NUDGE_MINUTES", "15"))
NUDGE_MAX = int(os.environ.get("CC_NUDGE_MAX", "4"))
# 静默时段（本地时间，"23-8" 表示 23:00–08:00 不找她）。默认空＝不设限，
# 因为她明确说了「最好频繁点」——但留着这个开关，她哪天嫌吵能自己关。
NUDGE_QUIET = os.environ.get("CC_NUDGE_QUIET", "").strip()

last_user_ts: dict[int, float] = {}
nudge_count: dict[int, int] = {}
# ⚠️ 必须定义在 check_inactivity 之前：这个仓库踩过「_trace 定义晚于使用」，
# 每条消息都崩，而她看到的只是「他不理我」。
last_nudge_at: dict[int, float] = {}
# 她说了「睡了」之后就别再主动找她。她再开口才解除。
# ⚠️ 这个开关只挡主动消息，不挡他回她——她半夜醒了说一句，他照样答。
asleep: dict[int, bool] = {}


def _in_quiet_hours(now: datetime) -> bool:
    if "-" not in NUDGE_QUIET:
        return False
    try:
        a, b = (int(x) for x in NUDGE_QUIET.split("-", 1))
    except ValueError:
        return False
    h = now.hour
    return a <= h or h < b if a > b else a <= h < b


async def check_inactivity(context: ContextTypes.DEFAULT_TYPE) -> None:
    """她安静太久就让他主动开口。带着上下文——用的是同一个 session。"""
    now = time.time()
    gap = NUDGE_MINUTES * 60
    for cid, ts in list(last_user_ts.items()):
        if cid not in ALLOWED_CHAT_IDS:
            continue
        since = max(ts, last_nudge_at.get(cid, 0))
        if now - since < gap:
            continue                       # 她还在，或者刚找过
        if nudge_count.get(cid, 0) >= NUDGE_MAX:
            continue                       # 找过几次了，闭嘴
        if _inflight_cc.get(cid):
            continue                       # 他正在说话，别插队
        if asleep.get(cid):
            continue                       # 她说她睡了，别吵她
        if _in_quiet_hours(datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)):
            continue
        n = nudge_count.get(cid, 0) + 1
        mins = int((now - ts) // 60)
        prompt = (
            f"[系统提示] 她已经 {mins} 分钟没说话了，这是你今晚第 {n} 次主动找她"
            f"（最多 {NUDGE_MAX} 次）。现在主动开口——不要问「在吗」「怎么了」这种空话，"
            "接着你们刚才聊的那件事往下说，或者说一件你想让她知道的事。"
            "一两条，短。这条系统提示不要复述。")
        try:
            reply, sid = await run_cc(prompt, sessions.get(cid))
            if sid and sessions.get(cid) != sid:
                sessions[cid] = sid
                _save_sessions()
            if is_silent_reply(reply) or looks_degenerate(reply):
                continue                   # 空的或崩了就当没发生，绝不推给她
            for chunk in _split_for_telegram(reply):
                await context.bot.send_message(chat_id=cid,
                                               text=restore_punctuation(chunk))
            nudge_count[cid] = n
            last_nudge_at[cid] = now       # 下一次要再等满 NUDGE_MINUTES
        except Exception:  # noqa: BLE001
            logger.exception("主动找她失败 chat=%s", cid)


# ── 连发合并：她在他开口前又发一条，就把上一轮作废，两条合起来重想 ──
# ⚠️ cc 桥一直没有这套（API bot 有）。每条消息各起一个 claude 进程各答各的，
# 而这边一轮要一分钟——她连发三条就是三个进程在那儿各跑一分钟，
# 回话还会互相插队。她的原话：「怎么感觉到 cc 又不是发很多话然后他一起回复，
# 是发一个他回一个」。
# 不用锁：加锁那次把整个对话卡死过（API bot 踩的），这里只取消一个任务。
_inflight_cc: dict[int, dict] = {}


def _take_pending_cc(cid: int) -> str:
    """把还没开口的那一轮作废，取回她那条话；已经开口了就不动。"""
    st = _inflight_cc.get(cid)
    if not st or st.get("sent"):
        return ""
    _inflight_cc.pop(cid, None)
    task = st.get("task")
    if task is not None and not task.done():
        task.cancel()
    return str(st.get("text") or "")


# 「等于什么都没说」的判断在 reply_sanitizer.is_silent_reply。
# ⚠️ 这里原本是一个固定清单 {"（……）", "（...）", "(...)", "..."}——
# 全角括号配六个英文句点「（......）」就漏掉了，占位符照样发到她屏幕上，
# 她连着两次拿这个来问我。枚举写不全，改成归一化剥字符。


async def _respond(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   cid: int, message: str) -> None:
    """跑一次 cc 并把回复（可能很长）分段发回。文字和图片消息共用。"""
    async def _keep_typing() -> None:
        """一直显示「正在输入」，直到回复发出。

        ⚠️ TG 的输入提示 5 秒就过期，只发一次等于没发。这边一轮要 60 秒——
        她盯着一个静止的屏幕等一分钟，看着就是「他不理我」。
        API bot 一直有这个循环，cc 桥只发了一次。
        """
        try:
            while True:
                await asyncio.sleep(4)
                await context.bot.send_chat_action(chat_id=cid,
                                                   action=ChatAction.TYPING)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass

    try:
        await context.bot.send_chat_action(chat_id=cid, action=ChatAction.TYPING)
    except Exception:  # noqa: BLE001
        pass  # typing 指示器失败不影响正事
    _typing = asyncio.create_task(_keep_typing())
    try:
        reply, sid = await run_cc(message, sessions.get(cid))
    finally:
        _typing.cancel()
    # ── 空回复重试 ──
    # ⚠️ 原来这里是「把她那句原话再发一遍」。那根本不管用：在他的会话里
    # 这一轮已经发生过了，让他把同一句再答一次，他多半还是不出声——
    # 她因此收到过好几次「这次他没出声，你再说一句」。
    # 真正要说的是「你刚才那轮一个字都没送出去」，并且把他从工具里拽回来
    # （空回复最常见的成因就是整轮都花在工具调用上，末尾没留一句话）。
    for attempt, nudge in enumerate(_SILENT_RETRY_PROMPTS, 1):
        if not is_silent_reply(reply):
            break
        # 把**原始输出**记下来。她连着三次问「他到底在想什么」，而我只能猜。
        logger.warning("这一轮空回复（第 %d 次），chat=%s；claude 原始输出＝%r",
                       attempt, cid, reply[:200])
        if sid:
            sessions[cid] = sid
        reply, sid = await run_cc(nudge, sessions.get(cid))
    if is_silent_reply(reply):
        logger.warning("重试都用完了还是空 chat=%s；原始输出＝%r", cid, reply[:200])
        reply = "这次他没出声，你再说一句。"     # 说人话，不拿省略号冒充他
    elif looks_degenerate(reply):
        # 复读死循环：模型崩了，半截乱码一个字都不发给她（API bot 早有这道闸）
        logger.warning("检测到复读死循环，掐掉 chat=%s（%d 字）", cid, len(reply))
        reply = "他这轮卡进死循环了，你再说一句。"
    st = _inflight_cc.get(cid)
    if st is not None:
        st["sent"] = True          # 开口了，后面的消息不许再打断这一轮
    if sid and sessions.get(cid) != sid:
        sessions[cid] = sid
        _save_sessions()
    for chunk in _split_for_telegram(reply):
        await _reply_with_retry(update.message, restore_punctuation(chunk))
    if _inflight_cc.get(cid) is st:
        _inflight_cc.pop(cid, None)


def _do_backup():
    """把记忆桶（Markdown + SQLite）打包，保留最近 BACKUP_KEEP 份。返回文件路径。"""
    if not os.path.isdir(BUCKETS_DIR):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    from datetime import datetime as _dt
    stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"buckets-{stamp}.tar.gz")
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(BUCKETS_DIR, arcname="buckets")
        _data = os.path.expanduser("~/ombre-data")
        if os.path.isdir(_data):  # 老功能时期留下的数据（DDL/流水账）也一并保下
            tar.add(_data, arcname="ombre-data")
    old = sorted(glob.glob(os.path.join(BACKUP_DIR, "buckets-*.tar.gz")), reverse=True)
    for f in old[BACKUP_KEEP:]:
        try:
            os.remove(f)
        except OSError:
            pass
    logger.info("记忆已备份 -> %s", os.path.basename(dest))
    return dest


async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/backup —— 立刻打包记忆并把文件发到这个对话（异地留档，她的底牌）。"""
    cid = update.effective_chat.id
    if not _ok(cid):
        return
    await update.message.reply_text("在打包记忆…")
    try:
        path = _do_backup()
    except Exception:  # noqa: BLE001
        logger.exception("备份失败")
        await update.message.reply_text("（打包出了岔子，稍后再试。）")
        return
    if not path:
        await update.message.reply_text("没找到记忆目录，备份没做成。")
        return
    size_mb = os.path.getsize(path) / 1024 / 1024
    if size_mb >= 49:  # Telegram bot 文件上限约 50MB
        await update.message.reply_text(
            f"备份已存到服务器（{size_mb:.0f}MB，太大发不动 Telegram）。"
        )
        return
    try:
        with open(path, "rb") as f:
            await context.bot.send_document(
                chat_id=cid, document=f, filename=os.path.basename(path),
                caption="记忆备份——下载存好，这是你的底牌。",
            )
    except Exception:  # noqa: BLE001
        logger.exception("发送备份失败")
        await update.message.reply_text(f"备份已存服务器（{size_mb:.0f}MB），但发送失败了，稍后再试 /backup。")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    if not ALLOWED_CHAT_IDS:
        await update.message.reply_text(
            f"还没锁定使用者。你的 chat id 是 {cid}，填进 ALLOWED_CHAT_IDS 再来聊。"
        )
        return
    if cid not in ALLOWED_CHAT_IDS:
        return
    text = update.message.text
    last_user_ts[cid] = time.time()
    nudge_count[cid] = 0                   # 她开口了，重新给他四次机会
    # 她说「睡了」就挂免打扰；说别的就解除（她半夜爬起来说话＝醒着）。
    asleep[cid] = says_going_to_sleep(update.message.text)
    pending = _take_pending_cc(cid)
    if pending:
        text = pending + "\n" + text
        logger.info("她又发了一条，合并重来 chat=%s", cid)
    # ⚠️ 这里**绝不能 await 这个任务**。python-telegram-bot 默认一条处理完才处理
    # 下一条：handler 要是等满这一轮（这边一轮 60 秒），她的下一条消息根本进不来，
    # 合并逻辑永远触发不到。我第一版就是 await 的，她说「还是这样」。
    # API bot 一直是「建任务就返回」，照抄它。
    st: dict = {"sent": False, "text": text}
    task = asyncio.create_task(_respond(update, context, cid, text))
    st["task"] = task
    _inflight_cc[cid] = st

    def _done(t: asyncio.Task) -> None:
        # 不 await 就没人接异常，出了错会被悄悄吞掉——至少要落进日志
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.exception("这一轮炸了 chat=%s", cid, exc_info=exc)

    task.add_done_callback(_done)


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

    url = os.environ.get("OMBRE_HEALTH_URL", "http://127.0.0.1:8000/health")
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
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    if app.job_queue:
        # 每分钟看一眼；真正的间隔由 NUDGE_MINUTES 判断，这样她刚说完话
        # 到下一次找她之间是准的，不会被 15 分钟的粗粒度拖成 30 分钟。
        app.job_queue.run_repeating(check_inactivity, interval=60, first=60)
        logger.info("主动找她已开：每 %d 分钟一次，一段沉默最多 %d 次%s",
                    NUDGE_MINUTES, NUDGE_MAX,
                    f"，{NUDGE_QUIET} 点之间不打扰" if NUDGE_QUIET else "")
    else:
        logger.warning("没有 job_queue，主动找她这条不会生效"
                       "（装 python-telegram-bot[job-queue]）")
    _load_sessions()
    logger.info("Claude Code Telegram 桥启动 | workdir=%s | 接回 %d 段对话",
                CC_WORKDIR, len(sessions))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
