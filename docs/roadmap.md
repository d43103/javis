# Roadmap

## Phase 1 (완료): Speech-to-Text MVP

### Delivered

- 연속 오디오 캡처
- VAD 기반 청킹 (Silero VAD)
- 한국어 STT 파이프라인 (Qwen3-ASR-0.6B via vLLM)
- 실시간 partial/final 이벤트
- 타임스탬프 트랜스크립트 저장 (SQLite)

---

## Phase 2 (완료): TTS + Infrastructure

### Delivered

- TTS: Qwen3-TTS-0.6B (vLLM, streaming PCM)
- STT 모델: Qwen3-ASR-0.6B (경량화)
- Docker Compose (vLLM 컨테이너)
- Auto TTS 파이프라인

---

## Phase 3 (완료): Swift 클라이언트 + OpenClaw 연동

### Delivered

- **Swift macOS 메뉴바 앱** (`ios/JavisClient/macOS/`) — 보조 클라이언트
- **Swift iOS 앱** (`ios/JavisClient/iOS/`) — 공통 HubConnection, Models 공유
- **VoiceHub 서버** (`src/voice_hub.py`) — WebSocket 멀티클라이언트
- **javis-tts 서버** (`src/javis_tts/tts_streaming_server.py`)
  - OpenAI 호환 `/v1/audio/speech`
  - ElevenLabs 호환 `/v1/text-to-speech/{voice_id}/stream`
  - systemd 서비스로 상시 실행

### 제거된 구성 요소

| 항목 | 이유 |
|------|------|
| `src/javis_menubar.py` (Python rumps) | Swift 앱으로 대체 |
| `scripts/mic_stream_client.py` | Hub 서버로 통합 |
| `scripts/ai.javis.mic-client.plist` | 삭제 |
| `deploy/com.javis.voice-bridge.plist` | 삭제 |

---

## Phase 4 (완료): OpenClaw Talk Mode 통합

### Goal

OpenClaw Talk Mode를 Primary 음성 인터페이스로 채택. javis TTS 서버를 OpenClaw에 연동.

### Delivered

- **OpenClaw Talk Mode 연동** (`~/Workspace/opensource/openclaw`, branch: `javis-talk-mode`)
  - `TalkModeRuntime.swift`: ttsBaseUrl, talkAgentId 라우팅, javis TTS 연동
  - `OpenAITTSPlayer`: TTS 재생 + 인터럽트 지원 (Bug 1 수정)
  - `GatewayEnvironment.swift`: readGatewayVersion fast path
  - Speaker Verification (FluidAudio/WeSpeaker, cosine similarity)
- **voice agent** (openclaw): Claude sonnet-4-6, `agent:voice:main` 세션
- **TTS 연결**: Mac mini → `POST /v1/audio/speech` → javis-tts (192.168.219.106:8031)

### 현재 음성 흐름

```
[마이크] → SFSpeechRecognizer (ko-KR)
         → SpeakerVerifier
         → openclaw-gateway → voice agent (Claude)
         → javis-tts (Qwen3-TTS) → [스피커]
```

---

## Phase 5 (계획): 외출 시 iPhone 접속

### Planned

- Tailscale을 통한 원격 openclaw-gateway 접속
- iPhone OpenClaw 앱 (operator 모드) → Mac mini gateway
- 동일한 `agent:voice:main` 세션 공유
- 집 안/밖 동일한 음성 대화 경험

### 구현 필요 사항

```bash
# openclaw.json: gateway.tailscale.mode = "on"
# iPhone OpenClaw 앱: Mac mini Tailscale IP 설정
```

---

## Out of Scope

- Full duplex real-time conversation (동시 말하기/듣기)
- Emotion/prosody fine-grained controls
- On-device LLM (현재 Claude API 사용)
