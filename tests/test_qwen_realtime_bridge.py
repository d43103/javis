from src.javis_stt.qwen_realtime_bridge import _safe_delta


def test_safe_delta_appends_suffix():
    assert _safe_delta("안녕", "안녕하세요") == "하세요"


def test_safe_delta_returns_current_on_prefix_miss():
    assert _safe_delta("테스트", "다른 값") == "다른 값"
