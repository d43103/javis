# Javis

Local-first Korean voice assistant.

4090 서버에서 STT/TTS를 처리하고, Mac에서 Claude LLM + 메뉴바 UI를 실행하는 분리 아키텍처.

## 현재 상태 (Phase 3)

- **서버** (192.168.219.106, RTX 4090 24GB): STT + TTS만 실행
- **Mac 클라이언트**: Python rumps 메뉴바 앱 — 마이크 + Claude LLM + TTS 재생 통합

### 구성 요소

| 구성 요소 | 위치 | 설명 |
|-----------|------|------|
| FastAPI 서버 | 서버 :8765 | WebSocket STT + `/v1/voice/turn` TTS + `/config/hallucinations` |
| vLLM STT | 서버 :8011 | Qwen3-ASR-0.6B (Docker) |
| STT Bridge | 서버 :8021 | qwen_realtime_bridge (vLLM 프록시) |
| vLLM TTS | 서버 :8031 | Qwen3-TTS-0.6B (Docker) |
| AmbientSoundService | 서버 | MIT 오디오 분류 모델 — 환경 음향 이벤트 감지 |
| 메뉴바 앱 | Mac | `src/javis_menubar.py` — rumps UI + VoiceBridge |
| VoiceBridge | Mac | `src/voice_llm_bridge.py` — 마이크 → STT → Claude → TTS |

## Quick Start

### 서버 (4090)

```bash
# 1. vLLM 컨테이너 시작
cd ~/Workspace/projects/javis
docker compose up -d

# 2. Health check
curl http://127.0.0.1:8011/health   # STT
curl http://127.0.0.1:8031/health   # TTS
curl http://127.0.0.1:8765/healthz  # FastAPI

# 3. FastAPI 서버 시작
./.venv/bin/python -m src.javis_stt.server

# systemd 서비스로 실행
systemctl --user restart javis-stt.service
systemctl --user status javis-stt.service --no-pager
```

### Mac 클라이언트

```bash
# 1. 의존성 설치
pip install -r requirements-mac-client.txt

# 2. API 키 설정
export ANTHROPIC_API_KEY=sk-...

# 3. 메뉴바 앱 실행
python -m src.javis_menubar --server ws://192.168.219.106:8765 --session mac-1 --auto-start

# 또는 bridge만 CLI로 실행 (fallback)
python -m src.voice_llm_bridge --server ws://192.168.219.106:8765 --session mac-1
```

### Mac launchd 서비스 등록

```bash
# plist에서 경로와 API 키 수정 후:
cp deploy/com.javis.menubar.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.javis.menubar.plist

# 상태 확인
launchctl list | grep javis

# 로그 확인
tail -f /tmp/javis-menubar.log
tail -f /tmp/javis-menubar-error.log
```

## 서버 설정

### Docker Compose 서비스

| 서비스 | 컨테이너 | 포트 | 모델 | GPU 메모리 |
|--------|----------|------|------|-----------|
| vllm-stt | javis-vllm-stt | 8011 | Qwen/Qwen3-ASR-0.6B | 10% |
| vllm-tts | javis-vllm-tts | 8031 | Qwen/Qwen3-TTS-0.6B | 12% |

> LLM (Qwen3-14B-AWQ)은 Phase 3에서 제거됨. Claude API (Mac)로 대체.

### 서버 설정 파일 (`config/stt.yaml`)

주요 설정:

```yaml
stt:
  provider: qwen3_asr_vllm         # 또는 qwen3_asr_vllm_realtime (WebSocket 방식)
  remote_model: Qwen/Qwen3-ASR-0.6B
  remote_base_url: http://127.0.0.1:8021  # qwen_realtime_bridge 포트

ai:
  enabled: false                   # Phase 3: Mac에서 Claude API 사용 → false 권장
  # enabled: true이면 8041 포트 LLM 필요 (현재 미실행)

tts:
  enabled: true
  base_url: http://127.0.0.1:8031
  model: Qwen/Qwen3-TTS-0.6B
  voice: Sohee
  sample_rate: 24000

ambient:
  enabled: true                    # 환경 음향 이벤트 감지
  model_id: MIT/ast-finetuned-audioset-10-10-0.4593
```

### systemd 서비스 (서버)

```bash
# FastAPI 서버
systemctl --user status javis-stt.service
tail -f /tmp/javis_server.log
```

### VRAM 사용량 (RTX 4090 24GB)

| 서비스 | VRAM |
|--------|------|
| STT (Qwen3-ASR-0.6B) | ~1.5GB |
| TTS (Qwen3-TTS-0.6B) | ~2GB |
| 시스템 | ~0.2GB |
| **합계** | **~3.7GB** |

## 프로젝트 구조

```
src/
  javis_stt/            # 서버 코드 (FastAPI, ASR, TTS, VAD 등)
  javis_tts/            # TTS 스트리밍 서버
  audio_devices.py      # 오디오 장치 목록 + gain 유틸리티
  voice_llm_bridge.py   # Mac: 마이크 → STT → Claude → TTS
  menubar_app.py        # Mac: rumps 메뉴바 앱
  javis_menubar.py      # Mac: 메뉴바 앱 진입점

tests/                  # pytest 테스트

config/stt.yaml         # 서버 런타임 설정
deploy/                 # launchd plist
scripts/                # 벤치마크, 설치 스크립트
```

## 문서

- `docs/architecture.md` — 런타임 아키텍처
- `docs/roadmap.md` — 단계별 로드맵
- `docs/runbook-4090.md` — 서버 운영 가이드
- `docs/spec-stt-realtime-v1.md` — Phase 1 스펙
- `docs/requirements-stt-realtime-v1.md` — Phase 1 요구사항
- `docs/plans/` — 단계별 설계/구현 계획

## 테스트

```bash
pytest tests/ -v
```
