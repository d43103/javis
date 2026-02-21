# Voice Hub + openclaw Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Mac을 WebSocket 허브 서버로 만들어 모바일 앱·menubar 클라이언트를 수용하고, LLM을 openclaw voice-assistant 에이전트로 통합한다.

**Architecture:** `src/voice_hub.py`가 `:8766`에서 클라이언트 WebSocket을 받아 4090 STT에 프록시하고, openclaw CLI subprocess로 LLM 호출 후 4090 TTS를 클라이언트로 스트리밍한다. `menubar_app.py`는 Hub에 연결하도록 수정된다. iOS 앱은 별도 Xcode 프로젝트로 개발된다.

**Tech Stack:** Python asyncio + websockets (Hub), openclaw CLI, Swift URLSessionWebSocketTask (iOS)

---

## Task 1: openclaw voice-assistant 에이전트 등록

**Files:**
- Modify: `~/.openclaw/openclaw.json` (agents.list 배열에 항목 추가)

### Step 1: 현재 에이전트 디렉토리 생성

```bash
mkdir -p ~/.openclaw/agents/voice-assistant/agent
```

### Step 2: openclaw.json 편집

`~/.openclaw/openclaw.json` 의 `"agents"` → `"list"` 배열 끝에 아래 항목을 추가한다:

```json
{
  "id": "voice-assistant",
  "name": "voice-assistant",
  "workspace": "/Users/d43103/Workspace/projects/javis",
  "agentDir": "/Users/d43103/.openclaw/agents/voice-assistant/agent",
  "model": "anthropic/claude-haiku-4-5-20251001",
  "identity": {
    "name": "Javis",
    "systemPrompt": "당신은 친근하고 간결한 한국어 개인 비서입니다.\n음성 대화에 최적화되어 있으므로 답변을 1-2 문장으로 짧게 유지하세요.\n마크다운, 목록, 코드 블록을 사용하지 마세요.\n자연스러운 구어체 한국어로 답변하세요."
  }
}
```

### Step 3: 에이전트 등록 확인

```bash
openclaw agents list
```

Expected: `voice-assistant` 가 목록에 표시된다.

### Step 4: 에이전트 동작 확인

```bash
openclaw agent --agent voice-assistant --session-id voice-test --local -m "안녕, 오늘 날씨 어때?" --json
```

Expected: JSON 출력에 `"content"` 필드로 한국어 응답이 포함된다.

---

## Task 2: VoiceHub 핵심 로직 — `VoiceSession` 클래스

**Files:**
- Create: `src/voice_hub.py`
- Create: `tests/test_voice_hub.py`

### Step 1: 실패 테스트 작성

`tests/test_voice_hub.py`:

```python
"""Tests for VoiceHub session management."""
import asyncio
import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.voice_hub import VoiceSession, _run_openclaw


class TestRunOpenclaw:
    def test_success_returns_text(self):
        """openclaw CLI 성공 시 응답 텍스트를 반환한다."""
        fake_result = json.dumps({"content": [{"text": "안녕하세요!"}]})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_result, stderr="")
            result = _run_openclaw("voice-assistant", "voice-test", "안녕")
        assert result == "안녕하세요!"

    def test_failure_returns_fallback(self):
        """openclaw CLI 실패 시 폴백 문자열을 반환한다."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = _run_openclaw("voice-assistant", "voice-test", "안녕")
        assert result == "죄송합니다, 잠시 후 다시 말씀해 주세요."

    def test_calls_correct_args(self):
        """올바른 CLI 인자로 openclaw를 호출한다."""
        fake_result = json.dumps({"content": [{"text": "응답"}]})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_result, stderr="")
            _run_openclaw("voice-assistant", "voice-mac", "테스트")
        call_args = mock_run.call_args[0][0]
        assert "voice-assistant" in call_args
        assert "voice-mac" in call_args
        assert "테스트" in call_args
        assert "--json" in call_args


class TestVoiceSession:
    def test_session_id_stored(self):
        """session_id가 올바르게 저장된다."""
        session = VoiceSession(
            session_id="voice-mac",
            stt_ws_url="ws://localhost:8765",
            tts_http_url="http://localhost:8765",
            agent_id="voice-assistant",
        )
        assert session.session_id == "voice-mac"

    def test_gain_defaults(self):
        """gain 기본값은 1.0이다."""
        session = VoiceSession(
            session_id="voice-mac",
            stt_ws_url="ws://localhost:8765",
            tts_http_url="http://localhost:8765",
            agent_id="voice-assistant",
        )
        assert session.input_gain == 1.0
        assert session.output_gain == 1.0

    def test_gain_update_from_json(self):
        """JSON gain 메시지로 gain을 업데이트한다."""
        session = VoiceSession(
            session_id="voice-mac",
            stt_ws_url="ws://localhost:8765",
            tts_http_url="http://localhost:8765",
            agent_id="voice-assistant",
        )
        session.apply_gain_message({"type": "gain", "input": 1.5, "output": 0.8})
        assert session.input_gain == 1.5
        assert session.output_gain == 0.8
```

