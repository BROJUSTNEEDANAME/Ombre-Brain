from writing_style import INTIMATE_WRITING_ENGINE


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
