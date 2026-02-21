import struct
from unittest.mock import patch, MagicMock

import numpy as np
import pytest


class _FakeResult:
    def __init__(self, val):
        self._val = val

    def item(self):
        return self._val


def _fake_model(tensor, sr):
    """Fake Silero model: returns peak amplitude as speech probability."""
    if hasattr(tensor, "numpy"):
        arr = tensor.numpy()
    elif hasattr(tensor, "_data"):
        arr = tensor._data
    else:
        arr = np.zeros(1)
    peak = float(np.max(np.abs(arr)))
    return _FakeResult(peak)


def _make_pcm_int16(value: int, n_samples: int) -> bytes:
    """Create PCM16 audio with constant sample value."""
    return struct.pack(f"<{n_samples}h", *([value] * n_samples))


@pytest.fixture(autouse=True)
def _patch_silero():
    """Patch load_silero_vad so tests don't need a real model."""
    with patch("src.javis_stt.vad_service.load_silero_vad", return_value=_fake_model):
        yield


# Import after patching is set up via fixture (module is already importable)
from src.javis_stt.vad_service import VADService  # noqa: E402


class TestVADService:
    def test_empty_bytes_returns_false(self):
        vad = VADService()
        assert vad.is_voiced(b"") is False

    def test_silence_returns_false(self):
        vad = VADService(threshold=0.5)
        audio = _make_pcm_int16(10, 1024)
        assert vad.is_voiced(audio) is False

    def test_loud_audio_returns_true(self):
        vad = VADService(threshold=0.5)
        audio = _make_pcm_int16(25000, 1024)
        assert vad.is_voiced(audio) is True

    def test_threshold_boundary(self):
        vad = VADService(threshold=0.5)
        audio = _make_pcm_int16(16384, 512)  # 16384/32768 = 0.5
        assert vad.is_voiced(audio) is True

    def test_below_threshold(self):
        vad = VADService(threshold=0.5)
        audio = _make_pcm_int16(16000, 512)  # 16000/32768 ≈ 0.488
        assert vad.is_voiced(audio) is False

    def test_short_audio_below_chunk_size(self):
        vad = VADService(threshold=0.5)
        audio = _make_pcm_int16(30000, 100)
        assert vad.is_voiced(audio) is True

    def test_mixed_audio_speech_in_second_chunk(self):
        vad = VADService(threshold=0.5)
        silence = _make_pcm_int16(10, 512)
        speech = _make_pcm_int16(25000, 512)
        audio = silence + speech
        assert vad.is_voiced(audio) is True

    def test_custom_threshold(self):
        vad = VADService(threshold=0.1)
        audio = _make_pcm_int16(5000, 512)  # 5000/32768 ≈ 0.15
        assert vad.is_voiced(audio) is True
