"""把 deploy/auto-update.sh **从头跑到尾**，在一个假世界里。

由来：部署器的每个零件单看都对，整体却每 5 分钟死在同一行（9/25、10/4），通知函数
自己也会把脚本杀掉（10/6 实测）。她每次都得自己开 VPS。所以这里不测零件，测整轮：
真 git 仓库 + bare origin；systemctl / runuser / logger / curl / install 用替身；
最后看它**发给她的那条 Telegram** 是什么。
"""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "deploy" / "auto-update.sh").read_text(encoding="utf-8")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _sh(p: Path, body: str):
    p.write_text("#!/bin/bash\n" + body, encoding="utf-8"); p.chmod(0o755)


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                   env={**os.environ, **GIT_ENV})


def _world(tmp_path: Path, *, regen_ok=True, restart_moves=True):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
    (seed / "personality.py").write_text("v1\n", encoding="utf-8")
    _git(seed, "add", "-A"); _git(seed, "commit", "-q", "-m", "v1"); _git(seed, "push", "-q", "origin", "HEAD:main")
    repo = tmp_path / "Ombre-Brain"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    (seed / "personality.py").write_text("v2\n", encoding="utf-8")
    _git(seed, "commit", "-q", "-am", "加一条外人那条规矩"); _git(seed, "push", "-q", "origin", "HEAD:main")
    (repo / ".env.ccbridge").write_text("TELEGRAM_BOT_TOKEN=tok\nALLOWED_CHAT_IDS=42\n", encoding="utf-8")
    # 人设生成器替身
    (repo / "scripts").mkdir()
    (repo / "scripts" / "make-cc-persona.py").write_text("", encoding="utf-8")
    venv = repo / ".venv" / "bin"; venv.mkdir(parents=True)
    _sh(venv / "python", "exit 0\n" if regen_ok else "exit 3\n")

    bin_ = tmp_path / "bin"; bin_.mkdir(); state = tmp_path / "state"; state.mkdir()
    sent = tmp_path / "sent.log"
    _sh(bin_ / "curl", f'echo "$@" >> {sent}\n')
    _sh(bin_ / "logger", "exit 0\n")
    _sh(bin_ / "runuser", 'while [ "$1" != "--" ]; do shift; done; shift; exec "$@"\n')
    moves = "1" if restart_moves else "0"
    _sh(bin_ / "systemctl", f'''S={state}
case "$1" in
  show)
    svc="$2"; prop="$4"; [ "$3" = "-p" ] || {{ svc="$2"; prop="$4"; }}
    case "$*" in
      *LoadState*) echo loaded ;;
      *ActiveEnterTimestampMonotonic*) cat "$S/$svc.mono" 2>/dev/null || echo 100 ;;
      *ActiveEnterTimestamp*) cat "$S/$svc.ts" 2>/dev/null || echo "Thu 2026-01-01 00:00:00 UTC" ;;
      *) echo "" ;;
    esac ;;
  is-enabled) echo enabled ;;
  is-active) exit 0 ;;
  restart) if [ {moves} = 1 ]; then
             echo $(( $(cat "$S/$2.mono" 2>/dev/null || echo 100) + 1 )) > "$S/$2.mono"
             date -u -d "+5 seconds" "+%a %Y-%m-%d %H:%M:%S UTC" > "$S/$2.ts"   # 真机：重启后启动时间往前走
           fi; exit 0 ;;
  daemon-reload) exit 0 ;;
  *) exit 0 ;;
esac
''')
    script = (SRC.replace("REPO=/home/ombre/Ombre-Brain", f"REPO={repo}")
                 .replace("SELF=/usr/local/bin/ombre-auto-update", f"SELF={tmp_path}/no-such-self")
                 .replace("/etc/systemd/system/", f"{tmp_path}/etc-systemd/"))
    (tmp_path / "etc-systemd").mkdir()
    sp = tmp_path / "auto-update.sh"; sp.write_text(script, encoding="utf-8")
    return sp, bin_, sent, repo


def _run(sp, bin_):
    return subprocess.run(["bash", str(sp)], capture_output=True, text=True, timeout=60,
                          env={"PATH": f"{bin_}:/usr/bin:/bin", "HOME": "/tmp", **GIT_ENV})


def test_a_real_new_commit_ends_with_a_success_message_to_her(tmp_path):
    sp, bin_, sent, repo = _world(tmp_path)
    r = _run(sp, bin_)
    assert r.returncode == 0, r.stdout + r.stderr
    msg = sent.read_text(encoding="utf-8")
    assert "✅ 已上线" in msg and "加一条外人那条规矩" in msg, msg
    assert (repo / "personality.py").read_text() == "v2\n", "代码得真的拉下来"


def test_persona_regen_failure_is_not_reported_as_success(tmp_path):
    sp, bin_, sent, _ = _world(tmp_path, regen_ok=False)
    r = _run(sp, bin_)
    msg = sent.read_text(encoding="utf-8")
    assert "✅ 已上线" not in msg
    assert "人设重新生成失败" in msg
    assert r.returncode != 0


def test_a_restart_that_did_not_swap_the_process_is_reported_not_celebrated(tmp_path):
    sp, bin_, sent, _ = _world(tmp_path, restart_moves=False)
    r = _run(sp, bin_)
    msg = sent.read_text(encoding="utf-8")
    assert "✅ 已上线" not in msg
    assert "没换进程" in msg
    assert r.returncode != 0


def test_nothing_new_means_silence(tmp_path):
    """没有新代码、服务也不旧——一个字都不发，不刷她屏。"""
    sp, bin_, sent, _ = _world(tmp_path)
    _run(sp, bin_)
    sent.unlink()
    r = _run(sp, bin_)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not sent.exists()
