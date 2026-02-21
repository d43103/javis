# STT Realtime V1 Spec

## Purpose

Build a local-first, realtime Korean Speech-to-Text service that continuously converts microphone audio into text.

## Product Scope

- In scope:
  - Realtime streaming transcription from microphone input
  - Korean-first recognition
  - Text handoff to local LLM service for text-only conversation
  - Timestamped transcript persistence
  - Long-running operation with restart safety
- Out of scope (V1):
  - TTS output
  - Voice turn management (barge-in, interruption)
  - Speaker diarization
  - Wake word

## Realtime Definition (V1)

- Partial transcript latency target (p95): <= 0.7 seconds from speech to first text fragment
- Final segment latency target (p95): <= 1.8 seconds from end-of-utterance to finalized text
- Continuous operation target: >= 8 hours without manual restart

## Low-Latency Profile (Default)

- Decoder profile: low-latency first, accuracy second
- Segment policy: short voiced chunks + immediate partial emission
- Backpressure policy: drop stale partial updates, never drop final events

## System Context

- Inference host: `100.67.60.57` (RTX 4090)
- STT stack:
  - `faster-whisper` (`large-v3`)
  - `silero-vad`
  - Python service + `sqlite`

## Data Flow

```text
Mic Stream -> Frame Buffer -> VAD -> Segment Queue -> STT -> Partial/Final Text -> SQLite
                                                          -> Final Text -> LLM Gateway -> AI Text Reply
```

## Output Model

Two transcript event types:

1. `partial`: interim text for realtime UX
2. `final`: committed text used for storage and downstream logic

Suggested event shape:

```json
{
  "type": "partial|final",
  "session_id": "string",
  "segment_id": "string",
  "started_at": "ISO-8601",
  "ended_at": "ISO-8601",
  "text": "string",
  "confidence": 0.0
}
```

## Operational Constraints

- Service must degrade gracefully when GPU pressure is high.
- When 18-19GB Ollama models are loaded, STT still prioritizes queue stability over throughput.
- Transcript persistence must survive process restarts.

## Success Criteria

- Korean realtime transcription works on live microphone input
- Partial and final transcript events are emitted correctly
- Final transcript is delivered to local AI endpoint and AI text reply is returned
- Timestamped final transcripts are stored without data loss
- Restarting process does not corrupt transcript DB