### Step 2: 테스트 실패 확인

```bash
cd /Users/d43103/Workspace/projects/javis
pytest tests/test_voice_hub.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'src.voice_hub'`

### Step 3: `_run_openclaw` + `VoiceSession` 구현

`src/voice_hub.py`:

```python
"""Mac Hub WebSocket 서버 — 멀티 클라이언트 voice 세션 관리."""
import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger("javis.hub")

_FALLBACK = "죄송합니다, 잠시 후 다시 말씀해 주세요."


def _run_openclaw(agent_id: str, session_id: str, text: str) -> str:
    """openclaw agent CLI를 subprocess로 실행해 응답 텍스트를 반환한다."""
    cmd = [
        "openclaw", "agent",
        "--agent", agent_id,
        "--session-id", session_id,
        "-m", text,
        "--json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.error("openclaw_error stderr=%s", result.stderr[:200])
            return _FALLBACK
        data = json.loads(result.stdout)
        # openclaw JSON 응답: {"content": [{"text": "..."}]}
        content = data.get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", _FALLBACK)
        # 단순 문자열 응답인 경우
        return data.get("text", _FALLBACK)
    except Exception as exc:
        logger.exception("openclaw_exception exc=%s", exc)
        return _FALLBACK


@dataclass
class VoiceSession:
    """단일 클라이언트 연결에 대한 voice 세션 상태."""
    session_id: str
    stt_ws_url: str
    tts_http_url: str
    agent_id: str = "voice-assistant"
    input_gain: float = 1.0
    output_gain: float = 1.0
    mic_muted: bool = False

    def apply_gain_message(self, msg: dict) -> None:
        """JSON gain 메시지로 gain 값을 업데이트한다."""
        if "input" in msg:
            self.input_gain = float(msg["input"])
        if "output" in msg:
            self.output_gain = float(msg["output"])
```

### Step 4: 테스트 통과 확인

```bash
pytest tests/test_voice_hub.py -v
```

Expected: 6개 테스트 모두 PASS

### Step 5: 커밋

```bash
git add src/voice_hub.py tests/test_voice_hub.py
git commit -m "feat: add VoiceSession + _run_openclaw (hub core)"
```

---

## Task 3: Hub WebSocket 서버 구현

**Files:**
- Modify: `src/voice_hub.py` (Hub 클래스 추가)

### Step 1: Hub 클래스 추가

`src/voice_hub.py` 끝에 추가:

```python
import urllib.parse


async def _send_json(ws, **kwargs) -> None:
    """WebSocket으로 JSON 메시지를 전송한다."""
    try:
        await ws.send(json.dumps(kwargs, ensure_ascii=False))
    except Exception:
        pass


class VoiceHub:
    """
    Mac Hub WebSocket 서버 (:8766 /ws/voice).

    클라이언트가 ?session_id=<id> 로 연결하면 VoiceSession을 생성하고
    4090 STT에 PCM을 프록시하며 openclaw로 LLM 호출 후 TTS를 스트리밍한다.
    """

    def __init__(
        self,
        server: str = "ws://192.168.219.106:8765",
        agent_id: str = "voice-assistant",
        host: str = "0.0.0.0",
        port: int = 8766,
        idle_flush_seconds: float = 1.5,
    ):
        self.server = server.rstrip("/")
        self.agent_id = agent_id
        self.host = host
        self.port = port
        self.idle_flush_seconds = idle_flush_seconds

        # ws://host:port → http://host:port
        self.http_base = (
            self.server
            .replace("wss://", "https://")
            .replace("ws://", "http://")
        )

    def _stt_url(self, session_id: str) -> str:
        return f"{self.server}/ws/stt?session_id={session_id}"

    async def _handle_client(self, client_ws) -> None:
        """클라이언트 WebSocket 연결 1개를 처리한다."""
        import websockets

        # session_id 파싱
        path = getattr(client_ws, "path", "") or getattr(client_ws, "request", None)
        if hasattr(path, "path"):
            path = path.path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(str(path)).query)
        session_id = (qs.get("session_id") or ["voice-unknown"])[0]

        session = VoiceSession(
            session_id=session_id,
            stt_ws_url=self._stt_url(session_id),
            tts_http_url=self.http_base,
            agent_id=self.agent_id,
        )
        logger.info("client_connected session=%s", session_id)
        await _send_json(client_ws, type="status", value="connected")

        pending_texts: list[str] = []
        flush_task: asyncio.Task | None = None

        async def flush_pending():
            await asyncio.sleep(self.idle_flush_seconds)
            merged = " ".join(pending_texts).strip()
            pending_texts.clear()
            if not merged:
                return
            await _send_json(client_ws, type="status", value="thinking")
            response = await asyncio.to_thread(
                _run_openclaw, session.agent_id, session.session_id, merged
            )
            await _send_json(client_ws, type="ai", text=response)
            await _stream_tts(client_ws, session, response)

        async def on_final(text: str):
            nonlocal flush_task
            pending_texts.append(text)
            if flush_task and not flush_task.done():
                flush_task.cancel()
            flush_task = asyncio.create_task(flush_pending())

        try:
            async with websockets.connect(
                self._stt_url(session_id),
                max_size=2 ** 24,
                ping_interval=20,
                ping_timeout=20,
            ) as stt_ws:
                # STT 이벤트 수신 태스크
                async def stt_receiver():
                    async for msg in stt_ws:
                        if not isinstance(msg, str):
                            continue
                        try:
                            evt = json.loads(msg)
                        except Exception:
                            continue
                        if evt.get("type") == "final":
                            text = evt.get("text", "").strip()
                            if text:
                                await _send_json(client_ws, type="final", text=text)
                                await on_final(text)
                        elif evt.get("type") == "partial":
                            text = evt.get("text", "")
                            if text:
                                await _send_json(client_ws, type="partial", text=text)

                # 클라이언트 메시지 수신 태스크 (PCM binary 또는 JSON 제어 메시지)
                async def client_receiver():
                    async for msg in client_ws:
                        if isinstance(msg, bytes):
                            # PCM 오디오 → STT 서버로 프록시
                            if session.mic_muted:
                                # 무음 전송
                                silence = bytes(len(msg))
                                await stt_ws.send(silence)
                            else:
                                from src.audio_devices import apply_gain_int16
                                data = apply_gain_int16(msg, session.input_gain)
                                await stt_ws.send(data)
                        elif isinstance(msg, str):
                            try:
                                ctrl = json.loads(msg)
                                if ctrl.get("type") == "gain":
                                    session.apply_gain_message(ctrl)
                            except Exception:
                                pass

                stt_task = asyncio.create_task(stt_receiver())
                client_task = asyncio.create_task(client_receiver())
                done, pending = await asyncio.wait(
                    {stt_task, client_task},
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                for t in pending:
                    t.cancel()

        except Exception as exc:
            logger.error("session_error session=%s exc=%s", session_id, exc)
        finally:
            logger.info("client_disconnected session=%s", session_id)

    async def run_async(self) -> None:
        import websockets

        logger.info("hub_start host=%s port=%s", self.host, self.port)
        async with websockets.serve(self._handle_client, self.host, self.port):
            print(f"🟢 Hub 서버 실행 중: ws://{self.host}:{self.port}/ws/voice")
            await asyncio.Future()  # run forever

    def run(self) -> None:
        asyncio.run(self.run_async())


async def _stream_tts(client_ws, session: VoiceSession, response_text: str) -> None:
    """4090 TTS를 스트리밍하여 클라이언트 WebSocket으로 PCM 바이너리를 전송한다."""
    import httpx
    import numpy as np

    await _send_json(client_ws, type="status", value="speaking")
    session.mic_muted = True
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                f"{session.tts_http_url}/v1/voice/turn",
                json={
                    "session_id": session.session_id,
                    "text": "",
                    "response_text": response_text,
                },
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    if chunk:
                        from src.audio_devices import apply_gain_float32
                        audio = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                        audio = apply_gain_float32(audio, session.output_gain)
                        await client_ws.send(audio.astype(np.float32).tobytes())
    except Exception as exc:
        logger.error("tts_error session=%s exc=%s", session.session_id, exc)
    finally:
        session.mic_muted = False
        await _send_json(client_ws, type="status", value="idle")


def main() -> None:
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Javis Mac Hub Server")
    parser.add_argument("--server", default="ws://192.168.219.106:8765")
    parser.add_argument("--agent", default="voice-assistant")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--idle-flush", type=float, default=1.5)
    args = parser.parse_args()

    hub = VoiceHub(
        server=args.server,
        agent_id=args.agent,
        host=args.host,
        port=args.port,
        idle_flush_seconds=args.idle_flush,
    )
    hub.run()


if __name__ == "__main__":
    main()
```

