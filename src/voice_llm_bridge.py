"""
Mac-side voice bridge: 마이크 오디오를 서버로 스트리밍하고, STT final 이벤트를
받아 Claude API 호출 후 TTS 음성을 재생합니다.

Usage:
  python -m src.voice_llm_bridge \
    --server ws://192.168.219.106:8765 \
    --session mac-1 \
    --model claude-haiku-4-5-20251001

Requirements (Mac):
  pip install anthropic websockets httpx sounddevice numpy
  export ANTHROPIC_API_KEY=sk-...
"""
import argparse
import asyncio
import json
import logging
import urllib.parse
import urllib.request
from collections import deque

logger = logging.getLogger("javis.bridge")

SYSTEM_PROMPT = """당신은 친근하고 간결한 한국어 개인 비서입니다.
음성 대화에 최적화되어 있으므로 답변을 1-2 문장으로 짧게 유지하세요.
마크다운, 목록, 코드 블록을 사용하지 마세요.
자연스러운 구어체 한국어로 답변하세요."""


def _build_ws_url(server: str, session_id: str) -> str:
    """ws://host:port  →  ws://host:port/ws/stt?session_id=..."""
    base = server.rstrip("/")
    if "/ws/stt" not in base:
        base = f"{base}/ws/stt"
    return f"{base}?session_id={session_id}"


def _build_http_base(server: str) -> str:
    """ws://host:port/...  →  http://host:port"""
    http = server.replace("wss://", "https://").replace("ws://", "http://")
    parsed = urllib.parse.urlparse(http)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _wait_for_server(http_base: str) -> None:
    healthz = f"{http_base}/healthz"
    while True:
        try:
            def _probe():
                with urllib.request.urlopen(healthz, timeout=2) as r:
                    return r.status == 200
            if await asyncio.to_thread(_probe):
                return
        except Exception:
            pass
        logger.info("waiting_for_server url=%s", healthz)
        await asyncio.sleep(2)


