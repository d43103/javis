import asyncio
import base64
import concurrent.futures
import io
import json
import logging
import unicodedata
import time
import threading
from urllib.parse import urlparse
import wave
from typing import Any, Callable

from faster_whisper import WhisperModel
import httpx
import numpy as np
import websockets

Event = dict[str, str | float | None]


ModelLoader = Callable[[str, str, str], Any]
TranscriptionRequester = Callable[[bytes], dict[str, Any]]
RealtimeRequester = Callable[[bytes], dict[str, Any]]


logger = logging.getLogger("javis.asr")


class ASRService:
    def __init__(
        self,
        model_size: str,
        compute_type: str,
        language: str,
        beam_size: int,
        condition_on_previous_text: bool = True,
        whisper_vad_filter: bool = False,
        temperature: float = 0.0,
        no_speech_threshold: float = 0.6,
        log_prob_threshold: float = -1.0,
        compression_ratio_threshold: float = 2.4,
        segment_min_avg_logprob: float = -0.9,
        segment_max_no_speech_prob: float = 0.75,
        repeated_text_window_seconds: float = 4.0,
        repeated_text_logprob_threshold: float = -0.25,
        hallucination_max_confidence: float = -0.15,
        hallucination_exact_phrases: list[str] | None = None,
        hallucination_always_block_contains: list[str] | None = None,
        provider: str = "faster_whisper",
        remote_base_url: str = "http://127.0.0.1:8011",
        remote_model: str = "Qwen/Qwen3-ASR-1.7B",
        remote_timeout_seconds: float = 60.0,
        remote_realtime_path: str = "/v1/realtime",
        remote_realtime_chunk_bytes: int = 4096,
        transcription_requester: TranscriptionRequester | None = None,
        realtime_requester: RealtimeRequester | None = None,
        model_loader: ModelLoader | None = None,
    ):
        self.provider = provider
        self.model_size = model_size
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.condition_on_previous_text = condition_on_previous_text
        self.whisper_vad_filter = whisper_vad_filter
        self.temperature = temperature
        self.no_speech_threshold = no_speech_threshold
        self.log_prob_threshold = log_prob_threshold
        self.compression_ratio_threshold = compression_ratio_threshold
        self.model_loader = model_loader or self._default_model_loader
        self.remote_base_url = remote_base_url.rstrip("/")
        self.remote_model = remote_model
        self.remote_timeout_seconds = remote_timeout_seconds
        self.remote_realtime_path = remote_realtime_path
        self.remote_realtime_chunk_bytes = max(512, remote_realtime_chunk_bytes)
        self.transcription_requester = transcription_requester or self._default_transcription_request
        self.realtime_requester = realtime_requester or self._default_realtime_request
        if self.provider == "faster_whisper":
            self.model = self.model_loader(self.model_size, "cuda", self.compute_type)
        else:
            self.model = None
        self.min_avg_logprob = segment_min_avg_logprob
        self.max_no_speech_prob = segment_max_no_speech_prob
        self.repeated_text_window_seconds = repeated_text_window_seconds
        self.repeated_text_logprob_threshold = repeated_text_logprob_threshold
        self.hallucination_max_confidence = hallucination_max_confidence
        base_exact_phrases = {
            phrase.strip()
            for phrase in (hallucination_exact_phrases or [])
            if phrase and phrase.strip()
        }
        base_contains_phrases = [
            phrase.strip().lower()
            for phrase in (hallucination_always_block_contains or [])
            if phrase and phrase.strip()
        ]
        self._hallucination_exact_phrases: frozenset[str] = frozenset(base_exact_phrases)
        self._hallucination_always_block_contains: tuple[str, ...] = tuple(base_contains_phrases)
        self._hallucination_lock = threading.Lock()
        self._recent_text: str | None = None
        self._recent_text_at: float = 0.0
        self._realtime_request_lock = threading.Lock()
        self._realtime_ws = None
        self._realtime_loop: asyncio.AbstractEventLoop | None = None
        self._realtime_loop_thread: threading.Thread | None = None
        if self.provider == "qwen3_asr_vllm_realtime" and realtime_requester is None:
            loop = asyncio.new_event_loop()
            self._realtime_loop = loop
            self._realtime_loop_thread = threading.Thread(
                target=self._run_realtime_loop,
                name="javis-asr-realtime-loop",
                daemon=True,
            )
            self._realtime_loop_thread.start()

    def _run_realtime_loop(self) -> None:
        if self._realtime_loop is None:
            return
        asyncio.set_event_loop(self._realtime_loop)
        self._realtime_loop.run_forever()

    def _run_realtime_coroutine(self, coro: Any) -> dict[str, Any]:
        if self._realtime_loop is None:
            raise RuntimeError("realtime_loop_not_initialized")
        future = asyncio.run_coroutine_threadsafe(coro, self._realtime_loop)
        timeout = max(5.0, self.remote_timeout_seconds + 5.0)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("realtime_request_timeout")

    async def _ensure_realtime_connection_async(self):
        if self._realtime_ws is not None:
            return self._realtime_ws

        uri = self._build_realtime_uri()
        timeout = self.remote_timeout_seconds
        ws = await websockets.connect(
            uri,
            open_timeout=timeout,
            close_timeout=timeout,
            ping_interval=60,
            ping_timeout=60,
        )
        session_created_raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        session_created = json.loads(session_created_raw)
        if session_created.get("type") != "session.created":
            await ws.close()
            raise RuntimeError(f"unexpected_realtime_event:{session_created.get('type')}")

        await ws.send(json.dumps({"type": "session.update", "model": self.remote_model}))
        if self.language:
            await ws.send(json.dumps({"type": "session.update", "language": self.language}))

        self._realtime_ws = ws
        return ws

    async def _reset_realtime_connection_async(self) -> None:
        ws = self._realtime_ws
        self._realtime_ws = None
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:
            return

    def _strip_low_confidence_trailing_hallucination(self, text: str, best_avg_logprob: float | None) -> str:
        if best_avg_logprob is not None and best_avg_logprob > -0.2:
            return text

        suffixes = [
            "시청해 주셔서 감사합니다.",
            "시청해주셔서 감사합니다.",
            "구독과 좋아요 부탁드립니다.",
            "아멘.",
            "아멘",
            "아멘, 다음 영상에서 만나요.",
            "아멘 다음 영상에서 만나요.",
            "다음 영상에서 만나요.",
            "한글자막 by 한효정",
            "한글 자막 by 한효정",
            "감사합니다.",
            "감사합니다",
        ]
        stripped = text.strip()
        for suffix in suffixes:
            if stripped.endswith(suffix):
                head = stripped[: -len(suffix)].strip(" ,.!?")
                if head:
                    return head
        return stripped

    def _contains_always_block_hallucination(self, text: str) -> bool:
        lowered = text.lower()
        return any(needle in lowered for needle in self._hallucination_always_block_contains)

    def _is_configured_hallucination(self, text: str, best_avg_logprob: float | None) -> bool:
        if text not in self._hallucination_exact_phrases:
            return False
        if best_avg_logprob is None:
            return True
        return best_avg_logprob <= self.hallucination_max_confidence

    def _contains_cjk_ideograph(self, text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    def _contains_hangul(self, text: str) -> bool:
        return any(("\uac00" <= ch <= "\ud7a3") or ("\u3130" <= ch <= "\u318f") for ch in text)

    def _is_non_korean_script_output(self, text: str) -> bool:
        if not self.language.lower().startswith("ko"):
            return False
        if self._contains_hangul(text):
            return False
        normalized = "".join(ch for ch in text if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"))
        if not normalized:
            return False
        return self._contains_cjk_ideograph(normalized) and len(normalized) <= 8

    def register_runtime_hallucinations(
        self,
        exact_phrases: list[str] | None = None,
        contains_phrases: list[str] | None = None,
        replace: bool = False,
    ) -> dict[str, int]:
        exact = {
            phrase.strip()
            for phrase in (exact_phrases or [])
            if phrase and phrase.strip()
        }
        contains = [
            phrase.strip().lower()
            for phrase in (contains_phrases or [])
            if phrase and phrase.strip()
        ]

        with self._hallucination_lock:
            if replace:
                self._hallucination_exact_phrases = frozenset(exact)
                self._hallucination_always_block_contains = tuple(contains)
            else:
                self._hallucination_exact_phrases = frozenset(set(self._hallucination_exact_phrases).union(exact))
                merged_contains = list(self._hallucination_always_block_contains)
                for needle in contains:
                    if needle not in merged_contains:
                        merged_contains.append(needle)
                self._hallucination_always_block_contains = tuple(merged_contains)

            return {
                "exact_count": len(self._hallucination_exact_phrases),
                "contains_count": len(self._hallucination_always_block_contains),
            }

    def _default_model_loader(self, model_size: str, device: str, compute_type: str) -> Any:
        return WhisperModel(model_size, device=device, compute_type=compute_type)

    def _build_wav_payload(self, audio_bytes: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_bytes)
        return buffer.getvalue()

    def _default_transcription_request(self, wav_bytes: bytes) -> dict[str, Any]:
        files = {
            "file": ("segment.wav", wav_bytes, "audio/wav"),
        }
        data: dict[str, str] = {
            "model": self.remote_model,
        }
        if self.language:
            data["language"] = self.language

        with httpx.Client(timeout=self.remote_timeout_seconds) as client:
            response = client.post(f"{self.remote_base_url}/v1/audio/transcriptions", data=data, files=files)
            response.raise_for_status()
            return response.json()

    def _transcribe_remote(self, audio_bytes: bytes) -> tuple[str, float | None]:
        wav_bytes = self._build_wav_payload(audio_bytes)
        raw = self.transcription_requester(wav_bytes)
        text = str(raw.get("text", "") or "").strip()
        confidence_raw = raw.get("confidence")
        confidence = float(confidence_raw) if confidence_raw is not None else None
        return text, confidence

    def _build_realtime_uri(self) -> str:
        parsed = urlparse(self.remote_base_url)
        if parsed.scheme in {"http", "https"}:
            ws_scheme = "wss" if parsed.scheme == "https" else "ws"
            host = parsed.hostname or "127.0.0.1"
            if parsed.port:
                host = f"{host}:{parsed.port}"
            base_path = parsed.path.rstrip("/")
            path = self.remote_realtime_path
            if not path.startswith("/"):
                path = "/" + path
            return f"{ws_scheme}://{host}{base_path}{path}"
        if parsed.scheme in {"ws", "wss"}:
            return self.remote_base_url.rstrip("/")
        return f"ws://127.0.0.1:8011{self.remote_realtime_path}"

    async def _transcribe_realtime_async(self, audio_bytes: bytes) -> dict[str, Any]:
        timeout = self.remote_timeout_seconds
        collected = ""
        chunk_size = self.remote_realtime_chunk_bytes
        ws = await self._ensure_realtime_connection_async()
        try:
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

            for i in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[i : i + chunk_size]
                await ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(chunk).decode("utf-8"),
                        }
                    )
                )

            await ws.send(json.dumps({"type": "input_audio_buffer.commit", "final": True}))

            while True:
                message_raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                message = json.loads(message_raw)
                event_type = message.get("type")
                if event_type == "transcription.delta":
                    collected += str(message.get("delta", "") or "")
                    continue
                if event_type == "transcription.done":
                    text = str(message.get("text", "") or "").strip()
                    return {"text": text if text else collected.strip()}
                if event_type == "error":
                    raise RuntimeError(str(message.get("error", "realtime_error")))
        except Exception:
            await self._reset_realtime_connection_async()
            raise

    def _default_realtime_request(self, audio_bytes: bytes) -> dict[str, Any]:
        if self._realtime_loop is None:
            return asyncio.run(self._transcribe_realtime_async(audio_bytes))
        with self._realtime_request_lock:
            return self._run_realtime_coroutine(self._transcribe_realtime_async(audio_bytes))

    def _transcribe_remote_realtime(self, audio_bytes: bytes) -> tuple[str, float | None]:
        raw = self.realtime_requester(audio_bytes)
        text = str(raw.get("text", "") or "").strip()
        confidence_raw = raw.get("confidence")
        confidence = float(confidence_raw) if confidence_raw is not None else None
        return text, confidence

    def transcribe_segment(
        self,
        audio_bytes: bytes,
        session_id: str,
        segment_id: str,
        started_at: float,
        ended_at: float,
    ) -> list[Event]:
        text = ""
        best_avg_logprob: float | None = None

        def log_drop(reason: str) -> list[Event]:
            logger.info(
                "stt_drop reason=%s provider=%s session=%s segment=%s text=%s confidence=%s",
                reason,
                self.provider,
                session_id,
                segment_id,
                text.replace("\n", " ")[:120],
                best_avg_logprob,
            )
            return []

        if self.provider == "faster_whisper":
            model = self.model
            if model is None:
                logger.error("asr_model_not_loaded provider=%s", self.provider)
                return log_drop("model_not_loaded")
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _info = model.transcribe(
                audio_array,
                language=self.language,
                beam_size=self.beam_size,
                condition_on_previous_text=self.condition_on_previous_text,
                temperature=self.temperature,
                no_speech_threshold=self.no_speech_threshold,
                log_prob_threshold=self.log_prob_threshold,
                compression_ratio_threshold=self.compression_ratio_threshold,
                vad_filter=self.whisper_vad_filter,
            )

            segment_list = list(segments)
            kept_texts: list[str] = []

            for seg in segment_list:
                seg_avg_logprob = float(getattr(seg, "avg_logprob", -99.0))
                seg_no_speech_prob = float(getattr(seg, "no_speech_prob", 0.0))

                if seg_avg_logprob < self.min_avg_logprob:
                    continue
                if seg_no_speech_prob > self.max_no_speech_prob:
                    continue

                piece = (str(getattr(seg, "text", "") or "")).strip()
                if not piece:
                    continue

                kept_texts.append(piece)
                if best_avg_logprob is None or seg_avg_logprob > best_avg_logprob:
                    best_avg_logprob = seg_avg_logprob

            text = " ".join(kept_texts).strip()
        elif self.provider == "qwen3_asr_vllm":
            try:
                text, best_avg_logprob = self._transcribe_remote(audio_bytes)
            except Exception:
                logger.exception("remote_asr_request_failed provider=%s", self.provider)
                return log_drop("remote_http_failed")
        elif self.provider == "qwen3_asr_vllm_realtime":
            try:
                text, best_avg_logprob = self._transcribe_remote_realtime(audio_bytes)
                if not text:
                    text, best_avg_logprob = self._transcribe_remote(audio_bytes)
                    logger.warning(
                        "remote_asr_realtime_empty_fallback_to_http provider=%s base_url=%s",
                        self.provider,
                        self.remote_base_url,
                    )
            except Exception:
                logger.exception("remote_asr_realtime_request_failed provider=%s", self.provider)
                try:
                    text, best_avg_logprob = self._transcribe_remote(audio_bytes)
                    logger.warning(
                        "remote_asr_realtime_fallback_to_http provider=%s base_url=%s",
                        self.provider,
                        self.remote_base_url,
                    )
                except Exception:
                    logger.exception("remote_asr_http_fallback_failed provider=%s", self.provider)
                    return log_drop("remote_realtime_and_http_failed")
        else:
            logger.error("unsupported_asr_provider provider=%s", self.provider)
            return log_drop("unsupported_provider")

        text = unicodedata.normalize("NFC", text)
        text = self._strip_low_confidence_trailing_hallucination(text, best_avg_logprob)
        if not text:
            return log_drop("empty_text")
        if self._is_non_korean_script_output(text):
            return log_drop("non_korean_script")
        if self._contains_always_block_hallucination(text):
            return log_drop("always_block_contains")

        now = time.time()
        if (
            self._recent_text == text
            and now - self._recent_text_at < self.repeated_text_window_seconds
            and (best_avg_logprob is None or best_avg_logprob < self.repeated_text_logprob_threshold)
        ):
            return log_drop("recent_repeat")
        self._recent_text = text
        self._recent_text_at = now

        low_confidence_hallucinations = {
            "감사합니다.",
            "감사합니다",
            "시청해주셔서 감사합니다.",
            "시청해 주셔서 감사합니다.",
            "구독과 좋아요 부탁드립니다.",
            "아멘.",
            "아멘",
            "아멘, 다음 영상에서 만나요.",
            "아멘 다음 영상에서 만나요.",
            "다음 영상에서 만나요.",
            "한글자막 by 한효정",
            "한글 자막 by 한효정",
        }
        if text in low_confidence_hallucinations and (best_avg_logprob is None or best_avg_logprob < -0.35):
            return log_drop("low_confidence_hallucination")
        if self._is_configured_hallucination(text, best_avg_logprob):
            return log_drop("configured_hallucination")

        partial_text = text if len(text) < 4 else text[: max(1, len(text) // 2)]
        events = [
            {
                "type": "partial",
                "session_id": session_id,
                "segment_id": segment_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "text": partial_text,
                "confidence": best_avg_logprob,
            },
            {
                "type": "final",
                "session_id": session_id,
                "segment_id": segment_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "text": text,
                "confidence": best_avg_logprob,
            },
        ]
        return events
