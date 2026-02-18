# Voice Hub + openclaw Integration Design

## Goal

- Mac을 허브 서버로 삼아 모바일 앱과 기존 menubar 클라이언트를 모두 수용
- LLM 호출을 openclaw voice-assistant 에이전트로 통합 → 대화 이력·메모리 관리 일원화
- iOS 앱이 백그라운드에서 음성 대화 가능 (LAN + Tailscale)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  LOCAL MAC                                                      │
│                                                                 │
│  ┌─────────────────────────────────────────┐                   │
│  │  Mac Hub Server  (src/voice_hub.py)     │                   │
│  │  WebSocket :8766  /ws/voice             │                   │
│  │                                         │                   │
│  │  클라이언트별 VoiceSession              │                   │
│  │    session_id: voice-mac / voice-mobile │                   │
│  │    ├─ PCM → 4090 STT WebSocket 프록시  │                   │
│  │    ├─ transcript → openclaw agent       │                   │
│  │    └─ TTS PCM → 클라이언트 스트림      │                   │
│  └─────────────────────────────────────────┘                   │
│          ▲                   ▲                                  │
│  [Mac menubar]        [모바일 앱]                               │
│  (VoiceBridge → Hub)  (iOS Swift, 새로 개발)                   │
│                        LAN / Tailscale                          │
│                                                                 │
│  openclaw voice-assistant agent                                 │
│    세션별 대화 히스토리 / 메모리 자동 관리                      │
└─────────────────────────────────────────────────────────────────┘
                    │ STT WebSocket  │ TTS HTTP SSE
          ┌─────────▼────────────────▼─────────────┐
          │  4090 SERVER (:8765)                    │
          │  STT vLLM (Qwen3-ASR-1.7B, :8011)      │
          │  TTS Streaming (Qwen3-TTS, :8031)       │
          └─────────────────────────────────────────┘
```

---

## Components

### 1. Mac Hub Server (`src/voice_hub.py`)

| 항목 | 내용 |
|------|------|
| 포트 | `:8766` |
| 엔드포인트 | `WebSocket /ws/voice?session_id=<id>` |
| 클라이언트 식별 | `session_id` query param |
| STT 연결 | 세션당 4090 STT WebSocket 1개 (`/ws/stt?session_id=<id>`) |
| LLM 호출 | `openclaw agent --agent voice-assistant --session-id <id> -m "<text>" --json` subprocess |
| TTS 스트림 | 4090 `POST /v1/voice/turn` HTTP streaming → Hub → 클라이언트 WebSocket binary |
| 멀티 세션 | asyncio로 동시 클라이언트 처리 |
| Gain 제어 | 클라이언트가 JSON 메시지로 input_gain / output_gain 조정 가능 |

**Hub ↔ 클라이언트 메시지 프로토콜:**

- 클라이언트 → Hub: binary = PCM int16 오디오
- 클라이언트 → Hub: `{"type":"gain","input":1.0,"output":1.0}` (JSON)
- Hub → 클라이언트: `{"type":"partial","text":"..."}` (JSON)
- Hub → 클라이언트: `{"type":"final","text":"..."}` (JSON)
- Hub → 클라이언트: `{"type":"ai","text":"..."}` (JSON)
- Hub → 클라이언트: `{"type":"status","value":"thinking|speaking|idle"}` (JSON)
- Hub → 클라이언트: binary = TTS PCM float32 오디오

### 2. openclaw voice-assistant 에이전트

| 항목 | 내용 |
|------|------|
| 에이전트 ID | `voice-assistant` |
| 모델 | `anthropic/claude-haiku-4-5-20251001` |
| Identity | 친근하고 간결한 한국어 개인 비서, 1-2문장 답변, 마크다운 금지 |
| 세션 | `voice-mac`, `voice-mobile` 등 클라이언트별 분리 |
| 히스토리 | openclaw gateway 자동 관리 (cross-session 메모리 포함) |
| 등록 방법 | `openclaw agents add` CLI + `openclaw.json` 수동 편집 |

### 3. menubar_app.py 수정

- `VoiceBridge` 직접 실행 → Hub WebSocket (`ws://localhost:8766/ws/voice?session_id=voice-mac`) 연결로 교체
- 또는 Hub를 내장 실행(subprocess)하고 로컬 WebSocket으로 연결
- Gain/디바이스 제어는 Hub 프로토콜 JSON 메시지로 전달