class VoiceBridge:
    def __init__(
        self,
        server: str,
        session_id: str,
        claude_model: str = "claude-haiku-4-5-20251001",
        max_turns: int = 10,
        mic_sample_rate: int = 16000,   # ASR 서버가 16kHz 기대
        tts_sample_rate: int = 24000,   # TTS 서버가 24kHz 출력
        chunk_ms: int = 80,
        device: str | None = None,
        idle_flush_seconds: float = 1.5,  # final 후 이 시간 침묵하면 AI 호출
    ):
        self.ws_url = _build_ws_url(server, session_id)
        self.http_base = _build_http_base(server)
        self.session_id = session_id
        self.claude_model = claude_model
        self.mic_sample_rate = mic_sample_rate
        self.tts_sample_rate = tts_sample_rate
        self.chunk_ms = chunk_ms
        self.device = device
        self.idle_flush_seconds = idle_flush_seconds
        self._history: deque[dict[str, str]] = deque(maxlen=max_turns * 2)
        self._pending_texts: list[str] = []
        self._flush_task: asyncio.Task | None = None

        import anthropic
        self._claude = anthropic.Anthropic()

    def _call_claude(self, text: str) -> str:
        messages = [*self._history, {"role": "user", "content": text}]
        response = self._claude.messages.create(
            model=self.claude_model,
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text

    def _post_tts_and_play(self, response_text: str) -> None:
        import httpx
        import numpy as np
        import sounddevice as sd

        with httpx.Client(timeout=30.0) as client:
            with client.stream(
                "POST",
                f"{self.http_base}/v1/voice/turn",
                json={
                    "session_id": self.session_id,
                    "text": "",
                    "response_text": response_text,
                },
            ) as resp:
                resp.raise_for_status()
                # 청크가 도착하는 즉시 재생 (버퍼링 없이)
                with sd.OutputStream(
                    samplerate=self.tts_sample_rate,
                    channels=1,
                    dtype="float32",
                ) as stream:
                    for chunk in resp.iter_bytes(chunk_size=4096):
                        if chunk:
                            audio = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                            stream.write(audio)

    def _handle_final(self, text: str) -> None:
        logger.info("stt_final text=%s", text[:100])
        try:
            response_text = self._call_claude(text)
            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": response_text})
            logger.info("claude_response text=%s", response_text[:100])
            self._post_tts_and_play(response_text)
        except Exception:
            logger.exception("handle_final_error text=%s", text[:80])

    async def _mic_sender(self, ws) -> None:
        """마이크 오디오를 캡처해서 WebSocket으로 전송합니다."""
        sd = __import__("sounddevice")
        channels = 1
        bytes_per_sample = 2  # int16
        chunk_frames = int(self.mic_sample_rate * self.chunk_ms / 1000)
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        loop = asyncio.get_running_loop()

        def callback(indata, _frames, _time, _status):
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        # resolve device index
        device_index = None
        if self.device is not None:
            try:
                device_index = int(self.device)
            except ValueError:
                devices = sd.query_devices()
                for i, d in enumerate(devices):
                    if self.device.lower() in d["name"].lower() and d["max_input_channels"] > 0:
                        device_index = i
                        break

        logger.info("mic_start device=%s mic_rate=%s tts_rate=%s chunk_ms=%s",
                    device_index, self.mic_sample_rate, self.tts_sample_rate, self.chunk_ms)

        with sd.RawInputStream(
            samplerate=self.mic_sample_rate,
            channels=channels,
            dtype="int16",
            device=device_index,
            blocksize=chunk_frames,
            callback=callback,
        ):
            while True:
                payload = await queue.get()
                await ws.send(payload)

    async def _flush_pending(self) -> None:
        """축적된 final 텍스트를 합쳐서 Claude 호출 후 TTS 재생."""
        merged = " ".join(self._pending_texts).strip()
        self._pending_texts.clear()
        if not merged:
            return
        print()  # partial 표시 줄 넘김
        logger.info("flush_pending text=%s", merged[:200])
        await asyncio.to_thread(self._handle_final, merged)

    async def _schedule_flush(self) -> None:
        """idle_flush_seconds 후에 flush를 실행하는 타이머."""
        await asyncio.sleep(self.idle_flush_seconds)
        await self._flush_pending()

    async def _event_receiver(self, ws) -> None:
        """서버에서 STT 이벤트를 수신합니다. final 이벤트를 축적 후 침묵 감지 시 flush."""
        async for message in ws:
            if not isinstance(message, str):
                continue
            try:
                event = json.loads(message)
            except Exception:
                continue

            event_type = event.get("type", "")
            if event_type == "final":
                text = event.get("text", "").strip()
                if text:
                    self._pending_texts.append(text)
                    # 이전 타이머 취소 → 새 타이머 시작 (debounce)
                    if self._flush_task and not self._flush_task.done():
                        self._flush_task.cancel()
                    self._flush_task = asyncio.create_task(self._schedule_flush())
            elif event_type == "partial":
                text = event.get("text", "")
                if text:
                    print(f"\r🎤 {text[:80]}     ", end="", flush=True)
            elif event_type in ("ai_request", "ai_response", "tts_start", "tts_done"):
                pass  # 서버 AI는 비활성화돼 있으므로 무시
            else:
                logger.debug("event type=%s", event_type)

    async def run_async(self) -> None:
        import websockets

        await _wait_for_server(self.http_base)
        logger.info("bridge_connecting url=%s", self.ws_url)

        while True:
            try:
                async with websockets.connect(
                    self.ws_url,
                    max_size=2 ** 24,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    logger.info("bridge_connected")
                    print("🟢 연결됨 — 말씀하세요")

                    sender = asyncio.create_task(self._mic_sender(ws))
                    receiver = asyncio.create_task(self._event_receiver(ws))
                    done, pending = await asyncio.wait(
                        {sender, receiver},
                        return_when=asyncio.FIRST_EXCEPTION,
                    )
                    for t in pending:
                        t.cancel()
                    for t in done:
                        if t.exception():
                            raise t.exception()

            except Exception as exc:
                logger.error("bridge_error error=%s — reconnecting in 3s", exc)
                print(f"\n🔴 연결 오류: {exc} — 3초 후 재연결")
                await asyncio.sleep(3)

    def run(self) -> None:
        asyncio.run(self.run_async())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Javis voice bridge — Mac LLM side")
    parser.add_argument("--server", default="ws://192.168.219.106:8765", help="서버 WebSocket base URL")
    parser.add_argument("--session", default="mac-1", help="Session ID")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Claude model ID")
    parser.add_argument("--max-turns", type=int, default=10, help="대화 히스토리 깊이")
    parser.add_argument("--chunk-ms", type=int, default=80, help="마이크 청크 크기 (ms)")
    parser.add_argument("--idle-flush", type=float, default=1.5, help="침묵 후 AI 호출까지 대기 시간 (초)")
    parser.add_argument("--device", default=None, help="입력 장치 인덱스 또는 이름")
    parser.add_argument("--list-devices", action="store_true", help="입력 장치 목록 출력")
    args = parser.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    bridge = VoiceBridge(
        server=args.server,
        session_id=args.session,
        claude_model=args.model,
        max_turns=args.max_turns,
        chunk_ms=args.chunk_ms,
        device=args.device,
        idle_flush_seconds=args.idle_flush,
    )
    bridge.run()


if __name__ == "__main__":
    main()
