from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "home.html").read_text(encoding="utf-8")


def test_background_recovery_uses_stable_request_id():
    assert "pendingChatRequest_" in HOME
    assert "await loadServerChat(true)" in HOME
    assert "m.reply_to===targetId" in HOME
    assert "pollForReply(300000,true" in HOME
    assert "recentUnanswered" in HOME


def test_server_sync_includes_the_private_chat_token():
    assert "api/chat/state?thread='+encodeURIComponent(curThread)+'&token=" in HOME
    assert "api/prefs?token='+encodeURIComponent(window.OMBRE_TOKEN||'')" in HOME
    assert "api/inner?token='+encodeURIComponent(window.OMBRE_TOKEN||'')" in HOME
    assert "api/threads?token='+encodeURIComponent(window.OMBRE_TOKEN||'')" in HOME


def test_possession_visuals_are_manual_only():
    assert "const red=!!manualVibe.red, glow=!!manualVibe.glow;" in HOME
    assert "const red=manualVibe.red||_endoDim" not in HOME
    assert "外观仅由你手动开启" in HOME


def test_expressed_emotion_wins_over_numeric_fallback():
    assert "const _emo=(data&&data.emotion)||(data&&data.endocrine" in HOME


def test_memory_notices_rehydrate_from_assistant_metadata():
    assert "function renderStoredChat(log)" in HOME
    assert "Array.isArray(m.recorded)" in HOME
    assert "recordBrain(x,false)" in HOME


def test_reasoning_markup_is_removed_in_browser_too():
    assert r"/<\s*think\b[^>]*>[\s\S]*?<\s*\/\s*think\s*>/gi" in HOME
    assert "const XMLBLOCK=" in HOME
    assert "XMLOPEN.test(s)||XMLPREFIX.test(s)" in HOME
