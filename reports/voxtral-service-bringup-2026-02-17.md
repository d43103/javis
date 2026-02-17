# Voxtral Service Bring-up (2026-02-17)

## Goal
- Bring `mistralai/Voxtral-Mini-4B-Realtime-2602` to a serving state on the 4090 host (`100.67.60.57`).

## Final Status
- `voxtral-realtime.service`: active/running under `systemd --user`.
- `/v1/models` responds successfully on `http://127.0.0.1:8011`.
- First real transcription request is currently unstable on vLLM 0.15.1 runtime in this environment.

## Effective Runtime Settings
- Unit file: `/home/d43103/.config/systemd/user/voxtral-realtime.service`
- ExecStart:
  - `/home/d43103/.venvs/voxtral/bin/vllm serve mistralai/Voxtral-Mini-4B-Realtime-2602 --host 0.0.0.0 --port 8011 --gpu-memory-utilization 0.80 --max-model-len 4096 --enforce-eager`

## Compatibility Patch Applied
- File patched on remote host:
  - `/home/d43103/.venvs/voxtral/lib/python3.12/site-packages/vllm/model_executor/models/whisper_causal.py`
- Change:
  - relaxed the rope/sliding-window assertion path for Voxtral encoder init by replacing the hard assertion with a no-op guard.
- Backup created:
  - `/home/d43103/.venvs/voxtral/lib/python3.12/site-packages/vllm/model_executor/models/whisper_causal.py.bak`

## Key Failure Modes Observed and Resolved
- Startup memory target too high (`gpu_memory_utilization` over available free VRAM).
- Rope/sliding-window assertion during model init.
- CUDA graph capture failure (`operation not permitted when stream is capturing`) fixed operationally by `--enforce-eager`.

## Verification Evidence
- Service status showed active process with `VLLM::EngineCore` child.
- Endpoint check returned model list including:
  - `mistralai/Voxtral-Mini-4B-Realtime-2602`
- Sample response included `"max_model_len": 4096`.

## Runtime Limitation Found During Smoke Request
- Request tested:
  - `POST /v1/audio/transcriptions` with `/tmp/test-01.wav`
- Observed behavior:
  - HTTP path returns, but engine hits fatal assertion and process restarts.
- Root error in log:
  - `AssertionError: For streaming you must provide a multimodal_embedding at every step.`
- Impact:
  - Service can recover and become healthy again (`/v1/models`), but transcription request is not reliably usable in current wheel/runtime combination.

## Notes
- This is a PoC stabilization path in the current environment (vLLM 0.15.1 in user venv).
- If upgrading vLLM to a compatible nightly/stable build that natively supports this model path, remove the local site-packages patch and re-validate.
