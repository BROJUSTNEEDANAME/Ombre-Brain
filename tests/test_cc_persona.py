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
