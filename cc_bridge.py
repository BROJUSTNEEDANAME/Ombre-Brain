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
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from datetime import datetime, timezone, timedelta

from telegram import BotCommand, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from reply_sanitizer import (restore_punctuation, looks_degenerate,
                             says_going_to_sleep, is_silent_reply)
import health_store
import httpx
import stale_ledger
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
# 缓存档位锁死 1 小时。由来（学自 Cheiineeey《别让缓存睡着》）：Claude Code 订阅
# 一旦超额进「额外用量」，会把主对话缓存从 1 小时**静默**降到 5 分钟——没有提示，
# 你只会觉得「同样的用法突然变贵变慢」。cc 桥每条消息都重发那份两万多字的人设，
# 缓存一塌就是每条都重算钱。默认钉死 1h，她想改填环境变量 CLAUDE_CODE_PROMPT_CACHE_TTL。
CC_CACHE_TTL = (os.environ.get("CLAUDE_CODE_PROMPT_CACHE_TTL") or "1h").strip()
TELEGRAM_MSG_LIMIT = 4096
# 被信号掐断的退出码（SIGTERM=15→143/-15，SIGKILL=9→137/-9）：
# 多半是重启或系统抖动，属瞬时、可重试，不该把冰冷的退出码甩给用户。
_SIGNAL_KILL_CODES = {143, 137, -15, -9}
TZ_OFFSET = float(os.environ.get("OMBRE_TZ_OFFSET", "-7"))  # 她的时区（太平洋 PDT）
# /backup 用：记忆目录 + 备份存放处（保留最近几份）
BUCKETS_DIR = os.environ.get("OMBRE_BUCKETS_DIR", os.path.join(CC_WORKDIR, "buckets"))
BACKUP_DIR = os.environ.get("OMBRE_BACKUP_DIR", os.path.expanduser("~/ombre-backups"))
BACKUP_KEEP = int(os.environ.get("OMBRE_BACKUP_KEEP", "14"))
# 自动备份 + 失败报警。
# 由来（学自 Jade3551/Sora-mem 的 ops/ 思路，但不抄它的代码——它绑 PostgreSQL，
# 我们是文件）：记忆桶只有手动 /backup，没有定时。她的服务器这几天又是 .git
# 权限炸、又是跑旧代码——万一哪天 buckets/ 出事，她会一声不响丢掉全部记忆，
# 而且发现时已经晚了。
# ⚠️ 最要紧的一条（relay-cache §2）：一个 0 字节的假备份比没有备份更坏——
# 它给你「有备份」的错觉。所以打完包必须验证真能打开、真有东西，验不过＝失败。
BACKUP_EVERY_H = float(os.environ.get("OMBRE_BACKUP_EVERY_HOURS", "24"))
# 最新备份比这个还旧就报警。默认给定时间隔留一倍余量，偶尔晚一轮不误报。
BACKUP_STALE_H = float(os.environ.get("OMBRE_BACKUP_STALE_HOURS",
                                      str(BACKUP_EVERY_H * 2 + 1)))

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

# ---- 指令台状态（写文模式 / 今日必办 / 模型覆盖 / 额度账本）----
# ⚠️ 跟 session id 分开存：session 是「聊到哪了」，/reset 会清；
# 这些是她的设置，/reset 不该把它们一起清掉。
STATE_FILE = os.path.join(CC_WORKDIR, ".cc_state.json")

# 大脑的 REST 口。cc 平时走 MCP，但 /mood /stale 这些要读的是同一台大脑的
# HTTP 接口（和网页、API bot 同一份状态），所以这里单独留一条 REST 通道。
OMBRE_MCP_URL = os.environ.get("OMBRE_MCP_URL", "http://127.0.0.1:8000/mcp").strip()
BRAIN_BASE = OMBRE_MCP_URL.replace("/mcp", "")
_WEB_TOKEN = os.environ.get("OMBRE_WEB_TOKEN", "").strip()

