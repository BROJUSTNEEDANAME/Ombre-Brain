"""自动部署器。

存在的理由：这个脚本每 5 分钟就会动她每天在用的服务。它做错事的代价是
「她那边发消息完全没反应」，而她第一时间不会知道是部署干的。
"""
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
SH = (_ROOT / "deploy" / "auto-update.sh").read_text(encoding="utf-8")


def test_the_cc_bridge_is_updated_too():
    """她问「可以也五分钟自动 push 一次吗」——原来那份只管 brain 和 apibot，
    cc 桥不在名单里，改了代码那边永远是旧的。"""
    assert "SERVICES+=(ombre-ccbridge)" in SH


def test_the_cc_bridge_is_only_touched_when_it_is_installed():
    """写死了的话，没装 cc 桥的机器每轮都会 restart 一个不存在的服务、刷红日志。

    但判断方式必须靠得住：原来用 `systemctl cat`，它会调分页器，在 timer 那种
    无终端环境里失败，于是**装了也检测不到**——她的 ccbridge 因此停在三天前的
    代码上，日志却每轮都报「已部署，两个服务都活着」。改用 LoadState。
    """
    i = SH.index("SERVICES+=(ombre-ccbridge)")
    guard = SH[SH.index("SERVICES=(ombre-brain"):i]
    guard_code = "\n".join(
        ln for ln in guard.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "systemctl show -p LoadState --value ombre-ccbridge.service" in guard_code
    assert "systemctl cat" not in guard_code, "分页器会让它在无终端环境里静默失败"


def test_the_generated_persona_is_regenerated_before_restart():
    """cc 的人设是从 personality.py **生成**的。光重启不重新生成，
    改完人设那边会一直用旧的，而且一点提示都没有——最难查的那种静默失败。"""
    assert "make-cc-persona.py" in SH
    i = SH.index("make-cc-persona.py")
    j = SH.index('for s in "${SERVICES[@]}"; do systemctl restart')
    assert i < j, "必须在重启之前生成，否则这一轮起来的还是旧人设"


def test_a_failed_persona_regen_is_reported_not_swallowed():
    assert "cc 人设重新生成失败" in SH


def test_the_persona_dir_comes_from_the_env_file_not_a_guess():
    """目录写死就会跟她实际配置对不上，而且错了也不会有人发现。"""
    assert "CC_WORKDIR=" in SH and ".env.ccbridge" in SH


def test_rollback_and_blocklist_are_still_there():
    """回滚和坏提交拉黑是她的保命闸，改这个脚本时最容易顺手弄丢。"""
    assert "reset --hard" in SH
    assert ".autoupdate-blocked" in SH
    assert "merge --ff-only" in SH


def test_the_success_line_no_longer_hardcodes_two_services():
    """原文写死「两个服务都活着」。加了 cc 桥之后那句就是错的——
    她看到的会是一句自信但不准确的捷报。"""
    # ⚠️ 只扫**会执行的行**：注释里引用了那句错话当反面教材，
    # 整份 grep 会命中说明文字而不是代码（CLAUDE.md 记过的「命中被提及处」）。
    code = "\n".join(ln for ln in SH.splitlines() if not ln.lstrip().startswith("#"))
    assert "两个服务都活着" not in code
    assert "${#SERVICES[@]}" in code


def test_a_service_running_older_code_than_HEAD_is_restarted_even_when_git_is_current():
    """病根就在这。原来开头是「本地==远端就 exit 0」。
    她这几天手动 git pull 过好几次——定时器五分钟后醒来，代码已经是最新的了，
    于是掉头就走，**根本走不到重启那一步**。服务跑着五个半小时前的旧代码，
    日志里一切正常。她连问三次「怎么还是这样」，其实我早就修好了。

    所以「代码是新的」必须不等于「跑的是新代码」：
    比 HEAD 的提交时间还早启动的服务，就是在跑旧代码，得重启。
    """
    # 1. 必须真的去问 systemd 服务什么时候起来的，而不是凭 git 状态猜
    assert "ActiveEnterTimestamp" in SH
    assert "log -1 --format=%ct" in SH, "得拿 HEAD 的提交时间来比"

    # 2. 那句致命的 exit 0 必须**只在没有旧服务时**才走
    i = SH.index('if [ "$LOCAL" = "$REMOTE" ]; then')
    tail = SH[i:i + 400]
    assert "exit 0" in tail
    assert '[ -z "$STALE" ] && exit 0' in tail, \
        "无条件 exit 0 就是原来的 bug：git 是最新的，但服务还跑着旧代码"

    # 3. 判定必须在那个 exit 之前算好，否则永远是空的
    assert SH.index("STALE=") < i

    # 4. 判定要覆盖到所有服务（包括动态加进来的 cc 桥），不能只看一个
    stale_block = SH[SH.index("HEAD_TS="):i]
    assert 'for s in "${SERVICES[@]}"' in stale_block


def test_a_failed_fetch_says_so_and_says_how_to_fix_it():
    """真事：仓库里混进了 root 拥有的 .git/objects，ombre 从此写不进去，
    每一轮 fetch 都是 insufficient permission。因为 set -e，脚本就死在那儿——
    日志里有，但没人会去看，表现出来只是「代码永远停在几小时前那个提交」。
    她连问四次「怎么还是这样」，我猜了四轮。"""
    assert "git fetch 失败" in SH
    assert "insufficient permission" in SH, "得认出这个具体错误"
    assert "chown -R ombre:ombre" in SH, "光说失败没用，要说怎么修"
    # 必须真的把 fetch 的错误文本带出来，不能只喊一句「失败了」
    i = SH.index("git fetch 失败")
    assert "$FETCH_ERR" in SH[i:i + 120]
    # 而且要在拿 LOCAL/REMOTE 之前就拦住——否则比的是过期的 origin 记录
    assert SH.index("FETCH_ERR") < SH.index("LOCAL=$(g rev-parse HEAD)")


def test_only_enabled_services_are_managed():
    """她早就不用 API bot 了，它在崩溃循环里；旧文写死 ombre-apibot，部署后一查它
    不活就整个回滚——她关不掉它。现在按 is-enabled 判，disabled/masked 的不管。"""
    code = "\n".join(ln for ln in SH.splitlines() if not ln.lstrip().startswith("#"))
    assert "SERVICES=(ombre-brain)" in code
    assert "SERVICES=(ombre-brain ombre-apibot)" not in code, "不许再写死 apibot"
    assert 'systemctl is-enabled ombre-apibot.service' in code
    assert 'systemctl is-enabled ombre-ccbridge.service' in code


def test_the_deployer_updates_its_own_installed_copy():
    """害得最惨的一条：systemd 跑的是 /usr/local/bin/ombre-auto-update，
    那是 install-autoupdate.sh 一次性拷过去的副本。仓库里这个脚本改了五次
    （包括「别用 systemctl cat」那条关键修复），一行都没生效——跑的始终是
    安装那天的旧副本。于是 ccbridge 一直不在重启名单里，从 9/12 起跑了三天
    旧进程，日志却每轮都印「✅ 已部署」。她那边表现成「新命令没有」。"""
    code = "\n".join(ln for ln in SH.splitlines() if not ln.lstrip().startswith("#"))
    assert "/usr/local/bin/ombre-auto-update" in code, "得知道自己被装在哪儿"
    assert "cmp -s" in code, "要比对仓库里的自己和装好的那份"
    assert "install -m 755" in code
    assert "exec " in code, "换完要用新版接着跑这一轮，不能等下一轮"
    # 防无限自我重启
    assert "OMBRE_SELF_UPDATED" in code
    # 必须在重启服务之前换好，否则这一轮仍然由旧逻辑决定重启谁
    assert code.index("/usr/local/bin/ombre-auto-update") < \
        code.index('for s in "${SERVICES[@]}"; do\n    systemctl restart')


def test_restarting_is_not_confused_with_actually_restarted():
    """真事：日志连着三天「✅ 已部署，两个服务都活着」，可 ccbridge 的
    ActiveEnterTimestamp 一直停在 9/12——它确实活着（is-active 过了），
    活的却是三天前那个进程。✅ 不许打在「我发了 restart」上。"""
    code = "\n".join(ln for ln in SH.splitlines() if not ln.lstrip().startswith("#"))
    assert "ActiveEnterTimestampMonotonic" in code, "得比进程有没有真的换掉"
    assert "WAS_AT" in code and "STUCK" in code
    # 没换进程必须是 ❌，而且不能还打那句 ✅
    i = code.index('if [ -n "$STUCK" ]')
    j = code.index("✅ 已部署")
    assert i < j, "STUCK 的判断必须挡在 ✅ 前面"
    assert "exit 1" in code[i:j]
    assert "还在跑旧代码" in SH


def test_the_installer_survives_a_service_she_turned_off():
    """旧版写死 `systemctl restart ombre-apibot`。她把 apibot 关了之后，
    set -e 会让安装在这一步直接死掉，后面什么都没装完。"""
    ISH = (_ROOT / "deploy" / "install-autoupdate.sh").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in ISH.splitlines() if not ln.lstrip().startswith("#"))
    assert "systemctl restart ombre-apibot\n" not in code, "不许再写死重启它"
    assert "LoadState" in code and "is-enabled" in code
    assert "ombre-ccbridge" in code, "装的时候也该把 cc 桥带上"


