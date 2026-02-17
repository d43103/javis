from src.javis_stt.client_utils import build_ws_url, pcm16_bytes_per_second


def test_build_ws_url_with_session():
    url = build_ws_url("ws://127.0.0.1:8765/", "session-1")
    assert url == "ws://127.0.0.1:8765/ws/stt?session_id=session-1"


def test_pcm16_bytes_per_second():
    assert pcm16_bytes_per_second(16000, 1) == 32000
