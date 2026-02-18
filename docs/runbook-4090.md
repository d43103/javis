# 4090 서버 Runbook

## 개요

RTX 4090 (24GB)에서 STT + TTS를 Docker Compose + vLLM로 운영.

서버: `192.168.219.106`

## 현재 상태 (Phase 3)

| 서비스 | 컨테이너 | 포트 | 모델 | GPU |
|--------|----------|------|------|-----|
| STT | javis-vllm-stt | 8011 | Qwen/Qwen3-ASR-0.6B | 10% |
| TTS | javis-vllm-tts | 8031 | Qwen/Qwen3-TTS-0.6B | 12% |
| FastAPI | — | 8765 | — | CPU |

> LLM (Qwen3-14B-AWQ)은 Phase 3에서 제거됨. Mac에서 Claude API로 대체.

## Docker Compose 배포

### 1) 모델 다운로드

```bash
huggingface-cli download Qwen/Qwen3-ASR-0.6B
huggingface-cli download Qwen/Qwen3-TTS-0.6B
```

### 2) 서비스 시작

```bash
cd ~/Workspace/projects/javis
docker compose up -d
docker compose ps
```

### 3) Health Check

```bash
curl http://127.0.0.1:8011/health   # STT
curl http://127.0.0.1:8031/health   # TTS
curl http://127.0.0.1:8765/healthz  # FastAPI
```

### 4) VRAM 확인

```bash
nvidia-smi --query-gpu=memory.used --format=csv,noheader
# 예상: ~3.7GB
```

### 5) FastAPI 서버 시작

```bash
cd ~/Workspace/projects/javis
./.venv/bin/python -m src.javis_stt.server
```

systemd 서비스:

```bash
systemctl --user restart javis-stt.service
systemctl --user status javis-stt.service --no-pager
```

## 설정 파일 (`config/stt.yaml`)

### STT 설정

```yaml
stt:
  provider: qwen3_asr_vllm
  remote_model: Qwen/Qwen3-ASR-0.6B
  remote_base_url: http://127.0.0.1:8021
  remote_timeout_seconds: 60.0
  language: ko
```

### AI 설정

```yaml
ai:
  enabled: true                      # Mac bridge 사용 시 false 권장
  api_format: openai
  base_url: http://127.0.0.1:8041   # Phase 3: LLM 컨테이너 없음. enabled: false로 설정 권장
  model: Qwen/Qwen3-14B-AWQ
  timeout_seconds: 20
  idle_flush_seconds: 2.1
  idle_flush_requires_sentence_end: true
  max_utterance_hold_seconds: 6.0
```

> **주의:** Phase 3에서 LLM은 Mac의 Claude API가 담당. 서버에 LLM 컨테이너가 없으므로
> Mac bridge를 사용하는 경우 `ai.enabled: false`로 설정하는 것을 권장.
> `enabled: true`이면 서버가 8041 포트로 요청을 보내지만 응답 실패 후 Mac bridge가 TTS를 처리함.

### TTS 설정

```yaml
tts:
  enabled: true
  provider: vllm
  base_url: http://127.0.0.1:8031
  model: Qwen/Qwen3-TTS-0.6B
  voice: Sohee
  sample_rate: 24000
  streaming: true
  chunk_size: 4096
  # voice_clone_ref_audio: /path/to/ref.wav  # 음성 클로닝 (선택)
  # voice_clone_ref_text: "참조 오디오 텍스트"
```

### VAD 설정

```yaml
vad:
  min_silence_duration_ms: 420
  speech_pad_ms: 520
```

### Ambient Sound 설정

```yaml
ambient:
  enabled: true
  model_id: MIT/ast-finetuned-audioset-10-10-0.4593  # 오디오 분류 모델
  confidence_threshold: 0.45   # 이벤트 발화 임계값
  top_k: 2                     # 상위 K개 카테고리 반환
  min_emit_interval_seconds: 1.5  # 동일 이벤트 최소 재발화 간격
```

