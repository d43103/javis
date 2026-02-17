# Phase 3 Design: Real-time Personal Voice Assistant

## Goal

Build a real-time Korean voice assistant where:
- The **4090 server** handles only STT + TTS (pure audio processing)
- The **local Mac** handles LLM reasoning via Claude Code / openclaw agent
- TTS speaks in **my voice** (cloned from reference recording)
- End-to-end latency target: **first audio chunk < 1.5s** from utterance end
- 24/7 operation, zero cloud STT/TTS cost

---

## Current State (Measured)

### Server (192.168.219.106, RTX 4090 24GB)

| Process | VRAM | Status |
|---------|------|--------|
| vLLM STT (Qwen3-ASR-0.6B) | 2,954 MB | healthy, 50ms RTF |
| vLLM LLM (Qwen3-14B-AWQ) | 15,028 MB | **to remove** |
| qwen3-tts-api (official backend) | 2,800 MB | **to remove** — 6.5s latency |
| Xorg/sys | 193 MB | stays |
| **Total** | **20,975 MB** | **3,589 MB free** |

**After removing LLM + old TTS:** ~3,147 MB used → **21,417 MB free**

### Local Mac

- Claude Code 2.1.44 installed (`/opt/homebrew/bin/claude`)
- openclaw installed with 15 agents (assistant, oracle, sisyphus, etc.)
- LAN latency to server: **0.685ms**
- `mic_stream_client.py` already connects to `ws://192.168.219.106:8765`

### Key Findings

- TTS `official` backend: 6.5s for short sentence, 357s for streaming attempt — **unusable for real-time**
- STT RTF: ~50ms per segment — **excellent, keep**
- `reference.wav`: 16.9s of my voice @ 16kHz — **5.6× minimum requirement, ideal for cloning**
- `dffdeeq/Qwen3-TTS-streaming` fork: implements `stream_generate_voice_clone()` with `emit_every_frames=4` (~330ms chunks) — **the missing piece**
- vLLM-Omni streaming: RFC #938 not yet merged — **do not use**

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  LOCAL MAC                                                      │
│                                                                 │
│  mic_stream_client.py                                          │
│    │  PCM 16kHz (WebSocket)          TTS PCM chunks           │
│    │─────────────────────────────────────────────────►speaker │
│    │                                                           │
│  voice_llm_agent (openclaw / claude code)                      │
│    ▲  final_transcript event                                    │
│    │  HTTP POST /v1/voice/turn  ◄── new endpoint               │
│    │                                                           │
│    └──────────────────── AI response text ──────────────────── │
│                                          │                      │
└──────────────────────────────────────────│──────────────────────┘
                                           │ HTTP SSE stream
                           ┌───────────────▼──────────────────────┐
                           │  4090 SERVER                         │
                           │                                      │
                           │  FastAPI (:8765)  ◄── javis-stt      │
                           │    │                                 │
                           │    ├── VADService (CPU, Silero)      │
                           │    ├── ASRService                    │
                           │    │     └► vLLM-STT (:8011)        │
                           │    │         Qwen3-ASR-1.7B          │
                           │    │                                 │
                           │    └── TTSService (streaming)        │
                           │          └► TTS Server (:8031)       │
                           │              Qwen3-TTS-12Hz-1.7B-Base│
                           │              streaming fork          │
                           │              voice: my reference.wav │
                           └──────────────────────────────────────┘
```

### Data Flow (one conversation turn)

```
1. Mac mic → PCM stream → server /ws/stt
2. VAD detects speech end
3. ASR → "오늘 날씨 어때?" (50ms)
4. Server sends {type:"final", text:"오늘 날씨 어때?"} to Mac client
5. Mac client → POST /v1/voice/turn {text, session_id} to server  [NEW]
   OR: Mac client calls claude/openclaw directly with the text
6. LLM (Claude on Mac) → "오늘 서울 날씨는..."
7. Mac sends AI response text back to server /v1/voice/turn response
   OR: Mac client → POST /v1/tts/stream {text} → server streams PCM back
