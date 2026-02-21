# Javis Project

## 개요

로컬 한국어 음성 어시스턴트. **Primary 음성 인터페이스는 OpenClaw Talk Mode**이며, javis 프로젝트는 서버 측 TTS/STT 모델 실행을 담당한다.

- **서버**: `192.168.219.106` (RTX 4090 24GB) — javis-tts, javis-stt
- **클라이언트**: Mac mini — OpenClaw Talk Mode + openclaw-gateway

---

## 현재 아키텍처

### 음성 대화 흐름

```
[마이크] → SFSpeechRecognizer (ko-KR, macOS 내장 STT)
         → SpeakerVerifier (화자 인증, FluidAudio/WeSpeaker)
         → TalkModeRuntime → GatewayConnection.chatSend()
         → openclaw-gateway (ws://localhost:18789)
         → voice agent (Claude claude-sonnet-4-6, "agent:voice:main" 세션)
         → waitForAssistantText() 폴링 (300ms, 45s timeout)
         → OpenAITTSPlayer → POST /v1/audio/speech
         → javis-tts (192.168.219.106:8031, pcm_24000)
         → AVAudioEngine → [스피커]
```

### 서비스 목록

| 서비스 | 위치 | 포트 | 역할 |
|--------|------|------|------|
| `javis-tts` (systemd) | 서버 | 8031 | Qwen3-TTS 스트리밍 (**핵심**) |
| vLLM TTS | 서버 Docker | 8031 | Qwen3-TTS-0.6B |
| vLLM STT | 서버 Docker | 8011 | Qwen3-ASR-0.6B |
| `javis-stt` (FastAPI) | 서버 | 8765 | ASR + VAD + voice_hub (보조) |
| openclaw-gateway | Mac mini | 18789 | 에이전트 오케스트레이터 |
| OpenClaw.app | Mac mini | — | Talk Mode UI |

---

## 소스 구조

```
src/
  javis_tts/
    tts_streaming_server.py   # TTS 스트리밍 서버 — OpenAI + ElevenLabs 호환
  javis_stt/
    server.py                 # FastAPI 메인
    asr_service.py            # ASR (Qwen3-ASR via vLLM)
    vad_service.py            # VAD (Silero)
    conversation_engine.py    # 대화 컨텍스트 관리
    session_manager.py        # 세션 관리
    config.py                 # 설정 로더
    db.py                     # SQLite 저장
    repository.py             # DB 레포지터리
    models.py                 # 데이터 모델
    tts_service.py            # TTS 클라이언트
    ai_gateway.py             # AI 게이트웨이 클라이언트
    qwen_realtime_bridge.py   # vLLM 실시간 브릿지
    ambient_service.py        # 환경 음향 감지
    client_utils.py           # 클라이언트 유틸
  voice_hub.py               # WebSocket 멀티클라이언트 허브
  voice_llm_bridge.py        # LLM 브릿지 (레거시, 직접 사용 안 함)
  audio_devices.py           # 오디오 장치 목록 + gain 유틸리티

ios/JavisClient/             # Swift 클라이언트 (보조)
  HubConnection.swift        # WebSocket (voice_hub 연결)
  Models.swift               # 메시지 모델
  iOS/                       # iOS 앱
  macOS/                     # macOS 메뉴바 앱

docker-compose/              # vLLM Docker Compose 설정
config/stt.yaml              # 서버 런타임 설정
tests/                       # pytest 테스트
docs/                        # 문서
```

---

## javis-tts 서버

**가장 중요한 서비스**. OpenClaw Talk Mode가 이 서버를 통해 TTS를 재생한다.

### API

```bash
# OpenAI 호환
POST /v1/audio/speech
{"model": "tts-1", "input": "안녕하세요", "voice": "Sohee", "response_format": "pcm"}

# ElevenLabs 호환
POST /v1/text-to-speech/{voice_id}/stream
{"text": "안녕하세요"}

# 헬스체크
curl http://192.168.219.106:8031/health
```

