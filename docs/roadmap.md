# Roadmap

## Phase 1 (Complete): Speech-to-Text MVP

### Goal

Convert Korean speech from local microphone input into realtime timestamped text continuously.

### Delivered

- Continuous audio capture process
- VAD-based chunking (skip silence)
- Korean STT transcription pipeline (faster-whisper, Qwen3-ASR)
- Realtime partial/final transcript event emission
- Final transcript handoff to local AI for text conversation
- Timestamped transcript storage (sqlite)
- Basic health logging and auto-restart behavior

### Exit Criteria (Met)

- 60+ minute run without manual restart
- Korean utterances are transcribed and stored with start/end timestamps
- Realtime latency targets met (partial <= 0.7s, final <= 1.8s)
- Final transcript to AI text reply loop works end-to-end
- Service restarts safely after process failure

## Phase 2 (Complete): TTS + Infrastructure Upgrade

### Goal

Add TTS for voice responses. Upgrade STT model (lighter), LLM (stronger), and unify inference on vLLM.

### Delivered

- STT model: Qwen3-ASR-1.7B -> Qwen3-ASR-0.6B (VRAM savings)
- LLM: phi4 (Ollama) -> Qwen3-14B-AWQ (vLLM, OpenAI-compatible)
- TTS: Qwen3-TTS-0.6B (vLLM, streaming PCM)
- Docker Compose for 3x vLLM services
- AI Gateway dual-mode: OpenAI + Ollama (legacy)
- TTSService with batch and streaming synthesis
- Auto TTS pipeline: ai_response -> tts_start -> audio -> tts_done
- /ws/tts standalone WebSocket endpoint
- Config schema: TTSConfig, AIConfig extensions

### VRAM Budget

Total ~13.7GB / 24GB (STT 1.5 + LLM 10 + TTS 2 + sys 0.2)

### Exit Criteria (Expected)

- All 3 vLLM containers run simultaneously within 24GB
- End-to-end latency <= 1.5s (STT + LLM + TTS)
- Rollback to Phase 1 via config change only

## Phase 3 (Next): Conversation Intelligence

### Goal

Add multi-turn context management, tool calling, and conversation memory.

### Planned

- Multi-turn conversation context (sliding window)
- Tool calling integration (weather, calendar, etc.)
- Conversation memory and summarization
- Speaker diarization
- Wake-word routing

### Out of Scope (Phase 3)

- Full duplex real-time conversation
- Emotion/prosody fine controls
