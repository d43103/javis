from collections import defaultdict, deque
from typing import Any

from .ai_gateway import AIResult


class ConversationEngine:
    def __init__(self, gateway: Any, max_turns: int = 10):
        self._gateway = gateway
        self._max_turns = max_turns
        self._histories: dict[str, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_turns * 2)
        )

    def turn(self, session_id: str, text: str) -> AIResult:
        history = list(self._histories[session_id])
        result = self._gateway.generate_with_history(
            session_id=session_id,
            text=text,
            history=history,
        )
        if not result.error:
            self._histories[session_id].append({"role": "user", "content": text})
            self._histories[session_id].append({"role": "assistant", "content": result.text})
        return result

    def clear(self, session_id: str) -> None:
        self._histories.pop(session_id, None)
