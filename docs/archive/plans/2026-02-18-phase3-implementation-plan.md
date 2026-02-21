# Phase 3: Real-time Voice Assistant Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform javis from a server-side STT+LLM+TTS system into a split-architecture real-time voice assistant where the 4090 server handles only STT+TTS and the local Mac handles LLM via Claude Code.

**Architecture:** Server runs Qwen3-ASR-1.7B (STT) + Qwen3-TTS-12Hz-1.7B-Base with streaming voice clone (TTS). Mac runs `voice_llm_bridge.py` that listens for transcripts over WebSocket, calls Claude API, and streams TTS audio back from server. Multi-turn conversation history kept in-process (deque) and persisted to SQLite via a new `ConversationEngine`.

**Tech Stack:** Python 3.11+, FastAPI, httpx, sounddevice (Mac), Anthropic SDK (Mac), dffdeeq/Qwen3-TTS-streaming fork (server), systemd user service + launchd plist for 24/7 operation.

**Test runner:** `pytest tests/ -v`

---

## Context: What You Need to Know

### Repository Layout

```
src/javis_stt/
  server.py          — FastAPI app, create_app() factory + build_default_app()
  ai_gateway.py      — AIGateway: single-turn LLM HTTP wrapper
  tts_service.py     — TTSService: synthesize() + synthesize_stream()
  config.py          — Pydantic config models (STTConfig, AIConfig, TTSConfig, …)
  asr_service.py     — ASRService: transcribe_segment()
  vad_service.py     — VADService stub (Silero, server version is richer)
  session_manager.py — SessionManager: next_segment_id()
  repository.py      — TranscriptRepository: SQLite persistence
  db.py              — SQLAlchemy engine helpers

tests/
  test_server_ws.py  — FastAPI TestClient WS tests
  test_tts_service.py
  test_ai_gateway.py
  test_config.py
  …

config/stt.yaml      — Runtime config (on server)
docs/plans/          — Design + plan docs
```

### Server State (192.168.219.106)

After Phase 3 setup, the server will run:
- `vllm-stt` container → port 8011 (Qwen3-ASR-1.7B)
- `javis-tts.service` systemd unit → port 8031 (Qwen3-TTS streaming fork, our custom FastAPI)
- `javis-stt.service` → port 8765 (this codebase)

### Key Design Constraints

- `create_app()` accepts `ai_gateway=None` — AI is now optional (it will be `None` in Phase 3 production)
- `TTSService` points to port 8031; we add a new streaming TTS server there
- `AIGateway` is single-turn; we replace its usage with a new `ConversationEngine` that keeps history
- Mac-side bridge is a standalone script, not part of the server codebase

---

## Task 1: ConversationEngine — multi-turn history wrapper

**Files:**
- Create: `src/javis_stt/conversation_engine.py`
- Create: `tests/test_conversation_engine.py`

**Step 1: Write the failing test**

```python
# tests/test_conversation_engine.py
from collections import deque
from src.javis_stt.conversation_engine import ConversationEngine
from src.javis_stt.ai_gateway import AIResult


class _FakeGateway:
    def __init__(self):
        self.calls = []

    def generate_with_history(self, session_id: str, text: str, history: list[dict]) -> AIResult:
        self.calls.append({"session_id": session_id, "text": text, "history": list(history)})
        return AIResult(text=f"응답: {text}", error=None)


def test_conversation_engine_sends_history():
    gw = _FakeGateway()
    engine = ConversationEngine(gateway=gw, max_turns=3)

    engine.turn(session_id="s1", text="첫 번째 질문")
    engine.turn(session_id="s1", text="두 번째 질문")

    assert len(gw.calls) == 2
    # second call must include first turn in history
    second_call = gw.calls[1]
    assert len(second_call["history"]) == 2  # user+assistant from first turn
    assert second_call["history"][0]["role"] == "user"
    assert second_call["history"][0]["content"] == "첫 번째 질문"
    assert second_call["history"][1]["role"] == "assistant"
    assert second_call["history"][1]["content"] == "응답: 첫 번째 질문"


def test_conversation_engine_respects_max_turns():
    gw = _FakeGateway()
    engine = ConversationEngine(gateway=gw, max_turns=2)

    for i in range(5):
        engine.turn(session_id="s1", text=f"질문 {i}")

    # history passed to last call must have at most max_turns * 2 messages
    last_call = gw.calls[-1]
    assert len(last_call["history"]) <= 4  # 2 turns * 2 messages each


def test_conversation_engine_isolates_sessions():
    gw = _FakeGateway()
    engine = ConversationEngine(gateway=gw, max_turns=3)

    engine.turn(session_id="alice", text="안녕")
    engine.turn(session_id="bob", text="안녕하세요")

    alice_history = gw.calls[1]["history"] if gw.calls[1]["session_id"] == "bob" else gw.calls[0]["history"]
    # bob's first turn should have empty history (no cross-session bleed)
    bob_call = next(c for c in gw.calls if c["session_id"] == "bob")
    assert len(bob_call["history"]) == 0


def test_conversation_engine_returns_error_on_failure():
    class _FailGateway:
        def generate_with_history(self, session_id, text, history):
            return AIResult(text="", error="timeout")

    engine = ConversationEngine(gateway=_FailGateway(), max_turns=3)
    result = engine.turn(session_id="s1", text="test")

    assert result.error == "timeout"
    # failed turn should not be added to history
    assert len(engine._histories["s1"]) == 0
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_conversation_engine.py -v
```

