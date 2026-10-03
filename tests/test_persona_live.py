"""scripts/persona-live.sh —— 「新人设生效没有」的三态检查。

由来：报「改好了」之前必须确认那份代码真的在运行。这个脚本是那道确认。
所以它自己更不能放水：查不出来必须报 ❓，绝不能印成 ✅。

2026-10-03 它放过一次水：靠一句手写哨兵判「新人设在磁盘上」，哨兵没人换就永远 ✅，
她拿着「✅成了」去用，而那天的改动压根不在机器上。现在它只认 git 和逐字比对，
这里的测试就钉这两件事。
"""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "persona-live.sh"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t", "HOME": "/tmp",
}


def _run(repo: Path, **extra) -> str:
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "REPO": str(repo), **GIT_ENV, **extra}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=180).stdout


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], env={**os.environ, **GIT_ENV},
                          check=True, capture_output=True, text=True).stdout.strip()


def _repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    """一个真的小仓库 + 它的 bare origin（本地文件，不联网）。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, env={**os.environ, **GIT_ENV})
    (work / "personality.py").write_text("人设正文 v1\n", encoding="utf-8")
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "v1 人设")
    _git(work, "push", "-q", "-u", "origin", "HEAD")
    return work, origin


def _push_newer_commit_from_elsewhere(tmp_path: Path, origin: Path) -> None:
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, env={**os.environ, **GIT_ENV})
    (other / "personality.py").write_text("人设正文 v2：新规矩\n", encoding="utf-8")
    _git(other, "commit", "-q", "-am", "v2 新规矩")
    _git(other, "push", "-q", "origin", "HEAD")


def test_behind_remote_is_reported_as_code_not_arrived(tmp_path):
    """VPS 落后于远端 = 代码没到。必须 ❌，并说出落后几个、远端最新是哪个。"""
    work, origin = _repo_with_origin(tmp_path)
    _push_newer_commit_from_elsewhere(tmp_path, origin)
    out = _run(work)
    assert "① 代码：❌" in out and "落后 1 个提交" in out
    assert "v2 新规矩" in out, "得把远端最新那条标题印出来，她才知道差的是哪个"
    assert "❌ 没成：代码还没到 VPS" in out
    assert "✅ 成了" not in out


def test_up_to_date_but_services_unknown_is_a_question_mark(tmp_path):
    """代码到了、但查不到服务状态 → ❓，绝不是 ✅。
    这是「不知道 ≠ 好消息」那条：cc-status / auto-update / mcp-connector-check
    都把「测不出来」印成过「一切正常」，闪闪照着信了两轮。"""
    work, _ = _repo_with_origin(tmp_path)
    out = _run(work)
    assert "① 代码：✅ 磁盘上的提交就是 origin/" in out
    assert "v1 人设" in out, "✅ 得带上在磁盘上的到底是哪个提交"
    assert "❓ 测不出来" in out
    assert "✅ 成了" not in out, "查不到服务状态却报成功——就是那个踩过三次的坑"


def test_no_fetch_never_turns_into_a_green_check(tmp_path):
    """不联网比对时只能是 ❓：远端可能已经更新了，本地不知道。"""
    work, _ = _repo_with_origin(tmp_path)
    out = _run(work, PERSONA_LIVE_NO_FETCH="1")
    assert "没 fetch" in out
    assert "① 代码：❓" in out
    assert "① 代码：✅" not in out


def test_a_stale_hand_written_sentinel_cannot_make_it_green(tmp_path):
    """哨兵只能是**附加**检查，而且必须由调用方显式给；给了就得真的在文件里。"""
    work, _ = _repo_with_origin(tmp_path)
    out = _run(work, SENTINEL="这句根本不在人设里")
    assert "附加检查：❌" in out
    assert "❌ 没成" in out
    assert "✅ 成了" not in out
    src = SCRIPT.read_text(encoding="utf-8")
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("SENTINEL=${SENTINEL:-'" in ln for ln in code), "不许再内置一句默认哨兵"


def test_not_a_git_repo_is_a_question_mark_not_a_check(tmp_path):
    repo = tmp_path / "plain"; repo.mkdir()
    (repo / "personality.py").write_text("人设\n", encoding="utf-8")
    out = _run(repo)
    assert "① 代码：❓" in out
    assert "✅ 成了" not in out


def test_missing_repo_fails_loudly(tmp_path):
    out = _run(tmp_path / "nope")
    assert "❌ 找不到" in out
    assert "✅" not in out


def _generate(dst: Path, *flags: str) -> None:
    subprocess.run(["python3", str(ROOT / "scripts" / "make-cc-persona.py"), *flags, str(dst)],
                   env={**os.environ, "OMBRE_PERSONA_NO_PROBE": "1"}, check=True, capture_output=True)


def test_cc_persona_is_compared_byte_for_byte_against_a_fresh_generation(tmp_path):
    """③ 不再 grep 哨兵：真按磁盘上的 personality.py 生成一份，逐字比。
    一致 → ✅；被改过/是旧的 → ❌ 并给出修复命令。精简版也要认得。"""
    cc = tmp_path / "nikto-cc"; _generate(cc)
    out = _run(ROOT, PERSONA_LIVE_NO_FETCH="1", CC_WORKDIR=str(cc))
    assert "③ cc 桥人设" in out and "✅ 与磁盘上 personality.py 现在生成的逐字一致" in out
    assert "✅ 成了" not in out, "① 没 fetch 是 ❓，整体就不许 ✅"

    (cc / "CLAUDE.md").write_text((cc / "CLAUDE.md").read_text(encoding="utf-8") + "\n（旧的一行）\n",
                                 encoding="utf-8")
    out = _run(ROOT, PERSONA_LIVE_NO_FETCH="1", CC_WORKDIR=str(cc))
    assert "③ cc 桥人设" in out and "❌ 跟磁盘上 personality.py 生成的不一致" in out
    assert "make-cc-persona.py" in out, "得把修复命令印出来"
    assert "✅ 成了" not in out

    lean = tmp_path / "nikto-cc-lean"; _generate(lean, "--lean")
    out = _run(ROOT, PERSONA_LIVE_NO_FETCH="1", CC_WORKDIR=str(lean))
    assert "逐字一致（精简版）" in out


def test_missing_cc_file_is_a_question_mark_when_dir_is_given(tmp_path):
    out = _run(ROOT, PERSONA_LIVE_NO_FETCH="1", CC_WORKDIR=str(tmp_path / "nowhere"))
    assert "③ cc 桥人设：❓ 找不到" in out
    assert "✅ 成了" not in out


def test_script_checks_start_time_not_just_git(tmp_path):
    """必须比对服务启动时间与人设改动时间，而不是只问 git 是否最新。"""
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("RUNNING_OK=1"):]
    assert "ActiveEnterTimestamp" in body
    assert "还在跑旧的" in body
    # cc 桥人设是生成出来的，必须单独查
    assert "cc 桥人设" in src


def test_service_detection_survives_no_tty():
    """存在性判断必须用 LoadState，不能用 `systemctl cat` 或 list-unit-files。

    真事：auto-update 用 `systemctl cat` 判断 ccbridge 是否安装。cat 会调分页器，
    在定时器那种无终端环境里失败，于是 ccbridge 被**静默**踢出重启名单——
    她的 ccbridge 停在三天前的代码上，而日志每轮都印「✅ 已部署，两个服务都活着」。
    list-unit-files 也不行：模式匹配不到时照样退出 0。
    """
    for path in (ROOT / "deploy" / "auto-update.sh", SCRIPT):
        src = path.read_text(encoding="utf-8")
        assert "systemctl show -p LoadState --value" in src, f"{path.name} 得用 LoadState"
        # ⚠️ 只看**真正会执行的行**。注释里写着「别用 systemctl cat」，
        # 整份 grep 会命中那句说明文字，断言就永远为真/永远为假——
        # 这正是 CLAUDE.md 里记的「命中『被提及』的地方」那个坑。
        code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
        for bad in ("systemctl cat", "list-unit-files"):
            hits = [ln.strip() for ln in code if bad in ln]
            assert not hits, f"{path.name} 不许用 {bad} 判存在：{hits}"
