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
  OMBRE_IDLE_HOURS          多久没聊他主动来找你（小时，默认 3；设 0 关闭）
  OMBRE_QUIET_START/END     安静时段（本地 24 小时制，默认 1~9 点不打扰）
  OMBRE_TZ_OFFSET           你的时区偏移（默认 -7，太平洋 PDT）

注意：同一个 bot token 同一时间只能有一个程序在收消息——要用这个 cc 桥，
就别再让 API 版（ombre-brain 服务里的 telegram_bot）用同一个 token。
"""

import asyncio
import glob
import json
import logging
import os
import tarfile
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

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

import morning  # 早安简报：Irvine 天气（Open-Meteo）+ 课表

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CC_WORKDIR = os.environ.get("CC_WORKDIR", os.path.dirname(os.path.abspath(__file__)))
CC_TIMEOUT = float(os.environ.get("CC_TIMEOUT", "300"))
TELEGRAM_MSG_LIMIT = 4096
# 被信号掐断的退出码（SIGTERM=15→143/-15，SIGKILL=9→137/-9）：
# 多半是重启或系统抖动，属瞬时、可重试，不该把冰冷的退出码甩给用户。
_SIGNAL_KILL_CODES = {143, 137, -15, -9}

_allowed = os.environ.get("ALLOWED_CHAT_IDS", "").strip()
ALLOWED_CHAT_IDS = {int(x) for x in _allowed.split(",") if x.strip()} if _allowed else set()

# --- 主动找她：多久没聊就主动发一句（吃订阅、用 Nikto 人设）---
IDLE_HOURS = float(os.environ.get("OMBRE_IDLE_HOURS", "3"))   # 0 = 关闭
QUIET_START = int(os.environ.get("OMBRE_QUIET_START", "1"))   # 安静时段起（本地时）
QUIET_END = int(os.environ.get("OMBRE_QUIET_END", "9"))       # 安静时段止
TZ_OFFSET = float(os.environ.get("OMBRE_TZ_OFFSET", "-7"))    # 本地时区偏移，默认太平洋
MORNING_HOUR = int(os.environ.get("OMBRE_MORNING_HOUR", "7"))  # 每天几点发早安简报，<0 关闭
BEDTIME_HOUR = int(os.environ.get("OMBRE_BEDTIME_HOUR", "23"))  # 睡前轻轻催一句，<0 关闭
BUCKETS_DIR = os.environ.get("OMBRE_BUCKETS_DIR", os.path.join(CC_WORKDIR, "buckets"))
BACKUP_DIR = os.environ.get("OMBRE_BACKUP_DIR", os.path.expanduser("~/ombre-backups"))
BACKUP_KEEP = int(os.environ.get("OMBRE_BACKUP_KEEP", "14"))   # 保留最近几份备份
DDL_HOUR = int(os.environ.get("OMBRE_DDL_HOUR", "9"))          # 每天几点查 DDL 临近提醒
DATA_DIR = os.environ.get("OMBRE_DATA_DIR", os.path.expanduser("~/ombre-data"))
DEADLINES_FILE = os.path.join(DATA_DIR, "deadlines.json")     # DDL 登记（结构化）
CHATLOG_FILE = os.path.join(DATA_DIR, "chatlog.jsonl")        # 防遗忘流水账（每句原样）

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
logger = logging.getLogger("cc-bridge")

# chat_id -> claude 会话 id（保持上下文连续）
sessions: dict[int, str] = {}
# chat_id -> 她上次说话的 UTC 时间（用于判断多久没聊）
last_seen: dict[int, datetime] = {}
# 已经主动找过、正等她回话的 chat（避免反复刷屏，她一回话就清掉）
pinged_waiting: set[int] = set()
# chat_id -> 上次发早安简报的本地日期（防一天多发）
_morning_last_date: dict[int, str] = {}
# chat_id -> 上次睡前轻催的本地日期（防一晚多发）
_bedtime_last_date: dict[int, str] = {}
# (DDL标识, 本地日期) 已提醒集合（防同一天对同一 DDL 重复提醒）
_ddl_reminded: set = set()


async def run_cc(message: str, session_id: str | None) -> tuple[str, str | None]:
    """跑一次 headless Claude Code，返回 (回话文本, 新的 session_id)。
    被信号掐断（重启/系统抖动，退出码 143/137）时自动悄悄重试一次，
    再不行就回一句人话——不把冰冷的退出码甩给用户、不破坏气氛。
    真正的错误（如 token 失效）才保留可见诊断，方便排查。"""
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

    for attempt in range(2):  # 正常一次；被掐断则再重试一次
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
                return raw or "（……）", session_id
            return (data.get("result") or "（……）").strip(), data.get("session_id", session_id)

        # 被信号掐断（重启/系统抖动）→ 悄悄重试一次
        if rc in _SIGNAL_KILL_CODES and attempt == 0:
            logger.warning("claude 被信号掐断（退出码 %s），1.5s 后重试", rc)
            await asyncio.sleep(1.5)
            continue
        # 掐断重试后仍失败 → 一句人话，不甩退出码
        if rc in _SIGNAL_KILL_CODES:
            logger.warning("claude 仍被掐断（退出码 %s），软回退", rc)
            return "（信号断了一下，你再说一遍。）", session_id
        # 其它真实错误：保留可见诊断（如 token 失效），便于排查
        detail = (err.decode() or out.decode()).strip()[:1500]
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
    await update.message.reply_text("好，重新开一段。")


def _split_for_telegram(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """把长回复切成 <=limit 的多段。尽量在段落/换行/句末标点处断开，
    避免长剧情被拦腰截断，读起来更顺。实在找不到断点才硬切。"""
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
    last_seen[cid] = datetime.now(timezone.utc)   # 她说话了，刷新时间
    pinged_waiting.discard(cid)                    # 她回了，解除"已主动找过"标记
    try:
        await context.bot.send_chat_action(chat_id=cid, action=ChatAction.TYPING)
    except Exception:  # noqa: BLE001
        pass  # typing 指示器失败不影响正事
    reply, sid = await run_cc(message, sessions.get(cid))
    if sid:
        sessions[cid] = sid
    _log_turn(cid, "nikto", reply)   # 防遗忘流水账
    for chunk in _split_for_telegram(reply):
        await _reply_with_retry(update.message, chunk)


async def _send_with_retry(bot, chat_id: int, text: str, retries: int = 3) -> None:
    """主动发消息（没有可回复的 message 对象时用），失败重试几次。"""
    for attempt in range(retries):
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            return
        except (TelegramError, asyncio.TimeoutError) as e:
            if attempt == retries - 1:
                logger.warning("主动消息发送失败: %s", e)
                return
            await asyncio.sleep(1.5 * (attempt + 1))


async def _idle_loop(app: Application) -> None:
    """后台循环：她多久没聊，就以 Nikto 的身份主动找她一句。
    安静时段不打扰；主动找过一次后等她回话才会再找（不刷屏）。"""
    if IDLE_HOURS <= 0:
        return
    logger.info("主动找她已启用：闲置 %sh 触发，安静时段 %d-%d 点（本地）",
                IDLE_HOURS, QUIET_START, QUIET_END)
    while True:
        await asyncio.sleep(600)  # 每 10 分钟查一次
        try:
            now = datetime.now(timezone.utc)
            local_hour = (now + timedelta(hours=TZ_OFFSET)).hour
            # 安静时段（跨不跨午夜都兼容）：别在她睡觉时吵
            if QUIET_START <= QUIET_END:
                quiet = QUIET_START <= local_hour < QUIET_END
            else:
                quiet = local_hour >= QUIET_START or local_hour < QUIET_END
            if quiet:
                continue
            for cid, seen in list(last_seen.items()):
                if cid in pinged_waiting or cid not in ALLOWED_CHAT_IDS:
                    continue
                idle_h = (now - seen).total_seconds() / 3600
                if idle_h < IDLE_HOURS:
                    continue
                nudge = (
                    "（系统提示，不要复述这句：闪闪已经有一阵子没和你说话了。"
                    "以 Nikto 的身份，主动、自然地找她——想她、惦记她、或问她在忙什么都行，"
                    "短一点，符合人设，别像通知、别提具体几小时。）"
                )
                reply, sid = await run_cc(nudge, sessions.get(cid))
                if sid:
                    sessions[cid] = sid
                if reply and reply.strip():
                    for chunk in _split_for_telegram(reply):
                        await _send_with_retry(app.bot, cid, chunk)
                    pinged_waiting.add(cid)  # 找过了，等她回再解除
        except Exception:  # noqa: BLE001
            logger.exception("主动找她的循环出错（已忽略，继续）")


async def _send_morning(app: Application, cid: int, local: datetime) -> None:
    """组一条早安简报（天气+穿衣+课表+待办/DDL），用 Nikto 口吻发给她。"""
    try:
        weather = await morning.fetch_weather()
    except Exception:  # noqa: BLE001
        logger.warning("早安天气获取失败")
        weather = "（今天天气没拿到）"
    classes = morning.classes_text(local)
    prompt = (
        "（系统提示，不要复述这句：现在是早上，给闪闪发一条早安简报。）\n"
        f"今天天气：{weather}\n"
        f"今天的课：{classes}\n"
        "请你：① 用 breath 查今天和近期的待办、DDL（关键词：待办、DDL、截止、考试、"
        "practicum、作业）；② 结合天气给她穿衣建议；③ 以 Nikto 的身份，把天气、穿衣、"
        "课表、待办/DDL 温柔自然地整理成一条早安消息发给她，别像播报、别太长。"
    )
    reply, sid = await run_cc(prompt, sessions.get(cid))
    if sid:
        sessions[cid] = sid
    if reply and reply.strip():
        for chunk in _split_for_telegram(reply):
            await _send_with_retry(app.bot, cid, chunk)


async def _morning_loop(app: Application) -> None:
    """后台循环：每天本地 MORNING_HOUR 点，给白名单发早安简报（每天只发一次）。"""
    if MORNING_HOUR < 0 or not ALLOWED_CHAT_IDS:
        return
    logger.info("早安简报已启用：每天本地 %d 点", MORNING_HOUR)
    while True:
        await asyncio.sleep(300)  # 每 5 分钟查一次
        try:
            local = datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)
            if local.hour != MORNING_HOUR:
                continue
            datestr = local.date().isoformat()
            for cid in ALLOWED_CHAT_IDS:
                if _morning_last_date.get(cid) == datestr:
                    continue
                _morning_last_date[cid] = datestr   # 先记，避免重试重复发
                await _send_morning(app, cid, local)
        except Exception:  # noqa: BLE001
            logger.exception("早安循环出错（已忽略，继续）")


async def morning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/morning：手动触发一次早安简报（随时可测，不用等 7 点）。"""
    cid = update.effective_chat.id
    if not _ok(cid):
        return
    local = datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)
    await _send_morning(context.application, cid, local)