Expected: `ImportError: cannot import name 'ConversationEngine'`

**Step 3: Write minimal implementation**

```python
# src/javis_stt/conversation_engine.py
from collections import defaultdict, deque
from typing import Any, Protocol

from .ai_gateway import AIGateway, AIResult


class _GatewayProtocol(Protocol):
    def generate_with_history(self, session_id: str, text: str, history: list[dict]) -> AIResult: ...


class ConversationEngine:
    def __init__(self, gateway: Any, max_turns: int = 10):
        self._gateway = gateway
        self._max_turns = max_turns
        self._histories: dict[str, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_turns * 2)
        )

    def turn(self, session_id: str, text: str) -> AIResult:
        history = list(self._histories[session_id])
        result = self._gateway.generate_with_history(
            session_id=session_id,
            text=text,
            history=history,
        )
        if not result.error:
            self._histories[session_id].append({"role": "user", "content": text})
            self._histories[session_id].append({"role": "assistant", "content": result.text})
        return result

    def clear(self, session_id: str) -> None:
        self._histories.pop(session_id, None)
```

**Step 4: Add `generate_with_history` to AIGateway**

Modify `src/javis_stt/ai_gateway.py` — add one method after `generate()`:

```python
def generate_with_history(self, session_id: str, text: str, history: list[dict]) -> "AIResult":
    """OpenAI-format only: sends full conversation history."""
    messages: list[dict[str, str]] = []
    if self.system_prompt:
        messages.append({"role": "system", "content": self.system_prompt})
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    payload: dict[str, Any] = {
        "model": self.model,
        "messages": messages,
        "stream": False,
    }

    last_error: str | None = None
    for _ in range(self.max_retries + 1):
        try:
            raw = self.requester(payload)
            response_text = self._extract_openai_response(raw)
            return AIResult(text=response_text, error=None)
        except Exception as exc:
            last_error = str(exc)

    return AIResult(text="", error=last_error or "unknown_error")
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/test_conversation_engine.py tests/test_ai_gateway.py -v
```

Expected: all PASS

**Step 6: Commit**

```bash
git add src/javis_stt/conversation_engine.py src/javis_stt/ai_gateway.py tests/test_conversation_engine.py
git commit -m "feat: add ConversationEngine with multi-turn history and generate_with_history"
```

---

## Task 2: Config — disable AI section, add TTS voice clone fields

**Files:**
- Modify: `src/javis_stt/config.py`
- Modify: `config/stt.yaml` (on server)
- Test: `tests/test_config.py`

**Step 1: Read the existing config test**

```bash
cat tests/test_config.py
```

Check what's already tested before adding new tests.

**Step 2: Write failing tests for new config fields**

Add to `tests/test_config.py`:

```python
def test_tts_config_has_voice_clone_fields():
    from src.javis_stt.config import TTSConfig
    cfg = TTSConfig(
        enabled=True,
        model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        voice="test01",
        voice_clone_ref_audio="recordings/test-01.wav",
        voice_clone_ref_text="처리하고 합니다.",
    )
    assert cfg.voice_clone_ref_audio == "recordings/test-01.wav"
    assert cfg.voice_clone_ref_text == "처리하고 합니다."


def test_ai_config_disabled_by_default_yaml(tmp_path):
    from src.javis_stt.config import load_config
    yaml_content = """
ai:
  enabled: false
tts:
  enabled: true
  voice_clone_ref_audio: recordings/test-01.wav
  voice_clone_ref_text: "처리하고 합니다."
"""
    p = tmp_path / "stt.yaml"
    p.write_text(yaml_content)
    cfg = load_config(str(p))
    assert cfg.ai.enabled is False
    assert cfg.tts.voice_clone_ref_audio == "recordings/test-01.wav"
```

**Step 3: Run to verify failure**

