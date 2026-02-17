from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class AIResult:
    text: str
    error: str | None = None


class AIGateway:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 20,
        max_retries: int = 2,
        keep_alive: str = "0s",
        api_format: str = "ollama",
        system_prompt: str = "",
        enable_thinking: bool = False,
        requester=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.keep_alive = keep_alive
        self.api_format = api_format
        self.system_prompt = system_prompt
        self.enable_thinking = enable_thinking
        self.requester = requester or self._default_request

    def _default_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            if self.api_format == "openai":
                response = client.post(
                    f"{self.base_url}/v1/chat/completions", json=payload
                )
            else:
                response = client.post(
                    f"{self.base_url}/api/generate", json=payload
                )
            response.raise_for_status()
            return response.json()

    def _build_ollama_payload(self, session_id: str, text: str) -> dict[str, Any]:  # deprecated: use api_format="openai"
        return {
            "model": self.model,
            "prompt": text,
            "stream": False,
            "options": {"num_ctx": 4096},
            "keep_alive": self.keep_alive,
            "metadata": {"session_id": session_id},
        }

    def _build_openai_payload(self, session_id: str, text: str) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": text})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if self.enable_thinking:
            payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": True}}
        return payload

    def _extract_ollama_response(self, raw: dict[str, Any]) -> str:  # deprecated: use api_format="openai"
        return raw.get("response", "")

    def _extract_openai_response(self, raw: dict[str, Any]) -> str:
        choices = raw.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if self.enable_thinking and content:
            # Strip <think>...</think> block if present
            import re

            content = re.sub(
                r"<think>.*?</think>\s*", "", content, flags=re.DOTALL
            ).strip()
        return content

    def generate(self, session_id: str, text: str) -> AIResult:
        if self.api_format == "openai":
            payload = self._build_openai_payload(session_id, text)
        else:
            payload = self._build_ollama_payload(session_id, text)

        last_error: str | None = None
        for _ in range(self.max_retries + 1):
            try:
                raw = self.requester(payload)
                if self.api_format == "openai":
                    response_text = self._extract_openai_response(raw)
                else:
                    response_text = self._extract_ollama_response(raw)
                return AIResult(text=response_text, error=None)
            except Exception as exc:
                last_error = str(exc)

        return AIResult(text="", error=last_error or "unknown_error")

    def generate_with_history(self, session_id: str, text: str, history: list[dict]) -> "AIResult":
        """OpenAI-format: sends full conversation history."""
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": text})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        last_error: str | None = None
        for _ in range(self.max_retries + 1):
            try:
                raw = self.requester(payload)
                response_text = self._extract_openai_response(raw)
                return AIResult(text=response_text, error=None)
            except Exception as exc:
                last_error = str(exc)

        return AIResult(text="", error=last_error or "unknown_error")