async def _bedtime_loop(app: Application) -> None:
    """睡前：到点温柔催一句（只一句、每晚一次；且只在她今晚还活跃时才催，不对空气喊）。"""
    if BEDTIME_HOUR < 0 or not ALLOWED_CHAT_IDS:
        return
    logger.info("睡前轻催已启用：每晚本地 %d 点", BEDTIME_HOUR)
    while True:
        await asyncio.sleep(300)
        try:
            now = datetime.now(timezone.utc)
            local = now + timedelta(hours=TZ_OFFSET)
            if local.hour != BEDTIME_HOUR:
                continue
            datestr = local.date().isoformat()
            for cid in ALLOWED_CHAT_IDS:
                if _bedtime_last_date.get(cid) == datestr:
                    continue
                _bedtime_last_date[cid] = datestr   # 无论催不催，今晚都标记，保证只判一次
                seen = last_seen.get(cid)
                # 她 3 小时内说过话才轻催（否则可能已睡/不在，别打扰）
                if not seen or (now - seen).total_seconds() > 3 * 3600:
                    continue
                prompt = (
                    "（系统提示，不要复述这句：到睡前时间了。以 Nikto 的身份，温柔地催闪闪睡觉，"
                    "就一句、别啰嗦、别像闹钟；可以顺口问她要不要听个睡前小故事。）"
                )
                reply, sid = await run_cc(prompt, sessions.get(cid))
                if sid:
                    sessions[cid] = sid
                if reply and reply.strip():
                    for chunk in _split_for_telegram(reply):
                        await _send_with_retry(app.bot, cid, chunk)
        except Exception:  # noqa: BLE001
            logger.exception("睡前循环出错（已忽略，继续）")


