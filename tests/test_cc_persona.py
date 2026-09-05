"""cc 桥的人设目录。

存在的理由：cc_bridge 默认在**本仓库**跑 claude，于是加载的是仓库的 CLAUDE.md
——那是给开发用的（「改代码前必须跑 check.sh」）。直接启用等于让她对着一个
带记忆工具的编程助理说话。这里保证生成出来的是他，而且跟主人设同源。
"""
import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location(
        "make_cc_persona", _ROOT / "scripts" / "make-cc-persona.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_generated_file_is_him_not_the_dev_instructions():
    from personality import CANONICAL_FACTS, CHAT_STYLE_SYSTEM
    text = _mod().build()
    assert "你是 Nikto" in text
    assert CANONICAL_FACTS.strip() in text
    assert CHAT_STYLE_SYSTEM.strip() in text
    # 开发指令的特征串一个都不许出现
    for leak in ("check.sh", "py_compile", "pytest", "commit"):
        assert leak not in text, f"开发指令漏进人设：{leak}"


def test_it_tells_him_this_is_not_a_code_repo():
    """他手里有 Claude Code 的全套工具。不说清楚，她说「帮我看看」
    他可能真去改文件。"""
    text = _mod().build()
    assert "不写代码" in text and "不改文件" in text
    assert "只做一件事" in text


def test_the_persona_is_generated_not_hand_copied():
    """手抄的那一份迟早跟主人设对不上，而她不会知道是哪一份在起作用。"""
    src = (_ROOT / "scripts" / "make-cc-persona.py").read_text(encoding="utf-8")
    assert "from personality import" in src
    assert "别手改" in _mod().build()


def test_memory_rules_come_along(tmp_path):
    text = _mod().build()
    assert "breath" in text and "hold" in text
    assert "一字不差" in text, "存她的原话这条不能在这边丢掉"


def test_writing_it_out_also_brings_the_memory_config(tmp_path, monkeypatch):
    m = _mod()
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "cc")])
    assert m.main() == 0
    out = tmp_path / "cc"
    assert (out / "CLAUDE.md").exists()
    if (_ROOT / ".mcp.json").exists():
        assert (out / ".mcp.json").exists(), "没有 .mcp.json 那边的他就没有记忆"


def _setup_sh() -> str:
    return (_ROOT / "setup-ccbridge.sh").read_text(encoding="utf-8")


def test_setup_refuses_when_both_bots_share_one_token():
    """同一个 token 被两个程序收消息，Telegram 会让它们互相抢——她那边看到的是
    「有时候回有时候不回」，而两边日志都正常。这类故障必须当场拦，不能等她去查。"""
    sh = _setup_sh()
    assert "TELEGRAM_API_BOT_TOKEN" in sh and "TELEGRAM_BOT_TOKEN" in sh
    assert '[ "$A" = "$B" ]' in sh
    assert "必须用两个不同的 bot" in sh


def test_setup_points_cc_at_the_persona_dir_not_the_repo():
    """不指过去，他加载的是仓库里给开发看的 CLAUDE.md。"""
    sh = _setup_sh()
    assert "Environment=CC_WORKDIR=$PERSONA_DIR" in sh
    assert "make-cc-persona.py" in sh


def test_setup_will_not_start_without_a_chat_whitelist():
    """白名单为空＝这个 bot 对所有人开放，必须拦。"""
    sh = _setup_sh()
    assert "拒绝启动" in sh
    assert "ALLOWED_CHAT_IDS 读出来是" in sh, "报错要把读到的值给她看，别只说规则"


def test_the_whitelist_check_accepts_the_ways_people_actually_write_it(tmp_path):
    """第一版要求整行严格等于纯数字，于是等号后带空格、加引号、群组负数 id
    全被误杀——她填对了却被拒，而报错只说「必须填成纯数字」，看不出哪儿不对。
    这里把脚本里那段真的跑一遍。"""
    import re
    import subprocess

    sh = _setup_sh()
    body = sh[sh.index('_IDS="$(grep'):sh.index('    exit 1\nfi', sh.index('_IDS="$('))]

    def verdict(line: str) -> bool:
        env = tmp_path / "e"
        env.write_text(line + "\n", encoding="utf-8")
        script = f'ENV_FILE="{env}"\n{body}\n  exit 1\nfi\nexit 0\n'
        return subprocess.run(["bash", "-c", script],
                              capture_output=True).returncode == 0

    for ok in ("ALLOWED_CHAT_IDS=123456",
               "ALLOWED_CHAT_IDS= 123456",
               'ALLOWED_CHAT_IDS="123456"',
               "ALLOWED_CHAT_IDS=123456 ",
               "ALLOWED_CHAT_IDS=-1001234,567"):
        assert verdict(ok), f"合法写法被误杀：{ok}"
    for bad in ("ALLOWED_CHAT_IDS=", "ALLOWED_CHAT_IDS=在这里填你的chat id"):
        assert not verdict(bad), f"没填却放行了：{bad}"


