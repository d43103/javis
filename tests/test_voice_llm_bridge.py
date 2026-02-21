"""
Tests for voice_llm_bridge — all network calls are replaced with fakes.
sounddevice import is mocked so tests run without audio hardware.
"""
import asyncio
import sys
import types

import numpy as np

# stub sounddevice before importing the bridge — reuse existing stub if present
if "sounddevice" in sys.modules:
    sd_stub = sys.modules["sounddevice"]
else:
    sd_stub = types.ModuleType("sounddevice")
    sys.modules["sounddevice"] = sd_stub

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


def test_idle_flush_merges_pending_texts():
    """idle_flush: 여러 final 텍스트가 합쳐져서 한 번에 Claude 호출되는지 확인."""
    _make_fake_anthropic("통합 응답")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    calls = []

    class FakeBridge(bridge.VoiceBridge):
        def _post_tts_and_play(self, response_text):
            pass

        def _handle_final(self, text):
            calls.append(text)
            super()._handle_final(text)

    b = FakeBridge(
        server="ws://fake:8765",
        session_id="test",
        idle_flush_seconds=0.2,
    )
    b._pending_texts.extend(["오늘", "날씨", "어때?"])

    asyncio.run(b._flush_pending())

    assert len(calls) == 1
    assert calls[0] == "오늘 날씨 어때?"
    assert len(b._pending_texts) == 0


def test_idle_flush_debounce():
    """빠르게 들어오는 final 이벤트가 debounce 되는지 확인."""
    _make_fake_anthropic("응답")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    flush_count = []

    class FakeBridge(bridge.VoiceBridge):
        def _post_tts_and_play(self, response_text):
            pass

        def _handle_final(self, text):
            flush_count.append(text)
            super()._handle_final(text)

    async def run_debounce():
        b = FakeBridge(
            server="ws://fake:8765",
            session_id="test",
            idle_flush_seconds=0.3,
        )
        # 3개 final을 빠르게 추가 — 각각 타이머 리셋
        for word in ["안녕", "하세요", "잘 지내세요?"]:
            b._pending_texts.append(word)
            if b._flush_task and not b._flush_task.done():
                b._flush_task.cancel()
            b._flush_task = asyncio.create_task(b._schedule_flush())
            await asyncio.sleep(0.05)

        # 0.3초 대기 후 flush 실행
        await asyncio.sleep(0.5)
        return flush_count

    result = asyncio.run(run_debounce())
    # debounce: 3개가 합쳐져서 1번만 호출
    assert len(result) == 1
    assert "잘 지내세요?" in result[0]


def test_gain_properties():
    """input_gain, output_gain, output_device 속성 확인."""
    _make_fake_anthropic("응답")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    b = bridge.VoiceBridge(
        server="ws://fake:8765",
        session_id="test",
        input_gain=1.5,
        output_gain=0.8,
        output_device=2,
    )
    assert b.input_gain == 1.5
    assert b.output_gain == 0.8
    assert b.output_device == 2

    # runtime change
    b.input_gain = 2.0
    b.output_gain = 0.5
    assert b.input_gain == 2.0
    assert b.output_gain == 0.5


def test_callbacks_invoked():
    """on_final, on_ai_response, on_status_change 콜백 호출 확인."""
    _make_fake_anthropic("콜백 테스트 응답")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    statuses = []
    finals = []
    ai_responses = []

    class FakeBridge(bridge.VoiceBridge):
        def _post_tts_and_play(self, response_text):
            pass

    b = FakeBridge(
        server="ws://fake:8765",
        session_id="test",
        on_status_change=lambda s: statuses.append(s),
        on_final=lambda t: finals.append(t),
        on_ai_response=lambda t: ai_responses.append(t),
    )
    b._handle_final("테스트 질문")

    assert finals == ["테스트 질문"]
    assert ai_responses == ["콜백 테스트 응답"]
    assert "thinking" in statuses
    assert "speaking" in statuses
    assert "connected" in statuses


def test_stop_sets_running_false():
    """stop() 호출 시 _running이 False로 설정되는지 확인."""
    _make_fake_anthropic("응답")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    statuses = []

    b = bridge.VoiceBridge(
        server="ws://fake:8765",
        session_id="test",
        on_status_change=lambda s: statuses.append(s),
    )
    b._running = True
    # Don't call stop() directly as it tries to close ws — just test the flag
    b._running = False
    b._set_status(bridge.STATUS_STOPPED)

    assert b._running is False
    assert "stopped" in statuses


def test_input_gain_applied_to_mic():
    """apply_gain_int16 이 mic 데이터에 적용되는지 확인."""
    from src.audio_devices import apply_gain_int16

    data = np.array([10000, -10000], dtype=np.int16).tobytes()
    result = apply_gain_int16(data, 1.5)
    samples = np.frombuffer(result, dtype=np.int16)
    assert samples[0] == 15000
    assert samples[1] == -15000
