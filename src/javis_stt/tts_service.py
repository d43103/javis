import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx


logger = logging.getLogger("javis.tts")


@dataclass
class TTSResult:
    audio_bytes: bytes
    error: str | None = None


class TTSService:
    def __init__(
        self,
        base_url: str,
        model: str,
        voice: str = "Sohee",
        sample_rate: int = 24000,
        timeout_seconds: float = 30.0,
        streaming: bool = True,
        chunk_size: int = 4096,
        requester=None,
        stream_requester=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.sample_rate = sample_rate
        self.timeout_seconds = timeout_seconds
        self.streaming = streaming
        self.chunk_size = chunk_size
        self.requester = requester or self._default_request
        self.stream_requester = stream_requester or self._default_stream_request

    def _build_payload(self, text: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "response_format": "pcm",
            "sample_rate": self.sample_rate,
        }

    def _default_request(self, payload: dict[str, Any]) -> bytes:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/v1/audio/speech",
                json=payload,
            )
            response.raise_for_status()
            return response.content

    def _default_stream_request(self, payload: dict[str, Any]) -> Iterator[bytes]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/v1/audio/speech",
                json=payload,
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=self.chunk_size):
                    if chunk:
                        yield chunk

    def synthesize(self, text: str) -> TTSResult:
        if not text or not text.strip():
            return TTSResult(audio_bytes=b"", error="empty_input")

        payload = self._build_payload(text.strip())
        try:
            audio_bytes = self.requester(payload)
            return TTSResult(audio_bytes=audio_bytes, error=None)
        except Exception as exc:
            logger.exception("tts_request_failed text=%s", text[:80])
            return TTSResult(audio_bytes=b"", error=str(exc))

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        if not text or not text.strip():
            return

        payload = self._build_payload(text.strip())
        try:
            yield from self.stream_requester(payload)
        except Exception:
            logger.exception("tts_stream_failed text=%s", text[:80])
