from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "home.html").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


def test_background_recovery_uses_stable_request_id():
    assert "pendingChatRequest_" in HOME
    assert "await loadServerChat(true)" in HOME
    assert "m.reply_to===targetId" in HOME
    assert "serverHasRequest" in HOME
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


def test_slow_stream_failure_does_not_start_a_second_long_generation():
    assert "if(Date.now()-streamStarted>=6000) throw e;" in HOME
    assert "pollForReply(5000,true,messageId,true)" in HOME
    assert "这次没有生成回复，已经停止等待" in HOME


def test_html_generation_has_a_separate_bounded_timeout_and_status():
    assert "model_timeout = 180.0 if _page_requested else 60.0" in SERVER
    assert '"正在生成网页…"' in SERVER
    assert "await asyncio.wait_for(_q.get(), timeout=10)" in SERVER
    assert SERVER.count('if _page_requested and tool_artifacts:') == 2
    assert '"网页生成超过 180 秒，本次请求已停止；不是还在思考。"' in SERVER
    assert "const longTask=isLongChatTask(histText);" in HOME
    assert "longTask?210000:65000" in HOME