### Step 2: import 오류 없이 로드되는지 확인

```bash
cd /Users/d43103/Workspace/projects/javis
python -c "from src.voice_hub import VoiceHub, VoiceSession, _run_openclaw; print('OK')"
```

Expected: `OK`

### Step 3: 기존 테스트 통과 확인

```bash
pytest tests/test_voice_hub.py -v
```

Expected: 6개 PASS

### Step 4: 커밋

```bash
git add src/voice_hub.py
git commit -m "feat: add VoiceHub WebSocket server (Task 3)"
```

---

## Task 4: Hub → openclaw 응답 파싱 검증

**Files:**
- Modify: `tests/test_voice_hub.py` (실제 openclaw JSON 포맷 확인 테스트 추가)

### Step 1: openclaw 실제 JSON 출력 확인

아래 명령으로 실제 JSON 포맷을 확인한다:

```bash
openclaw agent --agent voice-assistant --session-id voice-test --local \
  -m "안녕" --json 2>&1 | python3 -m json.tool
```

출력을 확인하고 `_run_openclaw` 파서가 올바른지 검증한다.

### Step 2: 파싱 포맷에 맞게 `_run_openclaw` 수정

실제 출력 포맷이 예상과 다를 경우 `src/voice_hub.py` 의 `_run_openclaw` 파서를 수정한다.

예: 출력이 `{"reply": "..."}` 형태면:

```python
return data.get("reply") or data.get("text") or _FALLBACK
```

### Step 3: 파싱 테스트 업데이트

실제 포맷에 맞게 `tests/test_voice_hub.py` 의 `test_success_returns_text` 픽스처 데이터를 수정한다.

### Step 4: 테스트 통과 확인