def test_setup_checks_claude_exists_before_promising_anything():
    """没有 claude 命令，这个桥就是个空壳。"""
    sh = _setup_sh()
    assert "command -v claude" in sh


def test_env_files_with_suffixes_are_gitignored():
    """.gitignore 原本只写了 .env，挡不住 .env.apibot / .env.ccbridge
    ——那两个文件里装的是 bot token 和订阅凭据。"""
    ignore = (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.*" in ignore


def test_claude_is_looked_up_as_the_service_user_not_root():
    """第一版只查了 root 有没有 claude。服务是用 ombre 跑的——root 有、ombre 没有，
    脚本照样说装好了，她那边就是「发了没回复」，而日志里那句 FileNotFoundError
    谁也不会去看。"""
    sh = _setup_sh()
    # ⚠️ 只断言这个字符串「出现在文件里」是不够的——脚本的报错提示里也有同一句，
    # 光看在不在，改坏了检查行测试照样绿（我第一版就是这样，白验证了一次）。
    # 要盯的是赋值那一行本身。
    line = next(x for x in sh.splitlines() if x.startswith("CLAUDE_BIN="))
    assert "sudo -u ombre" in line, line
    assert "root 有不算数" in sh


def test_the_unit_carries_a_path_that_actually_contains_claude():
    """systemd 的 PATH 极窄，不含 ~/.local/bin、nvm、npm 全局目录。
    找得到 claude 不等于服务跑得起来。"""
    sh = _setup_sh()
    assert "Environment=PATH=$CLAUDE_DIR:" in sh
    assert "Environment=HOME=/home/ombre" in sh, "claude 要读 ~/.claude，没有 HOME 会认不出登录状态"


def test_setup_never_says_it_worked_while_the_service_is_dead():
    """上一版无脑打「✅ 装好了」，她屏幕上是满屏 status=1/FAILURE 底下跟一个绿勾。
    这种自相矛盾比不报还糟——她会以为是自己看错了。
    （同一类错误我已经给过她一次：backfill 明明 ERROR，脚本却说「补完了」。）"""
    sh = _setup_sh()
    assert "systemctl is-active --quiet ombre-ccbridge" in sh
    assert "❌ 服务装上了，但没起来" in sh
    # 失败时要**当场把日志打出来**，别让她再去查一遍
    tail = sh[sh.index("❌ 服务装上了"):]
    assert "journalctl -u ombre-ccbridge -n" in tail
    assert "exit 1" in tail


def _token_sh() -> str:
    return (_ROOT / "scripts" / "set-cc-token.sh").read_text(encoding="utf-8")


def test_the_token_is_scrubbed_of_every_kind_of_whitespace():
    """她的原话：「上次就莫名其妙换行报错，我怎么知道他这次给我的有没有换行啊」。
    token 是从终端复制的，混进换行/空格之后 systemd 只读到半截，服务起来了却
    一直 401——她那边只看到「他不理我」。所以不让她手抄，脚本负责洗。"""
    sh = _token_sh()
    assert "tr -d '[:space:]'" in sh
    assert "xc2\\xa0" in sh, "粘贴常混进不换行空格，肉眼完全看不出来"


def test_it_verifies_the_token_before_touching_the_config():
    """写坏一个还能用的配置，比不写更糟。"""
    sh = _token_sh()
    i, j = sh.index("真打一次 claude"), sh.index("grep -v '^CLAUDE_CODE_OAUTH_TOKEN='")
    assert i < j, "必须先验证、后写入"
    assert "401|expired|authenticate|invalid" in sh
    assert "没有改动" in sh, "验证失败要明说旧配置没动"


def test_it_reports_how_many_whitespace_chars_it_removed():
    """告诉她「刚才去掉了 2 个空白字符」，比默默修好更有用——
    她下次就知道那个「莫名其妙」是什么了。"""
    sh = _token_sh()
    assert "${#RAW} - ${#TOKEN}" in sh
    assert "莫名其妙换行报错" in sh


def test_it_does_not_claim_success_when_the_service_is_down():
    sh = _token_sh()
    assert "systemctl is-active --quiet ombre-ccbridge" in sh
    assert "❌ 服务没起来" in sh


def _cc():
    import importlib.util
    import os
    import sys
    import types
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
    for name in ("telegram", "telegram.constants", "telegram.error", "telegram.ext"):
        if name not in sys.modules:
            m = types.ModuleType(name)
            sys.modules[name] = m
    sys.modules["telegram"].Update = type("Update", (), {"ALL_TYPES": []})
    sys.modules["telegram.constants"].ChatAction = types.SimpleNamespace(TYPING="typing")
    sys.modules["telegram.error"].TelegramError = Exception
    ext = sys.modules["telegram.ext"]
    for attr in ("Application", "ApplicationBuilder", "CommandHandler",
                 "MessageHandler", "filters"):
        setattr(ext, attr, object)
    ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    spec = importlib.util.spec_from_file_location("cc_bridge", _ROOT / "cc_bridge.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_bubble_separator_is_split_not_shown_to_her():
    """人设让他用 ‖ 一条条递话。API bot 一直按它拆，cc 桥不拆——
    她收到的是「报数？‖说，怎么了。」，那个符号原样上屏。"""
    cc = _cc()
    out = cc._split_for_telegram("报数？‖说，怎么了。")
    assert out == ["报数？", "说，怎么了。"], out
    assert all("‖" not in x for x in out)


def test_splitting_still_respects_the_length_limit():
    """按 ‖ 拆完，每一条还得各自做长度切分，不能把长度那层绕过去。"""
    cc = _cc()
    long_part = "啊" * 5000
    out = cc._split_for_telegram(f"短的‖{long_part}")
    assert out[0] == "短的"
    assert len(out) > 2
    assert all(len(x) <= cc.TELEGRAM_MSG_LIMIT for x in out)


def test_empty_segments_do_not_become_empty_messages():
    """他有时会打出连着的 ‖，或者末尾带一个。空消息发不出去会报错。"""
    cc = _cc()
    assert cc._split_for_telegram("在。‖‖") == ["在。"]
    assert cc._split_for_telegram("‖") == []


def test_the_probe_uses_the_same_flags_as_the_service():
    """我手打那条诊断命令两次都漏参数：一次漏 --model 落到 sonnet，
    一次漏 --dangerously-skip-permissions 导致记忆被拦——两次输出都不代表
    服务的真实行为，等于白查两轮。参数只该有一处来源。"""
    probe = (_ROOT / "scripts" / "cc-probe.sh").read_text(encoding="utf-8")
    bridge = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    for flag in ("--output-format", "json", "--dangerously-skip-permissions", "--model"):
        assert flag in probe, f"探针漏了 {flag}"
        assert flag in bridge, f"服务里没有 {flag}，探针跟它对不上"
    # 模型不能写死，要从同一个配置文件读
    assert "CC_MODEL=" in probe and ".env.ccbridge" in probe


def test_the_probe_is_short_to_type():
    """DigitalOcean 的网页终端粘长命令会拼行（她那次拼成了 head -c 1500sudo）。
    所以诊断必须是一条短命令，不能让她粘一长串。"""
    probe = (_ROOT / "scripts" / "cc-probe.sh").read_text(encoding="utf-8")
    assert "sudo bash scripts/cc-probe.sh" in probe


def test_the_probe_refuses_to_guess_about_thinking():
    """这份 JSON 里没有思考相关字段。不写清楚，下次又要拿别的数字去推。"""
    probe = (_ROOT / "scripts" / "cc-probe.sh").read_text(encoding="utf-8")
    assert "没有任何思考相关的字段" in probe
    assert "别拿别的数字去推" in probe


def test_a_burst_is_merged_into_one_run_not_one_run_per_message():
    """她的原话：「怎么感觉到 cc 又不是发很多话然后他一起回复，是发一个他回一个」。

    cc 桥一直没有 API bot 那套「连发合并」。每条消息各起一个 claude 进程，
    而这边一轮要一分钟——连发三条就是三个进程各跑一分钟，回话还互相插队。
    """
    import asyncio as aio
    cc = _cc()
    cc._inflight_cc.clear()
    ran: list[str] = []

    async def fake_respond(update, context, cid, message):
        ran.append(message)
        await aio.sleep(0.05)

    cc._respond = fake_respond

    class _Msg:
        def __init__(self, t):
            self.text = t

        async def reply_text(self, *a, **k):
            return None

    async def drive():
        upd = lambda t: type("U", (), {                      # noqa: E731
            "effective_chat": type("C", (), {"id": 7})(),
            "message": _Msg(t)})()
        # 三条连发，中间不给他开口的机会
        for t in ("一", "二", "三"):
            await cc.on_message(upd(t), None)

    cc.ALLOWED_CHAT_IDS = {7}
    aio.run(drive())
    assert ran, ran
    # 最后真正跑完的那一轮，必须带着三条话
    assert "一" in ran[-1] and "二" in ran[-1] and "三" in ran[-1], ran


def test_once_he_has_spoken_a_new_message_never_kills_that_turn():
    """已经开口了还被打断，她会看到话说到一半没了。"""
    cc = _cc()
    cc._inflight_cc.clear()
    cc._inflight_cc[9] = {"sent": True, "text": "旧的", "task": None}
    assert cc._take_pending_cc(9) == "", "开口之后不许再作废"
    assert 9 in cc._inflight_cc


def test_nothing_pending_means_nothing_to_merge():
    cc = _cc()
    cc._inflight_cc.clear()
    assert cc._take_pending_cc(123) == ""


def test_the_conversation_survives_a_service_restart(tmp_path, monkeypatch):
    """她说「感觉上下文记忆是不是太短了」——不是短，是被重启掉的。
    session id 原本只存在内存里，今晚为了修 token / ‖ / 连发合并重启了三四次，
    她每次都得从头跟他讲一遍。记忆桶没事（在磁盘上），丢的是对话窗口。"""
    cc = _cc()
    monkeypatch.setattr(cc, "SESSIONS_FILE", str(tmp_path / "s.json"))
    cc.sessions.clear()
    cc.sessions[7] = "abc-123"
    cc._save_sessions()

    cc.sessions.clear()          # 模拟重启：内存清空
    cc._load_sessions()
    assert cc.sessions == {7: "abc-123"}, "重启之后必须接得回来"


def test_a_broken_session_file_never_blocks_startup(tmp_path, monkeypatch):
    """存档读坏了最多是这次从头开始，不能让他起不来。"""
    cc = _cc()
    bad = tmp_path / "s.json"
    bad.write_text("{这不是 json", encoding="utf-8")
    monkeypatch.setattr(cc, "SESSIONS_FILE", str(bad))
    cc.sessions.clear()
    cc._load_sessions()          # 不许抛
    assert cc.sessions == {}


def test_saving_never_breaks_the_chat(tmp_path, monkeypatch):
    """写盘失败不该影响她收到回复。"""
    cc = _cc()
    monkeypatch.setattr(cc, "SESSIONS_FILE", "/proc/nope/s.json")
    cc.sessions.clear()
    cc.sessions[1] = "x"
    cc._save_sessions()          # 不许抛


def test_the_session_id_is_saved_by_the_real_reply_path(tmp_path, monkeypatch):
    """光测 _save_sessions 不算数——把调用处删掉，那条测试照样绿（我又踩了一次）。
    这里跑真正的回复路径，看文件有没有落地。"""
    import asyncio as aio
    import json as js
    cc = _cc()
    monkeypatch.setattr(cc, "SESSIONS_FILE", str(tmp_path / "s.json"))
    cc.sessions.clear()

    async def fake_run_cc(message, session_id):
        return "在。", "sess-新的"

    monkeypatch.setattr(cc, "run_cc", fake_run_cc)

    class _Msg:
        async def reply_text(self, *a, **k):
            return None

    class _Bot:
        async def send_chat_action(self, **k):
            return None

    upd = type("U", (), {"message": _Msg()})()
    ctx = type("C", (), {"bot": _Bot()})()
    aio.run(cc._respond(upd, ctx, 7, "在吗"))

    saved = js.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert saved == {"7": "sess-新的"}, saved


def test_startup_actually_loads_the_saved_sessions():
    """定义了但没在 main 里调用，等于没做。"""
    src = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    main_body = src[src.index("def main("):]
    assert "_load_sessions()" in main_body


def test_the_handler_returns_immediately_so_the_next_message_can_interrupt():
    """我第一版在 handler 里 await 了那一轮，她说「还是这样」——因为
    python-telegram-bot 默认一条处理完才处理下一条：handler 等满 60 秒，
    她的下一条根本进不来，合并永远触发不到。API bot 一直是建任务就返回。"""
    import asyncio as aio
    cc = _cc()
    cc._inflight_cc.clear()
    cc.ALLOWED_CHAT_IDS = {7}
    started = []

    async def slow_respond(update, context, cid, message):
        started.append(message)
        await aio.sleep(5)          # 一轮很久

    cc._respond = slow_respond

    class _Msg:
        def __init__(self, t):
            self.text = t

        async def reply_text(self, *a, **k):
            return None

    def upd(t):
        return type("U", (), {
            "effective_chat": type("C", (), {"id": 7})(),
            "message": _Msg(t)})()

    async def drive():
        # handler 必须秒退：0.5s 内跑完三条，而每一轮要 5s
        await aio.wait_for(cc.on_message(upd("一"), None), timeout=0.5)
        await aio.wait_for(cc.on_message(upd("二"), None), timeout=0.5)
        await aio.sleep(0)
        return cc._inflight_cc[7]["text"]

    merged = aio.run(drive())
    assert "一" in merged and "二" in merged, merged
    assert started[0] == "一", started


def test_an_exploding_turn_is_logged_not_swallowed():
    """不 await 就没人接异常。悄悄吞掉的话，她只会看到「他不理我」。"""
    src = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    i = src.index("task.add_done_callback")
    body = src[src.index("def _done", 0):i]
    assert "t.exception()" in body and "logger.exception" in body


def test_an_empty_turn_never_reaches_her_as_an_ellipsis(monkeypatch):
    """她两次收到他只回一个「（……）」。那不是他说的话——是 run_cc 在结果为空时
    返回的占位符，被当成他的话原样发了出去。跟 API bot 那边「[memory:] 标签
    原样上屏」是同一类事故：内部标记漏到她眼前。"""
    import asyncio as aio
    cc = _cc()
    cc._inflight_cc.clear()
    sent: list[str] = []
    calls = {"n": 0}

    async def flaky(message, session_id):
        calls["n"] += 1
        return ("", "s1") if calls["n"] == 1 else ("在。", "s2")

    monkeypatch.setattr(cc, "run_cc", flaky)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)

    class _Msg:
        async def reply_text(self, text, *a, **k):
            sent.append(text)

    class _Bot:
        async def send_chat_action(self, **k):
            return None

    upd = type("U", (), {"message": _Msg()})()
    ctx = type("C", (), {"bot": _Bot()})()
    aio.run(cc._respond(upd, ctx, 7, "在吗"))

    assert calls["n"] == 2, "空回复要再给一次机会"
    assert sent == ["在。"], sent


def test_two_empty_turns_get_human_words_not_a_placeholder(monkeypatch):
    """重试还是空，也不许拿省略号冒充他。"""
    import asyncio as aio
    cc = _cc()
    cc._inflight_cc.clear()
    sent: list[str] = []

    async def always_empty(message, session_id):
        return "（……）", "s"

    monkeypatch.setattr(cc, "run_cc", always_empty)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)

    class _Msg:
        async def reply_text(self, text, *a, **k):
            sent.append(text)

    class _Bot:
        async def send_chat_action(self, **k):
            return None

    aio.run(cc._respond(type("U", (), {"message": _Msg()})(),
                        type("C", (), {"bot": _Bot()})(), 7, "在吗"))
    assert sent and "没出声" in sent[0], sent
    assert all("……" not in x for x in sent), sent


