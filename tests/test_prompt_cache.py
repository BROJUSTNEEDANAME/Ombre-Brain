from prompt_cache import (
    cache_usage,
    inject_volatile_context,
    read_stats,
    record_usage,
    request_extra_body,
    stable_user_id,
)


def test_dynamic_context_only_changes_newest_user_message():
    original = [
        {"role": "system", "content": "stable persona"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
    ]
    changed = inject_volatile_context(original, "volatile time and memory")

    assert changed[:3] == original[:3]
    assert changed[-1]["content"].endswith("new question")
    assert original[-1]["content"] == "new question"


def test_zai_fields_are_added_without_overwriting_existing_values(monkeypatch):
    monkeypatch.setenv("OMBRE_PROMPT_CACHE_USER_ID", "private-stable-route")
    body = request_extra_body(
        {"vendor": "kept"},
        base_url="https://api.z.ai/api/paas/v4/",
        thinking={"thinking": {"type": "disabled"}},
    )

    assert body == {
        "vendor": "kept",
        "user_id": "private-stable-route",
        "thinking": {"type": "disabled"},
    }
    assert len(stable_user_id()) >= 6


def test_unknown_openai_provider_does_not_receive_zai_user_id():
    assert request_extra_body(base_url="https://example.invalid/v1") == {}


def test_cache_usage_and_private_aggregate_stats(tmp_path):
    path = tmp_path / "prompt_cache_stats.json"
    usage = {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 80}}

    assert cache_usage(usage) == (100, 80)
    record_usage(usage, "home", path)
    stats = read_stats(path)

    assert stats["requests"] == 1
    assert stats["hits"] == 1
    assert stats["hit_rate"] == 80.0
    assert path.stat().st_mode & 0o777 == 0o600