```bash
pytest tests/test_voice_hub.py -v
```

### Step 5: 커밋

```bash
git add src/voice_hub.py tests/test_voice_hub.py
git commit -m "fix: align openclaw JSON parser with actual output format"
```

---

## Task 5: menubar_app.py → Hub 클라이언트로 교체

**Files:**
- Modify: `src/menubar_app.py`
- Modify: `src/javis_menubar.py`

### Step 1: `HubClient` 클래스 생성

`menubar_app.py` 상단의 `VoiceBridge` import를 제거하고 Hub WebSocket 클라이언트를 추가한다.

`src/menubar_app.py` 의 import 섹션:

```python
# 기존: from src.voice_llm_bridge import VoiceBridge
# 새로: Hub에 연결하는 경량 클라이언트
import asyncio
import json
import logging
import queue
import threading

import rumps

from src.audio_devices import apply_gain_int16, list_input_devices, list_output_devices

logger = logging.getLogger("javis.menubar")
```

### Step 2: `JavisMenuBarApp._start_bridge` 수정

기존 `VoiceBridge` 생성 코드를 Hub WebSocket 클라이언트 코드로 교체한다:

```python
def _start_bridge(self):
    if self._running:
        return
    self._running = True
    self._toggle_item.title = "Stop"

    def run_in_thread():
        loop = asyncio.new_event_loop()
        self._bridge_loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_hub_client())
        finally:
            loop.close()
            self._bridge_loop = None
            self._running = False

    self._bridge_thread = threading.Thread(target=run_in_thread, daemon=True)
    self._bridge_thread.start()

async def _run_hub_client(self):
    import websockets
    import sounddevice as sd
    import numpy as np

    url = f"{self._hub_url}/ws/voice?session_id={self._session_id}"
    MIC_RATE = 16000
    TTS_RATE = 24000
    CHUNK_MS = 80
    chunk_frames = int(MIC_RATE * CHUNK_MS / 1000)

    async with websockets.connect(url, max_size=2**24, ping_interval=20) as ws:
        self._ui_queue.put(("status", "connected"))

        audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        loop = asyncio.get_running_loop()

        def mic_callback(indata, _frames, _time, _status):
            loop.call_soon_threadsafe(audio_q.put_nowait, bytes(indata))

        async def sender():
            silence = bytes(chunk_frames * 2)
            with sd.RawInputStream(samplerate=MIC_RATE, channels=1,
                                   dtype="int16", blocksize=chunk_frames,
                                   callback=mic_callback):
                while self._running:
                    try:
                        pcm = await asyncio.wait_for(audio_q.get(), 0.5)
                    except asyncio.TimeoutError:
                        continue
                    if self._mic_muted:
                        await ws.send(silence)
                    else:
                        pcm = apply_gain_int16(pcm, self.input_gain)
                        await ws.send(pcm)

        async def receiver():
            with sd.OutputStream(samplerate=TTS_RATE, channels=1,
                                  dtype="float32",
                                  device=self._selected_output_device) as stream:
                async for msg in ws:
                    if isinstance(msg, bytes):
                        audio = np.frombuffer(msg, dtype=np.float32)
                        stream.write(audio)
                    elif isinstance(msg, str):
                        try:
                            evt = json.loads(msg)
                        except Exception:
                            continue
                        t = evt.get("type", "")
                        if t == "status":
                            v = evt.get("value", "")
                            self._ui_queue.put(("status", v))
                            self._mic_muted = (v == "speaking")
                        elif t == "partial":
                            self._ui_queue.put(("partial", evt.get("text", "")))
                        elif t == "final":
                            self._ui_queue.put(("final", evt.get("text", "")))
                        elif t == "ai":
                            self._ui_queue.put(("ai", evt.get("text", "")))

        s = asyncio.create_task(sender())
        r = asyncio.create_task(receiver())
        done, pending = await asyncio.wait({s, r}, return_when=asyncio.FIRST_EXCEPTION)
        for t in pending:
            t.cancel()
```