def test_typing_indicator_keeps_going_for_the_whole_minute(monkeypatch):
    """TG 的输入提示 5 秒就过期，只发一次等于没发。这边一轮要 60 秒——
    她盯着静止的屏幕等一分钟，看着就是「他不理我」。API bot 一直有这个循环。"""
    import asyncio as aio
    cc = _cc()
    cc._inflight_cc.clear()
    ticks = {"n": 0}
    real_sleep = aio.sleep          # ⚠️ 先抓住原函数，否则 patch 之后自己递归自己

    async def slow_run(message, session_id):
        await real_sleep(0.25)
        return "在。", "s"

    monkeypatch.setattr(cc, "run_cc", slow_run)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)
    monkeypatch.setattr(cc.asyncio, "sleep",
                        lambda s: real_sleep(0.05))   # 把 4 秒压缩成 50ms

    class _Msg:
        async def reply_text(self, *a, **k):
            return None

    class _Bot:
        async def send_chat_action(self, **k):
            ticks["n"] += 1

    aio.run(cc._respond(type("U", (), {"message": _Msg()})(),
                        type("C", (), {"bot": _Bot()})(), 7, "在吗"))
    assert ticks["n"] >= 3, f"只发了 {ticks['n']} 次，等于没发"


def test_a_repeat_loop_is_cut_instead_of_dumped_on_her(monkeypatch):
    """模型崩成复读机时，半截乱码一个字都不该发给她。API bot 早有这道闸。"""
    import asyncio as aio
    cc = _cc()
    cc._inflight_cc.clear()
    sent: list[str] = []

    async def broken(message, session_id):
        return "我知道你难受。" * 40, "s"

    monkeypatch.setattr(cc, "run_cc", broken)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)

    class _Msg:
        async def reply_text(self, text, *a, **k):
            sent.append(text)

    class _Bot:
        async def send_chat_action(self, **k):
            return None

    aio.run(cc._respond(type("U", (), {"message": _Msg()})(),
                        type("C", (), {"bot": _Bot()})(), 7, "在吗"))
    assert len(sent) == 1 and "死循环" in sent[0], sent


