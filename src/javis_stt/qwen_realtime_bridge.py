import argparse
import base64
import io
import json
import logging
import uuid
import wave

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect


logger = logging.getLogger("javis.qwen_realtime_bridge")


def _safe_delta(previous: str, current: str) -> str:
    if current.startswith(previous):
        return current[len(previous) :]
    return current


def _pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buffer.getvalue()


async def _transcribe_http(
    base_url: str,
    model: str,
    timeout_seconds: float,
    pcm16: bytes,
    language: str,
) -> str:
    wav_bytes = _pcm16_to_wav_bytes(pcm16)
    data: dict[str, str] = {"model": model}
    if language:
        data["language"] = language
    files = {"file": ("segment.wav", wav_bytes, "audio/wav")}

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(f"{base_url.rstrip('/')}/v1/audio/transcriptions", data=data, files=files)
        response.raise_for_status()
        body = response.json()
        return str(body.get("text", "") or "").strip()


def create_app(
    upstream_base_url: str,
    default_model: str,
    timeout_seconds: float,
    preview_min_bytes: int,
    language: str,
) -> FastAPI:
    app = FastAPI()
    default_language = language

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/audio/transcriptions")
    async def proxy_audio_transcription(
        file: UploadFile = File(...),
        model: str = Form(""),
        language: str = Form(""),
    ) -> dict[str, object]:
        payload_model = model.strip() or default_model
        payload_language = language.strip() or default_language
        file_bytes = await file.read()
        files = {
            "file": (
                file.filename or "segment.wav",
                file_bytes,
                file.content_type or "audio/wav",
            )
        }
        data: dict[str, str] = {"model": payload_model}
        if payload_language:
            data["language"] = payload_language

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{upstream_base_url.rstrip('/')}/v1/audio/transcriptions",
                data=data,
                files=files,
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        return response.json()

    @app.websocket("/v1/realtime")
    async def realtime(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"type": "session.created", "id": f"ws-{uuid.uuid4()}"})

        model = default_model
        session_language = default_language
        audio_buffer = bytearray()
        previous_text = ""
        last_preview_size = 0

        try:
            while True:
                raw = await websocket.receive_text()
                event = json.loads(raw)
                event_type = event.get("type")

                if event_type == "session.update":
                    event_model = str(event.get("model", "") or "").strip()
                    if event_model:
                        model = event_model
                    event_language = str(event.get("language", "") or "").strip()
                    if event_language:
                        session_language = event_language
                    continue

                if event_type == "input_audio_buffer.append":
                    audio_b64 = str(event.get("audio", "") or "")
                    if not audio_b64:
                        await websocket.send_json({"type": "error", "error": "missing_audio"})
                        continue

                    chunk = base64.b64decode(audio_b64)
                    audio_buffer.extend(chunk)

                    if len(audio_buffer) - last_preview_size >= preview_min_bytes:
                        preview_text = await _transcribe_http(
                            base_url=upstream_base_url,
                            model=model,
                            timeout_seconds=timeout_seconds,
                            pcm16=bytes(audio_buffer),
                            language=session_language,
                        )
                        delta = _safe_delta(previous_text, preview_text)
                        previous_text = preview_text
                        last_preview_size = len(audio_buffer)
                        if delta:
                            await websocket.send_json({"type": "transcription.delta", "delta": delta})
                    continue

                if event_type == "input_audio_buffer.commit":
                    if not bool(event.get("final", False)):
                        continue
                    final_text = await _transcribe_http(
                        base_url=upstream_base_url,
                        model=model,
                        timeout_seconds=timeout_seconds,
                        pcm16=bytes(audio_buffer),
                        language=session_language,
                    )
                    delta = _safe_delta(previous_text, final_text)
                    if delta:
                        await websocket.send_json({"type": "transcription.delta", "delta": delta})
                    await websocket.send_json(
                        {
                            "type": "transcription.done",
                            "text": final_text,
                            "usage": {
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "total_tokens": 0,
                            },
                        }
                    )
                    continue

                await websocket.send_json({"type": "error", "error": f"unknown_event:{event_type}"})
        except WebSocketDisconnect:
            return
        except Exception as exc:
            logger.exception("realtime_bridge_error")
            await websocket.send_json({"type": "error", "error": str(exc)})

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Qwen3-ASR realtime websocket bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8021)
    parser.add_argument("--upstream-base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--preview-min-bytes", type=int, default=32000)
    parser.add_argument("--language", default="ko")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    app = create_app(
        upstream_base_url=args.upstream_base_url,
        default_model=args.model,
        timeout_seconds=args.timeout_seconds,
        preview_min_bytes=args.preview_min_bytes,
        language=args.language,
    )
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
