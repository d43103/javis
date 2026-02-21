# Requirements Definition - Realtime STT V1

## Document Goal

Define implementable requirements for Phase 1 realtime Speech-to-Text.

## Functional Requirements

### FR-001 Audio Input

- System shall capture mono microphone audio at 16kHz continuously.
- System shall allow explicit input device selection.

### FR-002 Voice Activity Detection

- System shall detect speech/non-speech regions using VAD.
- System shall skip non-speech audio from STT inference.

### FR-003 Realtime Transcription

- System shall produce partial transcript updates during speech.
- System shall produce final transcript after utterance end.

### FR-004 Korean Language Priority

- System shall prioritize Korean transcription quality.
- System shall support mixed Korean/English terms without process failure.

### FR-005 Persistence

- System shall store final transcripts with `started_at`, `ended_at`, `text`, and optional confidence.
- System shall keep data in local `sqlite` storage.

### FR-006 Session and Segment Tracking

- System shall assign session and segment identifiers.
- System shall preserve ordering of finalized segments.

### FR-007 Fault Handling

- System shall auto-restart after crash using supervisor policy.
- System shall recover and continue appending transcripts after restart.

### FR-008 Local LLM Handoff

- System shall send each finalized transcript segment to a local AI gateway.
- System shall receive AI text responses and persist request/response pairs.
- System shall continue STT even if AI response is delayed or fails.

### FR-009 Text Conversation Session

- System shall maintain session-scoped context window for text conversation.
- System shall support configurable max context turns and truncation policy.

## Non-Functional Requirements

### NFR-001 Latency

- Partial transcript p95 latency shall be <= 0.7s.
- Final transcript p95 latency shall be <= 1.8s.

### NFR-001A AI Turn Latency

- AI text response first token p95 latency shall be <= 2.0s under normal load.

### NFR-002 Availability

- Service shall run >= 8 hours continuously without manual intervention.

### NFR-003 Data Integrity

- Final transcript loss after abnormal termination shall be < 1 segment per forced crash test.

### NFR-004 Resource Behavior

- STT service shall stay stable under concurrent Ollama usage by applying bounded queues and backpressure.

### NFR-005 Privacy

- Audio and transcript processing shall remain local network/local storage only.
- No cloud API dependency shall be required for core STT operation.

## Interfaces

### Inbound

- Microphone PCM stream (16kHz mono)

### Outbound

- Realtime event stream (`partial`, `final`)
- Persistent final transcripts (`sqlite`)
- AI request/response event stream (`ai_request`, `ai_response`)

## Acceptance Tests

### AT-001 Basic Realtime

- Given live Korean speech for 5 minutes,
- when service is running,
- then partial and final transcripts are emitted with timestamps.

### AT-002 Latency Target

- Given normal GPU load,
- when user speaks 20 short utterances,
- then p95 latency meets NFR-001.

### AT-003 Restart Safety

- Given forced process termination,
- when supervisor restarts service,
- then transcription resumes and DB remains readable.

### AT-004 Long Run

- Given continuous operation for 8 hours,
- when periodic speech samples are injected,
- then finalized transcripts remain ordered and persisted.

### AT-005 AI Text Loop

- Given finalized Korean transcript segments,
- when local AI gateway is available,
- then AI text responses are returned and stored with matching session IDs.

## Explicitly Excluded in V1

- Speaker diarization
- TTS synthesis and playback
- Voice conversation orchestration (barge-in, interruption, duplex audio)