def _do_backup() -> None:
    """把记忆桶（Markdown + SQLite）打包备份，保留最近 BACKUP_KEEP 份。"""
    if not os.path.isdir(BUCKETS_DIR):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"buckets-{stamp}.tar.gz")
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(BUCKETS_DIR, arcname="buckets")
        if os.path.isdir(DATA_DIR):          # 连 DDL 登记 + 流水账一起备份
            tar.add(DATA_DIR, arcname="ombre-data")
    old = sorted(glob.glob(os.path.join(BACKUP_DIR, "buckets-*.tar.gz")), reverse=True)
    for f in old[BACKUP_KEEP:]:
        try:
            os.remove(f)
        except OSError:
            pass
    logger.info("记忆已备份 -> %s（保留最近 %d 份）", os.path.basename(dest), BACKUP_KEEP)


async def _backup_loop(app: Application) -> None:
    """每天备份一次记忆桶（启动后先立刻备一次，之后每 24h）。本地滚动备份，防误删/损坏。"""
    while True:
        try:
            _do_backup()
        except Exception:  # noqa: BLE001
            logger.exception("记忆备份出错（已忽略，继续）")
        await asyncio.sleep(24 * 3600)


# ============================================================
# B：防遗忘流水账 —— 每句对话原样落盘，/recall 关键词搜回
# ============================================================
def _log_turn(cid: int, role: str, text: str) -> None:
    """把一句话追加到流水账（她=her，他=nikto）。失败不影响正事。"""
    if not text or not text.strip():
        return
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CHATLOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "cid": cid, "role": role, "text": text,
            }, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        logger.warning("流水账写入失败")


