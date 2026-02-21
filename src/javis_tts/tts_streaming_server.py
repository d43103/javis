"""
Standalone TTS streaming server using qwen_tts.Qwen3TTSModel (true streaming).
Runs on port 8031 on the 4090 server.

Deploy: rsync -a src/javis_tts/ d43103@192.168.219.106:~/Workspace/projects/javis/src/javis_tts/
"""
import logging
from typing import Any, Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("javis.tts_server")


class SpeechRequest(BaseModel):
    input: str
    model: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    voice: str = "test01"
    response_format: str = "pcm"
    sample_rate: int = 24000


class ElevenLabsRequest(BaseModel):
    text: str
    model_id: Optional[str] = None
    output_format: Optional[str] = None
    voice_settings: Optional[dict] = None
    seed: Optional[int] = None
    apply_text_normalization: Optional[str] = None
    language_code: Optional[str] = None


def _sample_rate_from_output_format(output_format: Optional[str], default: int = 24000) -> int:
    """Parse sample rate from ElevenLabs output_format string like 'pcm_24000'."""
    if not output_format:
        return default
    fmt = output_format.strip().lower()
    if fmt.startswith("pcm_"):
        try:
            return int(fmt[4:])
        except ValueError:
            pass
    return default


def _make_generator(app: FastAPI, text: str, sample_rate: int):
    def generate():
        try:
            for audio_chunk, _sr in app.state.model.stream_generate_voice_clone(
                text=text.strip(),
                ref_audio=app.state.ref_audio_path,
                ref_text=app.state.ref_text,
                emit_every_frames=4,
            ):
                # audio_chunk is float32 in [-1, 1]; convert to int16 PCM bytes
                pcm = (np.clip(audio_chunk, -1.0, 1.0) * 32767).astype(np.int16)
                yield pcm.tobytes()
        except Exception:
            logger.exception("tts_stream_error text=%s", text[:80])
            raise
    return generate


def create_tts_app(
    model: Any,
    ref_audio_path: str,
    ref_text: str,
) -> FastAPI:
    app = FastAPI()
    app.state.model = model
    app.state.ref_audio_path = ref_audio_path
    app.state.ref_text = ref_text

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.post("/v1/audio/speech")
    async def speech(request: SpeechRequest):
        if not request.input.strip():
            raise HTTPException(status_code=400, detail="empty_input")
        logger.info("tts_openai text_len=%d sample_rate=%d", len(request.input), request.sample_rate)
        generate = _make_generator(app, request.input, request.sample_rate)
        return StreamingResponse(generate(), media_type="application/octet-stream")

    @app.post("/v1/text-to-speech/{voice_id}/stream")
    async def elevenlabs_stream(voice_id: str, request: ElevenLabsRequest, raw: Request):
        """ElevenLabs-compatible streaming endpoint for OpenClaw and similar clients."""
        if not request.text.strip():
            raise HTTPException(status_code=400, detail="empty_input")
        output_format = request.output_format or raw.query_params.get("output_format")
        sample_rate = _sample_rate_from_output_format(output_format)
        logger.info(
            "tts_elevenlabs voice=%s text_len=%d output_format=%s sample_rate=%d",
            voice_id, len(request.text), output_format, sample_rate,
        )
        generate = _make_generator(app, request.text, sample_rate)
        return StreamingResponse(generate(), media_type="audio/pcm")

    return app


def build_default_app(
    model_name: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    ref_audio_path: str = (
        "/home/d43103/Workspace/projects/Qwen3-TTS-Openai-Fastapi"
        "/custom_voices/d43103voice01/reference.wav"
    ),
    ref_text_path: str = (
        "/home/d43103/Workspace/projects/Qwen3-TTS-Openai-Fastapi"
        "/custom_voices/d43103voice01/reference.txt"
    ),
) -> FastAPI:
    """Load model and return app. Called by uvicorn factory."""
    import torch
    from qwen_tts import Qwen3TTSModel

    logger.info("loading_tts_model model=%s", model_name)

    kwargs = {}
    if torch.cuda.is_available():
        kwargs["device_map"] = "cuda:0"
        kwargs["torch_dtype"] = torch.float16

    model = Qwen3TTSModel.from_pretrained(model_name, **kwargs)
    model.model.eval()

    ref_text = ""
    try:
        with open(ref_text_path, encoding="utf-8") as f:
            ref_text = f.read().strip()
    except FileNotFoundError:
        logger.warning("ref_text_not_found path=%s", ref_text_path)

    logger.info("tts_model_ready ref_audio=%s", ref_audio_path)
    return create_tts_app(
        model=model,
        ref_audio_path=ref_audio_path,
        ref_text=ref_text,
    )


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(
        "src.javis_tts.tts_streaming_server:build_default_app",
        factory=True,
        host="0.0.0.0",
        port=8031,
    )


if __name__ == "__main__":
    main()