### 4. iOS 모바일 앱 (별도 Xcode 프로젝트)

| 항목 | 내용 |
|------|------|
| 언어 | Swift |
| 연결 | URLSessionWebSocketTask → `ws://<mac-ip>:8766/ws/voice?session_id=voice-mobile` |
| 주소 | LAN 자동 감지 or 수동 설정, Tailscale는 hostname 사용 |
| 백그라운드 오디오 | `AVAudioSession.sharedInstance().setCategory(.playAndRecord)` + Background Modes: Audio |
| PCM 포맷 | int16, 16kHz, mono (서버 STT 맞춤) |
| TTS 재생 | Hub에서 받은 float32 PCM → AVAudioEngine 스트리밍 재생 |
| UI | 상태 표시 (idle/listening/thinking/speaking), 세션 설정 |

---

## Data Flow (한 턴)

```
1. 클라이언트 마이크 → PCM int16 → WebSocket (Hub :8766)
2. Hub → PCM → 4090 STT WebSocket (/ws/stt?session_id=voice-mobile)
3. 4090 → {type:"partial"} → Hub → 클라이언트 (UI partial 표시)
4. 4090 → {type:"final", text:"안녕"} → Hub
5. Hub → subprocess:
       openclaw agent --agent voice-assistant
                      --session-id voice-mobile
                      -m "안녕" --json
6. openclaw → AI 응답 텍스트 → Hub
7. Hub → {type:"ai", text:"..."} → 클라이언트
8. Hub → POST /v1/voice/turn {response_text:...} → 4090 TTS
9. 4090 TTS → PCM chunks → Hub → binary WebSocket → 클라이언트 재생
```

---

## VRAM / Port Budget (변동 없음)

| Service | Port | Description |
|---------|------|-------------|
| STT vLLM | 8011 | Qwen3-ASR-1.7B |
| TTS Server | 8031 | Qwen3-TTS streaming fork |
| FastAPI (4090) | 8765 | Main app server |
| **Mac Hub** | **8766** | **새로 추가** |

---

## Implementation Order

1. **openclaw voice-assistant 에이전트 등록** — `openclaw agents add` + config 편집
2. **Mac Hub Server** — `src/voice_hub.py` 구현 (asyncio WebSocket 서버)
3. **Hub 통합 테스트** — Mac menubar가 Hub를 통해 정상 동작 확인
4. **menubar_app.py 수정** — VoiceBridge 직접 호출 → Hub 연결로 교체
5. **launchd plist** — Hub 서버 24/7 자동 실행 (`deploy/com.javis.hub.plist`)
6. **iOS 앱** — Xcode 프로젝트 생성, WebSocket 클라이언트 + 오디오 엔진 구현

---

## Decision Log

| Decision | Rationale |
|----------|-----------|
| Mac Hub 서버 도입 | 모바일 + 데스크탑 멀티 클라이언트를 단일 허브로 수용 |
| openclaw CLI subprocess | Gateway REST API 미공개; CLI는 `--json` 출력 지원, 세션 관리 자동 |
| session_id 분리 | 클라이언트별 독립 대화 컨텍스트 유지 (openclaw 세션 분리) |
| iOS URLSessionWebSocketTask | 별도 라이브러리 불필요, 백그라운드 동작 지원 |
| Tailscale 지원 | openclaw config에 allowTailscale: true 이미 설정됨 |