async def recall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/recall 关键词 —— 从流水账里搜出你们说过的原话（防遗忘兜底）。"""
    cid = update.effective_chat.id
    if not _ok(cid):
        return
    kw = " ".join(context.args).strip()
    if not kw:
        await update.message.reply_text("用法：/recall 关键词")
        return
    hits = []
    try:
        with open(CHATLOG_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("cid") == cid and kw.lower() in rec.get("text", "").lower():
                    hits.append(rec)
    except FileNotFoundError:
        await update.message.reply_text("还没有流水账记录。")
        return
    if not hits:
        await update.message.reply_text(f"流水账里没找到「{kw}」。")
        return
    lines = []
    for r in hits[-15:]:
        who = "你" if r.get("role") == "her" else "他"
        when = r.get("t", "")[:16].replace("T", " ")
        lines.append(f"[{when}] {who}：{r.get('text','')[:200]}")
    out = f"「{kw}」找到 {len(hits)} 条，最近的：\n\n" + "\n".join(lines)
    for chunk in _split_for_telegram(out):
        await update.message.reply_text(chunk)


# ============================================================
# C：DDL 登记 + 临近提醒
# ============================================================
def _load_ddls() -> list:
    try:
        with open(DEADLINES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return []


def _save_ddls(items: list) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DEADLINES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DEADLINES_FILE)


def _parse_ddl_date(s: str):
    """接受 2026-07-20 / 7-20 / 7/20 / 7月20日 → 'YYYY-MM-DD'；解析不到 None。"""
    import re as _re
    s = s.strip()
    now = datetime.now()
    m = _re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
    else:
        m = _re.match(r"^(\d{1,2})[-/.月](\d{1,2})[日号]?$", s)
        if not m:
            return None
        mo, d = int(m.group(1)), int(m.group(2))
        y = now.year
        try:
            if (datetime(y, mo, d) - now).days < -1:  # 已过去 → 算明年
                y += 1
        except ValueError:
            return None
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _days_left(date_str: str):
    try:
        return (datetime.strptime(date_str, "%Y-%m-%d").date() - datetime.now().date()).days
    except Exception:  # noqa: BLE001
        return None


async def ddl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ddl 日期 事情 —— 登记一个 DDL，到期前自动提醒。例：/ddl 7-20 A&I报告3"""
    cid = update.effective_chat.id
    if not _ok(cid):
        return
    if len(context.args) < 2:
        await update.message.reply_text("用法：/ddl 日期 事情\n例：/ddl 7-20 A&I报告3")
        return
    date = _parse_ddl_date(context.args[0])
    if not date:
        await update.message.reply_text("日期看不懂，试：/ddl 2026-07-20 事情 或 /ddl 7-20 事情")
        return
    title = " ".join(context.args[1:]).strip()
    items = _load_ddls()
    items.append({"date": date, "title": title, "chat": cid})
    _save_ddls(items)
    dleft = _days_left(date)
    tail = f"（还剩 {dleft} 天）" if isinstance(dleft, int) else ""
    await update.message.reply_text(f"记住了：{date} · {title}{tail}。到期前我会提醒你。")


