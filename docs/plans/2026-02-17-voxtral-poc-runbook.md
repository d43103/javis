# Voxtral PoC Runbook

## Goal
- Compare current `faster-whisper` pipeline (`turbo`) with `mistralai/Voxtral-Mini-4B-Realtime-2602` on the same Korean recordings.
- Decide go/no-go for production migration based on transcript quality, hallucination frequency, and latency.

## Readiness Snapshot
- GPU: RTX 4090 24GB (sufficient)
- Disk free: ~255GB (sufficient)
- Missing packages on server: `vllm`, `mistral_common`, `librosa`, `soundfile`, `soxr`

## Baseline Capture (Current System)
1. Keep current production server running with `config/stt.yaml`.
2. Run recordings through baseline benchmark:
   - `python3 scripts/benchmark_stt_models.py --inputs "recordings/*.wav" --models turbo --device cuda --compute-type float16 --output reports/stt-baseline-turbo.md --json-output reports/stt-baseline-turbo.json`
3. Copy transcript text into a manifest file based on `config/voxtral_poc_manifest.sample.json`.

## Voxtral Setup (PoC-only)
1. Install runtime dependencies (server-side, PoC environment):
   - `uv pip install -U vllm --torch-backend=auto --extra-index-url https://wheels.vllm.ai/nightly`
   - `uv pip install mistral_common soxr librosa soundfile`
2. Serve Voxtral with vLLM:
   - `VLLM_DISABLE_COMPILE_CACHE=1 vllm serve mistralai/Voxtral-Mini-4B-Realtime-2602 --compilation_config '{"cudagraph_mode":"PIECEWISE"}'`
3. Verify endpoint:
   - Check `/v1/realtime` route in vLLM logs.

## Voxtral Transcript Collection
- Use vLLM realtime client example (audio file mode) to transcribe each recording.
- Paste each transcript into the `systems.voxtral-mini-4b-realtime` field in your manifest.

## Scoring and Comparison
1. Run local scoring:
   - `python3 scripts/score_stt_outputs.py --manifest config/voxtral_poc_manifest.json --config config/stt.yaml --output reports/voxtral-poc-comparison.md --json-output reports/voxtral-poc-comparison.json`
2. Inspect these metrics:
   - CER (lower is better)
   - Hallucination hits (lower is better)
   - Subjective completeness for long sentences (missing leading words)

## Go / No-Go Criteria
- Go if all are met:
  - Voxtral CER <= turbo CER on long-form samples
  - Hallucination hits reduced by >= 30%
  - Realtime latency acceptable for your UX target
  - No major regression in Korean sentence completeness
- No-Go if any are true:
  - Frequent English drift or semantic collapse
  - More leading-word clipping than current pipeline
  - Operational complexity unacceptable for current phase

## Migration Scope (if Go)
- Replace `faster-whisper` inference call path with Voxtral realtime ingestion path.
- Keep existing hallucination controls and merge policy logic as compatibility guards.
- Add feature flag to allow rollback to current `turbo` pipeline.
