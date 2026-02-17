"""
Mac-side voice bridge: connects to server STT WebSocket, calls Claude, streams TTS back.

Usage:
  python -m src.voice_llm_bridge \
    --server ws://192.168.219.106:8765/ws/stt \
    --session mac-1 \
    --model claude-haiku-4-5-20251001

Requirements (Mac):
  pip install anthropic websocket-client httpx sounddevice numpy
  export ANTHROPIC_API_KEY=sk-...
"""
import argparse
import json
import logging
from collections import deque

logger = logging.getLogger("javis.bridge")

SYSTEM_PROMPT = """당신은 친근하고 간결한 한국어 개인 비서입니다.
음성 대화에 최적화되어 있으므로 답변을 1-2 문장으로 짧게 유지하세요.
마크다운, 목록, 코드 블록을 사용하지 마세요.
자연스러운 구어체 한국어로 답변하세요."""


class VoiceBridge:
    def __init__(
        self,
        server_ws_url: str,
        server_http_url: str,
        session_id: str,
        claude_model: str = "claude-haiku-4-5-20251001",
        max_turns: int = 10,
        sample_rate: int = 16000,
    ):
        self.server_ws_url = server_ws_url
        self.server_http_url = server_http_url.rstrip("/")
        self.session_id = session_id
        self.claude_model = claude_model
        self.sample_rate = sample_rate
        self._history: deque[dict[str, str]] = deque(maxlen=max_turns * 2)

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

    def _post_tts_and_play(self, session_id: str, response_text: str) -> None:
        import httpx
        import numpy as np
        import sounddevice as sd

        pcm_chunks: list[bytes] = []
        with httpx.Client(timeout=30.0) as client:
            with client.stream(
                "POST",
                f"{self.server_http_url}/v1/voice/turn",
                json={
                    "session_id": session_id,
                    "text": "",
                    "response_text": response_text,
                },
            ) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(chunk_size=4096):
                    if chunk:
                        pcm_chunks.append(chunk)

        if pcm_chunks:
            raw = b"".join(pcm_chunks)
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(audio, samplerate=self.sample_rate, blocking=True)

    def _handle_final_event(self, session_id: str, text: str) -> str:
        response_text = self._call_claude(text)
        self._history.append({"role": "user", "content": text})
        self._history.append({"role": "assistant", "content": response_text})
        self._post_tts_and_play(session_id, response_text)
        return response_text

    def run(self) -> None:
        import websocket  # websocket-client

        logger.info("bridge_connecting url=%s", self.server_ws_url)

        def on_message(ws, message):
            try:
                event = json.loads(message)
            except Exception:
                return

            if event.get("type") == "final":
                text = event.get("text", "").strip()
                if not text:
                    return
                session_id = event.get("session_id", self.session_id)
                logger.info("bridge_final session=%s text=%s", session_id, text[:100])
                try:
                    response = self._handle_final_event(session_id, text)
                    logger.info("bridge_response text=%s", response[:100])
                except Exception:
                    logger.exception("bridge_turn_error session=%s", session_id)

        def on_error(ws, error):
            logger.error("bridge_ws_error error=%s", error)

        def on_close(ws, close_status_code, close_msg):
            logger.info("bridge_ws_closed status=%s", close_status_code)

        def on_open(ws):
            logger.info("bridge_ws_connected")

        ws = websocket.WebSocketApp(
            f"{self.server_ws_url}?session_id={self.session_id}",
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        ws.run_forever(reconnect=5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    parser = argparse.ArgumentParser(description="Javis voice bridge — Mac LLM side")
    parser.add_argument("--server", default="ws://192.168.219.106:8765/ws/stt", help="Server WebSocket URL")
    parser.add_argument("--http", default="", help="Server HTTP URL (default: auto-convert from --server)")
    parser.add_argument("--session", default="mac-1", help="Session ID")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Claude model ID")
    parser.add_argument("--max-turns", type=int, default=10, help="Conversation history depth")
    args = parser.parse_args()

    http_url = args.http or args.server.replace("ws://", "http://").replace("wss://", "https://")
    from urllib.parse import urlparse
    parsed = urlparse(http_url)
    http_base = f"{parsed.scheme}://{parsed.netloc}"

    bridge = VoiceBridge(
        server_ws_url=args.server,
        server_http_url=http_base,
        session_id=args.session,
        claude_model=args.model,
        max_turns=args.max_turns,
    )
    bridge.run()


if __name__ == "__main__":
    main()