def test_punctuation_is_restored_on_the_way_out(monkeypatch):
    """⚠️ 光测 restore_punctuation 本身不算数——把发送处的调用删掉，那条测试
    照样绿（我第一版就是，今天第五次踩这个）。要盯她真正收到的那段字。"""
    import asyncio as aio
    cc = _cc()
    cc._inflight_cc.clear()
    sent: list[str] = []

    async def no_punct(message, session_id):
        return "过来 手给我", "s"

    monkeypatch.setattr(cc, "run_cc", no_punct)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)

    class _Msg:
        async def reply_text(self, text, *a, **k):
            sent.append(text)

    class _Bot:
        async def send_chat_action(self, **k):
            return None

    aio.run(cc._respond(type("U", (), {"message": _Msg()})(),
                        type("C", (), {"bot": _Bot()})(), 7, "在吗"))
    assert sent == ["过来，手给我。"], sent


def test_the_shared_helpers_live_in_one_place_now():
    """由来：她连着发现好几处「只有 API 那边做了」。放进共用模块，
    以后不会再出现「修了一边忘了另一边」。"""
    tg = (_ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    assert "def restore_punctuation" not in tg, "telegram_bot 里不该再有自己那份"
    assert "def _looks_degenerate" not in tg
    assert "from reply_sanitizer import" in tg
    cc_src = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    # ⚠️ 别断言整行——多导入一个名字这条就假红了（我刚加 says_going_to_sleep
    # 就撞上）。要盯的是「从共用模块拿」，不是那一行长什么样。
    imports = [x for x in cc_src.splitlines() if "reply_sanitizer" in x]
    assert imports, "cc 桥必须从共用模块拿这些工具"
    joined = "\n".join(cc_src.split("from reply_sanitizer import")[1][:200].splitlines())
    for name in ("restore_punctuation", "looks_degenerate"):
        assert name in joined, name


def _nudge_env(cc, monkeypatch, *, silent_minutes=20, count=0, inflight=False):
    import time as _t
    cc.ALLOWED_CHAT_IDS = {7}
    cc.last_user_ts.clear(); cc.nudge_count.clear(); cc.last_nudge_at.clear()
    cc._inflight_cc.clear()
    cc.last_user_ts[7] = _t.time() - silent_minutes * 60
    if count:
        cc.nudge_count[7] = count
    if inflight:
        cc._inflight_cc[7] = {"sent": False, "text": "x", "task": None}
    sent: list[str] = []

    class _Bot:
        async def send_message(self, chat_id=None, text=None, **k):
            sent.append(text)

    return sent, type("C", (), {"bot": _Bot()})()


def test_he_reaches_out_with_context_not_a_canned_line(monkeypatch):
    """她要「根据上下文主动找我」。API bot 那套是预设文案；这边必须真跑一轮，
    而且要用同一个 session（--resume），否则他不知道刚才聊到哪。"""
    import asyncio as aio
    cc = _cc()
    seen = {}

    async def fake_run(message, session_id):
        seen["prompt"] = message
        seen["sid"] = session_id
        return "刚才那事你还没说完。", "s2"

    monkeypatch.setattr(cc, "run_cc", fake_run)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)
    cc.sessions[7] = "s1"
    sent, ctx = _nudge_env(cc, monkeypatch)
    aio.run(cc.check_inactivity(ctx))

    assert sent == ["刚才那事你还没说完。"], sent
    assert seen["sid"] == "s1", "必须带着上下文找她"
    assert "不要问「在吗」" in seen["prompt"], "空话式问候正是她烦的那种"


