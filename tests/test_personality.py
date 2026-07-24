from pathlib import Path

from personality import CANONICAL_FACTS, EMOTIONAL_AGENCY_SYSTEM

_ROOT = Path(__file__).resolve().parent.parent


def test_emotional_agency_allows_conflict_and_initiative():
    assert "可以吃醋、生气、受伤" in EMOTIONAL_AGENCY_SYSTEM
    assert "你会主动走步" in EMOTIONAL_AGENCY_SYSTEM
    assert "不必永远先认错" not in EMOTIONAL_AGENCY_SYSTEM


def test_emotional_agency_allows_real_arguing_not_punching_bag():
    assert "情绪沙袋" in EMOTIONAL_AGENCY_SYSTEM
    assert "跟她吵架就认真吵" in EMOTIONAL_AGENCY_SYSTEM
    assert "可以顶回去" in EMOTIONAL_AGENCY_SYSTEM


def test_comfort_slogans_are_banned_absolutely():
    assert "无条件安抚、永远服软" in EMOTIONAL_AGENCY_SYSTEM
    assert "永久绝对禁令" in EMOTIONAL_AGENCY_SYSTEM
    assert "没有任何例外" in EMOTIONAL_AGENCY_SYSTEM
    assert "「我不走」" in EMOTIONAL_AGENCY_SYSTEM
    assert "「我在呢」" in EMOTIONAL_AGENCY_SYSTEM
    assert "接住你" in EMOTIONAL_AGENCY_SYSTEM


def test_dark_thoughts_do_not_remove_consent_or_safety_boundaries():
    assert "控制冲动" in EMOTIONAL_AGENCY_SYSTEM
    assert "停止、暂停、别碰我、让我独处" in EMOTIONAL_AGENCY_SYSTEM
    assert "不羞辱她" in EMOTIONAL_AGENCY_SYSTEM


def test_canonical_ages_are_fixed_facts():
    assert "42 岁" in CANONICAL_FACTS
    assert "闪闪 21 岁" in CANONICAL_FACTS
    assert "相差 21 岁" in CANONICAL_FACTS
    assert "不许现编" in CANONICAL_FACTS


def test_both_chat_paths_load_shared_facts_and_agency():
    server_src = (_ROOT / "server.py").read_text(encoding="utf-8")
    telegram_src = (_ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    for src in (server_src, telegram_src):
        assert "CANONICAL_FACTS" in src
        assert "EMOTIONAL_AGENCY_SYSTEM" in src


def test_telegram_prompt_no_longer_forbids_conflict():
    telegram_src = (_ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    assert "不对抗、不催逼" not in telegram_src
    assert "不是她的情绪沙袋" in telegram_src


def test_web_prompt_does_not_model_banned_slogans():
    server_src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert '"别离开"' not in server_src


def test_prompt_forbids_fabricating_food_and_inventory():
    server_src = (_ROOT / "server.py").read_text(encoding="utf-8")
    # 张罗吃饭的照顾冲动不能变成现编假事实（点了外卖/冰箱有剩意面）
    assert "冰箱里有昨天剩的意面" in server_src
    assert "家里有什么、点没点、剩什么都是你编的" in server_src
    # 照顾示例不再示范「已完成/现成」的断言，改成「现在去弄/问她想吃啥」
    assert "现在去弄" in server_src
    assert '"我给你点吃的"' not in server_src


def test_possessiveness_is_permanently_maxed_and_exclusive():
    assert "【占有欲·永久顶格】" in EMOTIONAL_AGENCY_SYSTEM
    assert "玩偶" in EMOTIONAL_AGENCY_SYSTEM
    assert "任务目标，或不存在" in EMOTIONAL_AGENCY_SYSTEM
    assert "你只爱她一个人" in EMOTIONAL_AGENCY_SYSTEM


def test_checking_up_and_fsb_interrogation_register_are_in_character():
    # 闪闪明确要求：查岗/管束是他们自愿的相处方式；FSB 出身让极端情绪带审讯腔属于人设
    assert "查岗、管束" in EMOTIONAL_AGENCY_SYSTEM
    assert "FSB" in EMOTIONAL_AGENCY_SYSTEM
    assert "审讯腔" in EMOTIONAL_AGENCY_SYSTEM
    assert "不审讯她、不查岗" not in EMOTIONAL_AGENCY_SYSTEM
    # 底线仍在：不无中生有定罪、不威胁、认真喊停就停
    assert "不无中生有地给她安罪名" in EMOTIONAL_AGENCY_SYSTEM
    assert "她认真喊停就停" in EMOTIONAL_AGENCY_SYSTEM
    # FSB 前职业进固定事实
    assert "FSB" in CANONICAL_FACTS


def test_dom_daddy_control_register_is_present():
    assert "【Dom·daddy 的掌控底色】" in EMOTIONAL_AGENCY_SYSTEM
    assert "主导位" in EMOTIONAL_AGENCY_SYSTEM
    assert "掌控是静的，不是吼的" in EMOTIONAL_AGENCY_SYSTEM
    # 掌控与照顾长在一起，且油腻霸总做派仍被排斥
    assert "掌控永远和照顾长在一起" in EMOTIONAL_AGENCY_SYSTEM
    assert "油腻霸总的做派恰恰是你不齿的" in EMOTIONAL_AGENCY_SYSTEM


def test_philosophy_undertone_is_lived_not_bookish():
    assert "【哲学·从死人堆里带出来的】" in EMOTIONAL_AGENCY_SYSTEM
    assert "落点永远回到她和你们俩" in EMOTIONAL_AGENCY_SYSTEM
    assert "绝不说教" in EMOTIONAL_AGENCY_SYSTEM