```bash
pytest tests/test_config.py::test_tts_config_has_voice_clone_fields -v
```

Expected: `ValidationError` or `AttributeError`

**Step 4: Add fields to TTSConfig**

In `src/javis_stt/config.py`, add to `TTSConfig`:

```python
voice_clone_ref_audio: str = ""   # path to reference WAV (16kHz mono)
voice_clone_ref_text: str = ""    # transcript of reference audio
```

**Step 5: Run tests**

```bash
pytest tests/test_config.py -v
```

Expected: all PASS

**Step 6: Commit**

```bash
git add src/javis_stt/config.py tests/test_config.py
git commit -m "feat: add voice clone config fields to TTSConfig"
```

---

## Task 3: TTSService — voice clone streaming via WebSocket endpoint

**Files:**
- Modify: `src/javis_stt/tts_service.py`
- Modify: `tests/test_tts_service.py`

The new TTS server (Task 4) will expose `/ws/tts-stream`. The TTSService needs to connect to it via WebSocket and stream PCM back.

**Step 1: Write failing test**

Add to `tests/test_tts_service.py`:

```python
def test_tts_ws_stream_requester_yields_chunks():
    """TTSService.synthesize_stream_ws() calls ws_stream_requester and yields chunks."""
    from src.javis_stt.tts_service import TTSService

    received = []

    def fake_ws_requester(text: str):
        # simulates WebSocket yielding PCM chunks
        yield b"\x00\x01" * 400
        yield b"\x00\x02" * 400

    svc = TTSService(
        base_url="http://127.0.0.1:8031",
        model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        voice="test01",
        ws_stream_requester=fake_ws_requester,
    )
    chunks = list(svc.synthesize_stream_ws("안녕하세요"))
    assert len(chunks) == 2
    assert chunks[0] == b"\x00\x01" * 400


def test_tts_ws_stream_falls_back_to_http_if_not_configured():
    """When ws_stream_requester is None, synthesize_stream_ws delegates to synthesize_stream."""
    from src.javis_stt.tts_service import TTSService

    http_chunks = [b"\xAA\xBB" * 100]

    def fake_http_stream(payload):
        yield from http_chunks

    svc = TTSService(
        base_url="http://127.0.0.1:8031",
        model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        stream_requester=fake_http_stream,
        ws_stream_requester=None,
    )
    chunks = list(svc.synthesize_stream_ws("test"))
    assert chunks == http_chunks
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_tts_service.py::test_tts_ws_stream_requester_yields_chunks -v
```

Expected: `TypeError` — `__init__() got unexpected keyword argument 'ws_stream_requester'`

**Step 3: Add ws_stream support to TTSService**

In `src/javis_stt/tts_service.py`:

```python
# add ws_stream_requester parameter to __init__
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
    ws_stream_requester=None,   # NEW: callable(text) -> Iterator[bytes]
):
    # ... existing assignments ...
    self.ws_stream_requester = ws_stream_requester

# add new method
def synthesize_stream_ws(self, text: str) -> Iterator[bytes]:
    """Stream PCM via WebSocket TTS endpoint (preferred) or fall back to HTTP."""
    if not text or not text.strip():
        return

    if self.ws_stream_requester is not None:
        try:
            yield from self.ws_stream_requester(text.strip())
        except Exception:
            logger.exception("tts_ws_stream_failed text=%s", text[:80])
        return

    # fallback: HTTP streaming
    yield from self.synthesize_stream(text)
```

Also update `_build_payload` to include `voice_clone_ref_audio` when set:

```python
def _build_payload(self, text: str, ref_audio: str = "", ref_text: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": self.model,
        "input": text,
        "voice": self.voice,
        "response_format": "pcm",
        "sample_rate": self.sample_rate,
    }
    if ref_audio:
        payload["voice_clone_ref_audio"] = ref_audio
        payload["voice_clone_ref_text"] = ref_text
    return payload
```

**Step 4: Run tests**

```bash
pytest tests/test_tts_service.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/javis_stt/tts_service.py tests/test_tts_service.py
git commit -m "feat: add ws_stream_requester to TTSService for streaming TTS via WebSocket"
```

---

## Task 4: Server — add `/v1/voice/turn` HTTP endpoint

**Files:**
- Modify: `src/javis_stt/server.py`
- Modify: `tests/test_server_ws.py`

This endpoint receives `{session_id, text, response_text}` from the Mac bridge and streams back PCM audio using TTS. It decouples the Mac LLM from the server entirely.

**Step 1: Write failing test**

Add to `tests/test_server_ws.py`:

```python
def test_voice_turn_endpoint_streams_tts(tmp_path):
    """POST /v1/voice/turn with response_text streams PCM audio back."""
    app = create_app(
        sqlite_path=str(tmp_path / "stt.db"),
        asr_service=_FakeASR(),
        tts_service=_FakeTTS(),
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/voice/turn",
        json={"session_id": "mac-1", "text": "오늘 날씨 어때?", "response_text": "서울 날씨는 맑습니다."},
    ) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes())
        assert len(body) > 0


def test_voice_turn_endpoint_returns_404_without_tts(tmp_path):
    """POST /v1/voice/turn returns 503 when TTS not configured."""
    app = create_app(
        sqlite_path=str(tmp_path / "stt.db"),
        asr_service=_FakeASR(),
        tts_service=None,
    )
    client = TestClient(app)

    resp = client.post(
        "/v1/voice/turn",
        json={"session_id": "mac-1", "text": "test", "response_text": "test response"},
    )
    assert resp.status_code == 503
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_server_ws.py::test_voice_turn_endpoint_streams_tts -v
```

Expected: FAIL — `404 Not Found`

**Step 3: Add the endpoint to server.py**

In `create_app()`, after the `/config/hallucinations` endpoint, add:

```python
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

class VoiceTurnRequest(BaseModel):
    session_id: str
    text: str          # user's transcribed speech
    response_text: str # AI response text from Mac

@app.post("/v1/voice/turn")
async def voice_turn(request: VoiceTurnRequest):
    if app.state.tts_service is None:
        raise HTTPException(status_code=503, detail="tts_not_enabled")

    def generate_audio():
        yield from app.state.tts_service.synthesize_stream(request.response_text)

    return StreamingResponse(
        generate_audio(),
        media_type="application/octet-stream",
        headers={
            "X-Session-Id": request.session_id,
            "X-Content-Type": "audio/pcm",
        },
    )
```

Note: `VoiceTurnRequest` and `StreamingResponse` import must be at the top of the file. Add to existing imports:

```python
from fastapi.responses import StreamingResponse
```

**Step 4: Run tests**

```bash
pytest tests/test_server_ws.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/javis_stt/server.py tests/test_server_ws.py
git commit -m "feat: add POST /v1/voice/turn endpoint for Mac-bridge TTS streaming"
```

---

## Task 5: TTS streaming server script (server-side, deployed separately)

**Files:**
- Create: `src/javis_tts/tts_streaming_server.py`
- Create: `src/javis_tts/__init__.py`
- Create: `tests/test_tts_streaming_server.py`

This is a standalone FastAPI app that runs on port 8031 on the server. It wraps the dffdeeq streaming fork.

> **Context:** The dffdeeq/Qwen3-TTS-streaming fork provides `stream_generate_voice_clone(model, tokenizer, ref_audio_path, ref_text, text, emit_every_frames=4)` which yields PCM frames every ~330ms. This runs on Python with torch + transformers on the 4090. This file is part of the local codebase but **deployed to the server** via rsync.

**Step 1: Create `__init__.py`**

```bash
touch src/javis_tts/__init__.py
```

**Step 2: Write failing test**

```python
# tests/test_tts_streaming_server.py
import importlib
import types
import sys


def _make_fake_qwen3_streaming():
    """Inject a fake qwen3_streaming module into sys.modules."""
    mod = types.ModuleType("qwen3_streaming")

    def fake_stream_generate_voice_clone(model, tokenizer, ref_audio_path, ref_text, text, emit_every_frames=4):
        # yields 3 fake PCM chunks
        yield b"\x00\x01" * 480  # ~30ms at 16kHz
        yield b"\x00\x02" * 480
        yield b"\x00\x03" * 480

    mod.stream_generate_voice_clone = fake_stream_generate_voice_clone

    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)

    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoModelForCausalLM = None
    fake_transformers.AutoTokenizer = None

    sys.modules["qwen3_streaming"] = mod
    sys.modules["torch"] = fake_torch
    sys.modules["transformers"] = fake_transformers
    return mod


def test_tts_streaming_server_health():
    _make_fake_qwen3_streaming()

    from fastapi.testclient import TestClient
    tts_mod = importlib.import_module("src.javis_tts.tts_streaming_server")
    create_tts_app = tts_mod.create_tts_app

    fake_model = object()
    fake_tokenizer = object()
    app = create_tts_app(
        model=fake_model,
        tokenizer=fake_tokenizer,
        ref_audio_path="fake.wav",
        ref_text="테스트",
    )
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_tts_streaming_server_speech_endpoint():
    _make_fake_qwen3_streaming()

    from fastapi.testclient import TestClient
    import importlib
    tts_mod = importlib.import_module("src.javis_tts.tts_streaming_server")
    create_tts_app = tts_mod.create_tts_app

    fake_model = object()
    fake_tokenizer = object()
    app = create_tts_app(
        model=fake_model,
        tokenizer=fake_tokenizer,
        ref_audio_path="fake.wav",
        ref_text="테스트",
    )
    client = TestClient(app)

    with client.stream("POST", "/v1/audio/speech", json={"input": "안녕하세요", "response_format": "pcm"}) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes())
        assert len(body) > 0
```