### Step 3: `__init__` 시그니처 수정

`JavisMenuBarApp.__init__` 에서 `server` 파라미터를 `hub_url`로 변경한다:

```python
def __init__(self, hub_url: str, session_id: str, auto_start: bool = False):
    ...
    self._hub_url = hub_url
    self._session_id = session_id
    self._running = False
    self._bridge_loop = None
    self._bridge_thread = None
    self._mic_muted = False
```

### Step 4: `javis_menubar.py` 인자 업데이트

```python
parser.add_argument("--hub", default="ws://localhost:8766", help="Mac Hub WebSocket URL")
# --server, --model 인자 제거 (Hub가 처리)
app = JavisMenuBarApp(
    hub_url=args.hub,
    session_id=args.session,
    auto_start=args.auto_start,
)
```

### Step 5: 수동 확인

Hub 서버를 먼저 실행한 뒤 menubar 앱을 실행해 연결되는지 확인한다:

```bash
# 터미널 1
python -m src.voice_hub --server ws://192.168.219.106:8765

# 터미널 2
python -m src.javis_menubar --hub ws://localhost:8766 --session voice-mac --auto-start
```

Expected: menubar 아이콘이 "connected" 상태로 전환된다.

### Step 6: 커밋

```bash
git add src/menubar_app.py src/javis_menubar.py
git commit -m "feat: menubar connects to Hub instead of direct VoiceBridge"
```

---

## Task 6: launchd plist — Hub 서버 24/7 실행

**Files:**
- Create: `deploy/com.javis.hub.plist`

### Step 1: plist 생성

`deploy/com.javis.hub.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.javis.hub</string>

    <key>ProgramArguments</key>
    <array>
        <string>/opt/anaconda3/bin/python3</string>
        <string>-m</string>
        <string>src.voice_hub</string>
        <string>--server</string>
        <string>ws://192.168.219.106:8765</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>8766</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/d43103/Workspace/projects/javis</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>/Users/d43103/Workspace/projects/javis</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/opt/anaconda3/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/tmp/javis-hub.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/javis-hub-error.log</string>
</dict>
</plist>
```

### Step 2: launchd 등록 및 시작

```bash
cp deploy/com.javis.hub.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.javis.hub.plist
launchctl start com.javis.hub
```

### Step 3: 동작 확인

```bash
sleep 2 && tail -20 /tmp/javis-hub.log
```

Expected: `🟢 Hub 서버 실행 중: ws://0.0.0.0:8766/ws/voice`

### Step 4: 커밋

```bash
git add deploy/com.javis.hub.plist
git commit -m "feat: add launchd plist for Hub server 24/7 operation"
```

---

## Task 7: iOS 앱 — Xcode 프로젝트 생성

**Files:**
- Create: `ios/JavisClient/` (새 Xcode 프로젝트)

### Step 1: Xcode 프로젝트 생성

1. Xcode → File → New → Project → iOS → App
2. Product Name: `JavisClient`
3. Bundle ID: `com.javis.client`
4. Language: Swift, Interface: SwiftUI
5. 저장 위치: `/Users/d43103/Workspace/projects/javis/ios/`

### Step 2: Background Audio 설정

`Info.plist` 에 추가:

```xml
<key>UIBackgroundModes</key>
<array>
    <string>audio</string>
</array>
```

### Step 3: `AudioEngine.swift` 생성

`ios/JavisClient/JavisClient/AudioEngine.swift`:

