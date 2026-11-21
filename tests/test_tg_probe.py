"""scripts/tg-probe.sh：每分钟分段测「服务器 → Telegram」，下次断线翻记录看断在哪段。

用一个假的 curl 真的把脚本跑一遍（不 skip）：按地址返回不同结果，看每格写得对不对、
机器人密码有没有被打出来、带引号的设置能不能读到。
"""
import os
import stat
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = _ROOT / "scripts" / "tg-probe.sh"

FAKE_CURL = r"""#!/bin/bash
url="${@: -1}"
case "$url" in
  https://relay.example/)              echo -n "404 0.120" ;;
  https://relay.example/bot*getMe)     echo -n "200 0.345" ;;
  https://api.telegram.org/)           echo -n "000 20.001" ;;
  https://www.google.com/generate_204) echo -n "204 7.250" ;;
  *) echo -n "000 0" ;;
esac
"""


def _run(tmp_path, env_text, *args):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    curl = bindir / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    envf = tmp_path / ".env.ccbridge"
    envf.write_text(env_text)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", ENV_FILE=str(envf),
               LOG=str(tmp_path / "probe.log"), REPO=str(tmp_path))
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True,
                          text=True, timeout=30)


def test_each_leg_is_reported_and_the_token_never_printed(tmp_path):
    # 11/21 真实的写法：值带单引号——我那天的 grep 就是栽在这上面
    r = _run(tmp_path, "TELEGRAM_BOT_TOKEN='123:SECRETTOKEN'\nTELEGRAM_API_BASE='https://relay.example/'\n")
    out = r.stdout
    assert r.returncode == 0, r.stderr
    assert "中转站 404/0.1s" in out
    assert "中转→TG 200/0.3s" in out
    assert "直连TG ❓连不上" in out, "000 不许写成别的"
    assert "对照 204/7.2s" in out or "对照 204/7.3s" in out
    assert "SECRETTOKEN" not in out + r.stderr


def test_no_relay_is_said_plainly(tmp_path):
    r = _run(tmp_path, "TELEGRAM_BOT_TOKEN=1:x\n")
    assert "中转站 没设" in r.stdout


def test_report_counts_bad_lines(tmp_path):
    log = tmp_path / "probe.log"
    log.write_text(
        "2026-11-21 10:00:00 | 中转站 404/0.1s | 中转→TG 200/0.3s | 直连TG 302/0.2s | 对照 204/0.1s\n"
        "2026-11-21 10:01:00 | 中转站 ❓连不上 | 中转→TG ❓连不上 | 直连TG ❓连不上 | 对照 204/0.1s\n"
        "2026-11-21 10:02:00 | 中转站 404/6.4s | 中转→TG 200/0.3s | 直连TG 302/0.2s | 对照 204/0.1s\n")
    r = _run(tmp_path, "", "--report")
    assert "共 3 行，其中出问题（连不上或超过 5 秒）的 2 行" in r.stdout
    assert "10:01:00" in r.stdout and "10:02:00" in r.stdout and "10:00:00" not in r.stdout
