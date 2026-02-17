# Realtime STT + AI Text Loop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a production-ready Phase 1 pipeline that performs realtime Korean STT on a remote RTX 4090 and sends finalized text to local AI for text conversation.

**Architecture:** Client streams microphone PCM to the 4090 server over WebSocket. The server applies VAD and Whisper `large-v3` (accuracy profile) to emit partial/final transcript events. Final transcripts are persisted to SQLite and forwarded to a local AI gateway, which stores request/response pairs by session.

**Tech Stack:** Python 3.11, faster-whisper (`large-v3`, FP16), silero-vad, FastAPI/WebSocket, SQLAlchemy + SQLite, Ollama HTTP API, pytest.

---

### Task 1: Create project skeleton and configuration

**Files:**
- Create: `src/javis_stt/__init__.py`
- Create: `src/javis_stt/config.py`
- Create: `src/javis_stt/models.py`
- Create: `src/javis_stt/db.py`
- Create: `config/stt.yaml`
- Create: `requirements.txt`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
from javis_stt.config import load_config


def test_load_config_defaults(tmp_path):
    cfg_path = tmp_path / "stt.yaml"
    cfg_path.write_text("stt:\n  model_size: large-v3\n")
    cfg = load_config(str(cfg_path))
    assert cfg.stt.model_size == "large-v3"
    assert cfg.stt.language == "ko"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_load_config_defaults -v`
Expected: FAIL with import or attribute error.

**Step 3: Write minimal implementation**

Implement:
- Typed config models for STT/VAD/AI/DB sections
- `load_config(path)` with defaults for accuracy profile (`beam_size=5`, `language=ko`)
- SQLite model tables: `transcripts`, `ai_turns`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_load_config_defaults -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/javis_stt config/stt.yaml requirements.txt tests/test_config.py
git commit -m "feat: bootstrap stt config and storage models"
```

### Task 2: Implement transcript persistence and session ordering

**Files:**
- Modify: `src/javis_stt/models.py`
- Modify: `src/javis_stt/db.py`
- Create: `src/javis_stt/repository.py`
- Test: `tests/test_repository.py`

**Step 1: Write the failing test**

```python
from javis_stt.repository import TranscriptRepository


def test_insert_final_segment_preserves_order(db_session):
    repo = TranscriptRepository(db_session)
    repo.save_final("s1", "seg-001", 0.0, 1.0, "안녕하세요")
    repo.save_final("s1", "seg-002", 1.0, 2.0, "테스트입니다")
    rows = repo.list_finals("s1")
    assert [r.segment_id for r in rows] == ["seg-001", "seg-002"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_repository.py::test_insert_final_segment_preserves_order -v`
Expected: FAIL because repository methods are missing.

**Step 3: Write minimal implementation**

Implement:
- DB initialization and migration bootstrap
- `save_partial`, `save_final`, `save_ai_turn`, `list_finals`
- Stable ordering by `session_id`, `segment_id`, `created_at`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_repository.py::test_insert_final_segment_preserves_order -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/javis_stt tests/test_repository.py
git commit -m "feat: add transcript repository and ordering guarantees"
```

### Task 3: Implement AI gateway client and retry isolation

**Files:**
- Create: `src/javis_stt/ai_gateway.py`
- Modify: `src/javis_stt/repository.py`
- Test: `tests/test_ai_gateway.py`

**Step 1: Write the failing test**

```python
from javis_stt.ai_gateway import AIGateway


def test_ai_gateway_returns_text_response(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:11434/api/generate",
        json={"response": "안녕하세요. 무엇을 도와드릴까요?", "done": True},
    )
    gw = AIGateway(base_url="http://127.0.0.1:11434", model="qwen3:14b")
    out = gw.generate(session_id="s1", text="오늘 일정 알려줘")
    assert "도와드릴까요" in out.text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_ai_gateway.py::test_ai_gateway_returns_text_response -v`
Expected: FAIL because client implementation does not exist.

**Step 3: Write minimal implementation**

Implement:
- Ollama generate client wrapper
- timeout/retry (max 2 retries)
- failure isolation: return structured error while STT keeps running
- persist AI request/response pair in `ai_turns`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_ai_gateway.py::test_ai_gateway_returns_text_response -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/javis_stt/ai_gateway.py src/javis_stt/repository.py tests/test_ai_gateway.py
git commit -m "feat: add local ollama ai gateway with isolated retries"
```

### Task 4: Implement VAD + Whisper transcription service

**Files:**
- Create: `src/javis_stt/asr_service.py`
- Create: `src/javis_stt/vad_service.py`
- Test: `tests/test_asr_service.py`

**Step 1: Write the failing test**