### 운영 (서버에서)

```bash
systemctl --user status javis-tts.service
systemctl --user restart javis-tts.service
tail -f /tmp/javis_tts.log
```

### 소스

`src/javis_tts/tts_streaming_server.py`
- `Qwen3TTSModel` (qwen_tts 패키지)
- `pcm_24000` 포맷: 24kHz, int16, mono
- 스트리밍 응답 (chunk 단위)

---

## javis-stt 서버

### 운영 (서버에서)

```bash
systemctl --user status javis-stt.service
# 또는 직접 실행
cd ~/Workspace/projects/javis
./.venv/bin/python -m src.javis_stt.server

tail -f /tmp/javis_server.log
```

### Docker vLLM

```bash
docker compose up -d
docker compose ps
curl http://127.0.0.1:8011/health   # STT
curl http://127.0.0.1:8031/health   # TTS
```

---

## OpenClaw Talk Mode 연동

### 소스 위치

- **repo**: `~/Workspace/opensource/openclaw`
- **branch**: `javis-talk-mode`
- **worktree**: `~/Workspace/opensource/openclaw` (main은 `~/Workspace/opensource/openclaw-javis`)

### 수정된 파일

**`apps/macos/Sources/OpenClaw/TalkModeRuntime.swift`**
- `ttsBaseUrl`, `talkAgentId` actor 프로퍼티 (UserDefaults에서 읽음)
- `sendAndSpeak()`: `talkAgentId` 우선 → `agent:{id}:main` 세션 라우팅
- `OpenAITTSPlayer` (@MainActor final class): TTS 재생 + `stopSpeaking()` 인터럽트 지원
  - int16 PCM → float32 AVAudioEngine 재생 (macOS pcmFormatInt16 미지원 우회)
  - `URLSessionConfiguration.ephemeral`: LAN IP "인터넷 없음" 오류 우회
- `stripDirectiveTags()`: TTS 전송 전 `[[reply_to_current]]` 등 디렉티브 제거

**`apps/macos/Sources/OpenClaw/GatewayEnvironment.swift`**
- `readGatewayVersion` fast path: `package.json` 직접 읽기 (Node.js 실행 1.6초 → 수ms)

**`apps/shared/OpenClawKit/Sources/OpenClawKit/GatewayChannel.swift`**
- keepalive 메커니즘 제거 (node channel health rejection 루프 방지)

### Mac UserDefaults 설정 (ai.openclaw.mac.debug)

```bash
defaults write ai.openclaw.mac.debug "openclaw.ttsBaseUrl" "http://192.168.219.106:8031"
defaults write ai.openclaw.mac.debug "openclaw.outputFormat" "pcm_24000"
defaults write ai.openclaw.mac.debug "openclaw.talkAgentId" "voice"
defaults write ai.openclaw.mac.debug "openclaw.talkEnabled" -bool true
defaults write ai.openclaw.mac.debug "openclaw.speakerVerification.enabled" -bool true
defaults write ai.openclaw.mac.debug "openclaw.speakerVerification.threshold" "0.35"

# 확인
defaults read ai.openclaw.mac.debug | grep -E "talk|tts|speaker|voice"
```

### 앱 빌드 및 배포

```bash
cd ~/Workspace/opensource/openclaw/apps/macos
swift build -c release

cp .build/release/OpenClaw dist/OpenClaw.app/Contents/MacOS/OpenClaw
xcrun install_name_tool -add_rpath "@loader_path/../Frameworks" dist/OpenClaw.app/Contents/MacOS/OpenClaw
codesign --force --deep --sign - dist/OpenClaw.app

open dist/OpenClaw.app
```

---

## openclaw voice 에이전트 설정

```bash
# ~/.openclaw/openclaw.json
# voice 에이전트: tools.deny에 agentToAgent 포함 (replyTo 태그 방지)
```