> AmbientSoundService는 VAD가 음성으로 분류하지 못한 구간에서도 박수, 음악, 알람 등
> 환경 음향을 감지하여 `ambient` 이벤트로 전송합니다. ambient speech 감지 시 VAD 결과를 override하여
> ASR 파이프라인으로 전달합니다.

## Docker Compose 구성 (`docker-compose.yaml`)

```yaml
services:
  vllm-stt:
    image: vllm/vllm-openai:latest
    container_name: javis-vllm-stt
    runtime: nvidia
    ports: ["8011:8000"]
    command:
      - --model=Qwen/Qwen3-ASR-0.6B
      - --gpu-memory-utilization=0.10
      - --max-model-len=4096
      - --dtype=float16
      - --trust-remote-code

  vllm-tts:
    image: vllm/vllm-openai:latest
    container_name: javis-vllm-tts
    runtime: nvidia
    ports: ["8031:8000"]
    command:
      - --model=Qwen/Qwen3-TTS-0.6B
      - --gpu-memory-utilization=0.12
      - --max-model-len=4096
      - --dtype=float16
      - --trust-remote-code
```

> `vllm-llm` 서비스는 Phase 3에서 제거됨.

## E2E 검증

### STT

```bash
python3 scripts/benchmark_asr.py
```

### TTS (vLLM 직접)

```bash
curl -X POST http://127.0.0.1:8031/v1/audio/speech \
  -d '{"model":"Qwen/Qwen3-TTS-0.6B","input":"안녕하세요","voice":"Sohee"}' > test.pcm
```

### TTS (FastAPI /v1/voice/turn)

```bash
curl -X POST http://127.0.0.1:8765/v1/voice/turn \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test","text":"","response_text":"안녕하세요"}' > test.pcm
aplay -r 24000 -f S16_LE -c 1 test.pcm  # 서버에서 재생 확인
```

### 환각 문구 등록 API

```bash
curl -X POST http://127.0.0.1:8765/config/hallucinations \
  -H "Content-Type: application/json" \
  -d '{"exact_phrases":["테스트 환각 문구"], "contains_phrases":[], "replace":false}'
# 예상: {"status":"ok","exact_count":N,"contains_count":M}
```

### Mac 메뉴바 앱

```bash
# Mac에서 실행
python -m src.javis_menubar --server ws://192.168.219.106:8765 --session mac-1 --auto-start
# 예상: 메뉴바 J 아이콘 → Start → 음성 인식 → AI 응답 → TTS 재생
```

## 로그 확인

### 서버

```bash
tail -f /tmp/javis_server.log                     # FastAPI
docker compose logs vllm-stt --tail 50 -f         # STT
docker compose logs vllm-tts --tail 50 -f         # TTS
```

### Mac

```bash
tail -f /tmp/javis-menubar.log                    # stdout
tail -f /tmp/javis-menubar-error.log              # stderr
```

## Troubleshooting

### GPU OOM

```bash
# gpu-memory-utilization 조정
# docker-compose.yaml에서 값 낮추기
docker compose up -d vllm-stt
```

### 컨테이너 시작 실패

```bash
docker compose logs vllm-stt --tail 50
docker compose logs vllm-tts --tail 50
```

### Mac에서 서버 연결 실패

```bash
curl http://192.168.219.106:8765/healthz
# {"status":"ok"} 이 나와야 함
```

### Mac 오디오 출력 없음

```bash
python3 -c "import sounddevice; print(sounddevice.query_devices())"
```

### Claude API 타임아웃

- `ANTHROPIC_API_KEY` 환경변수 확인
- `max_tokens` 256 → 128로 줄이기

## 보안

- 트랜스크립트는 로컬 SQLite에만 저장
- 서버 접근은 프라이빗 네트워크로 제한
- API 키는 환경변수 또는 launchd plist에 설정
