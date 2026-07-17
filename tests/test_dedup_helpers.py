from utils import (
    classify_chat_error,
    compact_inner_thoughts,
    collapse_repeated_reply,
    memory_text_similarity,
    merge_memory_details,
    same_memory_fact,
    structure_user_observation,
    repetitive_inner_thought,
)


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