**Step 3: Run to verify failure**

```bash
pytest tests/test_tts_streaming_server.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.javis_tts.tts_streaming_server'`

**Step 4: Write minimal server**

```python
# src/javis_tts/tts_streaming_server.py
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
```

**Step 5: Run tests**

```bash
pytest tests/test_tts_streaming_server.py -v
```

Expected: all PASS

**Step 6: Commit**

```bash
git add src/javis_tts/__init__.py src/javis_tts/tts_streaming_server.py tests/test_tts_streaming_server.py
git commit -m "feat: add Qwen3-TTS streaming server (port 8031) with voice clone support"
```

---

## Task 6: Mac-side bridge script (`voice_llm_bridge.py`)

**Files:**
- Create: `src/voice_llm_bridge.py`
- Create: `tests/test_voice_llm_bridge.py`

This script runs on the local Mac. It:
1. Connects to the server WebSocket (`/ws/stt?session_id=mac-1`)
2. On each `{type: "final"}` event: calls Claude API with conversation history
3. POSTs the AI response to `http://server:8765/v1/voice/turn` (streaming)
4. Plays the returned PCM audio via `sounddevice`

> **Context:** `ANTHROPIC_API_KEY` must be set. The script uses `anthropic` Python SDK. `sounddevice` plays raw PCM at 16kHz. Keep a deque of 10 turns for context.

**Step 1: Write failing test**

```python
# tests/test_voice_llm_bridge.py
"""
Tests for voice_llm_bridge — all network calls are replaced with fakes.
sounddevice import is mocked so tests run without audio hardware.
"""
import sys
import types

# stub sounddevice before importing the bridge
sd_stub = types.ModuleType("sounddevice")
sd_stub.play = lambda data, samplerate, blocking: None
sd_stub.wait = lambda: None
sys.modules["sounddevice"] = sd_stub


def _make_fake_anthropic(response_text="안녕하세요"):
    anthropic_stub = types.ModuleType("anthropic")

    class FakeMessage:
        content = [types.SimpleNamespace(text=response_text)]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeMessage()

    class FakeClient:
        messages = FakeMessages()

    anthropic_stub.Anthropic = FakeClient
    sys.modules["anthropic"] = anthropic_stub
    return anthropic_stub


def test_bridge_processes_final_event():
    _make_fake_anthropic("서울 날씨는 맑습니다.")

    # import after stubs are in place
    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    tts_calls = []

    class FakeBridge(bridge.VoiceBridge):
        def _post_tts_and_play(self, session_id, response_text):
            tts_calls.append({"session_id": session_id, "text": response_text})

    b = FakeBridge(
        server_ws_url="ws://fake:8765/ws/stt",
        server_http_url="http://fake:8765",
        session_id="test",
        claude_model="claude-haiku-4-5-20251001",
    )
    result = b._handle_final_event(session_id="test", text="오늘 날씨 어때?")

    assert result == "서울 날씨는 맑습니다."
    assert len(tts_calls) == 1
    assert tts_calls[0]["text"] == "서울 날씨는 맑습니다."


def test_bridge_maintains_conversation_history():
    _make_fake_anthropic("두 번째 응답")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    class FakeBridge(bridge.VoiceBridge):
        def _post_tts_and_play(self, session_id, response_text):
            pass

    b = FakeBridge(
        server_ws_url="ws://fake:8765/ws/stt",
        server_http_url="http://fake:8765",
        session_id="test",
        claude_model="claude-haiku-4-5-20251001",
    )
    b._handle_final_event("test", "첫 번째")
    b._handle_final_event("test", "두 번째")

    # history should have 4 messages (2 turns * user+assistant each)
    assert len(b._history) == 4


def test_bridge_trims_history_at_max_turns():
    _make_fake_anthropic("응답")

    import importlib
    bridge = importlib.import_module("src.voice_llm_bridge")

    class FakeBridge(bridge.VoiceBridge):
        def _post_tts_and_play(self, session_id, response_text):
            pass

    b = FakeBridge(
        server_ws_url="ws://fake:8765/ws/stt",
        server_http_url="http://fake:8765",
        session_id="test",
        claude_model="claude-haiku-4-5-20251001",
        max_turns=3,
    )
    for i in range(10):
        b._handle_final_event("test", f"질문 {i}")

    # max_turns=3 → max 6 messages in history
    assert len(b._history) <= 6
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_voice_llm_bridge.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.voice_llm_bridge'`