def test_the_installer_regenerates_the_persona_before_restarting():
    """真事：刚破完部署器自更新的僵局，服务是新的、人设还是旧的——
    install 这条路原来只重启不重生成。「重启了」不等于「换了人设」。"""
    ISH = (_ROOT / "deploy" / "install-autoupdate.sh").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in ISH.splitlines() if not ln.lstrip().startswith("#"))
    assert "make-cc-persona.py" in code
    assert ".env.ccbridge" in code, "目录得从配置读，写死了错了也没人发现"
    assert code.index("make-cc-persona.py") < code.index("systemctl restart"), \
        "必须在重启之前生成，否则这一轮起来的还是旧人设"


def _cc_workdir_block(src: str) -> str:
    i = src.index("# --- cc_workdir:begin ---")
    j = src.index("# --- cc_workdir:end ---")
    return src[i:j]


def _run_block_under_pipefail(block: str, repo: pathlib.Path) -> "subprocess.CompletedProcess[str]":
    import subprocess
    script = "set -euo pipefail\nREPO=%r\n%s\necho REACHED:$CC_WORKDIR\n" % (str(repo), block)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin:/bin"})


def test_missing_cc_workdir_in_env_file_must_not_kill_the_deployer(tmp_path):
    """2026-10-04 的病根。setup-ccbridge.sh 把 CC_WORKDIR 写进 unit 的 Environment=，
    不写 .env.ccbridge；原来那条裸的 `CC_WORKDIR=$(grep ... | tail | cut | tr)` 在
    set -euo pipefail 下 grep 没命中就把整个脚本杀了——印完「拉到新提交」就断气，
    没重启、没重新生成人设、没 ❌ 也没 ✅。她的服务从 9/25 到 10/4 一次都没被换过进程，
    日志却每 5 分钟喊一次「重启」。这里**真的跑**那几行，三种情况都必须活着走到底。"""
    block = _cc_workdir_block(SH)
    # 1) .env 文件根本不存在
    r = _run_block_under_pipefail(block, tmp_path / "no-such-repo")
    assert r.returncode == 0 and "REACHED:/home/ombre/nikto-cc" in r.stdout, r.stderr
    # 2) .env 存在但没有那一行（她机器上就是这样）
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / ".env.ccbridge").write_text("TOY_MCP_URL=https://x/mcp\n", encoding="utf-8")
    r = _run_block_under_pipefail(block, repo)
    assert r.returncode == 0 and "REACHED:/home/ombre/nikto-cc" in r.stdout, r.stderr
    # 3) .env 里配了，就用它的
    (repo / ".env.ccbridge").write_text("CC_WORKDIR=/srv/nikto\n", encoding="utf-8")
    r = _run_block_under_pipefail(block, repo)
    assert r.returncode == 0 and "REACHED:/srv/nikto" in r.stdout, r.stderr