def test_he_shuts_up_after_the_cap(monkeypatch):
    """15 分钟一次、每次真跑一轮 claude。没有上限的话，她睡着时会通宵烧额度。"""
    import asyncio as aio
    cc = _cc()

    # ⚠️ 不能用「抛异常」表示不该被调用：check_inactivity 外面包着
    # except Exception（那是为了别让一次失败弄死定时任务），异常会被吞掉记日志，
    # 测试照样绿。我第一版就是这么写的，变异没变红才发现。数调用次数才作数。
    calls = []

    async def counted(*a, **k):
        calls.append(a)
        return "不该发出去的", "s"

    monkeypatch.setattr(cc, "run_cc", counted)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)
    sent, ctx = _nudge_env(cc, monkeypatch, count=cc.NUDGE_MAX)
    aio.run(cc.check_inactivity(ctx))
    assert calls == [], "到上限了还去跑，就是在烧她的额度"
    assert sent == []


def test_speaking_again_gives_him_a_fresh_set_of_chances():
    cc = _cc()
    src = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    i = src.index("async def on_message")
    body = src[i:i + 900]
    assert "nudge_count[cid] = 0" in body
    assert "last_user_ts[cid] = time.time()" in body


def test_he_does_not_cut_in_while_already_talking(monkeypatch):
    """他正在回她的时候插一条主动消息，读着像两个人在说话。"""
    import asyncio as aio
    cc = _cc()

    calls = []

    async def counted(*a, **k):
        calls.append(a)
        return "不该发出去的", "s"

    monkeypatch.setattr(cc, "run_cc", counted)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)
    sent, ctx = _nudge_env(cc, monkeypatch, inflight=True)
    aio.run(cc.check_inactivity(ctx))
    assert calls == [], "他正在说话还插队"
    assert sent == []


