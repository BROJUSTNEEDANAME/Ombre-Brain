"""读 systemd 配置文件的测试。

由来：手动跑 backfill 和 sweep 各报过一次「missing API key」——key 在服务的
EnvironmentFile 里，shell 里没有。同一个错踩两次才抽出来，这里钉住它的行为。
"""
import os

import env_file


def test_parse_basic_and_quotes():
    got = env_file.parse(
        "# 注释\n"
        "\n"
        "LLM_API_KEY=abc123\n"
        'QUOTED="有 空格的值"\n'
        "SINGLE='单引号'\n"
        "WITH_COMMENT=value # 后面是注释\n"
        "  SPACED = 前后有空格 \n"
        "坏行没有等号\n"
    )
    assert got["LLM_API_KEY"] == "abc123"
    assert got["QUOTED"] == "有 空格的值"
    assert got["SINGLE"] == "单引号"
    assert got["WITH_COMMENT"] == "value"
    assert got["SPACED"] == "前后有空格"
    assert "坏行没有等号" not in got


def test_parse_keeps_equals_inside_value():
    """base64 的 key 常带 =，不能在第一个等号之后就截断。"""
    assert env_file.parse("K=a=b=c")["K"] == "a=b=c"


def test_load_does_not_override_existing_by_default(tmp_path, monkeypatch):
    """命令行显式传的优先——不许被配置文件盖掉。"""
    (tmp_path / ".env.apibot").write_text("LLM_API_KEY=fromfile\nONLY_FILE=x\n",
                                          encoding="utf-8")
    monkeypatch.setattr(env_file, "service_env_file", lambda s: "")
    monkeypatch.setenv("LLM_API_KEY", "fromshell")
    monkeypatch.delenv("ONLY_FILE", raising=False)

    used = env_file.load(repo_dir=str(tmp_path))
    assert used.endswith(".env.apibot")
    assert os.environ["LLM_API_KEY"] == "fromshell"      # 没被覆盖
    assert os.environ["ONLY_FILE"] == "x"                # 缺的补上了


def test_load_can_override_when_asked(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("K=fromfile\n", encoding="utf-8")
    monkeypatch.setattr(env_file, "service_env_file", lambda s: "")
    monkeypatch.setenv("K", "fromshell")
    env_file.load(repo_dir=str(tmp_path), override=True)
    assert os.environ["K"] == "fromfile"


def test_load_without_any_file_is_silent(tmp_path, monkeypatch):
    """没有配置文件不许抛异常——调用方要能自己给出人话提示。"""
    monkeypatch.setattr(env_file, "service_env_file", lambda s: "")
    assert env_file.load(repo_dir=str(tmp_path)) == ""


def test_service_env_file_strips_systemd_dash_prefix(monkeypatch):
    """systemd 输出形如 -/path/.env（ignore_errors=yes)，前面那个横杠不是路径。"""
    class _R:
        stdout = "-/home/ombre/Ombre-Brain/.env.apibot (ignore_errors=yes)\n"

    monkeypatch.setattr(env_file.subprocess, "run", lambda *a, **k: _R())
    assert env_file.service_env_file("ombre-brain") == \
        "/home/ombre/Ombre-Brain/.env.apibot"


def test_service_env_file_survives_no_systemd(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("systemctl 不存在")

    monkeypatch.setattr(env_file.subprocess, "run", boom)
    assert env_file.service_env_file("x") == ""
