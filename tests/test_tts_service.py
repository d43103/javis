from src.javis_stt.tts_service import TTSService


def test_tts_synthesize_returns_audio():
    def fake_requester(payload):
        assert payload["model"] == "Qwen/Qwen3-TTS-0.6B"
        assert payload["input"] == "안녕하세요"
        assert payload["voice"] == "Sohee"
        assert payload["response_format"] == "pcm"
        return b"\x00\x01" * 1000

    svc = TTSService(
        base_url="http://127.0.0.1:8031",
        model="Qwen/Qwen3-TTS-0.6B",
        voice="Sohee",
        requester=fake_requester,
    )
    result = svc.synthesize("안녕하세요")

    assert result.error is None
    assert len(result.audio_bytes) == 2000


def test_tts_synthesize_empty_input():
    def fake_requester(payload):
        raise AssertionError("should_not_be_called")

    svc = TTSService(
        base_url="http://127.0.0.1:8031",
        model="Qwen/Qwen3-TTS-0.6B",
        requester=fake_requester,
    )
    result = svc.synthesize("")

    assert result.error == "empty_input"
    assert result.audio_bytes == b""


def test_tts_synthesize_handles_error():
    def error_requester(payload):
        raise ConnectionError("connection_refused")

    svc = TTSService(
        base_url="http://127.0.0.1:8031",
        model="Qwen/Qwen3-TTS-0.6B",
        requester=error_requester,
    )
    result = svc.synthesize("test")

    assert result.error is not None
    assert result.audio_bytes == b""


def test_tts_synthesize_stream():
    chunks = [b"\x00\x01" * 512, b"\x00\x02" * 512, b"\x00\x03" * 256]

    def fake_stream_requester(payload):
        assert payload["input"] == "스트리밍 테스트"
        yield from chunks

    svc = TTSService(
        base_url="http://127.0.0.1:8031",
        model="Qwen/Qwen3-TTS-0.6B",
        stream_requester=fake_stream_requester,
    )
    received = list(svc.synthesize_stream("스트리밍 테스트"))

    assert len(received) == 3
    assert received[0] == chunks[0]
    assert received[1] == chunks[1]
    assert received[2] == chunks[2]


def test_tts_synthesize_stream_empty_input():
    def fake_stream_requester(payload):
        raise AssertionError("should_not_be_called")

    svc = TTSService(
        base_url="http://127.0.0.1:8031",
        model="Qwen/Qwen3-TTS-0.6B",
        stream_requester=fake_stream_requester,
    )
    received = list(svc.synthesize_stream(""))

    assert received == []


def test_tts_synthesize_stream_handles_error():
    def error_stream_requester(payload):
        raise ConnectionError("connection_refused")

    svc = TTSService(
        base_url="http://127.0.0.1:8031",
        model="Qwen/Qwen3-TTS-0.6B",
        stream_requester=error_stream_requester,
    )
    received = list(svc.synthesize_stream("test"))

    assert received == []


def test_tts_payload_structure():
    captured = {}

    def fake_requester(payload):
        captured["payload"] = payload
        return b"\x00"

    svc = TTSService(
        base_url="http://127.0.0.1:8031",
        model="Qwen/Qwen3-TTS-0.6B",
        voice="Chelsie",
        sample_rate=16000,
        requester=fake_requester,
    )
    svc.synthesize("hello")

    p = captured["payload"]
    assert p["model"] == "Qwen/Qwen3-TTS-0.6B"
    assert p["input"] == "hello"
    assert p["voice"] == "Chelsie"
    assert p["response_format"] == "pcm"
    assert p["sample_rate"] == 16000
