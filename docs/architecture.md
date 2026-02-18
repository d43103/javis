# Architecture

## 배포 구조

- 서버: `192.168.219.106` (RTX 4090, 24GB) — STT + TTS
- Mac 클라이언트: 메뉴바 앱 (rumps) — 마이크 + Claude LLM + TTS 재생
- 전송: LAN (192.168.219.x)

## Phase 3 런타임 (현재)

```text
Mac (menubar_app.py)
  ├── rumps 메뉴바 UI (메인 스레드)
  └── VoiceBridge (백그라운드 스레드, asyncio)
        ├── 마이크 → PCM 16kHz → WS(:8765) → 서버
        ├── STT final 이벤트 수신 ← 서버
        ├── Claude API 호출 (Anthropic SDK)
        └── POST /v1/voice/turn → TTS PCM ← 서버 → 스피커 재생

서버 (192.168.219.106)
  FastAPI (:8765)
    ├── Silero-VAD (CPU)
    ├── ASR Service → vLLM-STT (:8011) Qwen3-ASR-0.6B
    ├── TTS Service → vLLM-TTS (:8031) Qwen3-TTS-0.6B
    └── SQLite 저장
```

### 데이터 흐름 (한 턴)

```text
1. Mac 마이크 → PCM 16kHz → WebSocket → 서버 /ws/stt
2. 서버 VAD → 발화 감지 → ASR → 텍스트
3. 서버 → {type:"final", text:"..."} → Mac
4. Mac VoiceBridge → Claude API → AI 응답 텍스트
5. Mac → POST /v1/voice/turn {response_text} → 서버
6. 서버 TTS → PCM 스트리밍 → Mac → 스피커 재생
```

### 포트 할당

| 서비스 | 포트 | 설명 |
|--------|------|------|
| STT vLLM | 8011 | Qwen3-ASR-0.6B (Docker) |
| STT Bridge | 8021 | qwen_realtime_bridge |
| TTS vLLM | 8031 | Qwen3-TTS-0.6B (Docker) |
| FastAPI | 8765 | 메인 서버 |

### VRAM 사용량 (RTX 4090 24GB)

| 서비스 | 모델 | gpu-mem-util | VRAM |
|--------|------|-------------|------|
| STT | Qwen3-ASR-0.6B | 0.10 | ~1.5GB |
| TTS | Qwen3-TTS-0.6B | 0.12 | ~2GB |
| 시스템 | — | — | ~0.2GB |
| **합계** | | **0.22** | **~3.7GB** |

> Phase 2 대비 LLM (Qwen3-14B-AWQ, 10GB) 제거. Claude API (Mac)로 대체.

### 예상 지연시간

```text
발화 종료 (VAD 감지)
 ├── STT: ~50ms
 ├── 네트워크 + Claude API: ~500ms
 ├── 네트워크 + TTS 첫 청크: ~350ms
 └── 전체 체감: ~1-1.5s
```

## 컴포넌트

### 1) Mac 메뉴바 앱 (`menubar_app.py`)

- rumps 기반 macOS 상단바 앱
- 입력/출력 장치 선택, 게인 조절
- STT/AI 텍스트 실시간 표시
- 시작/정지 토글
- 백그라운드 스레드에서 VoiceBridge 실행

### 2) VoiceBridge (`voice_llm_bridge.py`)

- 마이크 PCM → WebSocket 전송
- STT final 이벤트 수신 → idle flush debounce → Claude API 호출
- AI 응답 → POST /v1/voice/turn → TTS PCM 스트리밍 재생
- 입력/출력 게인, 장치 런타임 변경 지원
- 콜백: `on_status_change`, `on_partial`, `on_final`, `on_ai_response`

### 3) 오디오 장치 (`audio_devices.py`)

- sounddevice 기반 입출력 장치 목록 조회
- PCM int16 / float32 게인 적용

### 4) VAD (`silero-vad`, 서버)

- 무음/배경 소음 필터링
- 발화 구간 감지 후 ASR로 전달

