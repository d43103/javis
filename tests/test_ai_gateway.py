from src.javis_stt.ai_gateway import AIGateway


def test_ai_gateway_returns_text_response():
    def fake_requester(payload):
        assert payload["model"] == "qwen3:14b"
        assert payload["stream"] is False
        return {"response": "안녕하세요. 무엇을 도와드릴까요?", "done": True}

    gw = AIGateway(
        base_url="http://127.0.0.1:11434",
        model="qwen3:14b",
        requester=fake_requester,
    )
    out = gw.generate(session_id="s1", text="오늘 일정 알려줘")

    assert "도와드릴까요" in out.text
    assert out.error is None


def test_ai_gateway_openai_format():
    def fake_requester(payload):
        assert "messages" in payload
        assert payload["model"] == "Qwen/Qwen3-14B-AWQ"
        assert payload["stream"] is False
        assert payload["messages"][-1]["role"] == "user"
        assert payload["messages"][-1]["content"] == "안녕하세요"
        return {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "반갑습니다! 무엇을 도와드릴까요?",
                    },
                }
            ]
        }

    gw = AIGateway(
        base_url="http://127.0.0.1:8041",
        model="Qwen/Qwen3-14B-AWQ",
        api_format="openai",
        requester=fake_requester,
    )
    out = gw.generate(session_id="s1", text="안녕하세요")

    assert "도와드릴까요" in out.text
    assert out.error is None


def test_ai_gateway_openai_with_system_prompt():
    captured = {}

    def fake_requester(payload):
        captured["payload"] = payload
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "OK"}}
            ]
        }

    gw = AIGateway(
        base_url="http://127.0.0.1:8041",
        model="Qwen/Qwen3-14B-AWQ",
        api_format="openai",
        system_prompt="You are a helpful assistant.",
        requester=fake_requester,
    )
    gw.generate(session_id="s1", text="test")

    messages = captured["payload"]["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a helpful assistant."
    assert messages[1]["role"] == "user"


def test_ai_gateway_openai_empty_choices():
    def fake_requester(payload):
        return {"choices": []}

    gw = AIGateway(
        base_url="http://127.0.0.1:8041",
        model="test",
        api_format="openai",
        requester=fake_requester,
    )
    out = gw.generate(session_id="s1", text="test")

    assert out.text == ""
    assert out.error is None


def test_ai_gateway_openai_with_thinking():
    def fake_requester(payload):
        assert "extra_body" in payload
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "<think>Let me think about this...</think>\n\n실제 응답입니다.",
                    }
                }
            ]
        }

    gw = AIGateway(
        base_url="http://127.0.0.1:8041",
        model="test",
        api_format="openai",
        enable_thinking=True,
        requester=fake_requester,
    )
    out = gw.generate(session_id="s1", text="test")

    assert out.text == "실제 응답입니다."
    assert "<think>" not in out.text


def test_ai_gateway_retry_on_error():
    call_count = 0

    def flaky_requester(payload):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("connection_refused")
        return {"response": "success"}

    gw = AIGateway(
        base_url="http://127.0.0.1:11434",
        model="test",
        max_retries=2,
        requester=flaky_requester,
    )
    out = gw.generate(session_id="s1", text="test")

    assert out.text == "success"
    assert call_count == 3


def test_ai_gateway_ollama_payload_structure():
    captured = {}

    def fake_requester(payload):
        captured["payload"] = payload
        return {"response": "ok"}

    gw = AIGateway(
        base_url="http://127.0.0.1:11434",
        model="phi4:latest",
        api_format="ollama",
        keep_alive="0s",
        requester=fake_requester,
    )
    gw.generate(session_id="s1", text="hello")

    p = captured["payload"]
    assert p["model"] == "phi4:latest"
    assert p["prompt"] == "hello"
    assert p["stream"] is False
    assert p["keep_alive"] == "0s"
    assert "metadata" in p
