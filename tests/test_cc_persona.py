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
    sh = _setup_sh()
    assert "ALLOWED_CHAT_IDS=[0-9]+" in sh
    assert "拒绝开放启动" in sh


def test_setup_checks_claude_exists_before_promising_anything():
    """没有 claude 命令，这个桥就是个空壳。"""
    sh = _setup_sh()
    assert "command -v claude" in sh


def test_env_files_with_suffixes_are_gitignored():
    """.gitignore 原本只写了 .env，挡不住 .env.apibot / .env.ccbridge
    ——那两个文件里装的是 bot token 和订阅凭据。"""
    ignore = (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.*" in ignore