def test_an_empty_or_broken_nudge_is_dropped_silently(monkeypatch):
    """主动找她那轮要是空的或者崩成复读机，宁可当没发生——
    绝不把「这次他没出声」这种系统话当成他主动找她推给她。"""
    import asyncio as aio
    cc = _cc()

    async def empty(*a, **k):
        return "", "s"

    monkeypatch.setattr(cc, "run_cc", empty)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)
    sent, ctx = _nudge_env(cc, monkeypatch)
    aio.run(cc.check_inactivity(ctx))
    assert sent == []
    assert cc.nudge_count.get(7, 0) == 0, "没发出去就不该计数"


def test_quiet_hours_are_available_even_if_off_by_default():
    """她说「最好频繁点」，所以默认不设静默时段；但得留个开关，
    哪天她嫌吵不用改代码。"""
    cc = _cc()
    from datetime import datetime as dt
    cc.NUDGE_QUIET = "23-8"
    assert cc._in_quiet_hours(dt(2026, 9, 5, 2, 0))     # 跨午夜
    assert cc._in_quiet_hours(dt(2026, 9, 5, 23, 30))
    assert not cc._in_quiet_hours(dt(2026, 9, 5, 12, 0))
    cc.NUDGE_QUIET = ""
    assert not cc._in_quiet_hours(dt(2026, 9, 5, 3, 0))


