from silero_vad import load_silero_vad


class VADService:
    def __init__(self, min_silence_duration_ms: int = 400, speech_pad_ms: int = 300):
        self.min_silence_duration_ms = min_silence_duration_ms
        self.speech_pad_ms = speech_pad_ms
        self.model = load_silero_vad()

    def is_voiced(self, audio_bytes: bytes) -> bool:
        return bool(audio_bytes)
