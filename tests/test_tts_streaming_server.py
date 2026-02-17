import importlib
import types
import sys


def _make_fake_qwen3_streaming():
    """Inject a fake qwen3_streaming module into sys.modules."""
    mod = types.ModuleType("qwen3_streaming")

    def fake_stream_generate_voice_clone(model, tokenizer, ref_audio_path, ref_text, text, emit_every_frames=4):
        yield b"\x00\x01" * 480
        yield b"\x00\x02" * 480
        yield b"\x00\x03" * 480

    mod.stream_generate_voice_clone = fake_stream_generate_voice_clone

    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)

    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoModelForCausalLM = None
    fake_transformers.AutoTokenizer = None

    sys.modules["qwen3_streaming"] = mod
    sys.modules["torch"] = fake_torch
    sys.modules["transformers"] = fake_transformers
    return mod


def test_tts_streaming_server_health():
    _make_fake_qwen3_streaming()

    from fastapi.testclient import TestClient
    tts_mod = importlib.import_module("src.javis_tts.tts_streaming_server")
    create_tts_app = tts_mod.create_tts_app

    fake_model = object()
    fake_tokenizer = object()
    app = create_tts_app(
        model=fake_model,
        tokenizer=fake_tokenizer,
        ref_audio_path="fake.wav",
        ref_text="테스트",
    )
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_tts_streaming_server_speech_endpoint():
    _make_fake_qwen3_streaming()

    from fastapi.testclient import TestClient
    import importlib
    tts_mod = importlib.import_module("src.javis_tts.tts_streaming_server")
    create_tts_app = tts_mod.create_tts_app

    fake_model = object()
    fake_tokenizer = object()
    app = create_tts_app(
        model=fake_model,
        tokenizer=fake_tokenizer,
        ref_audio_path="fake.wav",
        ref_text="테스트",
    )
    client = TestClient(app)

    with client.stream("POST", "/v1/audio/speech", json={"input": "안녕하세요", "response_format": "pcm"}) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes())
        assert len(body) > 0