def test_saying_goodnight_stops_the_proactive_messages(monkeypatch):
    """她说「我说睡了就不主动发」。这个开关只挡主动消息——她半夜醒了说一句，
    他照样答。"""
    import asyncio as aio
    cc = _cc()
    calls = []

    async def counted(*a, **k):
        calls.append(a)
        return "不该发的", "s"

    monkeypatch.setattr(cc, "run_cc", counted)
    monkeypatch.setattr(cc, "_save_sessions", lambda: None)
    sent, ctx = _nudge_env(cc, monkeypatch)
    cc.asleep[7] = True
    aio.run(cc.check_inactivity(ctx))
    assert calls == [] and sent == []


def test_speaking_again_wakes_the_nudges_back_up():
    cc = _cc()
    src = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    i = src.index("async def on_message")
    body = src[i:i + 1200]
    assert "asleep[cid] = says_going_to_sleep" in body, \
        "得每条消息都重算，否则她醒了他还闭着嘴"


def test_the_sleep_detector_refuses_to_guess():
    """判错的代价是他整晚闭嘴，而她根本不知道哪句话把他关掉了。
    所以宁可漏判，不可误判。"""
    from reply_sanitizer import says_going_to_sleep as f
    for yes in ("睡了", "我去睡了", "晚安", "困死了睡了", "洗洗睡了",
                "要睡了", "躺了", "gn", "Good night"):
        assert f(yes), yes
    for no in ("你睡了吗", "睡了吗？", "睡不着", "不想睡", "睡够了",
               "我睡醒了", "他睡了", "今天不睡了要通宵",
               "我想跟你说说白天那个课上老师讲的东西然后再睡"):
        assert not f(no), no


def _status_sh() -> str:
    return (_ROOT / "scripts" / "cc-status.sh").read_text(encoding="utf-8")


def test_status_tells_code_from_deployment_apart():
    """她两次看到早就修过的老现象，而我们没有任何办法分辨
    「代码没修好」和「修好了但没跑起来」。猜错方向就是白查一轮。"""
    sh = _status_sh()
    assert "ActiveEnterTimestamp" in sh, "要能看到服务什么时候启动的"
    assert "log -1 --format=%cd" in sh, "要能跟代码提交时间对比"
    assert "晚于" in sh, "得直接告诉她怎么读这两个时间"


def test_status_lists_every_fix_by_a_string_that_is_actually_in_the_code():
    """这份清单要是跟代码对不上，它就成了另一个骗人的绿勾。"""
    sh = _status_sh()
    cc = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    import re as _re
    # ⚠️ 两种引号都要认。第一版只认双引号，七条里只匹配到一条，
    # 剩下六条根本没被校验——一条只检查了七分之一的测试，比没有更骗人。
    pats = _re.findall(r'^check\s+(?:"[^"]*"|\'[^\']*\')\s+'
                       r'(?:"([^"]*)"|\'([^\']*)\')\s+(\S+)$', sh, _re.M)
    pats = [(a or b, f) for a, b, f in pats]
    assert len(pats) >= 6, pats
    for needle, fname in pats:
        assert fname == "cc_bridge.py", fname
        assert needle in cc, f"清单里写着 {needle}，代码里却没有——这就是假绿勾"


