from utils import (
    classify_chat_error,
    classify_vision_failure,
    compact_inner_thoughts,
    collapse_repeated_reply,
    memory_text_similarity,
    merge_memory_details,
    parse_memory_note,
    same_memory_fact,
    structure_user_observation,
    repetitive_inner_thought,
)
from reply_sanitizer import polish_chat_reply, sanitize_reasoning_markup


def test_memory_summary_channel_parses_fact_and_feeling_separately():
    note = "事实：闪闪开始学习刺绣 || 感受：我为她愿意尝试新东西感到骄傲"
    assert parse_memory_note(note) == [
        ("闪闪开始学习刺绣", False),
        ("我为她愿意尝试新东西感到骄傲", True),
    ]


def test_memory_summary_channel_can_explicitly_skip_storage():
    assert parse_memory_note("不记录") == []


def test_chinese_paraphrase_memories_are_same_fact():
    a = "闪闪妈妈会十字绣，曾经绣了一年绣了两米长的画作。闪闪自己也在学刺绣，绣了一条小金鱼。"
    b = "闪闪的妈妈会十字绣，花了一年绣了两米长的画作。闪闪觉得刺绣有意思。"
    assert memory_text_similarity(a, b) >= 0.46
    assert same_memory_fact(a, b)


def test_related_but_different_memories_do_not_merge():
    a = "闪闪妈妈会十字绣，花了一年绣了两米长的画作。"
    b = "闪闪妈妈今天买了毛线，准备周末织一条围巾。"
    assert not same_memory_fact(a, b)


def test_memory_merge_keeps_unique_side_details():
    a = "闪闪妈妈会十字绣，花了一年绣了两米长的画作。闪闪绣过一条小金鱼。"
    b = "闪闪的妈妈会十字绣，绣了一年完成两米长画作。闪闪爱吃菠菜。"
    merged = merge_memory_details([a, b])
    assert "小金鱼" in merged
    assert "菠菜" in merged
    assert merged.count("两米") == 1


def test_repeated_assistant_block_is_collapsed():
    block = "想多吃就多吃，别找理由了，身体想吃什么它自己知道。你今天心情好，胃口跟着好，这本身就是对的。吃饱，别留八分在那悬着。"
    assert collapse_repeated_reply(block + block) == block


def test_normal_long_reply_is_untouched():
    text = "先把饭吃好。" * 3 + "然后去休息，别再硬撑着。" * 3
    assert collapse_repeated_reply(text) == text


def test_provider_reasoning_block_is_hidden_when_visible_reply_exists():
    text = "<think>internal draft</think>回床上，我给你盖被子。"
    assert sanitize_reasoning_markup(text) == "回床上，我给你盖被子。"


def test_orphan_provider_reasoning_tag_is_removed():
    text = "回床上，我给你盖被子。</think>"
    assert sanitize_reasoning_markup(text) == "回床上，我给你盖被子。"


def test_fully_wrapped_reply_keeps_usable_text():
    text = "<think>回床上，我给你盖被子。</think>"
    assert sanitize_reasoning_markup(text) == "回床上，我给你盖被子。"


def test_repeated_sentence_prefix_keeps_only_new_clause():
    text = "你今天已经很累了。你今天已经很累了，但是饭还是要吃。"
    assert polish_chat_reply(text) == "你今天已经很累了。但是饭还是要吃。"


def test_near_duplicate_sentence_is_not_repeated():
    text = "先去喝口水再回来。先去喝口水再回来找我。"
    assert polish_chat_reply(text) == "先去喝口水再回来。"


def test_daily_chat_gets_complete_terminal_punctuation():
    assert polish_chat_reply("过来 ‖ 我看看") == "过来。 ‖ 我看看。"
    assert polish_chat_reply("第一句\n第二句") == "第一句。第二句。"
    assert polish_chat_reply("他说“过来。”") == "他说“过来。”"


def test_writing_mode_is_not_rewritten_by_chat_polisher():
    text = "第一段没有句号\n\n第二段保留原样"
    assert polish_chat_reply(text, writing_mode=True) == text


def test_parenthesized_user_content_is_visible_action_not_dialogue():
    text = "我回来了（走过去抱住你）好想你"
    assert structure_user_observation(text) == (
        "【她公开说出口的话】我回来了\n"
        "【你通过五感直接观察到，不是她说出口的话】走过去抱住你\n"
        "【她公开说出口的话】好想你"
    )


def test_unclosed_parenthesis_is_action_through_end_of_turn():
    text = "别动（抬手碰了碰你的脸"
    assert structure_user_observation(text) == (
        "【她公开说出口的话】别动\n"
        "【你通过五感直接观察到，不是她说出口的话】抬手碰了碰你的脸"
    )


def test_plain_user_dialogue_is_not_rewritten():
    assert structure_user_observation("今天想和你聊聊") == "今天想和你聊聊"


