# Architecture

## Deployment Boundary

- Primary inference host: `100.67.60.57` (RTX 4090, 24GB)
- Local client machine: microphone capture and operator control
- Transport: private network (Tailscale/LAN)

## Phase 2 Runtime (STT + LLM + TTS)

```text
Mic (Client) --WS(:8765)--> FastAPI Server (CPU)
                               |-- Silero-VAD (CPU)
                               |-- ASR Service --HTTP--> vLLM-STT (:8011)
                               |                        Qwen3-ASR-0.6B (~1.5GB)
                               |-- AI Gateway --HTTP--> vLLM-LLM (:8041)
                               |   (OpenAI compat)      Qwen3-14B-AWQ (~10GB)
                               |-- TTS Service --HTTP--> vLLM-TTS (:8031)
                               |   (stream)              Qwen3-TTS-0.6B (~2GB)
                               |-- Ambient (CPU/GPU)
                               +-- SQLite
```

### VRAM Budget (RTX 4090 24GB)

| Service | Model | gpu-mem-util | VRAM | Notes |
|---------|-------|-------------|------|-------|
| STT | Qwen3-ASR-0.6B | 0.10 | ~1.5GB | CER 6.73%, RTF 0.042 |
| LLM | Qwen3-14B-AWQ | 0.50 | ~10GB | Tool calling F1 0.971 |
| TTS | Qwen3-TTS-0.6B | 0.12 | ~2GB | RTF 0.52-0.68 |
| System | Xorg etc | - | ~0.2GB | |
| **Total** | | **0.72** | **~13.7GB** | **~10GB headroom** |

### Port Assignments

| Service | Port | Description |
|---------|------|-------------|
| STT vLLM | 8011 | Qwen3-ASR-0.6B |
| STT Bridge | 8021 | qwen_realtime_bridge |
| TTS vLLM | 8031 | Qwen3-TTS-0.6B |
| LLM vLLM | 8041 | Qwen3-14B-AWQ |
| FastAPI | 8765 | Application server |

### Expected Latency

```text
User utterance ends
 |-- STT: ~0.3s
 |-- LLM first token: ~0.5s
 |-- TTS first audio: ~0.1s (streaming)
 +-- Total perceived: ~1-1.5s
```

## Components

### 1) Audio Capture Service

- Captures mono PCM at 16kHz from selected input device
- Emits short frames to processing queue
- Handles device reconnect and stream restart

### 2) VAD Processor (`silero-vad`)

- Filters silence and background noise
- Produces speech segments with start/end offsets
- Passes only voiced chunks to ASR

### 3) STT Service (vLLM + Qwen3-ASR)

- Transcribes each voiced segment in Korean via vLLM HTTP API
- Returns realtime partial/final text and timestamps
- Provider: `qwen3_asr_vllm` (HTTP) or `qwen3_asr_vllm_realtime` (WebSocket)

### 4) AI Gateway (vLLM + Qwen3-14B-AWQ)

- OpenAI-compatible `/v1/chat/completions` API
- Dual-mode support: `openai` (vLLM) and `ollama` (legacy)
- System prompt and thinking mode support

### 5) TTS Service (vLLM + Qwen3-TTS)

- Text-to-speech via `/v1/audio/speech` endpoint
- Streaming PCM output for low-latency playback
- Integrated into AI response pipeline (auto TTS after ai_response)
- Standalone `/ws/tts` WebSocket endpoint

### 6) Transcript Store (`sqlite`)

- Persists utterances and timing metadata
- Stores AI turn request/response pairs

### 7) Ops and Health

- Docker Compose for all vLLM services
- Process supervisor (systemd for FastAPI)
- Health checks on all endpoints

## WebSocket Protocol

```text
Client -> Server:
  - Binary: PCM audio (16kHz, 16bit, mono)

Server -> Client:
  - {"type": "partial", "session_id", "text", ...}
  - {"type": "final", "session_id", "text", ...}
  - {"type": "ai_request", "text"}
  - {"type": "ai_response", "text"}
  - {"type": "tts_start", "session_id", "segment_id"}
  - Binary: PCM audio chunks (TTS output)
  - {"type": "tts_done", "session_id", "segment_id"}
  - {"type": "ambient", "text", "confidence"}
```

## Deferred Capabilities

- Speaker diarization
- Wake-word routing
- Full duplex real-time conversation loop
- Emotion/prosody fine controls