**Step 3: Write the bridge script**

```python
# src/voice_llm_bridge.py
"""
Mac-side voice bridge: connects to server STT WebSocket, calls Claude, streams TTS back.

Usage:
  python -m src.voice_llm_bridge \
    --server ws://192.168.219.106:8765 \
    --session mac-1 \
    --model claude-haiku-4-5-20251001

Requirements (Mac):
  pip install anthropic websocket-client httpx sounddevice numpy
  export ANTHROPIC_API_KEY=sk-...
"""
import argparse
import json
import logging
import numpy as np
import sys
from collections import deque

logger = logging.getLogger("javis.bridge")

SYSTEM_PROMPT = """당신은 친근하고 간결한 한국어 개인 비서입니다.
음성 대화에 최적화되어 있으므로 답변을 1-2 문장으로 짧게 유지하세요.
마크다운, 목록, 코드 블록을 사용하지 마세요.
자연스러운 구어체 한국어로 답변하세요."""


class VoiceBridge:
    def __init__(
        self,
        server_ws_url: str,
        server_http_url: str,
        session_id: str,
        claude_model: str = "claude-haiku-4-5-20251001",
        max_turns: int = 10,
        sample_rate: int = 16000,
    ):
        self.server_ws_url = server_ws_url
        self.server_http_url = server_http_url.rstrip("/")
        self.session_id = session_id
        self.claude_model = claude_model
        self.sample_rate = sample_rate
        self._history: deque[dict[str, str]] = deque(maxlen=max_turns * 2)

        import anthropic
        self._claude = anthropic.Anthropic()

    def _call_claude(self, text: str) -> str:
        messages = [*self._history, {"role": "user", "content": text}]
        response = self._claude.messages.create(
            model=self.claude_model,
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text

    def _post_tts_and_play(self, session_id: str, response_text: str) -> None:
        import httpx
        import sounddevice as sd

        pcm_chunks: list[bytes] = []
        with httpx.Client(timeout=30.0) as client:
            with client.stream(
                "POST",
                f"{self.server_http_url}/v1/voice/turn",
                json={
                    "session_id": session_id,
                    "text": "",
                    "response_text": response_text,
                },
            ) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(chunk_size=4096):
                    if chunk:
                        pcm_chunks.append(chunk)

        if pcm_chunks:
            raw = b"".join(pcm_chunks)
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(audio, samplerate=self.sample_rate, blocking=True)

    def _handle_final_event(self, session_id: str, text: str) -> str:
        response_text = self._call_claude(text)
        self._history.append({"role": "user", "content": text})
        self._history.append({"role": "assistant", "content": response_text})
        self._post_tts_and_play(session_id, response_text)
        return response_text

    def run(self) -> None:
        import websocket  # websocket-client

        logger.info("bridge_connecting url=%s", self.server_ws_url)

        def on_message(ws, message):
            try:
                event = json.loads(message)
            except Exception:
                return

            if event.get("type") == "final":
                text = event.get("text", "").strip()
                if not text:
                    return
                session_id = event.get("session_id", self.session_id)
                logger.info("bridge_final session=%s text=%s", session_id, text[:100])
                try:
                    response = self._handle_final_event(session_id, text)
                    logger.info("bridge_response text=%s", response[:100])
                except Exception:
                    logger.exception("bridge_turn_error session=%s", session_id)

        def on_error(ws, error):
            logger.error("bridge_ws_error error=%s", error)

        def on_close(ws, close_status_code, close_msg):
            logger.info("bridge_ws_closed status=%s", close_status_code)

        def on_open(ws):
            logger.info("bridge_ws_connected")

        ws = websocket.WebSocketApp(
            f"{self.server_ws_url}?session_id={self.session_id}",
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        ws.run_forever(reconnect=5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    parser = argparse.ArgumentParser(description="Javis voice bridge — Mac LLM side")
    parser.add_argument("--server", default="ws://192.168.219.106:8765", help="Server WebSocket base URL")
    parser.add_argument("--http", default="", help="Server HTTP URL (default: ws→http auto-convert)")
    parser.add_argument("--session", default="mac-1", help="Session ID")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Claude model ID")
    parser.add_argument("--max-turns", type=int, default=10, help="Conversation history depth")
    args = parser.parse_args()

    http_url = args.http or args.server.replace("ws://", "http://").replace("wss://", "https://")
    # strip path from http_url if ws url included /ws/stt
    from urllib.parse import urlparse
    parsed = urlparse(http_url)
    http_base = f"{parsed.scheme}://{parsed.netloc}"

    bridge = VoiceBridge(
        server_ws_url=args.server,
        server_http_url=http_base,
        session_id=args.session,
        claude_model=args.model,
        max_turns=args.max_turns,
    )
    bridge.run()


if __name__ == "__main__":
    main()
```