def test_the_restart_log_line_does_not_claim_a_new_commit_when_there_is_none():
    """代码没变、只是服务跑旧代码时，原来也印「拉到新提交 @ 同一个号」——
    她看着日志以为每 5 分钟都拉到了新东西。日志不许说假话。"""
    code = "\n".join(ln for ln in SH.splitlines() if not ln.lstrip().startswith("#"))
    assert "代码没变（@ $NEW）" in code
    i = code.index('if [ "$LOCAL" = "$REMOTE" ]; then\n    log "代码没变')
    assert "拉到新提交 $BRANCH @ $NEW" in code[i:i+400]


# ---------------------------------------------------------------------------
# 2026-10-06：「出事就发 Telegram 给她」从写上那天起一条都没发出去过。
# 下面几条都是**真的执行**部署器里的代码，用假 curl 记下它到底发没发。
# ---------------------------------------------------------------------------
import os as _os
import subprocess as _sp


def _fn(src: str, name: str) -> str:
    i = src.index(f"{name}() {{")
    j = src.index("\n}\n", i) + 3
    return src[i:j]


def _fake_env(tmp_path, env_lines: str):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / ".env.ccbridge").write_text(env_lines, encoding="utf-8")
    bin_ = tmp_path / "bin"; bin_.mkdir()
    log = tmp_path / "curl.log"
    (bin_ / "curl").write_text(f'#!/bin/sh\necho "$@" >> {log}\n', encoding="utf-8")
    (bin_ / "curl").chmod(0o755)
    (bin_ / "logger").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_ / "logger").chmod(0o755)
    return repo, bin_, log


