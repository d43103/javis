# Architecture

## 배포 구조

- **서버**: `192.168.219.106` (RTX 4090, 24GB VRAM) — TTS + STT 모델 실행
- **Mac mini**: OpenClaw 앱 (Talk Mode) + openclaw-gateway — 음성 대화 오케스트레이션

## 현재 런타임 (Phase 4)

```
Mac mini
  OpenClaw.app (Talk Mode)
    ├── SFSpeechRecognizer (ko-KR) — STT
    ├── SpeakerVerifier (FluidAudio/WeSpeaker 임베딩) — 화자 인증
    ├── TalkModeRuntime (actor) — 오케스트레이터
    └── OpenAITTSPlayer — TTS 재생

  openclaw-gateway (Node.js :18789)
    └── voice agent (Claude claude-sonnet-4-6)

서버 (192.168.219.106)
  javis-tts (systemd :8031)
    └── vLLM :8031 → Qwen3-TTS-0.6B

  javis-stt (FastAPI :8765)   [선택적]
    ├── vLLM :8011 → Qwen3-ASR-0.6B
    └── voice_hub (WebSocket 멀티클라이언트)
```

## 음성 대화 흐름 (한 턴)

```
1. [마이크] → AVAudioEngine.installTap → PCM 버퍼
2. SFSpeechRecognizer → 부분 transcript → 무음 감지 → finalizeTranscript()
3. SpeakerVerifier.verify(samples, sampleRate)
   → resampleToMono16k (48kHz → 16kHz)
   → DiarizerManager 세그먼트 추출
   → cosine similarity vs threshold (0.35)
   → pass/fail (2초 미만 발화는 pass-through)
4. GatewayConnection.chatSend(session: "agent:voice:main", text)
   → openclaw-gateway WebSocket (ws://localhost:18789)
   → voice agent 실행 (Claude sonnet-4-6)
5. waitForAssistantText()
   → chat.history 폴링 (300ms 간격, 45초 타임아웃)
6. OpenAITTSPlayer.play(text)
   → POST /v1/audio/speech → javis-tts (192.168.219.106:8031)
   → Content-Type: audio/pcm (24000Hz, int16)
   → int16 → float32 변환 → AVAudioEngine → [스피커]
```

## 포트 할당

| 서비스 | 포트 | 설명 |
|--------|------|------|
| openclaw-gateway | 18789 | Mac mini WebSocket 게이트웨이 |
| javis-tts (vLLM) | 8031 | Qwen3-TTS-0.6B (Docker) |
| javis-stt (vLLM) | 8011 | Qwen3-ASR-0.6B (Docker) |
| javis-stt (FastAPI) | 8765 | STT WebSocket + voice_hub |

## VRAM 사용량 (RTX 4090 24GB)

| 서비스 | 모델 | VRAM |
|--------|------|------|
| TTS | Qwen3-TTS-0.6B | ~2GB |
| STT | Qwen3-ASR-0.6B | ~1.5GB |
| 시스템 | — | ~0.2GB |
| **합계** | | **~3.7GB** |

## TTS 서버 API (javis-tts, :8031)

OpenAI 호환:
```
POST /v1/audio/speech
Content-Type: application/json
{"model": "tts-1", "input": "텍스트", "voice": "Sohee", "response_format": "pcm"}
```

ElevenLabs 호환:
```
POST /v1/text-to-speech/{voice_id}/stream
Content-Type: application/json
{"text": "텍스트"}
```

## OpenClaw ↔ Mac 앱 인터페이스

### 연결 구조

```
OpenClaw.app
  └─ GatewayConnection (actor, singleton)
       └─ GatewayChannelActor (WebSocket 클라이언트)
            └─ ws://localhost:18789
                  └─ openclaw-gateway (Node.js)
```

### 인증 2계층

| 계층 | 파일 | 역할 |
|------|------|------|
| 서비스 토큰 | `~/.openclaw/openclaw.json` → `gateway.auth.token` | 게이트웨이 공유 시크릿 |
| 디바이스 토큰 | `~/.openclaw/identity/device-auth.json` | operator 토큰으로 설정 필요 |

### 주요 WebSocket 메서드

| Method | 설명 |
|--------|------|
| `talk.config` | TTS 설정 로드 |
| `chat.send` | 에이전트에 메시지 전송 |
| `chat.history` | 대화 히스토리 조회 |
| `chat.abort` | 에이전트 실행 중단 |
| `health` | 헬스 체크 (operator 권한 필요) |

## TalkModeRuntime 상태 머신

```
idle
  → setEnabled(true) → start()
listening      ← 기본 상태. RMS ticker가 원형 UI 업데이트
  → 발화 감지 → transcript 누적
  → 무음 (0.7s) → finalizeTranscript()
  → SpeakerVerifier pass → sendAndSpeak()
processing
  → chat.send → waitForAssistantText()
speaking
  → OpenAITTSPlayer.play()
  → 발화 인터럽트 감지 → stopSpeaking() → listening
  → 재생 완료 → startListening() → listening
idle
  → setEnabled(false) → stop()
```

## 세션 라우팅

```swift
// sendAndSpeak() 우선순위
let sessionKey = if let agentId = talkAgentId {
    "agent:\(agentId):main"          // UserDefaults: openclaw.talkAgentId = "voice"
} else if let activeSessionKey {
    activeSessionKey                  // WebChat UI 활성 세션
} else {
    await GatewayConnection.shared.mainSessionKey()
}
```

## 지연시간 (실측)

| 구간 | 지연 |
|------|------|
| 발화 종료 → STT 확정 | ~700ms (무음 감지) |
| STT → chat.send | ~즉시 |
| chat.send → 에이전트 응답 | ~1-3s (Claude API) |
| 에이전트 응답 → TTS 첫 청크 | ~300ms |
| **전체 체감** | **~2-4s** |
