from fastapi.testclient import TestClient
import importlib

AIResult = importlib.import_module("src.javis_stt.ai_gateway").AIResult
create_app = importlib.import_module("src.javis_stt.server").create_app


class _FakeASR:
    def transcribe_segment(self, audio_bytes, session_id, segment_id, started_at, ended_at):
        _ = (audio_bytes, session_id, segment_id, started_at, ended_at)
        return [
            {
                "type": "partial",
                "session_id": session_id,
                "segment_id": segment_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "text": "안녕",
                "confidence": 0.1,
            },
            {
                "type": "final",
                "session_id": session_id,
                "segment_id": segment_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "text": "안녕하세요",
                "confidence": 0.9,
            },
        ]


class _FakeAI:
    def generate(self, session_id, text):
        _ = (session_id, text)
        return AIResult(text="반가워요", error=None)


class _FakeTTS:
    def synthesize_stream(self, text):
        yield b"\x00\x01" * 512
        yield b"\x00\x02" * 256


def test_websocket_emits_partial_and_final_events(tmp_path):
    app = create_app(
        sqlite_path=str(tmp_path / "stt.db"),
        asr_service=_FakeASR(),
        ai_gateway=_FakeAI(),
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/stt?session_id=s1") as ws:
        ws.send_bytes(b"\x00\x01" * 20000)
        first = ws.receive_json()
        second = ws.receive_json()
        third = ws.receive_json()
        fourth = ws.receive_json()

    assert first["type"] == "partial"
    assert second["type"] == "final"
    assert third["type"] == "ai_request"
    assert fourth["type"] == "ai_response"


def test_health_endpoint(tmp_path):
    app = create_app(
        sqlite_path=str(tmp_path / "stt.db"),
        asr_service=_FakeASR(),
        ai_gateway=None,
    )
    client = TestClient(app)

    resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_websocket_tts_pipeline(tmp_path):
    app = create_app(
        sqlite_path=str(tmp_path / "stt.db"),
        asr_service=_FakeASR(),
        ai_gateway=_FakeAI(),
        tts_service=_FakeTTS(),
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/stt?session_id=s1") as ws:
        ws.send_bytes(b"\x00\x01" * 20000)
        events = []
        # Collect: partial, final, ai_request, ai_response, tts_start, audio, audio, tts_done
        for _ in range(8):
            try:
                msg = ws.receive()
                if "text" in msg:
                    events.append(msg["text"])
                elif "bytes" in msg:
                    events.append(("bytes", len(msg["bytes"])))
            except Exception:
                break

    event_types = []
    for e in events:
        if isinstance(e, str):
            import json
            parsed = json.loads(e)
            event_types.append(parsed.get("type"))
        else:
            event_types.append("audio")

    assert "partial" in event_types
    assert "final" in event_types
    assert "ai_request" in event_types
    assert "ai_response" in event_types
    assert "tts_start" in event_types
    assert "tts_done" in event_types
    assert "audio" in event_types


def test_websocket_tts_endpoint(tmp_path):
    app = create_app(
        sqlite_path=str(tmp_path / "stt.db"),
        asr_service=_FakeASR(),
        tts_service=_FakeTTS(),
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/tts?session_id=s1") as ws:
        ws.send_json({"text": "안녕하세요"})
        tts_start = ws.receive_json()
        assert tts_start["type"] == "tts_start"

        audio_chunks = []
        while True:
            msg = ws.receive()
            if "bytes" in msg:
                audio_chunks.append(msg["bytes"])
            elif "text" in msg:
                import json
                parsed = json.loads(msg["text"])
                if parsed.get("type") == "tts_done":
                    break

        assert len(audio_chunks) > 0


def test_websocket_tts_endpoint_not_enabled(tmp_path):
    app = create_app(
        sqlite_path=str(tmp_path / "stt.db"),
        asr_service=_FakeASR(),
        tts_service=None,
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/tts?session_id=s1") as ws:
        error_msg = ws.receive_json()
        assert error_msg["type"] == "error"
        assert error_msg["error"] == "tts_not_enabled"
