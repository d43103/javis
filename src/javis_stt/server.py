import asyncio
import time
import logging
from pathlib import Path
from typing import ClassVar, override

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .ambient_service import AmbientSoundService
from .ai_gateway import AIGateway
from .asr_service import ASRService
from .config import load_config
from .db import create_session_factory, session_scope
from .repository import TranscriptRepository
from .session_manager import SessionManager
from .tts_service import TTSService
from .vad_service import VADService


logger = logging.getLogger("javis.stt")


class _DialogueColorFormatter(logging.Formatter):
    USER_COLOR: ClassVar[str] = "\033[96m"
    AI_COLOR: ClassVar[str] = "\033[92m"
    RESET: ClassVar[str] = "\033[0m"

    @override
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        role = getattr(record, "dialogue_role", "")
        if role == "user":
            return f"USER: {self.USER_COLOR}{message}{self.RESET}"
        if role == "assistant":
            return f"AI: {self.AI_COLOR}{message}{self.RESET}"
        return message


def _configure_dialogue_logger(log_path: str) -> logging.Logger:
    dialogue_logger = logging.getLogger("javis.dialogue")
    has_file = any(h.get_name() == "javis-dialogue-file" for h in dialogue_logger.handlers)
    has_console = any(h.get_name() == "javis-dialogue-console" for h in dialogue_logger.handlers)
    if has_file and has_console:
        return dialogue_logger

    dialogue_logger.setLevel(logging.INFO)
    dialogue_logger.propagate = False

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not has_file:
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.set_name("javis-dialogue-file")
        file_handler.setFormatter(_DialogueColorFormatter())
        dialogue_logger.addHandler(file_handler)

    if not has_console:
        console_handler = logging.StreamHandler()
        console_handler.set_name("javis-dialogue-console")
        console_handler.setFormatter(_DialogueColorFormatter())
        dialogue_logger.addHandler(console_handler)

    return dialogue_logger


def _compact_text(value: str) -> str:
    return " ".join(value.split())[:400]


def _split_tokens(value: str) -> list[str]:
    return [token for token in value.strip().split() if token]


def _merge_utterance_texts(parts: list[str]) -> str:
    merged_tokens: list[str] = []
    for raw in parts:
        tokens = _split_tokens(raw)
        if not tokens:
            continue
        if not merged_tokens:
            merged_tokens.extend(tokens)
            continue

        max_overlap = min(8, len(merged_tokens), len(tokens))
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if merged_tokens[-size:] == tokens[:size]:
                overlap = size
                break

        merged_tokens.extend(tokens[overlap:])

    return " ".join(merged_tokens).strip()