```python
from javis_stt.asr_service import ASRService


def test_asr_service_uses_accuracy_profile(monkeypatch):
    service = ASRService(
        model_size="large-v3",
        compute_type="float16",
        language="ko",
        beam_size=5,
    )
    assert service.language == "ko"
    assert service.beam_size == 5
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_asr_service.py::test_asr_service_uses_accuracy_profile -v`
Expected: FAIL due to missing ASR service.

**Step 3: Write minimal implementation**

Implement:
- Whisper model loading (`large-v3`, fp16)
- `transcribe_segment(audio)` returning partial/final events
- VAD segmentation wrapper with tunable silence/padding

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_asr_service.py::test_asr_service_uses_accuracy_profile -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/javis_stt/asr_service.py src/javis_stt/vad_service.py tests/test_asr_service.py
git commit -m "feat: add vad and whisper large-v3 transcription services"
```

### Task 5: Build realtime WebSocket ingestion and event streaming

**Files:**
- Create: `src/javis_stt/server.py`
- Create: `src/javis_stt/session_manager.py`
- Test: `tests/test_server_ws.py`

**Step 1: Write the failing test**

```python
def test_websocket_emits_partial_and_final_events(test_client):
    ws = test_client.websocket_connect("/ws/stt?session_id=s1")
    ws.send_bytes(b"...pcm16-frame...")
    msg = ws.receive_json()
    assert msg["type"] in {"partial", "final"}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_server_ws.py::test_websocket_emits_partial_and_final_events -v`
Expected: FAIL because endpoint is not implemented.

**Step 3: Write minimal implementation**

Implement:
- WebSocket endpoint for PCM frame ingestion
- bounded per-session queue and backpressure policy
- realtime event emission (`partial`, `final`, `ai_request`, `ai_response`)

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_server_ws.py::test_websocket_emits_partial_and_final_events -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/javis_stt/server.py src/javis_stt/session_manager.py tests/test_server_ws.py
git commit -m "feat: add realtime websocket stt server and event streaming"
```

### Task 6: Add latency instrumentation and acceptance tests

**Files:**
- Create: `src/javis_stt/metrics.py`
- Modify: `src/javis_stt/server.py`
- Create: `tests/test_acceptance_realtime.py`
- Create: `scripts/run_acceptance.sh`

**Step 1: Write the failing test**

```python
def test_latency_metrics_track_p95():
    from javis_stt.metrics import LatencyTracker
    t = LatencyTracker()
    for v in [0.2, 0.4, 0.8, 1.0, 1.2]:
        t.observe_partial(v)
    assert t.p95_partial() >= 0.8
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_acceptance_realtime.py::test_latency_metrics_track_p95 -v`
Expected: FAIL because metrics are not implemented.

**Step 3: Write minimal implementation**

Implement:
- p95 trackers for partial/final/ai-first-token latency
- acceptance script for AT-001..AT-005 in `docs/requirements-stt-realtime-v1.md`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_acceptance_realtime.py::test_latency_metrics_track_p95 -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/javis_stt/metrics.py src/javis_stt/server.py tests/test_acceptance_realtime.py scripts/run_acceptance.sh
git commit -m "test: add latency instrumentation and acceptance harness"
```

### Task 7: Prepare deployment scripts and service launch

**Files:**
- Create: `scripts/install_4090.sh`
- Create: `scripts/start_server.sh`
- Create: `scripts/check_health.sh`
- Modify: `docs/runbook-4090.md`

**Step 1: Write the failing test**

```python
def test_install_script_contains_accuracy_model_defaults():
    text = open("scripts/install_4090.sh", "r", encoding="utf-8").read()
    assert "large-v3" in text
    assert "float16" in text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_deploy_scripts.py::test_install_script_contains_accuracy_model_defaults -v`
Expected: FAIL because scripts do not exist.

**Step 3: Write minimal implementation**

Implement:
- one-command install script for server dependencies
- start script with uvicorn entrypoint
- health script checking websocket readiness and DB writability

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_deploy_scripts.py::test_install_script_contains_accuracy_model_defaults -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add scripts docs/runbook-4090.md
git commit -m "chore: add deployment scripts for 4090 stt runtime"
```

### Task 8: Full verification and release note

**Files:**
- Modify: `README.md`
- Create: `docs/reviews/2026-02-16-phase1-verification.md`

**Step 1: Write the failing test**

```python
def test_readme_mentions_realtime_and_ai_text_loop():
    text = open("README.md", "r", encoding="utf-8").read()
    assert "Realtime" in text
    assert "AI text" in text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_docs_contract.py::test_readme_mentions_realtime_and_ai_text_loop -v`
Expected: FAIL if README contract is missing.

**Step 3: Write minimal implementation**

Implement:
- README runtime section with execution commands
- verification report documenting AT-001..AT-005 outputs

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_docs_contract.py::test_readme_mentions_realtime_and_ai_text_loop -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add README.md docs/reviews/2026-02-16-phase1-verification.md
git commit -m "docs: publish phase1 verification and runtime usage"
```
