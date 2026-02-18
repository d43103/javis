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
        fake_result = json.dumps({"payloads": [{"text": "안녕하세요!"}]})
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
        fake_result = json.dumps({"payloads": [{"text": "응답"}]})
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
