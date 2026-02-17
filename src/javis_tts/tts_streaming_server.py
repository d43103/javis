"""
Standalone TTS streaming server using dffdeeq/Qwen3-TTS-streaming fork.
Runs on port 8031 on the 4090 server.

Deploy: rsync -a src/javis_tts/ server:/opt/javis/src/javis_tts/
"""
import importlib
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("javis.tts_server")


class SpeechRequest(BaseModel):
    input: str
    model: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    voice: str = "test01"
    response_format: str = "pcm"
    sample_rate: int = 16000


def create_tts_app(
    model: Any,
    tokenizer: Any,
    ref_audio_path: str,
    ref_text: str,
) -> FastAPI:
    app = FastAPI()
    app.state.model = model
    app.state.tokenizer = tokenizer
    app.state.ref_audio_path = ref_audio_path
    app.state.ref_text = ref_text

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.post("/v1/audio/speech")
    async def speech(request: SpeechRequest):
        if not request.input.strip():
            return {"error": "empty_input"}, 400

        def generate():
            try:
                qwen3_streaming = importlib.import_module("qwen3_streaming")
                for chunk in qwen3_streaming.stream_generate_voice_clone(
                    model=app.state.model,
                    tokenizer=app.state.tokenizer,
                    ref_audio_path=app.state.ref_audio_path,
                    ref_text=app.state.ref_text,
                    text=request.input.strip(),
                    emit_every_frames=4,
                ):
                    yield chunk
            except Exception:
                logger.exception("tts_stream_error text=%s", request.input[:80])

        return StreamingResponse(generate(), media_type="application/octet-stream")

    return app


def build_default_app(
    model_name: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    ref_audio_path: str = "recordings/test-01.wav",
    ref_text_path: str = "recordings/reference.txt",
) -> FastAPI:
    """Load model and return app. Called by uvicorn factory."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    logger.info("loading_tts_model model=%s", model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=torch.float16)
    if torch.cuda.is_available():
        model = model.cuda()
    model.eval()

    ref_text = ""
    try:
        with open(ref_text_path, encoding="utf-8") as f:
            ref_text = f.read().strip()
    except FileNotFoundError:
        logger.warning("ref_text_not_found path=%s", ref_text_path)

    logger.info("tts_model_ready ref_audio=%s", ref_audio_path)
    return create_tts_app(
        model=model,
        tokenizer=tokenizer,
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
