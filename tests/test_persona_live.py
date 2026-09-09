"""scripts/persona-live.sh —— 「新人设生效没有」的三态检查。

由来：报「改好了」之前必须确认那份代码真的在运行。这个脚本是那道确认。
所以它自己更不能放水：查不出来必须报 ❓，绝不能印成 ✅。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "persona-live.sh"
SENTINEL = "你不对她顶嘴。一次都不。"


def _run(repo: Path) -> str:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "REPO": str(repo)},
        capture_output=True, text=True, timeout=60,
    ).stdout


def _repo(tmp_path: Path, *, sentinel: bool) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    body = "人设正文\n" + (SENTINEL if sentinel else "（这里是旧人设）") + "\n"
    (repo / "personality.py").write_text(body, encoding="utf-8")
    return repo


def test_reports_missing_code_when_sentinel_absent(tmp_path):
    out = _run(_repo(tmp_path, sentinel=False))
    assert "① 新人设在磁盘上：❌ 没有" in out
    assert "❌ 没成：代码还没到 VPS" in out
    assert "✅ 成了" not in out


def test_does_not_claim_success_when_services_unknown(tmp_path):
    """代码在磁盘上、但查不到服务状态时，必须报 ❓ 而不是 ✅。

    这是「不知道 ≠ 好消息」那条：cc-status / auto-update / mcp-connector-check
    都把「测不出来」印成过「一切正常」，闪闪照着信了两轮。
    """
    out = _run(_repo(tmp_path, sentinel=True))
    assert "① 新人设在磁盘上：✅ 有" in out
    assert "❓ 测不出来" in out
    assert "✅ 成了" not in out, "查不到服务状态却报成功——就是那个踩过三次的坑"


def test_missing_repo_fails_loudly(tmp_path):
    out = _run(tmp_path / "nope")
    assert "❌ 找不到" in out
    assert "✅" not in out


def test_script_checks_start_time_not_just_git(tmp_path):
    """必须比对服务启动时间与人设改动时间，而不是只问 git 是否最新。"""
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("RUNNING_OK=1"):]
    assert "ActiveEnterTimestamp" in body
    assert "还在跑旧的" in body
    # cc 桥人设是生成出来的，必须单独查
    assert "cc 桥人设" in src
