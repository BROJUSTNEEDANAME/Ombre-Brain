"""cc-status.sh：不知道的时候，它必须说「不知道」。

这条规矩来自 Cheiineeey/relay-cache-where-it-breaks 第 2 节：

    加任何判定之前，先问一句：**不知道的时候，它会说什么？**
    如果答案是「跟坏消息一样」，那这个判定就是错的。

而这个脚本自己就犯过：git fetch 失败（不知道）它印的是「跟远端一致 ✅」。
她照着信了两轮，我也跟着往错方向查了两轮。

所以这个文件不读源码——它**真的把脚本跑一遍**，在一个「什么都看不见」的
环境里，然后检查它有没有嘴硬。
"""
import os
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SH = _ROOT / "scripts" / "cc-status.sh"

# 「看不见」的时候绝不许出现的话——每一句都是一个确定的结论
FORBIDDEN_WHEN_BLIND = [
    "跟远端一致 ✅",
    "这份代码没有",
    "内容不对",
    "没有记到空回复",
    "跑的是**旧代码**",
]


def _run(tmp_path, *, journalctl_ok: bool, repo: pathlib.Path):
    """在一个全是替身的 PATH 上跑真脚本。journalctl_ok=False 模拟读不到日志。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def stub(name, body):
        f = bin_dir / name
        f.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        f.chmod(0o755)

    # journalctl 失败：权限不够（真机上非 root 跑就是这样）
    stub("journalctl", "echo 'Failed to add match: Operation not permitted' >&2\nexit 1\n"
         if not journalctl_ok else "exit 0\n")
    # systemctl：什么都查不到
    stub("systemctl", "exit 1\n")
    # runuser：把 git 命令原样跑掉，但 fetch 一定失败（模拟 .git 权限问题）
    stub("runuser", 'shift 2; if [ "$1" = "--" ]; then shift; fi\n'
                    'for a in "$@"; do [ "$a" = fetch ] && '
                    '{ echo "insufficient permission" >&2; exit 1; }; done\n'
                    'exec "$@"\n')
    stub("date", 'exec /bin/date "$@"\n')

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["REPO"] = str(repo)
    r = subprocess.run(["bash", str(_SH)], capture_output=True, text=True,
                       env=env, timeout=120)
    return r.stdout + r.stderr


@pytest.fixture
def blind_repo(tmp_path):
    """一个存在、但里面什么都没有的仓库——文件读不到、日志读不到、服务查不到。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty",
                    "-m", "x"], check=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})
    return repo


def test_it_never_states_a_conclusion_it_could_not_measure(tmp_path, blind_repo):
    """什么都看不见的时候，一句确定的结论都不许有。"""
    out = _run(tmp_path, journalctl_ok=False, repo=blind_repo)
    for line in FORBIDDEN_WHEN_BLIND:
        assert line not in out, (
            f"看不见的时候却说了「{line}」——这就是把「没测出来」说成"
            f"「测出来是坏的」。\n实际输出：\n{out}")


def test_it_says_out_loud_that_it_cannot_see(tmp_path, blind_repo):
    """光是「不说错话」不够——得让她知道这一段没测出来，不然她以为一切正常。"""
    out = _run(tmp_path, journalctl_ok=False, repo=blind_repo)
    assert "❓" in out, "至少要有一处明确标成「看不见」"
    assert "读不到日志" in out
    assert "测不出任何结论" in out or "测不出来" in out


def test_a_failed_fetch_is_reported_with_its_remedy(tmp_path, blind_repo):
    out = _run(tmp_path, journalctl_ok=False, repo=blind_repo)
    assert "连不上远端" in out
    assert "chown -R ombre:ombre" in out, "认出权限问题就要直接给出修法"


def test_a_missing_service_is_not_reported_as_a_stopped_one(tmp_path, blind_repo):
    """「没装」和「装了没起来」处置完全不同，不许都说成「没在跑」。"""
    out = _run(tmp_path, journalctl_ok=False, repo=blind_repo)
    assert "没装" in out