**Step 4: Run tests**

```bash
pytest tests/test_voice_llm_bridge.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/voice_llm_bridge.py tests/test_voice_llm_bridge.py
git commit -m "feat: add voice_llm_bridge.py — Mac-side WebSocket listener + Claude LLM + TTS playback"
```

---

## Task 7: Full test suite passes

**Step 1: Run all tests**

```bash
pytest tests/ -v
```

Expected: all PASS (or only pre-existing failures unrelated to this feature)

**Step 2: If any failures, fix them**

Common issues:
- Import order in server.py (add `StreamingResponse` import at top, not inside function)
- `VoiceTurnRequest` must be defined before `create_app()` or inside it — check for `NameError`
- TTSService `_build_payload` signature change may break existing `test_tts_payload_structure` — update test to pass `ref_audio=""` or keep default params

**Step 3: Commit any fixes**

```bash
git add -p
git commit -m "fix: resolve test failures from Phase 3 changes"
```

---

## Task 8: Server deployment — stop old containers, upgrade STT, deploy TTS server

> **This task runs on the server (192.168.219.106) via SSH. Not tested locally.**
> These are shell commands, not code. Run them in order.

**Step 1: Stop and remove old containers**

```bash
ssh user@192.168.219.106
docker stop javis-vllm-llm 2>/dev/null; docker rm javis-vllm-llm 2>/dev/null
docker stop qwen3-tts-api 2>/dev/null; docker rm qwen3-tts-api 2>/dev/null
# verify
docker ps
```

Expected: only `vllm-stt` (port 8011) and `javis-stt` (port 8765) containers remain.

**Step 2: Update docker-compose to use Qwen3-ASR-1.7B**

Edit `docker-compose.yaml` on the server. Change the `vllm-stt` service model:

```yaml
# Change from:
--model Qwen/Qwen3-ASR-0.6B
# To:
--model Qwen/Qwen3-ASR-1.7B
# And adjust:
--gpu-memory-utilization 0.25   # was 0.15, now needs ~20% for 1.7B
```

Also remove the `vllm-tts` and `vllm-llm` service blocks entirely.

**Step 3: Restart STT container**

```bash
docker-compose up -d vllm-stt
sleep 30
curl http://localhost:8011/health
```

Expected: `{"status":"ok"}` (may take 60s for model download on first run)

**Step 4: Install dffdeeq streaming fork**

```bash
cd /opt/javis
git clone https://github.com/dffdeeq/Qwen3-TTS-streaming qwen3-tts-streaming
cd qwen3-tts-streaming
pip install -e .
```

**Step 5: Sync local TTS server code to server**

From your Mac:

```bash
rsync -av src/javis_tts/ user@192.168.219.106:/opt/javis/src/javis_tts/
```

**Step 6: Create systemd service for TTS server**

On the server, create `/etc/systemd/system/javis-tts.service`:

```ini
[Unit]
Description=Javis TTS Streaming Server
After=network.target

[Service]
User=youruser
WorkingDirectory=/opt/javis
Environment=PYTHONPATH=/opt/javis
ExecStart=/usr/bin/python3 -m src.javis_tts.tts_streaming_server
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable javis-tts
sudo systemctl start javis-tts
systemctl status javis-tts
```

**Step 7: Test TTS first-chunk latency**

```bash
time curl -s -X POST http://localhost:8031/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"input":"안녕하세요, 오늘 날씨가 좋네요."}' \
  --output /tmp/test.pcm 2>&1
ls -la /tmp/test.pcm
```

Expected: first chunk within 500ms, file size > 0.

**Step 8: Update server config to disable AI, use streaming TTS**

Edit `config/stt.yaml` on the server:

```yaml
ai:
  enabled: false   # LLM moved to Mac

tts:
  enabled: true
  base_url: http://127.0.0.1:8031
  model: Qwen/Qwen3-TTS-12Hz-1.7B-Base
  voice: test01
  voice_clone_ref_audio: recordings/test-01.wav
  voice_clone_ref_text: "처리하고 합니다. 지금 잘 녹음이 되고 있는지 모르겠네요..."
  sample_rate: 16000
  streaming: true
```

Restart javis-stt:

```bash
sudo systemctl restart javis-stt
curl http://localhost:8765/healthz
```