def _looks_sentence_complete(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.endswith((".", "?", "!", "…")):
        return True
    return text.endswith(("요", "다", "죠", "네", "까")) and len(text) >= 10


class HallucinationConfigUpdate(BaseModel):
    exact_phrases: list[str] = Field(default_factory=list)
    contains_phrases: list[str] = Field(default_factory=list)
    replace: bool = False


class VoiceTurnRequest(BaseModel):
    session_id: str
    text: str           # user's transcribed speech (for logging)
    response_text: str  # AI response text from Mac — this gets synthesized


def create_app(
    sqlite_path: str,
    asr_service=None,
    ai_gateway=None,
    vad_service=None,
    ambient_service=None,
    tts_service=None,
    dialogue_log_path: str = "logs/dialogue.log",
    ai_idle_flush_seconds: float = 1.8,
    ai_idle_flush_requires_sentence_end: bool = False,
    ai_max_utterance_hold_seconds: float = 8.0,
    min_segment_duration_seconds: float = 1.2,
    pre_roll_ms: int = 400,
) -> FastAPI:
    app = FastAPI()
    session_factory = create_session_factory(sqlite_path)
    sessions = SessionManager()
    dialogue_logger = _configure_dialogue_logger(dialogue_log_path)

    app.state.session_factory = session_factory
    app.state.asr_service = asr_service
    app.state.ai_gateway = ai_gateway
    app.state.vad_service = vad_service
    app.state.ambient_service = ambient_service
    app.state.tts_service = tts_service

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.post("/config/hallucinations")
    async def update_hallucinations(payload: HallucinationConfigUpdate):
        if app.state.asr_service is None or not hasattr(app.state.asr_service, "register_runtime_hallucinations"):
            raise HTTPException(status_code=503, detail="asr_service_not_ready")

        result = app.state.asr_service.register_runtime_hallucinations(
            exact_phrases=payload.exact_phrases,
            contains_phrases=payload.contains_phrases,
            replace=payload.replace,
        )
        logger.info(
            "hallucination_config_update exact=%s contains=%s replace=%s",
            len(payload.exact_phrases),
            len(payload.contains_phrases),
            payload.replace,
        )
        return {"status": "ok", **result}

    @app.post("/v1/voice/turn")
    async def voice_turn(request: VoiceTurnRequest):
        if app.state.tts_service is None:
            raise HTTPException(status_code=503, detail="tts_not_enabled")

        logger.info(
            "voice_turn session=%s request_text=%s",
            request.session_id,
            request.text[:100] if request.text else "(empty)",
        )

        async def generate_audio():
            for chunk in await asyncio.to_thread(
                lambda: list(app.state.tts_service.synthesize_stream(request.response_text))
            ):
                yield chunk

        return StreamingResponse(
            generate_audio(),
            media_type="audio/pcm",
            headers={"X-Session-Id": request.session_id},
        )

    @app.websocket("/ws/tts")
    async def websocket_tts(websocket: WebSocket):
        await websocket.accept()
        session_id = websocket.query_params.get("session_id", "default")
        logger.info("ws_tts_connect session=%s client=%s", session_id, websocket.client)

        if app.state.tts_service is None:
            await websocket.send_json({"type": "error", "error": "tts_not_enabled"})
            await websocket.close(code=1008, reason="tts_not_enabled")
            return

        try:
            while True:
                data = await websocket.receive_json()
                text = data.get("text", "")
                if not text:
                    continue

                await websocket.send_json({"type": "tts_start", "session_id": session_id})
                try:
                    for chunk in await asyncio.to_thread(
                        lambda t=text: list(app.state.tts_service.synthesize_stream(t))
                    ):
                        await websocket.send_bytes(chunk)
                except Exception:
                    logger.exception("ws_tts_stream_error session=%s", session_id)
                await websocket.send_json({"type": "tts_done", "session_id": session_id})
        except WebSocketDisconnect:
            logger.info("ws_tts_disconnect session=%s", session_id)
            return
        except Exception:
            logger.exception("ws_tts_loop_error session=%s", session_id)
            raise

    @app.websocket("/ws/stt")
    async def websocket_stt(websocket: WebSocket):
        await websocket.accept()
        session_id = websocket.query_params.get("session_id", "default")
        logger.info("ws_connect session=%s client=%s", session_id, websocket.client)
        sample_rate = 16000
        channels = 1
        bytes_per_second = sample_rate * channels * 2
        min_segment_bytes = int(bytes_per_second * min_segment_duration_seconds)
        pre_roll_bytes = max(0, int(bytes_per_second * (pre_roll_ms / 1000.0)))
        audio_buffer = bytearray()
        pending_final_texts: list[str] = []
        pending_final_segment_id: str | None = None
        last_final_at: float | None = None
        first_final_at: float | None = None
        previous_tail = b""
        previous_segment_voiced = False
        ingress_chunks = 0
        ingress_bytes = 0
        ingress_window_started_at = time.time()

        async def request_ai_turn(segment_id: str, request_text: str):
            if app.state.ai_gateway is None or not request_text:
                return None

            await websocket.send_json(
                {
                    "type": "ai_request",
                    "session_id": session_id,
                    "segment_id": segment_id,
                    "text": request_text,
                }
            )
            ai_result = await asyncio.to_thread(
                app.state.ai_gateway.generate,
                session_id=session_id,
                text=request_text,
            )
            await websocket.send_json(
                {
                    "type": "ai_response",
                    "session_id": session_id,
                    "segment_id": segment_id,
                    "text": ai_result.text,
                    "error": ai_result.error,
                }
            )
            logger.info(
                "ai_event session=%s segment=%s text=%s error=%s",
                session_id,
                segment_id,
                ai_result.text.replace("\n", " ")[:200],
                ai_result.error,
            )
            dialogue_logger.info(
                _compact_text(ai_result.text) if ai_result.text else f"(error) {ai_result.error or 'unknown_error'}",
                extra={"dialogue_role": "assistant"},
            )

            if app.state.tts_service is not None and ai_result.text and not ai_result.error:
                await websocket.send_json(
                    {
                        "type": "tts_start",
                        "session_id": session_id,
                        "segment_id": segment_id,
                    }
                )
                try:
                    for chunk in await asyncio.to_thread(
                        lambda: list(app.state.tts_service.synthesize_stream(ai_result.text))
                    ):
                        await websocket.send_bytes(chunk)
                except Exception:
                    logger.exception("tts_stream_error session=%s segment=%s", session_id, segment_id)
                await websocket.send_json(
                    {
                        "type": "tts_done",
                        "session_id": session_id,
                        "segment_id": segment_id,
                    }
                )

            return ai_result

        async def flush_pending_ai_turn() -> None:
            nonlocal pending_final_segment_id
            nonlocal last_final_at
            nonlocal first_final_at

            if not pending_final_texts or app.state.ai_gateway is None:
                return

            merged_text = _merge_utterance_texts(pending_final_texts)
            if not merged_text:
                pending_final_texts.clear()
                pending_final_segment_id = None
                last_final_at = None
                first_final_at = None
                return

            dialogue_logger.info(
                _compact_text(merged_text),
                extra={"dialogue_role": "user"},
            )

            segment_id = pending_final_segment_id or sessions.next_segment_id(session_id)
            ai_result = await request_ai_turn(
                segment_id=segment_id,
                request_text=merged_text,
            )
            if ai_result is not None:
                with session_scope(session_factory) as db_session:
                    repo = TranscriptRepository(db_session)
                    repo.save_ai_turn(
                        session_id=session_id,
                        segment_id=segment_id,
                        request_text=merged_text,
                        response_text=ai_result.text,
                        error=ai_result.error,
                    )

            pending_final_texts.clear()
            pending_final_segment_id = None
            last_final_at = None
            first_final_at = None

        try:
            while True:
                audio_bytes = await websocket.receive_bytes()
                ingress_chunks += 1
                ingress_bytes += len(audio_bytes)
                now = time.time()
                elapsed = now - ingress_window_started_at
                if elapsed >= 5.0:
                    logger.info(
                        "ws_ingress session=%s chunks=%s bytes=%s bps=%.1f",
                        session_id,
                        ingress_chunks,
                        ingress_bytes,
                        ingress_bytes / elapsed,
                    )
                    ingress_chunks = 0
                    ingress_bytes = 0
                    ingress_window_started_at = now

                audio_buffer.extend(audio_bytes)
                if len(audio_buffer) < min_segment_bytes:
                    continue
                payload = bytes(audio_buffer)
                audio_buffer.clear()
                segment_duration_seconds = len(payload) / bytes_per_second
                ended_at = time.time()
                started_at = ended_at - segment_duration_seconds
                segment_id = sessions.next_segment_id(session_id)

                is_voiced = True
                if app.state.vad_service is not None:
                    is_voiced = await asyncio.to_thread(app.state.vad_service.is_voiced, payload)

                ambient_events: list[dict[str, str | float | None]] = []
                if app.state.ambient_service is not None:
                    ambient_events = await asyncio.to_thread(
                        app.state.ambient_service.detect_events,
                        audio_bytes=payload,
                        session_id=session_id,
                        segment_id=segment_id,
                        started_at=started_at,
                        ended_at=ended_at,
                    )

                if ambient_events:
                    with session_scope(session_factory) as db_session:
                        repo = TranscriptRepository(db_session)
                        for event in ambient_events:
                            started = event.get("started_at")
                            ended = event.get("ended_at")
                            confidence = event.get("confidence")
                            repo.save_ambient(
                                session_id=str(event["session_id"]),
                                segment_id=str(event["segment_id"]),
                                started_at=float(started if started is not None else started_at),
                                ended_at=float(ended if ended is not None else ended_at),
                                text=str(event["text"]),
                                confidence=(float(confidence) if confidence is not None else None),
                            )
                            logger.info(
                                "ambient_event session=%s segment=%s text=%s confidence=%.3f",
                                event["session_id"],
                                event["segment_id"],
                                event["text"],
                                float(event["confidence"] or 0.0),
                            )
                            await websocket.send_json(event)

                has_ambient_speech = any(
                    "speech" in str(event.get("text", "")).lower()
                    and float(event.get("confidence") or 0.0) >= 0.70
                    for event in ambient_events
                )
                if not is_voiced and has_ambient_speech:
                    is_voiced = True
                    logger.info(
                        "vad_fallback session=%s segment=%s reason=ambient_speech",
                        session_id,
                        segment_id,
                    )

                if not is_voiced:
                    await flush_pending_ai_turn()
                    previous_segment_voiced = False
                    previous_tail = payload[-pre_roll_bytes:] if pre_roll_bytes > 0 else b""
                    continue

                use_pre_roll = previous_segment_voiced is False and bool(previous_tail)
                asr_payload = payload
                asr_started_at = started_at
                if use_pre_roll:
                    asr_payload = previous_tail + payload
                    asr_started_at = started_at - (len(previous_tail) / bytes_per_second)

                events = await asyncio.to_thread(
                    app.state.asr_service.transcribe_segment,
                    audio_bytes=asr_payload,
                    session_id=session_id,
                    segment_id=segment_id,
                    started_at=asr_started_at,
                    ended_at=ended_at,
                )
                if not events:
                    logger.info(
                        "stt_no_events session=%s segment=%s payload_bytes=%s voiced=%s",
                        session_id,
                        segment_id,
                        len(asr_payload),
                        is_voiced,
                    )

                with session_scope(session_factory) as db_session:
                    repo = TranscriptRepository(db_session)
                    for event in events:
                        if event["type"] == "partial":
                            repo.save_partial(
                                session_id=event["session_id"],
                                segment_id=event["segment_id"],
                                started_at=event["started_at"],
                                ended_at=event["ended_at"],
                                text=event["text"],
                                confidence=event.get("confidence"),
                            )
                        if event["type"] == "final":
                            repo.save_final(
                                session_id=event["session_id"],
                                segment_id=event["segment_id"],
                                started_at=event["started_at"],
                                ended_at=event["ended_at"],
                                text=event["text"],
                                confidence=event.get("confidence"),
                            )
                        if event["type"] in {"partial", "final"}:
                            logger.info(
                                "stt_event type=%s session=%s segment=%s text=%s",
                                event["type"],
                                event["session_id"],
                                event["segment_id"],
                                str(event["text"]).replace("\n", " ")[:200],
                            )
                        await websocket.send_json(event)

                    final_events = [e for e in events if e["type"] == "final"]
                    if final_events:
                        pending_final_texts.extend(str(e["text"]).strip() for e in final_events if str(e["text"]).strip())
                        pending_final_segment_id = str(final_events[-1]["segment_id"])
                        last_final_at = float(final_events[-1]["ended_at"])
                        if first_final_at is None:
                            first_final_at = last_final_at

                previous_segment_voiced = True
                previous_tail = payload[-pre_roll_bytes:] if pre_roll_bytes > 0 else b""

                if app.state.vad_service is None and pending_final_texts and app.state.ai_gateway is not None:
                    await flush_pending_ai_turn()

                if (
                    pending_final_texts
                    and app.state.ai_gateway is not None
                    and last_final_at is not None
                    and time.time() - last_final_at >= ai_idle_flush_seconds
                ):
                    merged_text = _merge_utterance_texts(pending_final_texts)
                    utterance_age = 0.0
                    if first_final_at is not None:
                        utterance_age = time.time() - first_final_at

                    if (
                        not ai_idle_flush_requires_sentence_end
                        or _looks_sentence_complete(merged_text)
                        or utterance_age >= ai_max_utterance_hold_seconds
                    ):
                        await flush_pending_ai_turn()
        except WebSocketDisconnect:
            logger.info("ws_disconnect session=%s", session_id)
            return
        except Exception:
            logger.exception("ws_loop_error session=%s", session_id)
            raise

    return app


def build_default_app(config_path: str = "config/stt.yaml") -> FastAPI:
    cfg = load_config(config_path)
    asr = ASRService(
        provider=cfg.stt.provider,
        model_size=cfg.stt.model_size,
        compute_type=cfg.stt.compute_type,
        language=cfg.stt.language,
        beam_size=cfg.stt.beam_size,
        remote_base_url=cfg.stt.remote_base_url,
        remote_model=cfg.stt.remote_model,
        remote_timeout_seconds=cfg.stt.remote_timeout_seconds,
        remote_realtime_path=cfg.stt.remote_realtime_path,
        remote_realtime_chunk_bytes=cfg.stt.remote_realtime_chunk_bytes,
        condition_on_previous_text=cfg.stt.condition_on_previous_text,
        whisper_vad_filter=cfg.stt.whisper_vad_filter,
        temperature=cfg.stt.temperature,
        no_speech_threshold=cfg.stt.no_speech_threshold,
        log_prob_threshold=cfg.stt.log_prob_threshold,
        compression_ratio_threshold=cfg.stt.compression_ratio_threshold,
        segment_min_avg_logprob=cfg.stt.segment_min_avg_logprob,
        segment_max_no_speech_prob=cfg.stt.segment_max_no_speech_prob,
        repeated_text_window_seconds=cfg.stt.repeated_text_window_seconds,
        repeated_text_logprob_threshold=cfg.stt.repeated_text_logprob_threshold,
        hallucination_max_confidence=cfg.stt.hallucination_max_confidence,
        hallucination_exact_phrases=cfg.stt.hallucination_exact_phrases,
        hallucination_always_block_contains=cfg.stt.hallucination_always_block_contains,
    )
    ai = None
    if cfg.ai.enabled:
        ai = AIGateway(
            base_url=cfg.ai.base_url,
            model=cfg.ai.model,
            timeout_seconds=cfg.ai.timeout_seconds,
            max_retries=cfg.ai.max_retries,
            keep_alive=cfg.ai.keep_alive,
            api_format=cfg.ai.api_format,
            system_prompt=cfg.ai.system_prompt,
            enable_thinking=cfg.ai.enable_thinking,
        )
    vad = None
    if cfg.stt.vad_filter:
        vad = VADService(
            min_silence_duration_ms=cfg.vad.min_silence_duration_ms,
            speech_pad_ms=cfg.vad.speech_pad_ms,
        )

    ambient = None
    if cfg.ambient.enabled:
        ambient = AmbientSoundService(
            model_id=cfg.ambient.model_id,
            confidence_threshold=cfg.ambient.confidence_threshold,
            top_k=cfg.ambient.top_k,
            min_emit_interval_seconds=cfg.ambient.min_emit_interval_seconds,
        )

    tts = None
    if cfg.tts.enabled:
        tts = TTSService(
            base_url=cfg.tts.base_url,
            model=cfg.tts.model,
            voice=cfg.tts.voice,
            sample_rate=cfg.tts.sample_rate,
            timeout_seconds=cfg.tts.timeout_seconds,
            streaming=cfg.tts.streaming,
            chunk_size=cfg.tts.chunk_size,
        )

    return create_app(
        sqlite_path=cfg.db.sqlite_path,
        asr_service=asr,
        ai_gateway=ai,
        vad_service=vad,
        ambient_service=ambient,
        tts_service=tts,
        dialogue_log_path=cfg.logging.dialogue_log_path,
        ai_idle_flush_seconds=cfg.ai.idle_flush_seconds,
        ai_idle_flush_requires_sentence_end=cfg.ai.idle_flush_requires_sentence_end,
        ai_max_utterance_hold_seconds=cfg.ai.max_utterance_hold_seconds,
        min_segment_duration_seconds=cfg.stt.min_segment_duration_seconds,
        pre_roll_ms=cfg.stt.pre_roll_ms,
    )


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    uvicorn.run("src.javis_stt.server:build_default_app", factory=True, host="0.0.0.0", port=8765)


if __name__ == "__main__":
    main()
