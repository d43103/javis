"""
Tests for voice_llm_bridge — all network calls are replaced with fakes.
sounddevice import is mocked so tests run without audio hardware.
"""
import sys
import types

# stub sounddevice before importing the bridge
sd_stub = types.ModuleType("sounddevice")
sd_stub.play = lambda data, samplerate, blocking: None
sd_stub.wait = lambda: None


class _FakeOutputStream:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def write(self, data):
        pass


sd_stub.OutputStream = _FakeOutputStream
sd_stub.RawInputStream = _FakeOutputStream
sys.modules["sounddevice"] = sd_stub


def _make_fake_anthropic(response_text="안녕하세요"):
    anthropic_stub = types.ModuleType("anthropic")

    class FakeMessage:
        content = [types.SimpleNamespace(text=response_text)]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeMessage()

    class FakeClient:
        messages = FakeMessages()

    anthropic_stub.Anthropic = FakeClient
    sys.modules["anthropic"] = anthropic_stub
    return anthropic_stub


def test_bridge_processes_final_event():
    _make_fake_anthropic("서울 날씨는 맑습니다.")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    tts_calls = []

    class FakeBridge(bridge.VoiceBridge):
        def _post_tts_and_play(self, response_text):
            tts_calls.append({"text": response_text})

    b = FakeBridge(
        server="ws://fake:8765",
        session_id="test",
        claude_model="claude-haiku-4-5-20251001",
    )
    b._handle_final("오늘 날씨 어때?")

    assert len(tts_calls) == 1
    assert tts_calls[0]["text"] == "서울 날씨는 맑습니다."


def test_bridge_maintains_conversation_history():
    _make_fake_anthropic("두 번째 응답")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    class FakeBridge(bridge.VoiceBridge):
        def _post_tts_and_play(self, response_text):
            pass

    b = FakeBridge(
        server="ws://fake:8765",
        session_id="test",
        claude_model="claude-haiku-4-5-20251001",
    )
    b._handle_final("첫 번째")
    b._handle_final("두 번째")

    # history should have 4 messages (2 turns * user+assistant each)
    assert len(b._history) == 4


def test_bridge_trims_history_at_max_turns():
    _make_fake_anthropic("응답")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    class FakeBridge(bridge.VoiceBridge):
        def _post_tts_and_play(self, response_text):
            pass

    b = FakeBridge(
        server="ws://fake:8765",
        session_id="test",
        claude_model="claude-haiku-4-5-20251001",
        max_turns=3,
    )
    for i in range(10):
        b._handle_final(f"질문 {i}")

    # max_turns=3 → max 6 messages in history
    assert len(b._history) <= 6