8. Server TTS: sentence-split → stream_generate_voice_clone() → PCM chunks
9. PCM chunks → WebSocket → Mac → speaker playback
```

### Two Integration Modes

**Mode A — Server-mediated (simpler client):**
- Server `/ws/stt` receives audio, emits transcripts
- Mac client receives `final` event → calls LLM locally → POSTs response text to server `/v1/tts/stream`
- Server streams back PCM audio

**Mode B — openclaw voice agent (richer):**
- openclaw `voice-assistant` agent runs on Mac
- Subscribes to server transcript WebSocket
- On each `final` event: calls Claude with conversation history
- POSTs AI text to server `/v1/tts/stream`
- Receives and plays PCM stream

**Phase 3 implements Mode A first, Mode B as enhancement.**

---

## Component Changes

### Remove (server)

```bash
docker stop javis-vllm-llm && docker rm javis-vllm-llm
docker stop qwen3-tts-api && docker rm qwen3-tts-api
# removes 15,028 + 2,800 = 17,828 MB VRAM
```

### Upgrade: STT (server)

| | Current | Target |
|--|---------|--------|
| Model | Qwen3-ASR-0.6B | **Qwen3-ASR-1.7B** |
| VRAM | 2,954 MB | ~4,500 MB |
| Korean CER | 6.73% | ~4% (−40% error rate) |
| RTF | 0.042 | ~0.08 |

Still well under 100ms per segment. VRAM increase acceptable given freed headroom.

### New: TTS Server (server)

**Stack:** `dffdeeq/Qwen3-TTS-streaming` + custom FastAPI wrapper

```
Model:    Qwen3-TTS-12Hz-1.7B-Base   (~7GB VRAM)
Voice:    recordings/test-01.wav      (16.9s reference, 16kHz)
Ref text: reference.txt               (already exists)
Endpoint: /v1/audio/speech            (OpenAI-compatible, batch)
Endpoint: /v1/audio/speech/stream     (WebSocket, true streaming)
          /ws/tts-stream              (alternative WS endpoint)
Frames:   emit_every_frames=4         → 330ms audio chunks
```

**Why 1.7B over 0.6B for TTS:**
- VRAM is no longer constrained (21GB free after cleanup)
- 15% better Korean pronunciation accuracy
- More natural prosody on longer sentences
- Single user, no concurrent request pressure

**Expected latency (RTX 4090, single request):**

| Stage | Estimate |
|-------|----------|
| TTS model load (warm) | 0ms |
| Voice clone context setup | ~50ms (cached after first) |
| First 4 frames (330ms audio) | ~300-400ms |
| Subsequent chunks | ~300ms each |

### New: `/v1/voice/turn` endpoint (server)

Simple HTTP endpoint to decouple LLM from server:

```
POST /v1/voice/turn
{
  "session_id": "mac-1",
  "text": "오늘 날씨 어때?",
  "response_text": "오늘 서울은 맑고..."   ← Mac provides this
}

Response: chunked PCM audio stream (my voice)
```

This allows the Mac-side LLM to drive the TTS independently.

### New: `voice_llm_bridge.py` (local Mac)

Thin Python script on Mac that:
1. Connects to `ws://192.168.219.106:8765/ws/stt`
2. On each `{type: "final"}` event: calls Claude Code subprocess or openclaw agent
3. Posts AI response to `http://192.168.219.106:8765/v1/voice/turn`
4. Receives PCM stream → plays via `sounddevice`

### openclaw voice-assistant agent (Mac, Mode B)

New openclaw agent `~/.openclaw/agents/voice-assistant/`:
- IDENTITY: Korean personal voice assistant, responds in 1-2 concise sentences
- SOUL: Natural conversation, remembers context within session
- Triggered by: MCP tool `javis_transcript` (custom MCP server on Mac)
- Output: text only, bridge handles TTS

---

## VRAM Budget (after Phase 3)

| Service | Model | VRAM |
|---------|-------|------|
| STT vLLM | Qwen3-ASR-1.7B | ~4,500 MB |
| TTS Server | Qwen3-TTS-12Hz-1.7B-Base | ~7,000 MB |
| Xorg/sys | — | ~200 MB |
| **Total** | | **~11,700 MB** |
| **Free** | | **~12,900 MB (52%)** |

Leaves 12.9GB headroom for:
- Future local LLM (Qwen3-8B-AWQ ~5GB) if moving back on-server
- Model warmup spikes
- Future speaker gating model (CPU anyway)

