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
