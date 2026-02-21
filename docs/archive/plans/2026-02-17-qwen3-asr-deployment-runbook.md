# Qwen3-ASR Deployment Runbook

## Goal
- Deploy `Qwen/Qwen3-ASR-1.7B` on the 4090 host with vLLM Docker.
- Provide a stable `/v1/audio/transcriptions` endpoint for smoke testing and PoC scoring.

## Target Host
- `100.67.60.57`

## Prerequisites
- NVIDIA driver and Docker GPU runtime are already available.
- Docker image pull is allowed from the host.
- Hugging Face access token is available as `HF_TOKEN` when needed for gated pulls.

## 1) Pre-flight Checks

Run on the server:

```bash
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader
docker --version
```

Expected:
- GPU is visible.
- Docker is available.

## 2) Stop and Remove Old Voxtral Container

```bash
sudo docker rm -f voxtral-nightly 2>/dev/null || true
```

## 3) Run Qwen3-ASR Container

```bash
sudo docker run -d --name qwen3-asr \
  --gpus all \
  --runtime=nvidia \
  --ipc=host \
  --restart unless-stopped \
  -p 8011:8000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -e HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
  --entrypoint /bin/bash \
  vllm/vllm-openai:nightly \
  -lc "
set -e
rm -f /etc/ld.so.conf.d/cuda-compat.conf || true
find /etc/ld.so.conf.d -type f -name '*.conf' -exec sed -i '/cuda-.*compat/d' {} + || true
ldconfig
export LD_LIBRARY_PATH=/usr/local/nvidia/lib64:/usr/local/nvidia/lib:/usr/lib/x86_64-linux-gnu
uv pip install --system 'vllm[audio]'
vllm serve Qwen/Qwen3-ASR-1.7B \
  --host 0.0.0.0 \
  --port 8000 \
  --enforce-eager \
  --gpu-memory-utilization 0.65 \
  --max-model-len 4096 \
  --max-num-seqs 4 \
  --max-num-batched-tokens 1024
"
```

Notes:
- `--restart unless-stopped` keeps service alive across host reboot.
- `cuda-compat` cleanup is applied at container start to avoid CUDA compatibility-path conflicts observed during Voxtral bring-up.

## 4) Verify Service Health

```bash
sudo docker logs -f qwen3-asr
```

In another shell:

```bash
curl -sS http://127.0.0.1:8011/v1/models
```

Expected:
- Model list includes `Qwen/Qwen3-ASR-1.7B`.

## 5) Smoke Test (Audio Transcription)

From local workspace:

```bash
scp recordings/test-01.wav 100.67.60.57:/tmp/test-01.wav
ssh 100.67.60.57 "curl -sS -X POST http://127.0.0.1:8011/v1/audio/transcriptions \
  -F model=Qwen/Qwen3-ASR-1.7B \
  -F file=@/tmp/test-01.wav"
```

Expected:
- JSON response with transcript text.

## 6) Operational Commands

```bash
# status
sudo docker ps --filter name=qwen3-asr

# restart
sudo docker restart qwen3-asr

# stop
sudo docker stop qwen3-asr

# remove
sudo docker rm -f qwen3-asr
```

## 7) Troubleshooting

### A. CUDA initialization errors (803 / 804)

Symptoms:
- `torch.cuda.is_available() == False`
- `cudaGetDeviceCount` errors in container logs

Actions:
- Ensure no mixed NVIDIA driver branches are installed (single branch only).
- Confirm Docker NVIDIA runtime is configured:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

- Ensure `cuda-compat` paths are not injected in container startup (already handled in run command).

### B. OOM during startup

Actions:
- Lower memory and concurrency flags:
  - `--gpu-memory-utilization 0.55`
  - `--max-model-len 2048`
  - `--max-num-seqs 2`
  - `--max-num-batched-tokens 512`

### C. Slow cold start

Reason:
- Container installs `vllm[audio]` on each fresh start.

Action:
- Bake a custom image with dependencies preinstalled for faster restarts.

## 8) Next Step for PoC Scoring
- Run transcription on all test recordings.
- Fill the manifest and score against turbo:

```bash
python3 scripts/score_stt_outputs.py \
  --manifest config/voxtral_poc_manifest.json \
  --config config/stt.yaml \
  --output reports/stt-output-comparison.md \
  --json-output reports/stt-output-comparison.json
```

## 9) RTX 4090 Recommended Profile (Stable Mixed Workload)

When Qwen3-ASR and Ollama run on the same 4090, keep Qwen conservative to avoid VRAM pressure:

```bash
vllm serve Qwen/Qwen3-ASR-1.7B \
  --host 0.0.0.0 \
  --port 8000 \
  --gpu-memory-utilization 0.55 \
  --max-model-len 3072 \
  --max-num-seqs 2 \
  --max-num-batched-tokens 768
```

Notes:
- This profile favors stability over peak throughput.
- If 4090 is dedicated to ASR only, move up gradually:
  - `--gpu-memory-utilization 0.60`
  - `--max-num-seqs 3`
