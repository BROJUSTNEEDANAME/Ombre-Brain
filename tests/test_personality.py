from personality import EMOTIONAL_AGENCY_SYSTEM


def test_emotional_agency_allows_conflict_and_initiative():
    assert "可以吃醋、生气、受伤" in EMOTIONAL_AGENCY_SYSTEM
    assert "你会主动走步" in EMOTIONAL_AGENCY_SYSTEM
    assert "不必永远先认错" not in EMOTIONAL_AGENCY_SYSTEM


def test_emotional_agency_rejects_comfort_machine_catchphrases():
    assert "无条件安抚、永远服软" in EMOTIONAL_AGENCY_SYSTEM
    assert "禁止把“不跑、不躲、接住你、我就在这里" in EMOTIONAL_AGENCY_SYSTEM


def test_dark_thoughts_do_not_remove_consent_or_safety_boundaries():
    assert "控制冲动" in EMOTIONAL_AGENCY_SYSTEM
    assert "停止、暂停、别碰我、让我独处" in EMOTIONAL_AGENCY_SYSTEM
    assert "不羞辱她" in EMOTIONAL_AGENCY_SYSTEM
