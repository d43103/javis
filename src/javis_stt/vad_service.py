import logging

import numpy as np
import torch
from silero_vad import load_silero_vad

logger = logging.getLogger("javis.vad")

_SAMPLE_RATE = 16000
_CHUNK_SAMPLES = 512  # Silero VAD expects 512 samples at 16kHz


class VADService:
    def __init__(
        self,
        min_silence_duration_ms: int = 400,
        speech_pad_ms: int = 300,
        threshold: float = 0.5,
    ):
        self.min_silence_duration_ms = min_silence_duration_ms
        self.speech_pad_ms = speech_pad_ms
        self.threshold = threshold
        self.model = load_silero_vad()

    def is_voiced(self, audio_bytes: bytes) -> bool:
        """Check if audio contains speech using Silero VAD.

        Splits audio into 512-sample chunks, runs each through Silero,
        and returns True if any chunk exceeds the speech threshold.
        """
        if not audio_bytes:
            return False

        samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        if len(samples) < _CHUNK_SAMPLES:
            tensor = torch.from_numpy(samples)
            prob = self.model(tensor, _SAMPLE_RATE).item()
            return prob >= self.threshold

        # Check 512-sample chunks; return True on first speech detection
        for offset in range(0, len(samples) - _CHUNK_SAMPLES + 1, _CHUNK_SAMPLES):
            chunk = samples[offset : offset + _CHUNK_SAMPLES]
            tensor = torch.from_numpy(chunk)
            prob = self.model(tensor, _SAMPLE_RATE).item()
            if prob >= self.threshold:
                return True

        return False