```swift
import AVFoundation
import Foundation

/// 마이크 캡처 및 TTS PCM 재생을 담당한다.
class AudioEngine: ObservableObject {
    private let engine = AVAudioEngine()
    private let inputNode: AVAudioInputNode
    private let playerNode = AVAudioPlayerNode()
    private let MIC_RATE: Double = 16000
    private let TTS_RATE: Double = 24000
    private let CHUNK_FRAMES = 1280  // 80ms @ 16kHz

    var onPCMChunk: ((Data) -> Void)?

    init() {
        inputNode = engine.inputNode
        engine.attach(playerNode)
        let ttsFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: TTS_RATE,
            channels: 1,
            interleaved: false
        )!
        engine.connect(playerNode, to: engine.mainMixerNode, format: ttsFormat)
    }

    func start() throws {
        try AVAudioSession.sharedInstance().setCategory(
            .playAndRecord,
            mode: .voiceChat,
            options: [.defaultToSpeaker, .allowBluetooth]
        )
        try AVAudioSession.sharedInstance().setActive(true)

        let micFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: MIC_RATE,
            channels: 1,
            interleaved: true
        )!

        inputNode.installTap(onBus: 0, bufferSize: AVAudioFrameCount(CHUNK_FRAMES),
                              format: micFormat) { [weak self] buffer, _ in
            guard let self = self,
                  let data = self.bufferToData(buffer) else { return }
            self.onPCMChunk?(data)
        }

        try engine.start()
        playerNode.play()
    }

    func stop() {
        inputNode.removeTap(onBus: 0)
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    func playPCMFloat32(_ data: Data) {
        let floatCount = data.count / 4
        guard floatCount > 0 else { return }
        let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: TTS_RATE,
            channels: 1,
            interleaved: false
        )!
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                             frameCapacity: AVAudioFrameCount(floatCount)) else { return }
        buffer.frameLength = AVAudioFrameCount(floatCount)
        data.withUnsafeBytes { ptr in
            let floats = ptr.bindMemory(to: Float.self)
            buffer.floatChannelData?[0].update(from: floats.baseAddress!, count: floatCount)
        }
        playerNode.scheduleBuffer(buffer, completionHandler: nil)
    }

    private func bufferToData(_ buffer: AVAudioPCMBuffer) -> Data? {
        guard let channelData = buffer.int16ChannelData else { return nil }
        let frameLength = Int(buffer.frameLength)
        return Data(bytes: channelData[0], count: frameLength * 2)
    }
}
```

### Step 4: `HubConnection.swift` 생성

`ios/JavisClient/JavisClient/HubConnection.swift`:

```swift
import Foundation
import Combine

/// Mac Hub WebSocket 연결 및 메시지 처리를 담당한다.
class HubConnection: NSObject, ObservableObject, URLSessionWebSocketDelegate {
    @Published var status: String = "disconnected"
    @Published var partialText: String = ""
    @Published var lastAI: String = ""

    private var ws: URLSessionWebSocketTask?
    private var session: URLSession?
    private let audioEngine = AudioEngine()

    var hubURL: URL

    init(hubURL: URL) {
        self.hubURL = hubURL
        super.init()
        audioEngine.onPCMChunk = { [weak self] data in
            self?.sendBinary(data)
        }
    }

    func connect(sessionID: String) {
        var comps = URLComponents(url: hubURL, resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "session_id", value: sessionID)]
        let url = comps.url!

        session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        ws = session?.webSocketTask(with: url)
        ws?.resume()
        receiveLoop()
        try? audioEngine.start()
        status = "connecting"
    }

    func disconnect() {
        ws?.cancel()
        audioEngine.stop()
        status = "disconnected"
    }

    private func receiveLoop() {
        ws?.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let msg):
                self.handleMessage(msg)
                self.receiveLoop()
            case .failure:
                DispatchQueue.main.async { self.status = "disconnected" }
            }
        }
    }

    private func handleMessage(_ msg: URLSessionWebSocketTask.Message) {
        switch msg {
        case .data(let data):
            // TTS PCM float32 binary
            DispatchQueue.main.async {
                self.audioEngine.playPCMFloat32(data)
            }
        case .string(let text):
            guard let evt = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any]
            else { return }
            let type = evt["type"] as? String ?? ""
            DispatchQueue.main.async {
                switch type {
                case "status":
                    self.status = evt["value"] as? String ?? ""
                case "partial":
                    self.partialText = evt["text"] as? String ?? ""
                case "final":
                    self.partialText = ""
                case "ai":
                    self.lastAI = evt["text"] as? String ?? ""
                default: break
                }
            }
        @unknown default: break
        }
    }

    private func sendBinary(_ data: Data) {
        ws?.send(.data(data)) { _ in }
    }
}
```

