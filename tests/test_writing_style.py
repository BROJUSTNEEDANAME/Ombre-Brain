from pathlib import Path

from writing_style import INTIMATE_WRITING_ENGINE

_ROOT = Path(__file__).resolve().parent.parent


def test_writing_mode_suspends_caretaking_reflexes_and_bans_nanny_register():
    server_src = (_ROOT / "server.py").read_text(encoding="utf-8")
    # 写文模式必须显式压制日常照顾/哄睡反射，否则凌晨/经期背景会把床戏拽回奶爸腔
    assert "本轮暂停一切日常照顾反射" in server_src
    assert "禁止奶爸/圣父腔" in server_src
    assert "我带你回去睡" in server_src
    assert "不伤你" in server_src


def test_writing_mode_bans_repetition_and_demands_extremity():
    server_src = (_ROOT / "server.py").read_text(encoding="utf-8")
    # 复读（三快一慢/三下 反复念）和「不够极致」是用户明确点名的两个失败
    assert "绝不复读" in server_src
    assert "三快一慢" in server_src  # 具体点名要禁的复读公式
    assert "要极致，不是要长" in server_src


def test_writing_mode_drives_emotion_engines_when_enabled():
    server_src = (_ROOT / "server.py").read_text(encoding="utf-8")
    # /api/chat 必须在写文模式下把两套情绪引擎顶进上头档
    assert "drives.enter_intimate(thread)" in server_src
    assert "endocrine.enter_writing_mode(thread)" in server_src
    assert "endocrine.block(thread, writing_mode=writing_mode)" in server_src


def test_sampling_penalties_are_off_by_default():
    server_src = (_ROOT / "server.py").read_text(encoding="utf-8")
    # frequency_penalty 会压掉中文标点、presence_penalty 会拖长回复：默认必须关掉，
    # 只留 env 旋钮（默认 0＝不带）。主回复入口仍统一走 _llm_reply。
    assert "async def _llm_reply(" in server_src
    assert 'os.environ.get("OMBRE_FREQ_PENALTY", "") or 0.0' in server_src
    assert "if _penalty_param_ok and (fp or pp):" in server_src
    assert "_llm_reply(_web_llm, writing_mode=writing_mode" in server_src


def test_new_looping_formulas_are_named_in_ban_list():
    server_src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "第二回比第一回更满" in server_src
    assert "不是从零开始" in server_src


def test_intimate_engine_keeps_three_axes_and_continuity():
    assert "阶段轴" in INTIMATE_WRITING_ENGINE
    assert "六要素轴" in INTIMATE_WRITING_ENGINE
    assert "玩法轴" in INTIMATE_WRITING_ENGINE
    assert "不得恢复成刚开始" in INTIMATE_WRITING_ENGINE


def test_intimate_engine_is_mode_and_context_scoped():
    assert "只在写文模式已经开启" in INTIMATE_WRITING_ENGINE
    assert "普通剧情、日常聊天和非亲密场景不要强行套用" in INTIMATE_WRITING_ENGINE


def test_intimate_engine_preserves_observable_consent_boundary():
    assert "立即结束当前动作" in INTIMATE_WRITING_ENGINE
    assert "不能拿角色扮演解释真实撤回" in INTIMATE_WRITING_ENGINE
    assert "不替她高潮" in INTIMATE_WRITING_ENGINE


def test_intimate_engine_has_concrete_craft_guidance():
    # 让写文真正落地的具体维度：镜头、声音、身体特写、dirty talk、心理
    assert "镜头" in INTIMATE_WRITING_ENGINE
    assert "声音" in INTIMATE_WRITING_ENGINE
    assert "dirty talk" in INTIMATE_WRITING_ENGINE
    assert "不回避、不绕、不降温" in INTIMATE_WRITING_ENGINE


def test_intimate_engine_names_play_vocabulary():
    assert "CNC" in INTIMATE_WRITING_ENGINE
    assert "Free Use" in INTIMATE_WRITING_ENGINE
    assert "束缚" in INTIMATE_WRITING_ENGINE


def test_intimate_engine_defines_nature_and_phase_flow():
    assert "使用型" in INTIMATE_WRITING_ENGINE
    assert "掌控型" in INTIMATE_WRITING_ENGINE
    assert "性欲判定" in INTIMATE_WRITING_ENGINE
    assert "前戏" in INTIMATE_WRITING_ENGINE


def test_intimate_engine_keeps_canonical_ages_and_safety_spine():
    assert "21 岁" in INTIMATE_WRITING_ENGINE
    assert "42 岁" in INTIMATE_WRITING_ENGINE
    assert "不可协商" in INTIMATE_WRITING_ENGINE