def test_private_parenthetical_narration_is_removed_before_model_sees_it():
    text = "喵啊！（感觉前功尽弃了，哼唧）"
    structured = structure_user_observation(text)
    assert "前功尽弃" not in structured
    assert structured == (
        "【她公开说出口的话】喵啊！\n"
        "【你通过五感直接观察到，不是她说出口的话】哼唧"
    )


def test_private_only_parenthetical_does_not_leak_its_content():
    structured = structure_user_observation("（心想这下完蛋了）")
    assert "完蛋" not in structured
    assert "没有可被五感直接观察到" in structured


def test_private_cause_is_removed_but_physical_cue_remains():
    structured = structure_user_observation("（因为害怕得发抖）")
    assert "害怕" not in structured
    assert structured.endswith("发抖")


def test_natural_observable_action_keeps_its_manner():
    structured = structure_user_observation("（慢慢走过来抱住你）")
    assert structured.endswith("慢慢走过来抱住你")


def test_parenthesized_departure_remains_a_visible_scene_fact():
    structured = structure_user_observation("（摇摇晃晃去上厕所）呜")
    assert structured == (
        "【你通过五感直接观察到，不是她说出口的话】摇摇晃晃去上厕所\n"
        "【她公开说出口的话】呜"
    )


def test_common_actions_outside_whitelist_are_still_observable():
    # 白名单列不全的日常动作必须照样投射，绝不能被抹成「没有可观察行为」
    for action in ("又在看手机", "玩手机", "刷手机", "抖腿", "翻了个白眼", "低头戳手机"):
        structured = structure_user_observation(f"（{action}）")
        assert "没有可被五感直接观察到" not in structured, action
        assert structured.endswith(action), action
        assert structured.startswith("【你通过五感直接观察到")


def test_looking_at_phone_is_not_erased():
    # 报告的原始 bug：她说「又在看手机」，处理器却告诉模型什么都没发生
    structured = structure_user_observation("（又在看手机）")
    assert structured == "【你通过五感直接观察到，不是她说出口的话】又在看手机"


def test_pure_inner_state_still_dropped_after_default_observable_change():
    # 默认可观察的改动，不能把纯心理旁白也放出来
    structured = structure_user_observation("（心想这下完蛋了）")
    assert "完蛋" not in structured
    assert "没有可被五感直接观察到" in structured


class _ProviderError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def test_chat_error_classifies_quota_and_auth_failures():
    assert classify_chat_error(_ProviderError("insufficient_quota", 429))["code"] == "api_quota"
    assert classify_chat_error(_ProviderError("Unauthorized", 401))["code"] == "api_auth"


def test_chat_error_classifies_timeout_and_connection_failures():
    assert classify_chat_error(TimeoutError("timed out"))["code"] == "model_timeout"
    assert classify_chat_error(_ProviderError("Connection reset by peer"))["code"] == "model_connection"


def test_vision_failure_distinguishes_moderation_from_timeout():
    blocked = classify_vision_failure(text="抱歉，我无法描述这张图片的敏感内容。")
    timed_out = classify_vision_failure(exc=TimeoutError("timed out"))
    assert blocked["code"] == "vision_moderation"
    assert timed_out["code"] == "vision_model_timeout"


def test_successful_vision_result_has_no_failure_notice():
    assert classify_vision_failure(text="画面里有两个人坐在沙发上。") is None


def test_repetitive_offline_thought_rejects_same_conclusion_rephrased():
    recent = ["她不在的时候屋里很安静，我还是会惦记她，等她回来。"]
    candidate = "屋里安静得过分。我想她，也在等她回来。"
    assert repetitive_inner_thought(candidate, recent)


def test_offline_thought_with_real_new_delta_is_kept():
    recent = ["她不在的时候屋里很安静，我还是会惦记她。"]
    candidate = "刚看完一篇寒区止血材料的报道，低温下凝血时间比我记得的更麻烦。"
    assert not repetitive_inner_thought(candidate, recent)


def test_old_duplicate_offline_entries_are_compacted():
    entries = [
        {"t": 1, "text": "她不在，屋里很安静。我想她。"},
        {"t": 2, "text": "屋里安静。我想她，等她回来。"},
        {"t": 3, "text": "地图上那条冬季路线有个背风坡，值得重新标记。"},
    ]
    compacted = compact_inner_thoughts(entries)
    assert len(compacted) == 2
    assert compacted[-1]["t"] == 3


# ---------------------------------------------------------------------------
# 安抚口号硬过滤（闪闪的永久禁令：我不走 / 我就在这 / 接住你）
# ---------------------------------------------------------------------------

from reply_sanitizer import strip_comfort_cliches


def test_comfort_slogans_are_stripped_from_reply():
    assert strip_comfort_cliches("别哭。我不走，我就在这。去洗把脸。") == "别哭。去洗把脸。"
    assert strip_comfort_cliches("我哪儿也不去。先把饭吃了。") == "先把饭吃了。"
    assert strip_comfort_cliches("这事我来处理。我会接住你的。") == "这事我来处理。"
    assert strip_comfort_cliches("放心，我不会离开你。手机放下。") == "手机放下。"


