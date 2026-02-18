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
        # openclaw JSON 응답: {"payloads": [{"text": "..."}]}
        payloads = data.get("payloads", [])
        if payloads and isinstance(payloads, list):
            return payloads[0].get("text", _FALLBACK)
        return _FALLBACK
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
