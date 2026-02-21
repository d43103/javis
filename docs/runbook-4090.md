# 4090 서버 Runbook

## 개요

RTX 4090 (24GB)에서 TTS + STT를 Docker Compose + vLLM로 운영.

서버: `192.168.219.106`

## 서비스 현황

| 서비스 | 컨테이너/systemd | 포트 | 모델 | GPU |
|--------|-----------------|------|------|-----|
| **javis-tts** | systemd | 8031 | Qwen3-TTS-0.6B | ~8% |
| vLLM TTS | javis-vllm-tts (Docker) | 8031 | Qwen3-TTS-0.6B | 12% |
| vLLM STT | javis-vllm-stt (Docker) | 8011 | Qwen3-ASR-0.6B | 10% |
| javis-stt | FastAPI (systemd) | 8765 | — | CPU |

> **javis-tts** (port 8031)가 핵심 서비스. OpenClaw Talk Mode가 이 서버로 TTS 요청.

## javis-tts 운영 (핵심)

```bash
# 상태 확인
systemctl --user status javis-tts.service

# 재시작
systemctl --user restart javis-tts.service

# 로그
tail -f /tmp/javis_tts.log

# 헬스체크
curl http://127.0.0.1:8031/health

# TTS 테스트
curl -s -X POST http://127.0.0.1:8031/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","input":"안녕하세요","voice":"Sohee","response_format":"pcm"}' \
  | wc -c   # 0이 아니면 정상
```

## Docker Compose (vLLM)

### 초기 모델 다운로드

```bash
huggingface-cli download Qwen/Qwen3-ASR-0.6B
huggingface-cli download Qwen/Qwen3-TTS-0.6B
```

### 운영

```bash
cd ~/Workspace/projects/javis
docker compose up -d
docker compose ps

# 헬스체크
curl http://127.0.0.1:8011/health   # STT vLLM
curl http://127.0.0.1:8031/health   # TTS vLLM

# VRAM 확인
nvidia-smi --query-gpu=memory.used --format=csv,noheader
# 예상: ~3.7GB
```

## javis-stt FastAPI 서버

```bash
# systemd
systemctl --user restart javis-stt.service
systemctl --user status javis-stt.service --no-pager

# 직접 실행
cd ~/Workspace/projects/javis
./.venv/bin/python -m src.javis_stt.server

# 헬스체크
curl http://127.0.0.1:8765/healthz

# 로그
tail -f /tmp/javis_server.log
```

## 설정 파일 (`config/stt.yaml`)

### STT

```yaml
stt:
  provider: qwen3_asr_vllm
  remote_model: Qwen/Qwen3-ASR-0.6B
  remote_base_url: http://127.0.0.1:8021
  language: ko
```

### TTS

```yaml
tts:
  enabled: true
  base_url: http://127.0.0.1:8031
  model: Qwen/Qwen3-TTS-0.6B
  voice: Sohee
  sample_rate: 24000
  streaming: true
```

### AI

```yaml
ai:
  enabled: false   # LLM은 openclaw voice agent가 담당. 서버에서 직접 쓰지 않음.
```

### VAD

```yaml
vad:
  min_silence_duration_ms: 420
  speech_pad_ms: 520
```

### Ambient Sound

```yaml
ambient:
  enabled: true
  model_id: MIT/ast-finetuned-audioset-10-10-0.4593
  confidence_threshold: 0.45
  top_k: 2
  min_emit_interval_seconds: 1.5
```

## 검증

### TTS (javis-tts, OpenAI 호환)

```bash
curl -X POST http://127.0.0.1:8031/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","input":"안녕하세요","voice":"Sohee","response_format":"pcm"}' \
  > /tmp/test.pcm
aplay -r 24000 -f S16_LE -c 1 /tmp/test.pcm
```

### TTS (ElevenLabs 호환)

```bash
curl -X POST http://127.0.0.1:8031/v1/text-to-speech/Sohee/stream \
  -H "Content-Type: application/json" \
  -d '{"text":"안녕하세요"}' > /tmp/test.pcm
```

### STT

```bash
# vLLM STT 헬스
curl http://127.0.0.1:8011/health

# 벤치마크 (Mac에서)
python3 scripts/benchmark_asr.py
```

## Docker Compose 구성 요약

```yaml
services:
  vllm-stt:
    container_name: javis-vllm-stt
    ports: ["8011:8000"]
    command: [--model=Qwen/Qwen3-ASR-0.6B, --gpu-memory-utilization=0.10]

  vllm-tts:
    container_name: javis-vllm-tts
    ports: ["8031:8000"]
    command: [--model=Qwen/Qwen3-TTS-0.6B, --gpu-memory-utilization=0.12]
```

## 로그

```bash
tail -f /tmp/javis_server.log                    # FastAPI
tail -f /tmp/javis_tts.log                       # javis-tts
docker compose logs vllm-stt --tail 50 -f
docker compose logs vllm-tts --tail 50 -f
```

## Troubleshooting

### TTS 서버 응답 없음

```bash
curl http://127.0.0.1:8031/health
systemctl --user restart javis-tts.service
docker compose restart vllm-tts
```

### GPU OOM

```bash
# docker-compose.yaml에서 gpu-memory-utilization 낮추기
docker compose up -d
```

### 컨테이너 시작 실패

```bash
docker compose logs vllm-stt --tail 50
docker compose logs vllm-tts --tail 50
```
