# 4090 Runbook

## Objective

Run STT + LLM + TTS on RTX 4090 (24GB) using Docker Compose with vLLM.

Target host: `100.67.60.57`

## Current State (Phase 2)

- 3x vLLM containers via Docker Compose
- STT: Qwen3-ASR-0.6B (:8011)
- LLM: Qwen3-14B-AWQ (:8041)
- TTS: Qwen3-TTS-0.6B (:8031)
- FastAPI server (:8765) with Silero-VAD (CPU)

## Docker Compose Deployment

### 1) Model Download

```bash
huggingface-cli download Qwen/Qwen3-ASR-0.6B
huggingface-cli download Qwen/Qwen3-14B-AWQ
huggingface-cli download Qwen/Qwen3-TTS-0.6B
```

### 2) Start vLLM Services

```bash
cd ~/Workspace/projects/javis
docker compose up -d
docker compose ps
```

### 3) Health Checks

```bash
curl http://127.0.0.1:8011/health   # STT
curl http://127.0.0.1:8031/health   # TTS
curl http://127.0.0.1:8041/health   # LLM
curl http://127.0.0.1:8765/healthz  # FastAPI
```

### 4) VRAM Verification

```bash
nvidia-smi --query-gpu=memory.used --format=csv,noheader
# Expected: ~13.7GB
```

### 5) Start FastAPI Server

```bash
cd ~/Workspace/projects/javis
./.venv/bin/python -m src.javis_stt.server
```

Or via systemd:

```bash
systemctl --user restart javis-stt.service
systemctl --user status javis-stt.service --no-pager
```

## E2E Verification

### STT Quality

```bash
python3 scripts/benchmark_asr.py
```

### LLM Tool Calling

```bash
curl -X POST http://127.0.0.1:8041/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-14B-AWQ","messages":[{"role":"user","content":"hello"}]}'
```

### TTS

```bash
curl -X POST http://127.0.0.1:8031/v1/audio/speech \
  -d '{"model":"Qwen/Qwen3-TTS-0.6B","input":"test","voice":"Sohee"}' > test.pcm
```

### Full Pipeline

```bash
python3 scripts/mic_stream_client.py --session-id e2e-test --show-events
# Expected: partial -> final -> ai_response -> tts_start -> (audio) -> tts_done
```

## Rollback

Revert `config/stt.yaml` to Phase 1 settings:

```yaml
stt:
  remote_model: Qwen/Qwen3-ASR-1.7B
ai:
  api_format: ollama
  base_url: http://127.0.0.1:11434
  model: phi4:latest
tts:
  enabled: false
```

Then restart FastAPI and bring back old Docker container + Ollama.

## Troubleshooting

### OOM on GPU

Reduce LLM gpu-memory-utilization:

```bash
# In docker-compose.yaml, change vllm-llm command:
# --gpu-memory-utilization=0.45
docker compose up -d vllm-llm
```

### Container Won't Start

```bash
docker compose logs vllm-stt --tail 50
docker compose logs vllm-llm --tail 50
docker compose logs vllm-tts --tail 50
```

### Security and Privacy

- Store transcripts locally only
- Restrict server access to private network
- Keep voice reference files encrypted at rest if possible
