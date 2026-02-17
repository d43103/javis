from types import SimpleNamespace
import importlib

ASRService = importlib.import_module("src.javis_stt.asr_service").ASRService


class _FakeSegment:
    def __init__(self, text: str, avg_logprob: float = -0.2):
        self.text = text
        self.avg_logprob = avg_logprob


class _FakeModel:
    def transcribe(self, audio, **kwargs):
        return iter([_FakeSegment("안녕하세요 테스트")]), SimpleNamespace(language=kwargs.get("language", "ko"))


def test_asr_service_uses_accuracy_profile():
    captured = {}

    def fake_loader(model_size, device, compute_type):
        captured["model_size"] = model_size
        captured["device"] = device
        captured["compute_type"] = compute_type
        return _FakeModel()

    service = ASRService(
        model_size="large-v3",
        compute_type="float16",
        language="ko",
        beam_size=5,
        model_loader=fake_loader,
    )

    assert captured["model_size"] == "large-v3"
    assert captured["device"] == "cuda"
    assert captured["compute_type"] == "float16"
    assert service.language == "ko"
    assert service.beam_size == 5


def test_asr_service_emits_partial_and_final():
    service = ASRService(
        model_size="large-v3",
        compute_type="float16",
        language="ko",
        beam_size=5,
        model_loader=lambda *_args, **_kwargs: _FakeModel(),
    )

    events = service.transcribe_segment(
        audio_bytes=b"\x00\x01" * 16,
        session_id="s1",
        segment_id="seg-001",
        started_at=0.0,
        ended_at=1.0,
    )

    assert [e["type"] for e in events] == ["partial", "final"]
    assert events[-1]["text"] == "안녕하세요 테스트"
