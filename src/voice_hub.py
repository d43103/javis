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