### 5) STT (vLLM + Qwen3-ASR, 서버)

- Docker 컨테이너 (포트 8011)
- qwen_realtime_bridge (포트 8021)를 통해 접근
- 한국어 실시간 partial/final 트랜스크립트
- ASR 프로바이더: `qwen3_asr_vllm` (HTTP), `qwen3_asr_vllm_realtime` (WebSocket 실시간, HTTP fallback 포함)

### 6) TTS (vLLM + Qwen3-TTS, 서버)

- Docker 컨테이너 (포트 8031)
- `/v1/voice/turn` HTTP POST → PCM 스트리밍
- `/ws/tts` WebSocket → 텍스트 입력 → PCM 스트리밍 (대안)

### 7) AmbientSoundService (서버)

- MIT/ast-finetuned-audioset-10-10-0.4593 오디오 분류 모델
- 주변 환경 음향(박수, 음악, 알람 등) 이벤트 감지
- VAD가 음성으로 분류하지 못한 경우에도 ambient speech 감지 시 ASR로 전달 (fallback)
- `config/stt.yaml`의 `ambient` 섹션으로 설정

### 8) Transcript Store (SQLite, 서버)

- 발화 및 타이밍 메타데이터 저장
- AI 턴 request/response 쌍 저장
- ambient 이벤트 저장

### 9) 운영

- Docker Compose (vLLM 서비스)
- systemd (FastAPI 서버)
- launchd (Mac 메뉴바 앱)
- 각 엔드포인트 health check

## API 엔드포인트

### HTTP

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/healthz` | 서버 상태 확인 (`{"status":"ok"}`) |
| POST | `/v1/voice/turn` | AI 응답 텍스트 → TTS PCM 스트리밍 |
| POST | `/config/hallucinations` | 런타임 환각 문구 등록/업데이트 |

#### POST /v1/voice/turn

```json
{
  "session_id": "mac-1",
  "text": "",
  "response_text": "안녕하세요, 무엇을 도와드릴까요?"
}
```

응답: `Content-Type: audio/pcm` — PCM 스트리밍 (24kHz, int16, mono)

#### POST /config/hallucinations

```json
{
  "exact_phrases": ["시청해주셔서 감사합니다."],
  "contains_phrases": ["subtitle by"],
  "replace": false
}
```

### WebSocket

#### /ws/stt — 음성 스트리밍 (메인)

```text
Client → Server:
  - Binary: PCM 오디오 (16kHz, 16bit, mono)

Server → Client:
  - {"type": "partial", "session_id", "segment_id", "text", "confidence", ...}
  - {"type": "final", "session_id", "segment_id", "text", "confidence", ...}
  - {"type": "ai_request", "session_id", "segment_id", "text"}
  - {"type": "ai_response", "session_id", "segment_id", "text", "error"}
  - {"type": "tts_start", "session_id", "segment_id"}
  - Binary: PCM 오디오 청크 (서버 AI 활성화 시 TTS 출력)
  - {"type": "tts_done", "session_id", "segment_id"}
  - {"type": "ambient", "session_id", "segment_id", "text", "confidence"}
```

> Phase 3: Mac에서 Claude를 사용하는 경우 `ai_request/response`, `tts_*` 이벤트는 서버에서 발생하지 않음.
> Mac VoiceBridge가 `final` 이벤트 수신 후 직접 Claude 호출 → `/v1/voice/turn` 으로 TTS 요청.

#### /ws/tts — TTS 전용 (선택적)

```text
Client → Server:
  - JSON: {"text": "읽을 텍스트"}

Server → Client:
  - {"type": "tts_start", "session_id"}
  - Binary: PCM 오디오 청크 (24kHz, int16, mono)
  - {"type": "tts_done", "session_id"}
```

## 향후 계획

- Speaker diarization
- Wake-word routing
- Full duplex real-time conversation
- TTS voice cloning (내 목소리)
- Emotion/prosody fine controls