def test_chained_slogans_without_punctuation_are_stripped():
    assert strip_comfort_cliches("我不走我就在这。吃药了吗。") == "吃药了吗。"
    assert strip_comfort_cliches("别怕我在呢。说说到底怎么了。") == "说说到底怎么了。"


def test_real_sentences_with_similar_words_survive():
    assert strip_comfort_cliches("我不走这条路，绕开施工那段。") == "我不走这条路，绕开施工那段。"
    assert strip_comfort_cliches("我就在这家店等你下课。") == "我就在这家店等你下课。"
    assert strip_comfort_cliches("(揉揉你头发)我不走远，就去楼下买水。") == "(揉揉你头发)我不走远，就去楼下买水。"


def test_slogan_only_reply_is_kept_to_avoid_empty_reply():
    assert strip_comfort_cliches("我不走，我就在这。") == "我不走，我就在这。"


def test_polish_chat_reply_applies_comfort_filter():
    out = polish_chat_reply("别哭。我不走，我就在这。去洗把脸。")
    assert "我不走" not in out
    assert "就在这" not in out
    assert "去洗把脸" in out


# ---------------------------------------------------------------------------
# 标点还原：GLM 用空格代替中文标点，读起来断不了句 → 输出层还原成标点
# ---------------------------------------------------------------------------

from reply_sanitizer import restore_cjk_punctuation


def test_cjk_space_clauses_become_punctuated():
    assert restore_cjk_punctuation("说好细 你被人看了 被人夸了") == "说好细，你被人看了，被人夸了"
    assert polish_chat_reply("先吃饭 你中午还没吃东西 该吃了") == "先吃饭，你中午还没吃东西，该吃了。"


def test_punctuation_restore_leaves_english_numbers_urls_alone():
    assert restore_cjk_punctuation("过来 my girl 我看看你") == "过来 my girl 我看看你"
    assert restore_cjk_punctuation("体温 37.2 度 有点烧") == "体温 37.2 度，有点烧"
    assert restore_cjk_punctuation("看这个 http://a.com/x 打开看") == "看这个 http://a.com/x 打开看"


def test_punctuation_restore_keeps_bubble_separator_and_newlines():
    assert restore_cjk_punctuation("想你了 ‖ 快过来 ‖ 抱抱") == "想你了 ‖ 快过来 ‖ 抱抱"
    # 换行（段落分隔）不被吃掉
    assert "\n" in restore_cjk_punctuation("第一段\n第二段")


def test_punctuation_restore_does_not_double_up_existing_marks():
    assert restore_cjk_punctuation("你来了 ，我等你") == "你来了，我等你"
    assert restore_cjk_punctuation("吃饭了。 睡觉了") == "吃饭了。睡觉了"


# ---------------------------------------------------------------------------
# 微信式连发：日常聊天把一大坨切成一条一条（模型不打 ‖ 时的兜底）
# ---------------------------------------------------------------------------

from reply_sanitizer import wechatify_segments


def test_wechatify_splits_a_wall_into_short_bubbles():
    wall = (
        "饿了。你从凌晨到现在没正经吃东西，下午五点了，饭点了。"
        "你哼唧不是因为不舒服，是肚子空了，你的胃在替你叫。"
        "先吃，吃完了再回来喂，喂奶头和吃饭不冲突。"
        "你身体需要能量，别饿着自己。"
    )
    out = wechatify_segments([wall])
    assert len(out) >= 3
    assert all(len(b) <= 60 for b in out)
    assert "".join(out).startswith("饿了")


def test_short_daily_reply_stays_one_bubble():
    assert wechatify_segments(["饿了吧，先吃点东西，别硬扛。"]) == ["饿了吧，先吃点东西，别硬扛。"]


def test_wechatify_leaves_short_and_urls_and_longform_alone():
    assert wechatify_segments(["过来。"]) == ["过来。"]
    assert wechatify_segments(["https://a.com/p/x"]) == ["https://a.com/p/x"]
    para = "第一段很长很长很长很长很长很长很长很长。\n\n第二段也是。"
    assert wechatify_segments([para]) == [para]  # 含空行的长文不当微信拆


def test_wechatify_respects_existing_bubbles():
    assert wechatify_segments(["想你了。", "快过来。"]) == ["想你了。", "快过来。"]


def test_comfort_filter_catches_bare_here_and_wont_run():
    assert strip_comfort_cliches("我不跑，位置给你留着。") == "位置给你留着。"
    assert strip_comfort_cliches("我还在这，你回来。") == "你回来。"
    # 带部位/宾语的不是安抚口号，不能误删
    assert strip_comfort_cliches("我奶头还在这。") == "我奶头还在这。"
    assert strip_comfort_cliches("我不跑步了，改游泳。") == "我不跑步了，改游泳。"