### Step 5: `ContentView.swift` 구현

```swift
import SwiftUI

struct ContentView: View {
    @StateObject private var hub = HubConnection(
        hubURL: URL(string: "ws://192.168.219.106:8766")!
    )
    @State private var sessionID = "voice-mobile"

    var body: some View {
        VStack(spacing: 24) {
            Text("Javis")
                .font(.largeTitle.bold())

            StatusBadge(status: hub.status)

            if !hub.partialText.isEmpty {
                Text(hub.partialText)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            if !hub.lastAI.isEmpty {
                Text(hub.lastAI)
                    .font(.body)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            Spacer()

            Button(hub.status == "disconnected" ? "연결" : "연결 해제") {
                if hub.status == "disconnected" {
                    hub.connect(sessionID: sessionID)
                } else {
                    hub.disconnect()
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
    }
}

struct StatusBadge: View {
    let status: String
    var body: some View {
        HStack {
            Circle()
                .fill(color)
                .frame(width: 10, height: 10)
            Text(status)
                .font(.caption)
        }
    }
    var color: Color {
        switch status {
        case "connected", "idle": return .green
        case "thinking": return .yellow
        case "speaking": return .blue
        case "connecting": return .orange
        default: return .red
        }
    }
}
```

### Step 6: iOS 빌드 및 시뮬레이터 확인

Xcode에서 빌드 후 iPhone 시뮬레이터로 실행한다. (시뮬레이터는 마이크 접근 불가 — 실제 기기로 테스트 필요)

### Step 7: 커밋

```bash
git add ios/
git commit -m "feat: add iOS JavisClient app (WebSocket + AVAudio)"
```

---

## Task 8: 통합 테스트

### Step 1: Hub 서버 시작

```bash
python -m src.voice_hub --server ws://192.168.219.106:8765
```

### Step 2: 간단한 WebSocket 클라이언트로 Hub 확인

```bash
python3 - <<'EOF'
import asyncio, json
import websockets

async def test():
    async with websockets.connect("ws://localhost:8766/ws/voice?session_id=voice-test") as ws:
        msg = await ws.recv()
        print("Hub says:", msg)
asyncio.run(test())
EOF
```

Expected: `{"type": "status", "value": "connected"}`

### Step 3: menubar 앱으로 End-to-End 확인

```bash
python -m src.javis_menubar --hub ws://localhost:8766 --session voice-mac --auto-start
```

한국어로 말한 후 AI 응답이 음성으로 재생되는지 확인한다.

### Step 4: openclaw 세션 확인

openclaw 웹 UI (`http://localhost:18789`) 에서 `voice-mac` 세션에 대화가 기록되는지 확인한다.

---

## Task 9: API 키 노출 수정 (보안)

**Files:**
- Modify: `deploy/com.javis.menubar.plist` — API 키 제거
- Modify: `.gitignore` — plist 파일 제외

### Step 1: .gitignore에 plist 추가

```bash
echo "deploy/*.plist" >> .gitignore
```

### Step 2: 기존 노출된 plist 수정

`deploy/com.javis.menubar.plist` 의 `EnvironmentVariables` 섹션에서 `ANTHROPIC_API_KEY` 항목을 제거한다. (Hub를 사용하면 menubar는 API 키 불필요)

### Step 3: 커밋

```bash
git add .gitignore deploy/com.javis.menubar.plist
git commit -m "security: remove API key from plist, add deploy/*.plist to gitignore"
```