def _bash(script: str, bin_) -> "_sp.CompletedProcess[str]":
    return _sp.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                   env={"PATH": f"{bin_}:/usr/bin:/bin"})


def test_tg_actually_sends_when_optional_api_base_is_missing(tmp_path):
    """病根：.env 里没有 TELEGRAM_API_BASE（几乎所有人都没有），grep 没命中 →
    pipefail → set -e 在 tg() 里把整个脚本杀了，curl 一次都没跑到。"""
    repo, bin_, log = _fake_env(tmp_path, "TELEGRAM_BOT_TOKEN=abc\nALLOWED_CHAT_IDS=123,456\n")
    r = _bash(f"set -euo pipefail\nREPO={repo}\n{_fn(SH, 'tg')}\ntg '你好'\necho AFTER\n", bin_)
    assert r.returncode == 0 and "AFTER" in r.stdout, r.stderr
    sent = log.read_text(encoding="utf-8")
    assert "botabc/sendMessage" in sent and "chat_id=123" in sent and "text=你好" in sent


def test_tg_with_no_env_file_is_a_quiet_noop_not_a_crash(tmp_path):
    repo, bin_, log = _fake_env(tmp_path, "")
    (repo / ".env.ccbridge").unlink()
    r = _bash(f"set -euo pipefail\nREPO={repo}\n{_fn(SH, 'tg')}\ntg 'x'\necho AFTER\n", bin_)
    assert r.returncode == 0 and "AFTER" in r.stdout
    assert not log.exists()


def test_an_unexpected_death_on_any_line_is_sent_to_her(tmp_path):
    """9/25 和 10/4：每 5 分钟死在同一行，悄无声息。现在任何一行意外退出都要发到她面前。"""
    repo, bin_, log = _fake_env(tmp_path, "TELEGRAM_BOT_TOKEN=abc\nALLOWED_CHAT_IDS=123\n")
    i = SH.index("set -E\non_unexpected_exit()")
    j = SH.index("trap 'on_unexpected_exit $LINENO' ERR") + len("trap 'on_unexpected_exit $LINENO' ERR")
    harness = (f"set -euo pipefail\nREPO={repo}\n{_fn(SH, 'tg')}\n"
               "log() { echo \"$*\"; }\n" + SH[i:j] +
               "\nX=$(grep '^NOPE=' /dev/null | tail -1)\necho SHOULD_NOT_REACH\n")
    r = _bash(harness, bin_)
    assert r.returncode != 0 and "SHOULD_NOT_REACH" not in r.stdout
    assert log.exists(), "崩了却没发消息——就是这次修的那个病"
    assert "自己崩了" in log.read_text(encoding="utf-8")


def test_success_is_also_sent_but_only_after_the_real_checks():
    """成功也要告诉她，不然她只能自己开 VPS。但「✅ 已上线」必须排在
    起不来（FAILED）和没换进程（STUCK）两道检查之后，人设重生成失败时不许发。"""
    code = "\n".join(ln for ln in SH.splitlines() if not ln.lstrip().startswith("#"))
    ok = code.index('tg "✅ 已上线')
    assert code.index('if [ -n "$FAILED" ]') < ok
    assert code.index('if [ -n "$STUCK" ]') < ok
    pf = code.index('if [ -n "${PERSONA_FAILED:-}" ]')
    assert pf < ok and "exit 1" in code[pf:ok]
    assert "PERSONA_FAILED=1" in code