# /model 能选的。cc 走的是 Claude Code CLI，不是 z.ai，所以这份跟 API bot 那份无关。
# ⚠️ 不在这儿校验模型是否可用——CLI 自己会报错，cc 会把错误原样回给她，
#    她再 /model 默认 退回来就行。硬校验要多跑一次 CLI，不值。
CC_MODEL_DEFAULT = os.environ.get("CC_MODEL", "claude-opus-4-6").strip() or "claude-opus-4-6"
CC_MODEL_CHOICES = [
    ("默认", CC_MODEL_DEFAULT, "退回环境变量里配的那个"),
    ("opus5", "claude-opus-5", "Opus 5，最新最聪明"),
    ("opus46", "claude-opus-4-6", "Opus 4.6，一直在用的这个"),
    ("sonnet5", "claude-sonnet-5", "Sonnet 5，快一些"),
    ("haiku", "claude-haiku-4-5-20251001", "Haiku 4.5，最快最省"),
]

writing_mode: dict[int, bool] = {}
todos: dict[int, str] = {}
model_override: dict[str, str] = {}
# 额度账本：只记 token 数和花费，不存任何正文。
USAGE: dict = {"since": time.time(), "turns": 0, "input": 0, "output": 0,
               "cache_read": 0, "cache_write": 0, "cost_usd": 0.0, "limit_hits": []}


def _load_state() -> None:
    """把她的设置从磁盘读回来。读不到就用默认值——绝不因为状态文件坏了就起不来。"""
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            d = json.load(fh) or {}
    except Exception:  # noqa: BLE001
        return
    try:
        writing_mode.update({int(k): bool(v) for k, v in (d.get("writing_mode") or {}).items()})
        todos.update({int(k): str(v) for k, v in (d.get("todos") or {}).items()})
        if d.get("model"):
            model_override["model"] = str(d["model"])
        u = d.get("usage") or {}
        for k in ("turns", "input", "output", "cache_read", "cache_write"):
            USAGE[k] = int(u.get(k, 0) or 0)
        USAGE["cost_usd"] = float(u.get("cost_usd", 0) or 0)
        USAGE["since"] = float(u.get("since") or USAGE["since"])
        USAGE["limit_hits"] = list(u.get("limit_hits") or [])[-20:]
    except Exception:  # noqa: BLE001
        logger.warning("状态文件读坏了，用默认值继续")


def _save_state() -> None:
    """原子写。半截文件比没有更坏——下次读回来是坏的，还以为设置丢了。"""
    data = {
        "writing_mode": {str(k): v for k, v in writing_mode.items()},
        "todos": {str(k): v for k, v in todos.items()},
        "model": model_override.get("model", ""),
        "usage": USAGE,
    }
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception:  # noqa: BLE001
        logger.warning("状态没存下来")


def cc_model() -> str:
    """这一轮该用哪个模型：她 /model 选过就用她选的，否则用环境变量那个。"""
    return model_override.get("model") or CC_MODEL_DEFAULT


