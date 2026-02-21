# Javis

Local-first Korean voice assistant. RTX 4090 서버에서 TTS/STT를 처리하고, Mac에서 OpenClaw Talk Mode로 음성 대화를 운영한다.

## 현재 상태

**Primary 음성 인터페이스**: OpenClaw Talk Mode (Mac mini)
**TTS 서버**: `javis-tts` (192.168.219.106:8031, Qwen3-TTS)
**STT**: macOS SFSpeechRecognizer (ko-KR) — 서버 STT 미사용

## 서비스 구성

| 서비스 | 위치 | 포트 | 역할 |
|--------|------|------|------|
| `javis-tts` systemd | 서버 (192.168.219.106) | 8031 | Qwen3-TTS 스트리밍 (OpenAI + ElevenLabs 호환) |
| vLLM TTS | 서버 Docker | 8031 | Qwen3-TTS-0.6B |
| vLLM STT | 서버 Docker | 8011 | Qwen3-ASR-0.6B |
| `javis-stt` FastAPI | 서버 | 8765 | ASR + VAD + voice_hub WebSocket |
| OpenClaw gateway | Mac mini | 18789 | 에이전트 오케스트레이션 |
| OpenClaw app | Mac mini | — | Talk Mode UI (MenuBarExtra) |

## 음성 대화 흐름 (OpenClaw Talk Mode)

```
[마이크] → SFSpeechRecognizer (ko-KR)
         → SpeakerVerifier (화자 인증, FluidAudio/WeSpeaker)
         → openclaw-gateway (ws://localhost:18789)
         → voice agent (Claude sonnet-4-6)
         → OpenAITTSPlayer → POST /v1/audio/speech
         → javis-tts (192.168.219.106:8031, pcm_24000)
         → AVAudioEngine → [스피커]
```

## 서버 운영

자세한 내용: [`docs/runbook-4090.md`](docs/runbook-4090.md)

```bash
# TTS 서버 (javis-tts systemd)
ssh 192.168.219.106
systemctl --user status javis-tts.service
systemctl --user restart javis-tts.service
tail -f /tmp/javis_tts.log

# Docker 컨테이너 (vLLM)
docker compose ps
docker compose up -d
curl http://127.0.0.1:8031/health   # TTS
curl http://127.0.0.1:8011/health   # STT
```

## OpenClaw Talk Mode 설정

```bash
# Mac UserDefaults (ai.openclaw.mac.debug)
defaults write ai.openclaw.mac.debug "openclaw.ttsBaseUrl" "http://192.168.219.106:8031"
defaults write ai.openclaw.mac.debug "openclaw.outputFormat" "pcm_24000"
defaults write ai.openclaw.mac.debug "openclaw.talkAgentId" "voice"
defaults write ai.openclaw.mac.debug "openclaw.talkEnabled" -bool true
defaults write ai.openclaw.mac.debug "openclaw.speakerVerification.threshold" "0.35"

# 현재 설정 확인
defaults read ai.openclaw.mac.debug | grep -E "talk|tts|speaker|voice"
```

## 프로젝트 구조

```
src/
  javis_tts/
    tts_streaming_server.py     # TTS 스트리밍 서버 (OpenAI + ElevenLabs 호환)
  javis_stt/
    server.py                   # FastAPI 메인 (STT WebSocket + voice_hub)
    asr_service.py              # ASR (Qwen3-ASR via vLLM)
    vad_service.py              # VAD (Silero)
    conversation_engine.py      # 대화 컨텍스트
    session_manager.py          # 세션 관리
  voice_hub.py                  # WebSocket 허브 (다중 클라이언트)
  voice_llm_bridge.py           # LLM 브릿지 (레거시)
  audio_devices.py              # 오디오 장치 유틸리티

ios/JavisClient/                # Swift iOS/macOS 클라이언트 (보조)
  HubConnection.swift           # WebSocket 연결
  Models.swift                  # 메시지 모델
  iOS/                          # iOS 앱
  macOS/                        # macOS 메뉴바 앱

docker-compose/                 # vLLM Docker 설정
config/stt.yaml                 # 서버 런타임 설정
tests/                          # pytest 테스트
docs/                           # 문서
```

## 문서

- [`docs/architecture.md`](docs/architecture.md) — 런타임 아키텍처 (현재)
- [`docs/roadmap.md`](docs/roadmap.md) — 단계별 로드맵
- [`docs/runbook-4090.md`](docs/runbook-4090.md) — 서버 운영 가이드
- [`CLAUDE.md`](CLAUDE.md) — 개발 컨텍스트 (AI 코딩 어시스턴트용)

## 테스트

```bash
pytest tests/ -v
```
