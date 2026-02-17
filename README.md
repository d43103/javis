# javis

Local-first voice assistant project.

This repository starts with a strict two-phase roadmap:

1. Phase 1: Speech-to-Text (STT) only
2. Phase 2: Text-to-Speech (TTS) with personal voice style

Current deployment target is an RTX 4090 server.

## Scope

- In scope now: Realtime continuous Korean speech recognition to text and local AI text conversation.
- Deferred: Speaker diarization, wake word, voice output conversation loop.
- Next phase: Korean TTS + voice style cloning.

## Decision Summary

- Compute node: `100.67.60.57` (SSH reachable)
- GPU: `RTX 4090 24GB`
- Existing Ollama models are present and large-model VRAM pressure is expected.
- Chosen Phase 1 stack:
  - `faster-whisper` (`large-v3`)
  - `silero-vad` for speech chunking
  - Python service for ingest/transcribe/store
- Chosen Phase 2 stack:
  - `MeloTTS` for Korean base voice
  - `OpenVoice v2` for voice style transfer

## Documents

- `docs/roadmap.md`: What will be built in each phase
- `docs/architecture.md`: Runtime architecture and boundaries
- `docs/runbook-4090.md`: Server setup and operations checklist
- `docs/spec-stt-realtime-v1.md`: Phase 1 product spec
- `docs/requirements-stt-realtime-v1.md`: Phase 1 requirements definition
- `docs/plans/2026-02-16-stt-realtime-ai-text-loop-implementation.md`: Phase 1 implementation plan

## Quick Run

- Start server on 4090 host:
  - `cd ~/Workspace/projects/javis`
  - `./.venv/bin/python -m src.javis_stt.server`
- Health check:
  - `curl http://127.0.0.1:8765/healthz`
- Server log tail (text conversion 확인):
  - `tail -f /tmp/javis_server.log`
- Mac mic client setup:
  - `python3 -m pip install -r requirements-mac-client.txt`
  - `python3 scripts/mic_stream_client.py --server ws://100.67.60.57:8765 --session-id mac-1`
  - `python3 scripts/mic_stream_client.py --server ws://100.67.60.57:8765 --session-id mac-1 --log-file logs/client.log`
  - input device list: `python3 scripts/mic_stream_client.py --list-devices`
  - built-in mic force: `python3 scripts/mic_stream_client.py --server ws://100.67.60.57:8765 --session-id mac-1 --device "MacBook" --log-file logs/client.log`
- WAV smoke run from Mac:
  - `python3 scripts/mic_stream_client.py --server ws://100.67.60.57:8765 --session-id wav-smoke --wav sample.wav`

## Service Mode

- 4090 server (`systemd --user`):
  - `systemctl --user status javis-stt.service`
  - log tail: `tail -f /tmp/javis_server.log`
- Mac client (`launchd`):
  - `launchctl print gui/$(id -u)/ai.javis.mic-client`
  - log tail: `tail -f ~/Workspace/projects/javis/logs/client-service.log`
