from src.javis_stt.conversation_engine import ConversationEngine
from src.javis_stt.ai_gateway import AIResult


class _FakeGateway:
    def __init__(self):
        self.calls = []

    def generate_with_history(self, session_id: str, text: str, history: list[dict]) -> AIResult:
        self.calls.append({"session_id": session_id, "text": text, "history": list(history)})
        return AIResult(text=f"응답: {text}", error=None)


def test_conversation_engine_sends_history():
    gw = _FakeGateway()
    engine = ConversationEngine(gateway=gw, max_turns=3)

    engine.turn(session_id="s1", text="첫 번째 질문")
    engine.turn(session_id="s1", text="두 번째 질문")

    assert len(gw.calls) == 2
    second_call = gw.calls[1]
    assert len(second_call["history"]) == 2  # user+assistant from first turn
    assert second_call["history"][0]["role"] == "user"
    assert second_call["history"][0]["content"] == "첫 번째 질문"
    assert second_call["history"][1]["role"] == "assistant"
    assert second_call["history"][1]["content"] == "응답: 첫 번째 질문"


def test_conversation_engine_respects_max_turns():
    gw = _FakeGateway()
    engine = ConversationEngine(gateway=gw, max_turns=2)

    for i in range(5):
        engine.turn(session_id="s1", text=f"질문 {i}")

    last_call = gw.calls[-1]
    assert len(last_call["history"]) <= 4  # 2 turns * 2 messages each


def test_conversation_engine_isolates_sessions():
    gw = _FakeGateway()
    engine = ConversationEngine(gateway=gw, max_turns=3)

    engine.turn(session_id="alice", text="안녕")
    engine.turn(session_id="bob", text="안녕하세요")

    bob_call = next(c for c in gw.calls if c["session_id"] == "bob")
    assert len(bob_call["history"]) == 0


def test_conversation_engine_returns_error_on_failure():
    class _FailGateway:
        def generate_with_history(self, session_id, text, history):
            return AIResult(text="", error="timeout")

    engine = ConversationEngine(gateway=_FailGateway(), max_turns=3)
    result = engine.turn(session_id="s1", text="test")

    assert result.error == "timeout"
    assert len(engine._histories["s1"]) == 0