openclaw voice 에이전트 workspace: `~/.openclaw/workspace/voice/`
- `TOOLS.md`: 개발 작업 디렉토리 규칙 등 환경 설정
- `SOUL.md`, `USER.md`: 에이전트 정체성 + 유저 컨텍스트

---

## 알려진 문제 및 해결책

### ✅ Bug 1: OpenAI TTS 인터럽트 불가 (수정 완료)

**원인**: `playInt16PCMStream()` 정적 함수가 로컬 AVAudioEngine을 생성 → `stopSpeaking()`과 무관한 인스턴스.

**해결**: `OpenAITTSPlayer` (@MainActor final class) 신규 추가. engine/player/continuation을 인스턴스 프로퍼티로 보관. `stopSpeaking()` 첫 줄에 `await OpenAITTSPlayer.shared.stop()` 추가.

---

### ✅ Bug 2: Talk Mode 권한 부여 후 미작동 (레이스 조건)

**원인**: `setTalkEnabled(true)` 내에서 `talkEnabled = true` didSet이 먼저 실행되어 `TalkModeRuntime.setEnabled(true)` → `isEnabled = true`로 설정. 이후 권한 다이얼로그 대기(await). 권한 승인 후 다시 `setEnabled(true)` 호출 시 `isEnabled == true`라 guard로 리턴.

**증상**: 권한 승인 후에도 원형 UI 미반응.

**해결 (임시)**: 권한 승인 후 Talk Mode를 **끄고 다시 켜기**.

---

### 🟡 Warning: ElevenLabs 경고 로그 (무해)

**원인**: `preparePlaybackInput()`이 항상 ElevenLabs 유효성 검사 수행. `ttsBaseUrl` 설정 시에도 "missing ELEVENLABS_API_KEY" 경고 출력.

**결과**: 동작 영향 없음. `playAssistant()`가 `ttsBaseUrl` 있으면 ElevenLabs 경로 건너뜀.

---

### 🟡 Warning: chat.history 폴링 방식 (설계 한계)

**현황**: `waitForAssistantText()`가 300ms마다 폴링. WebSocket 이벤트 기반이 아님.

**영향**: 최대 300ms 추가 지연. 45초 내 응답 없으면 재청취로 전환.

---

### 🔵 Config: device role (참고)

macOS 앱의 `~/.openclaw/identity/device-auth.json`은 **operator 토큰** 사용 필요.

```bash
# device-auth.json의 tokens.node가 아닌 tokens.operator 값을 사용해야 함
# paired.json에서 operator 토큰 확인 가능
```

---

## 디버깅

### Talk Mode 로그

```bash
# 실시간 로그 (Talk Mode 관련)
/usr/bin/log stream --process OpenClaw --level debug 2>&1 | grep -v "CoreFoundation\|LaunchServices\|CoreLocation"

# TCC 권한 확인
sqlite3 "$HOME/Library/Application Support/com.apple.TCC/TCC.db" \
  "SELECT service, auth_value FROM access WHERE client='ai.openclaw.mac.debug';"

# Talk Mode 활성화 상태 확인
defaults read ai.openclaw.mac.debug 'openclaw.talkEnabled'

# Speech Recognition 권한 리셋 (트러블슈팅)
tccutil reset Microphone ai.openclaw.mac.debug
tccutil reset SpeechRecognition ai.openclaw.mac.debug
```

### TTS 서버 확인

```bash
# 헬스체크
curl http://192.168.219.106:8031/health
curl http://192.168.219.106:8880/health   # Qwen3-TTS (ElevenLabs 호환, port 8880)

# TTS 테스트
curl -s -X POST http://192.168.219.106:8031/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","input":"안녕하세요","voice":"Sohee","response_format":"pcm"}' \
  | wc -c   # 0이 아니면 정상
```

### Gateway 로그

```bash
# 활성 게이트웨이 로그 위치
ls -lt /tmp/openclaw/
tail -f /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log

# 또는
tail -f ~/.openclaw/logs/gateway.log
```