---

## Latency Budget (target)

```
Utterance ends (VAD detects silence)
  ├── STT (Qwen3-ASR-1.7B):       ~80ms
  ├── Network to Mac + LLM call:  ~500ms  (Claude Haiku / sonnet)
  ├── Network back + TTS setup:   ~50ms
  ├── TTS first chunk (330ms audio): ~350ms
  └── Audio playback starts:      ~80ms (buffer)

Total perceived latency: ~1,060ms  ← under 1.5s target ✓
```

---

## Port Assignments (Phase 3)

| Service | Port | Description |
|---------|------|-------------|
| STT vLLM | 8011 | Qwen3-ASR-1.7B |
| TTS Server | 8031 | Qwen3-TTS-12Hz-1.7B-Base (streaming fork) |
| FastAPI | 8765 | Main app server |
| ~~LLM vLLM~~ | ~~8041~~ | **removed** |
| ~~qwen3-tts-api~~ | ~~8880~~ | **removed** |

---

## Phase 3 Implementation Steps

### Step 1: Server cleanup + STT upgrade
- Stop/remove vLLM-LLM container and qwen3-tts-api container
- Update docker-compose: remove vllm-tts and vllm-llm services
- Update vllm-stt: change model to Qwen3-ASR-1.7B, adjust gpu-memory-utilization
- Update `config/stt.yaml`: `remote_model: Qwen/Qwen3-ASR-1.7B`
- Verify STT health: `curl http://192.168.219.106:8011/health`

### Step 2: New TTS server
- Clone `dffdeeq/Qwen3-TTS-streaming` on server
- Copy `recordings/test-01.wav` → TTS reference
- Build `src/javis_tts/tts_streaming_server.py`:
  - FastAPI app on :8031
  - `/v1/audio/speech` (OpenAI-compat, for existing TTSService)
  - `/v1/audio/speech/stream` (WebSocket, true streaming PCM)
  - Voice clone cache (load reference once, reuse)
- Register as `javis-tts.service` (systemd user)
- Test: first chunk latency < 500ms

### Step 3: `/v1/voice/turn` + server sync
- Add endpoint to `server.py`
- Sync local code → server (merge server's ai_gateway extras into local)
- Update `config/stt.yaml` on server: disable ai section (LLM moved to Mac)
- Update `TTSService`: point to new streaming TTS server

### Step 4: Mac-side bridge (`voice_llm_bridge.py`)
- WebSocket listener for `final` transcript events
- Claude Code subprocess call (or Anthropic API direct)
- Conversation history (deque, 10 turns)
- POST response → server `/v1/voice/turn` → stream PCM → play audio
- Launchd plist for 24/7 Mac operation

### Step 5: ConversationEngine (server-side, for future)
- `src/javis_stt/conversation_engine.py`: session history store
- SQLite persistence across restarts
- Used if LLM ever moves back to server

### Step 6: openclaw voice-assistant agent (Mode B)
- `~/.openclaw/agents/voice-assistant/` config
- IDENTITY.md: concise Korean assistant
- Custom MCP server: `javis_mcp_server.py` on Mac
  - Tool: `get_transcript()` → latest final transcript
  - Tool: `speak(text)` → POST to TTS, returns when audio done

---

## Out of Scope (Phase 4)

- Speaker diarization / gating (identify my voice vs others)
- Mobile app (iOS Swift client)
- Wake word routing
- Full duplex (talking while AI is speaking)
- Fine-tuning TTS on more of my voice data
- Moving LLM back on-server (possible with freed VRAM)

---

## Decision Log

| Decision | Rationale |
|----------|-----------|
| Remove LLM from server | Frees 15GB; Mac handles LLM via Claude — better quality, zero VRAM cost |
| Qwen3-TTS-1.7B over 0.6B | VRAM is now free; 15% better Korean; single user |
| dffdeeq streaming fork over vLLM-Omni | Only available true streaming path; vLLM-Omni streaming not yet merged |
| Mode A first (HTTP bridge) | Simpler, testable independently of openclaw; Mode B adds value later |
| ASR upgrade to 1.7B | 40% error reduction; VRAM freed by LLM removal covers the increase |
| reference.wav 16.9s | Already exists, exceeds 3s minimum, no re-recording needed |