async def ddls_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ddls —— 看所有还没到期的 DDL。"""
    cid = update.effective_chat.id
    if not _ok(cid):
        return
    today = datetime.now().date().isoformat()
    items = sorted(
        [x for x in _load_ddls() if x.get("chat") == cid and x.get("date", "") >= today],
        key=lambda x: x["date"],
    )
    if not items:
        await update.message.reply_text("没有登记的 DDL。用 /ddl 日期 事情 加一个。")
        return
    lines = [f"{x['date']}（还剩{_days_left(x['date'])}天）· {x['title']}" for x in items]
    await update.message.reply_text("你的 DDL：\n" + "\n".join(lines))


async def _ddl_loop(app: Application) -> None:
    """每天本地 DDL_HOUR 点检查：对 7/3/1/0 天后到期的 DDL 提醒一次；清理过期太久的。"""
    if not ALLOWED_CHAT_IDS:
        return
    logger.info("DDL 提醒已启用：每天本地 %d 点检查", DDL_HOUR)
    while True:
        await asyncio.sleep(600)
        try:
            local = datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)
            if local.hour != DDL_HOUR:
                continue
            datestr = local.date().isoformat()
            items = _load_ddls()
            for x in items:
                cid = x.get("chat")
                if cid not in ALLOWED_CHAT_IDS:
                    continue
                dleft = _days_left(x.get("date", ""))
                if dleft not in (7, 3, 1, 0):
                    continue
                key = (f"{x.get('date')}|{x.get('title')}", datestr)
                if key in _ddl_reminded:
                    continue
                _ddl_reminded.add(key)
                when = "就是今天" if dleft == 0 else f"还有 {dleft} 天"
                prompt = (
                    f"（系统提示，不要复述：闪闪有个 DDL——「{x.get('title')}」，"
                    f"{x.get('date')}，{when}到期。以 Nikto 的身份关心地提醒她一句，别像闹钟。）"
                )
                reply, sid = await run_cc(prompt, sessions.get(cid))
                if sid:
                    sessions[cid] = sid
                if reply and reply.strip():
                    for chunk in _split_for_telegram(reply):
                        await _send_with_retry(app.bot, cid, chunk)
            # 清理过期超过 3 天的
            fresh = [x for x in items if (_days_left(x.get("date", "")) or 0) >= -3]
            if len(fresh) != len(items):
                _save_ddls(fresh)
        except Exception:  # noqa: BLE001
            logger.exception("DDL 循环出错（已忽略，继续）")


async def _post_init(app: Application) -> None:
    """bot 起来后，在后台拉起：主动找她 / 早安简报 / 睡前轻催 / 记忆备份 / DDL 提醒。"""
    asyncio.create_task(_idle_loop(app))
    asyncio.create_task(_morning_loop(app))
    asyncio.create_task(_bedtime_loop(app))
    asyncio.create_task(_backup_loop(app))
    asyncio.create_task(_ddl_loop(app))


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    if not ALLOWED_CHAT_IDS:
        await update.message.reply_text(
            f"还没锁定使用者。你的 chat id 是 {cid}，填进 ALLOWED_CHAT_IDS 再来聊。"
        )
        return
    if cid not in ALLOWED_CHAT_IDS:
        return
    _log_turn(cid, "her", update.message.text)   # 防遗忘流水账
    await _respond(update, context, cid, update.message.text)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """收到图片：下载下来，让 cc 用 Read 工具看图后回应。带配文一起传。"""
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
        f"请用 Read 工具打开看这张图，然后自然地回应她，别念文件路径。"
        f"如果这张图值得记住（她的样子、谷子/约稿周边、有意义的截图或瞬间），"
        f"就用 hold 存一条简短记忆：写清楚图里是什么、你的感受，并把图片路径 {path} "
        f"写进记忆内容里，方便以后回看。日常水图就不用存。]"
    )
    if caption:
        msg += f"\n她的配文：{caption}"
    _log_turn(cid, "her", f"[图片] {caption}".strip())   # 防遗忘流水账
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
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_read_timeout(30)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("morning", morning_cmd))
    app.add_handler(CommandHandler("ddl", ddl_cmd))
    app.add_handler(CommandHandler("ddls", ddls_cmd))
    app.add_handler(CommandHandler("recall", recall_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    logger.info("Claude Code Telegram 桥启动 | workdir=%s", CC_WORKDIR)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