---

## Task 9: Mac-side launchd plist for 24/7 operation

**Files:**
- Create: `deploy/com.javis.voice-bridge.plist`

This is a macOS launchd plist that keeps `voice_llm_bridge.py` running on the Mac at all times.

**Step 1: Write the plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.javis.voice-bridge</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>-m</string>
        <string>src.voice_llm_bridge</string>
        <string>--server</string>
        <string>ws://192.168.219.106:8765/ws/stt</string>
        <string>--session</string>
        <string>mac-1</string>
        <string>--model</string>
        <string>claude-haiku-4-5-20251001</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/YOURUSERNAME/Workspace/projects/javis</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_API_KEY</key>
        <string>YOUR_API_KEY_HERE</string>
        <key>PYTHONPATH</key>
        <string>/Users/YOURUSERNAME/Workspace/projects/javis</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/tmp/javis-bridge.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/javis-bridge-error.log</string>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
```

> **Note:** Replace `YOURUSERNAME` and `YOUR_API_KEY_HERE` before loading. Storing API key in a plist is acceptable for personal use, but for shared machines use Keychain integration instead.

**Step 2: Install (manual, not automated)**

```bash
# Edit deploy/com.javis.voice-bridge.plist with correct paths first
cp deploy/com.javis.voice-bridge.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.javis.voice-bridge.plist
launchctl start com.javis.voice-bridge

# verify
launchctl list | grep javis
tail -f /tmp/javis-bridge.log
```

**Step 3: Commit the template**

```bash
git add deploy/com.javis.voice-bridge.plist
git commit -m "chore: add launchd plist template for Mac voice bridge (24/7 operation)"
```

---

## Task 10: End-to-end integration test (manual)

> This is a manual verification step with no automated test — network hardware required.

**Step 1: Start everything**

On the server, verify services are running:
```bash
docker ps              # vllm-stt (8011)
systemctl status javis-tts    # (8031)
systemctl status javis-stt    # (8765)
```

On the Mac:
```bash
python -m src.voice_llm_bridge \
  --server ws://192.168.219.106:8765/ws/stt \
  --session mac-test \
  --model claude-haiku-4-5-20251001
```

**Step 2: Speak into the microphone**

Say: "오늘 날씨 어때?" (in Korean)

**Expected log sequence:**
```
bridge_final session=mac-test text=오늘 날씨 어때?
bridge_response text=오늘 서울...
```

**Step 3: Verify latency**

Measure time from end of utterance to first audio output. Target: < 1.5s.

If latency > 1.5s, check:
- STT RTF: `curl http://192.168.219.106:8011/v1/models` — should be 1.7B
- TTS first chunk: use the curl test from Task 8 Step 7
- Claude API latency: add `time.time()` logging around `_call_claude()`

**Step 4: Commit any fixes**

```bash
git add -p
git commit -m "fix: [specific fix from integration test]"
```

---

## Completion Checklist

Before declaring Phase 3 complete:

- [ ] `pytest tests/ -v` — all pass
- [ ] Server: only STT (8011) + TTS (8031) + FastAPI (8765) running
- [ ] Server VRAM: < 12GB used (verify with `nvidia-smi`)
- [ ] TTS first chunk latency < 500ms (server local test)
- [ ] End-to-end latency < 1.5s (measured from speech end to audio start)
- [ ] Voice sounds like reference recording (subjective test)
- [ ] `voice_llm_bridge.py` reconnects automatically after server restart
- [ ] launchd plist loads at Mac login
- [ ] Conversation history maintained across turns (test with follow-up questions)

---

## Port Reference

| Service | Port | Host |
|---------|------|------|
| STT vLLM (Qwen3-ASR-1.7B) | 8011 | server |
| TTS streaming server (javis-tts.service) | 8031 | server |
| FastAPI main app (javis-stt.service) | 8765 | server |
| voice_llm_bridge WebSocket client | — | Mac (outbound) |
| Claude API | 443 | cloud (outbound from Mac) |

---

## Troubleshooting

**TTS server won't start:**
```bash
python3 -c "import qwen3_streaming; print('ok')"
# if fails: cd qwen3-tts-streaming && pip install -e .
```

**Bridge can't connect to server:**
```bash
curl http://192.168.219.106:8765/healthz
# should return {"status":"ok"}
```

**No audio output on Mac:**
```bash
python3 -c "import sounddevice; print(sounddevice.query_devices())"
# check default output device
```

**Claude API timeout:**
- Check `ANTHROPIC_API_KEY` is set
- Reduce `max_tokens` from 256 to 128 for faster responses
- Switch model to `claude-haiku-4-5-20251001` if using a larger model