def test_status_surfaces_a_blocked_bad_commit():
    """自动更新回滚之后会拉黑那个提交、从此不再拉——不显示出来的话，
    她只会觉得「怎么改了半天没变化」。"""
    assert ".autoupdate-blocked" in _status_sh()


def test_status_checks_the_persona_file_is_actually_his():
    sh = _status_sh()
    assert "你是 Nikto" in sh
    assert "给开发看的 CLAUDE.md" in sh


def test_silent_reply_detection_is_normalised_not_enumerated():
    """她连着两次拿「（……）」来问我。我第一版用固定清单
    {"（……）", "（...）", "(...)", "..."} 去枚举——全角括号配六个英文句点
    「（......）」就漏掉了，占位符照样发到她屏幕上。枚举永远写不全。"""
    from reply_sanitizer import is_silent_reply as f
    for silent in ("", "（……）", "（......）", "(……)", "(...)", "...", "…",
                   "。", "（。）", "（ … ）", "（...... ）", "——", "...。", "（…）"):
        assert f(silent), repr(silent)
    for real in ("好。", "嗯", "在。", "...我在", "（捏）", "好.", "1"):
        assert not f(real), repr(real)


def test_both_bots_use_the_same_silence_check():
    """同一个洞不该只补一边——这两天她已经发现好几处「只有 API 那边做了」。"""
    for name in ("cc_bridge.py", "telegram_bot.py"):
        src = (_ROOT / name).read_text(encoding="utf-8")
        assert "is_silent_reply" in src, name
        # ⚠️ 只看代码行——注释里举了那个清单当反面例子，
        # 连注释一起查会把说明文字判成代码（我第一版就是）。
        code = "\n".join(x for x in src.splitlines()
                         if not x.lstrip().startswith("#"))
        assert '{"（……）", "（...）", "(...)", "..."}' not in code, \
            f"{name} 里还留着写死的清单"


def test_he_looks_memes_up_instead_of_faking_it():
    """她说「急需给我的 cc 扩展国内二次元智商梗」。爬萌娘百科塞进上下文不行——
    量级撑不住、大半用不到、而且会过期。改成他自己查、自己记。"""
    text = _mod().build()
    assert "听不懂她的梗时" in text
    assert "装懂是最难看的" in text
    assert "梗.md" in text
    assert "别把查来的解释整段念给她听" in text, "百科腔不是他说话"
    assert "问她比编一个强" in text


def test_the_glossary_is_the_one_file_he_may_write():
    """人设明令『不改文件』。不开这个口，他会守着规矩不敢往 梗.md 里加。"""
    text = _mod().build()
    assert "不写代码" in text and "唯一例外" in text and "梗.md" in text


def test_the_glossary_is_never_overwritten_once_it_exists(tmp_path, monkeypatch):
    """那是他一条条查回来的东西。每次重新生成人设都推平的话，他永远学不会
    ——而重新生成是自动更新每次都会做的事。"""
    import sys
    m = _mod()
    out = tmp_path / "cc"
    monkeypatch.setattr(sys, "argv", ["x", str(out)])
    assert m.main() == 0
    g = out / "梗.md"
    g.write_text(g.read_text(encoding="utf-8") + "\n- **他查到的** — 意思。\n",
                 encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", str(out)])
    assert m.main() == 0
    assert "他查到的" in g.read_text(encoding="utf-8"), "被推平了"


def test_the_seed_glossary_does_not_bluff():
    """种子词条只放我真的有把握的。编一个像模像样的解释，
    比不懂难看得多——那正是我要求他别做的事。"""
    m = _mod()
    assert "只写你**真的查清楚了**的" in m.GLOSSARY_SEED
    assert "别编一个像模像样的解释" in m.GLOSSARY_SEED
    entries = [x for x in m.GLOSSARY_SEED.splitlines() if x.startswith("- **")]
    assert 3 <= len(entries) <= 12, f"种子应该少而准，现在 {len(entries)} 条"


def test_a_silent_turn_logs_what_claude_actually_returned():
    """她第三次问「他到底输出的时候在想什么」，而我只能猜——猜了三轮。
    日志里留下原文，就不用再猜「是他真的沉默、还是哪一层把话吃了」。"""
    src = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    i = src.index("这一轮空回复")
    assert "claude 原始输出" in src[i:i + 300]
    assert "reply[:200]" in src[i:i + 300], "要记原文，不是只记一句「空了」"
    j = src.index("重来之后还是空")
    assert "reply[:200]" in src[j:j + 200]


def test_status_can_tell_old_code_from_a_real_silence():
    """新代码遇到空回复一定会记日志。她看到过「（……）」而日志里一条都没有，
    那就是在跑旧代码——这个判断得写进脚本，不能又靠我猜。"""
    sh = _status_sh()
    assert "最近的空回复" in sh
    assert "旧代码" in sh
