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
    """写死了的话，没装 cc 桥的机器每轮都会 restart 一个不存在的服务、刷红日志。"""
    i = SH.index("SERVICES+=(ombre-ccbridge)")
    guard = SH[SH.index("SERVICES=(ombre-brain"):i]
    assert "systemctl cat ombre-ccbridge.service" in guard


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
    assert "两个服务都活着" not in SH
    assert "${#SERVICES[@]}" in SH
