from utils import sanitize_scripted_transcript


def test_normal_chat_keeps_only_nikto_first_turn_from_fabricated_transcript():
    raw = "她：我腿痒。 你：先别挠，给我看看有没有红点。 她：（发来照片） 你：蚊子包。"
    assert sanitize_scripted_transcript(raw) == "先别挠，给我看看有没有红点"


def test_normal_chat_removes_leading_assistant_label():
    assert sanitize_scripted_transcript("Nikto：过来，我看看。") == "过来，我看看。"


def test_real_sentence_with_colon_is_not_mistaken_for_transcript():
    raw = "我问你：现在还痒不痒。"
    assert sanitize_scripted_transcript(raw) == raw


def test_writing_mode_does_not_rewrite_requested_script_format():
    raw = "她：第一句。 你：第二句。"
    assert sanitize_scripted_transcript(raw, writing_mode=True) == raw