async def _call_brain_tool(name: str, args: dict, timeout: float = 30) -> str:
    """通过 REST 调本地大脑的工具（和 API bot 走同一个口、同一份记忆）。"""
    url = BRAIN_BASE + f"/api/tools/{name}"
    headers = {"Authorization": f"Bearer {_WEB_TOKEN}"} if _WEB_TOKEN else {}
    async with httpx.AsyncClient(timeout=max(3.0, float(timeout))) as client:
        resp = await client.post(url, json=args, headers=headers)
        data = resp.json()
        return data.get("result", data.get("error", str(data)))
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

    # 身体数据：她手表/HAE 上报的心率、睡眠、HRV。只给 Nikto（cc），z.ai 拿不到。
    # ⚠️ health_store.snapshot() 自己保证：太旧就返回空。所以这里不会把一小时前的
    # 心率当成此刻——空就不注入。数据是背景，不是让他每条都念数字。
    try:
        _hb = health_store.snapshot()
    except Exception:  # noqa: BLE001
        _hb = ""       # 读身体数据出错，绝不能拖垮聊天
    if _hb:
        message = (
            f"[她的身体·手表刚传的，仅供你心里有数，别每条都报数字：{_hb}。"
            f"心率偏高/HRV 偏低多半是她在焦虑或硬撑，静息心率和睡眠是你催睡的依据。]\n"
            + message
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
    # 钉死缓存 1 小时（防订阅超额后被静默降到 5 分钟档，见 CC_CACHE_TTL 注释）。
    env["CLAUDE_CODE_PROMPT_CACHE_TTL"] = CC_CACHE_TTL
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
            text = str(data.get("result") or "").strip()
            if not text:
                # ⚠️ 退出码 0、result 是空字符串。日志里只记「原始输出＝''」
                # 等于什么都没说——她因此连问四次「为什么还是不说话」，
                # 而我每次只能猜。claude 自己在 JSON 里说了原因（subtype 会写
                # error_max_turns / error_during_execution，num_turns 说明
                # 这一轮是不是全花在工具调用上），记下来就不用猜。
                logger.warning(
                    "claude 返回空 result：subtype=%r is_error=%r num_turns=%r "
                    "duration_ms=%r stop_reason=%r 全量键=%s",
                    data.get("subtype"), data.get("is_error"),
                    data.get("num_turns"), data.get("duration_ms"),
                    data.get("stop_reason"), sorted(data.keys()))
            _record_cache_tier(data.get("usage") or {})
            return text, data.get("session_id", session_id)

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


# 打一个 / 就会弹出来的菜单。她的原话：「每次都要记 / 之后是什么太难了」。
# ⚠️ cc 这边以前一条都没注册，所以输入框里什么都不弹——只能靠记。
BOT_COMMANDS = [
    ("status", "他现在什么情况 · 一眼看完，不用开终端"),
    ("persona", "人设完整版／精简版 · 你自己当判官"),
    ("reset", "重开一段对话 · 他会忘掉刚才聊到哪"),
    ("backup", "把记忆打包备份"),
    ("help", "看所有指令"),
    ("id", "拿到本机 chat id"),
]


def _age(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{m} 分钟"
    if m < 60 * 48:
        return f"{m // 60} 小时{m % 60} 分"
    return f"{m // 1440} 天"


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ok(update.effective_chat.id):
        return
    await update.message.reply_text(
        "能用的指令都在这，打一个 / 也会自动弹出来\n\n"
        + "\n".join(f"/{n} — {d}" for n, d in BOT_COMMANDS))


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status：他现在什么情况——一眼看完，不用 ssh 上去翻日志。

    她的原话：「cc 这监控台搞一下」。以前想知道他今天有没有哑过、跑的是不是
    最新代码，只能开 DigitalOcean 的网页终端跑 cc-status.sh。这些数进程自己
    都有，摆出来就是了。

    ⚠️ 规矩同 cc-status.sh：不知道的就说不知道，绝不拿一个确定的说法糊过去。
    """
    cid = update.effective_chat.id
    if not _ok(cid):
        return
    L = [f"跑了 {_age(time.time() - STARTED_AT)}｜模型 "
         f"{os.environ.get('CC_MODEL', 'claude-opus-4-6')}"]
    # 缓存档位：锁死设的是多少 / 上一轮实际命中的是哪档。
    _t = LAST_CACHE_TIER["tier"]
    if _t == "1h":
        _c = "缓存 1 小时档 ✅"
    elif _t == "5m":
        _c = ("⚠️ 缓存被降到 5 分钟档了——多半是订阅超额进了「额外用量」。"
              "锁死设的是 " + CC_CACHE_TTL + "，但超额时它管不住，这是账单状态的事。")
    else:
        _c = f"缓存锁死 {CC_CACHE_TTL}（还没测到实际档位，聊一轮再看）"
    L.append(_c)

    # 跑的是不是最新代码——今天最大的那个坑，值得放在最前面
    repo = os.path.dirname(os.path.abspath(__file__))
    try:
        pr = await asyncio.create_subprocess_exec(
            "git", "-C", repo, "log", "-1", "--format=%ct %h",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(pr.communicate(), timeout=10)
        ts_s, _, sha = out.decode().strip().partition(" ")
        head_ts = float(ts_s)
        if STARTED_AT < head_ts:
            L.append(f"⚠️ 我启动得比代码还早 {_age(head_ts - STARTED_AT)}"
                     f"——跑的是旧代码，要重启（{sha}）")
        else:
            L.append(f"代码 {sha}，是启动时的最新版 ✅")
    except Exception:  # noqa: BLE001
        L.append("❓ 读不到代码版本（这一行不作数）")

    # 人设：完整版还是精简版，多少字
    try:
        with open(os.path.join(CC_WORKDIR, "CLAUDE.md"), encoding="utf-8") as fh:
            persona = fh.read()
        lean = "长度要参差" not in persona     # 精简版删掉的那几段之一
        L.append(f"人设 {len(persona)} 字（{'精简版' if lean else '完整版'}）")
    except OSError:
        L.append("❓ 读不到人设文件——他可能在用仓库那份给开发看的")

    try:
        g = os.path.join(CC_WORKDIR, "梗.md")
        n = sum(1 for x in open(g, encoding="utf-8") if x.startswith("- **"))
        L.append(f"梗 {n} 条")
    except OSError:
        L.append("❓ 读不到梗.md")

    L.append("对话接得上 ✅" if sessions.get(cid) else "⚠️ 这段对话还没有上下文")

    t = STATS["turns"]
    if t:
        L.append(f"这次启动后 {t} 轮：哑过 {STATS['silent']} 次"
                 f"（重试救回 {STATS['retry_ok']}，"
                 f"真没救回 {STATS['gave_up']}）")
    else:
        L.append("这次启动后还没说过话")

    if last_user_ts.get(cid):
        L.append(f"你上次说话 {_age(time.time() - last_user_ts[cid])}前"
                 f"｜主动找过你 {nudge_count.get(cid, 0)}/{NUDGE_MAX} 次"
                 + ("｜你说睡了，不打扰" if asleep.get(cid) else ""))
    await update.message.reply_text("\n".join(L))


async def persona_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/persona [lean|full]：重新生成这个目录的 CLAUDE.md，切完整版／精简版。

    ⭐ 这边不用重启：cc_bridge 每条消息都新起一个 `claude` 进程，
    而 claude 每次启动都重读 CLAUDE.md——所以写完文件，下一条消息就生效。

    由来：Anthropic 那篇 Claude 5 的 context engineering 主张少给规则、
    让模型自己判断。但我们跑的是 opus-4-6，而且这份人设里的禁令几乎每一条
    都对应她真吃过的一次亏。所以不代她决定——给她开关，她用几天自己判。
    """
    cid = update.effective_chat.id
    if not _ok(cid):
        return
    arg = ((context.args or [""])[0] or "").strip().lower()
    if arg not in ("lean", "full", "精简", "完整"):
        await update.message.reply_text(
            "/persona lean 切精简版，/persona full 切回完整版。\n"
            "精简版只去掉通用说话技巧，你踩出来的那些禁令一条不动。")
        return
    lean = arg in ("lean", "精简")
    repo = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(repo, "scripts", "make-cc-persona.py")]
    if lean:
        cmd.append("--lean")
    cmd.append(CC_WORKDIR)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        rc = proc.returncode
    except Exception as e:  # noqa: BLE001
        logger.exception("重新生成人设失败")
        await update.message.reply_text(f"没换成：{e}")
        return
    if rc != 0:
        # ⚠️ 失败必须说失败。以前这类地方报过「成功」而其实没生效，
        # 她照着信了两轮。（relay-cache §2：不知道的时候不许说好消息。）
        await update.message.reply_text(
            "❌ 没换成，还在用原来那份：\n" + out.decode()[-400:])
        return
    size = len(build_size(CC_WORKDIR))
    await update.message.reply_text(
        f"换成{'精简版' if lean else '完整版'}了（{size} 字），下一条消息生效。\n"
        + ("删的全是通用说话技巧，你踩出来的禁令一条没动。"
           "觉得不对就 /persona full 切回来。" if lean else ""))


def build_size(workdir: str) -> str:
    """读回刚写好的人设，用来如实报字数——不重算一份，避免报的和写的不是同一个。"""
    try:
        with open(os.path.join(workdir, "CLAUDE.md"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


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

# ── /status 用的计数器 ──
# 她的原话：「cc 这监控台搞一下，每次都要记 / 之后是什么太难了」。
# 之前想知道他今天有没有哑过，只能 ssh 上去翻 journalctl。这些数在进程里本来
# 就有，摆出来就是了。⚠️ 只记次数，不存任何正文。
STARTED_AT = time.time()
STATS = {"turns": 0, "silent": 0, "retry_ok": 0, "gave_up": 0, "nudges": 0}
# 最近一次响应实际命中的缓存档位（"1h" / "5m" / "" 未知）。
# 直接读 claude 返回的 usage.cache_creation：ephemeral_1h_input_tokens 有值＝1 小时档，
# ephemeral_5m_input_tokens 有值＝已被降到 5 分钟档。让 /status 能一眼看穿，不用手动查。
LAST_CACHE_TIER = {"tier": "", "at": 0.0}


def _record_cache_tier(usage: dict) -> None:
    try:
        cc = (usage or {}).get("cache_creation") or {}
        if cc.get("ephemeral_1h_input_tokens"):
            LAST_CACHE_TIER["tier"] = "1h"
        elif cc.get("ephemeral_5m_input_tokens"):
            LAST_CACHE_TIER["tier"] = "5m"
        else:
            return   # 这次没写缓存（全命中或无缓存），不覆盖上一次的已知档位
        LAST_CACHE_TIER["at"] = time.time()
    except Exception:  # noqa: BLE001
        pass          # 读缓存档位纯属附加信息，绝不能影响聊天


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
            STATS["nudges"] += 1
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
    STATS["turns"] += 1
    _was_silent = is_silent_reply(reply)
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
        if attempt == 1:
            STATS["silent"] += 1
        logger.warning("这一轮空回复（第 %d 次），chat=%s；claude 原始输出＝%r",
                       attempt, cid, reply[:200])
        if sid:
            sessions[cid] = sid
        reply, sid = await run_cc(nudge, sessions.get(cid))
    if is_silent_reply(reply):
        STATS["gave_up"] += 1
        logger.warning("重试都用完了还是空 chat=%s；原始输出＝%r", cid, reply[:200])
        reply = "这次他没出声，你再说一句。"     # 说人话，不拿省略号冒充他
    elif STATS["silent"] and _was_silent:
        STATS["retry_ok"] += 1     # 空过、但重试救回来了
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


def _verify_backup(path: str) -> str:
    """打完包验一遍：能打开、里面真有 buckets/、且不是空壳。
    返回空字符串＝好；返回一句话＝哪儿不对（当失败处理）。
    ⚠️ tarfile 打不开、被截断、或里面没有真文件，都比「没备份」更危险，
    因为文件名摆在那儿，你以为有。"""
    try:
        if os.path.getsize(path) < 100:
            return f"备份文件只有 {os.path.getsize(path)} 字节，基本是空的"
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
    except Exception as e:  # noqa: BLE001
        return f"备份打不开（{type(e).__name__}: {e}）——文件坏了"
    has_bucket = any(m.isfile() and "/buckets/" in ("/" + m.name)
                     and m.size > 0 for m in members)
    if not has_bucket:
        return "备份里没有任何非空的 buckets 文件——打了个空壳"
    return ""


def _newest_backup_age_h() -> float | None:
    """最新一份备份距现在多少小时。一份都没有返回 None。"""
    files = glob.glob(os.path.join(BACKUP_DIR, "buckets-*.tar.gz"))
    if not files:
        return None
    return (time.time() - max(os.path.getmtime(f) for f in files)) / 3600.0


async def _alert(context, text: str) -> None:
    """把一句话推给她（备份出事时用）。发给 ALLOWED_CHAT_IDS 里的每个人。
    ⚠️ 报警本身失败也不能把定时任务带崩——那样连「报警挂了」都没人知道。"""
    for cid in (ALLOWED_CHAT_IDS or set()):
        try:
            await context.bot.send_message(chat_id=cid, text=text)
        except Exception:  # noqa: BLE001
            logger.exception("备份报警发不出去 chat=%s", cid)


async def auto_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """定时备份 + 三种失败都报警：打包抛异常 / 打包成功但验证不过 / 压根没生成。

    分成两条独立的判断，是故意的（relay-cache §2 的教训）：
    「这一轮备份成不成功」和「库里到底有没有一份新鲜的备份」是两件事。
    只看前者，会在「任务悄悄不再运行」时完全沉默——而那恰恰最危险。
    """
    dest = None
    try:
        dest = await asyncio.to_thread(_do_backup)
    except Exception as e:  # noqa: BLE001
        logger.exception("自动备份抛异常")
        await _alert(context, f"⚠️ 记忆自动备份失败了：{type(e).__name__}: {e}\n"
                              "先别慌，手动 /backup 试一次；连着几天这样就得上服务器看。")
    else:
        if dest is None:
            await _alert(context, "⚠️ 记忆自动备份没找到 buckets 目录——"
                                  f"它该在 {BUCKETS_DIR}。是不是路径变了、或者大脑没在这台机器上？")
        else:
            bad = _verify_backup(dest)
            if bad:
                await _alert(context, f"⚠️ 记忆备份生成了，但验证不过：{bad}\n"
                                      "这份不能信，当没备份处理。手动 /backup 看看。")
            else:
                logger.info("自动备份 OK：%s", os.path.basename(dest))

    # 独立的第二道：不管上面成没成，看看库里最新那份有多旧。
    # 这条能抓住「任务已经好几轮没真的跑成功」——只靠上面那段是抓不到的。
    age = _newest_backup_age_h()
    if age is None:
        await _alert(context, "⚠️ 一份记忆备份都没有。要么从没成功过，要么备份目录被清了。")
    elif age > BACKUP_STALE_H:
        await _alert(context, f"⚠️ 最新的记忆备份已经是 {age:.0f} 小时前的了"
                              f"（该每 {BACKUP_EVERY_H:.0f} 小时一份）。备份可能悄悄停了。")


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
    app.add_handler(CommandHandler("persona", persona_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    if app.job_queue:
        # 每分钟看一眼；真正的间隔由 NUDGE_MINUTES 判断，这样她刚说完话
        # 到下一次找她之间是准的，不会被 15 分钟的粗粒度拖成 30 分钟。
        app.job_queue.run_repeating(check_inactivity, interval=60, first=60)
        # 自动备份：每 BACKUP_EVERY_H 小时一次，启动 2 分钟后先跑一次
        # （这样每次重启都会顺手存一份，也顺手验证一次报警链路通不通）。
        app.job_queue.run_repeating(auto_backup,
                                    interval=BACKUP_EVERY_H * 3600, first=120)
        logger.info("自动备份已开：每 %.0f 小时一份，超过 %.0f 小时没新备份就报警",
                    BACKUP_EVERY_H, BACKUP_STALE_H)
        logger.info("主动找她已开：每 %d 分钟一次，一段沉默最多 %d 次%s",
                    NUDGE_MINUTES, NUDGE_MAX,
                    f"，{NUDGE_QUIET} 点之间不打扰" if NUDGE_QUIET else "")
    else:
        logger.warning("没有 job_queue，主动找她这条不会生效"
                       "（装 python-telegram-bot[job-queue]）")
    # 把命令注册给 Telegram：她打一个 / 就有菜单，不用记。
    # ⚠️ 失败不能拦住启动——菜单没了只是不方便，他不理她才是事故。
    async def _set_menu(_app):
        try:
            await _app.bot.set_my_commands(
                [BotCommand(n, d) for n, d in BOT_COMMANDS])
            logger.info("命令菜单已注册（%d 条）", len(BOT_COMMANDS))
        except Exception:  # noqa: BLE001
            logger.warning("命令菜单注册失败，输入框里不会弹出来", exc_info=True)

    app.post_init = _set_menu
    _load_sessions()
    logger.info("Claude Code Telegram 桥启动 | workdir=%s | 接回 %d 段对话",
                CC_WORKDIR, len(sessions))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
